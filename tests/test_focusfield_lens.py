from dataclasses import replace
import json
from pathlib import Path
import subprocess
import sys

import cv2
import numpy as np
import pytest
import tifffile

from focusfield.acquisition import Capture, capture_metadata
from focusfield.chart import edge_rois, load_chart, register_capture
from focusfield.config import Recipe, load_recipe, simulation_fixture
from focusfield.controller import ScanController
from focusfield.hardware import SimCamera, SimStage
from focusfield.lens import LensCalibration
from focusfield.pose import estimate_chart_pose
from focusfield.reports import analyze_run
from machine_settings import SETTINGS


def inverse_map(lens):
    width, height = lens.image_size_px
    y, x = np.indices((height, width), dtype=np.float32)
    points = np.dstack((x, y)).reshape(-1, 1, 2)
    matrix = np.asarray(lens.camera_matrix)
    return cv2.undistortPointsIter(points, matrix, np.asarray(lens.dist_coeffs), None, matrix,
                                   (3, 30, 1e-6)).reshape(height, width, 2)


@pytest.fixture(scope="module")
def distorted_chart():
    recipe = Recipe(pixel_format="Mono16", wd_start_mm=19.8, wd_end_mm=20.2, step_mm=0.1,
                    frames_per_position=1, repetitions=1, settle_s=0)
    chart = load_chart(recipe.chart_path)
    lens = LensCalibration("SIMULATED", (1600, 1280), ((920, 0, 800), (0, 920, 640), (0, 0, 1)),
                           (-0.34, 0.13, 0, 0, 0))
    return replace(recipe, lens_calibration=lens), chart, inverse_map(lens)


@pytest.mark.parametrize("pixel_format", ["Mono16", "BayerRG12"])
def test_known_barrel_distortion_recovers_native_edge_rois(distorted_chart, pixel_format):
    recipe, chart, mapping = distorted_chart
    recipe = replace(recipe, pixel_format=pixel_format)
    # Leave enough native samples for the existing 30-analysis-pixel ROI gate on G1.
    chart = dict(chart, slanted_squares=[dict(square, side_mm=4.8) for square in chart["slanted_squares"]])
    camera = SimCamera(SimStage(simulation_fixture()), recipe, chart)
    original = camera.capture()
    pixels = cv2.remap(original.pixels, mapping, None, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
    capture = Capture(pixels, original.metadata).validate()
    with pytest.raises(ValueError, match="reprojection RMS"):
        register_capture(capture, chart, replace(recipe, lens_calibration=None))
    registration, gray, pitch = register_capture(capture, chart, recipe)
    assert registration["lens_corrected"] and registration["full_field_visible"]
    assert registration["reprojection_rms_px"] < 2.5
    assert registration["chart_center_px"] == pytest.approx([800, 640], abs=2)
    raw = pixels if pitch == 1 else pixels[::2, 1::2]
    np.testing.assert_array_equal(gray, raw.astype(float)/(2**capture.metadata["bit_depth"] - 1))
    for square in chart["slanted_squares"]:
        rois = edge_rois(gray, registration["homography"], square,
                         project_chart=registration["project_chart"])
        assert len(rois) == 4 and all(np.shares_memory(roi[2], gray) for roi in rois)


def test_rectification_fallback_still_requires_all_four_tags(distorted_chart, monkeypatch):
    recipe, chart, mapping = distorted_chart
    frame = SimCamera(SimStage(simulation_fixture()), recipe, chart).capture()
    frame.pixels = cv2.remap(frame.pixels, mapping, None, cv2.INTER_LINEAR)
    original = cv2.aruco.ArucoDetector
    calls = []

    class RetryDetector:
        def __init__(self, *args):
            self.detector = original(*args)
            self.refinement = self.detector.getDetectorParameters().cornerRefinementMethod

        def detectMarkers(self, image):
            calls.append((image, self.refinement))
            if len(calls) == 1:
                return (), None, ()
            return self.detector.detectMarkers(image)

    monkeypatch.setattr(cv2.aruco, "ArucoDetector", RetryDetector)
    registration, gray, _ = register_capture(frame, chart, recipe, refine_optical_corners=True)
    assert registration["rectified_marker_detection"]
    assert registration["reprojection_rms_px"] < 3 and len(calls) == 3
    assert calls[1][0] is calls[2][0] and calls[2][1] == cv2.aruco.CORNER_REFINE_SUBPIX
    np.testing.assert_array_equal(gray, frame.pixels / 65535)
    for key in ("marker_corners_px", "refined_marker_corners_px"):
        pose = estimate_chart_pose(chart, registration[key], recipe.lens_calibration)
        assert pose.camera_center_axial_distance_mm == pytest.approx(920 / 16, abs=0.4)
    frame.pixels[:400, :520] = 54000
    with pytest.raises(ValueError, match="missing IDs"):
        register_capture(frame, chart, recipe)


def test_lens_mismatch_stops_before_scan_movement(distorted_chart, tmp_path):
    recipe, chart, _ = distorted_chart
    fixture = simulation_fixture()
    stage = SimStage(fixture)
    recipe = replace(recipe, lens_calibration=replace(recipe.lens_calibration, camera_serial="OTHER"))
    result = ScanController(recipe, SETTINGS, fixture, stage, SimCamera(stage, recipe, chart), tmp_path).run()
    assert result.state == "FAILED" and "for camera OTHER" in result.message
    assert not any(call[0] == "move" for call in stage.calls)
    frame = Capture(np.zeros((64, 64), dtype=np.uint16), capture_metadata("Mono16", "OTHER", 10000, 1))
    with pytest.raises(ValueError, match="frame size"):
        recipe.lens_calibration.validate_capture(frame)


def test_scan_snapshots_profile_for_offline_reanalysis(distorted_chart, tmp_path):
    recipe, chart, mapping = distorted_chart
    fixture, saved = simulation_fixture(), []
    stage = SimStage(fixture)

    class DistortedCamera(SimCamera):
        def capture(self):
            frame = super().capture()
            frame.pixels = cv2.remap(frame.pixels, mapping, None, cv2.INTER_LINEAR)
            saved.append(frame.pixels.copy())
            return frame

    camera = DistortedCamera(stage, recipe, chart)
    result = ScanController(recipe, SETTINGS, fixture, stage, camera, tmp_path).run(analyze=False)
    assert result.state == "COMPLETED", result.message
    run = Path(result.path)
    assert Recipe(**json.loads((run / "config.json").read_text())).lens_calibration == recipe.lens_calibration
    np.testing.assert_array_equal(tifffile.imread(run / "images/frame_000001.tiff"), saved[1])
    assert analyze_run(run)["valid_frames"] == 5


def test_calibration_command_fits_and_validates_separate_checkerboard_images(tmp_path):
    width, height = 800, 600
    lens = LensCalibration("TEST", (width, height), ((690, 0, 400), (0, 700, 300), (0, 0, 1)),
                           (-0.25, 0.07, 0, 0, 0))
    mapping = inverse_map(lens)
    matrix = np.asarray(lens.camera_matrix)
    rect = np.float32([[0, 0], [8*20, 0], [8*20, 6*20], [0, 6*20]])
    board = np.zeros((240, 320), dtype=np.uint8)
    for row in range(6):
        for col in range(8):
            board[40*row:40*(row+1), 40*col:40*(col+1)] = 235 if (row + col) % 2 else 20
    bounds = np.float32([[0, 0], [320, 0], [320, 240], [0, 240]])
    positions = [(-88, -67), (-141, -100), (-65, -106), (-116, -38), (-33, -81),
                 (-118, -68), (-75, -37), (-91, -112), (-130, -42), (-58, -63)]
    images = []
    for i, (x, y) in enumerate(positions):
        rotation = np.array([(i % 3 - 1) * 0.17, (i % 4 - 1.5) * 0.14, (i % 5 - 2) * 0.04])
        translation = np.array([x, y, 345 + 12*(i % 4)], dtype=float)
        projected, _ = cv2.projectPoints(np.column_stack((rect, np.zeros(4))), rotation,
                                         translation, matrix, None)
        ideal = cv2.warpPerspective(board, cv2.getPerspectiveTransform(bounds, projected.reshape(4, 2).astype(np.float32)),
                                    (width, height), borderValue=185)
        img = cv2.remap(ideal, mapping, None, cv2.INTER_LINEAR)
        path = tmp_path / f"view_{i}.png"
        assert cv2.imwrite(str(path), img)
        images.append(str(path))
    output = tmp_path / "profile.json"
    # Run the command through its normal entry point, separately from the Qt test process.
    completed = subprocess.run(
        [sys.executable, "-m", "focusfield", "calibrate-lens", "--camera-serial", "TEST",
         "--corners", "7", "5", "--square-mm", "20", "--output", str(output),
         "--images", *images[:8], "--validation", *images[8:]],
        capture_output=True, text=True, timeout=90,
    )
    assert completed.returncode == 0, completed.stderr
    result = LensCalibration.load(output)
    assert result.camera_serial == "TEST" and result.image_size_px == (width, height)
    assert result.calibration_rms_px < 1 and max(result.validation_rms_px) < 1
    assert result.camera_matrix[0][0] == pytest.approx(690, abs=50)
    assert result.dist_coeffs[0] == pytest.approx(-0.25, abs=0.12)
    recipe = tmp_path / "recipe.toml"
    recipe.write_text('lens_calibration = "profile.json"\n')
    assert load_recipe(recipe).lens_calibration == result


def test_nominal_field_follows_the_fisheye_datasheet_lens():
    import math
    from focusfield.lens import nominal_field_of_view_mm

    width, height = nominal_field_of_view_mm(20, 3.2, 1.55, (4056, 3040))
    assert width == pytest.approx(59.94, abs=0.01) and height == pytest.approx(36.25, abs=0.01)
    assert nominal_field_of_view_mm(40, 3.2, 1.55, (4056, 3040))[0] == pytest.approx(2 * width)
    # Equidistant projection matches the datasheet's 117° / 86° within a few degrees
    assert math.degrees(2 * math.atan(width / 40)) == pytest.approx(117, abs=6)
    with pytest.raises(ValueError):
        nominal_field_of_view_mm(20, 0.5, 1.55, (4056, 3040))


def test_calibrated_field_of_view_matches_its_model():
    import math
    from focusfield.lens import LensCalibration

    f = 3.2 / 0.00155
    fisheye = LensCalibration("TEST", (4056, 3040), ((f, 0, 2028), (0, f, 1520), (0, 0, 1)),
                              (0, 0, 0, 0), model="fisheye")
    assert fisheye.field_of_view_mm(20) == pytest.approx((59.94, 36.25), abs=0.05)
    pinhole = LensCalibration("TEST", (4000, 3000), ((2000, 0, 2000), (0, 2000, 1500), (0, 0, 1)),
                              (0, 0, 0, 0, 0))
    assert pinhole.field_of_view_mm(10) == pytest.approx((20, 15))
