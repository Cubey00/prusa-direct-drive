from copy import deepcopy
from dataclasses import asdict, replace
import csv
import json
from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from focusfield.acquisition import Capture
from focusfield.chart import load_chart, register_capture
from focusfield.config import DEFAULT_CHART, Recipe, load_recipe, simulation_fixture
from focusfield.controller import OperatorChecks, ScanController
from focusfield.hardware import SimCamera, SimStage
from focusfield.lens import LensCalibration
from focusfield.optical_distance import (OpticalDistanceValidation, OpticalReference, lens_fingerprint,
                                        marker_geometry_fingerprint, verify_working_distance)
from focusfield.pose import estimate_chart_pose
from machine_settings import SETTINGS


@pytest.fixture
def chart():
    return load_chart(DEFAULT_CHART)


def calibrated_lens(model):
    f = 1800 if model == "fisheye" else 850
    distortion = (-0.06, 0.015, -0.003, 0.0005) if model == "fisheye" else (-0.05, 0.008, 0.002, -0.001, 0)
    return LensCalibration("TEST", (4056, 3040), ((f, 0, 2040), (0, f*1.02, 1505), (0, 0, 1)),
                           distortion, model=model)


def project_chart(chart, lens, axial, *, rotation=(0.04, -0.05, 0.015), offset=(2.1, -1.6)):
    points = []
    for marker in chart["fiducials"]:
        x, y = marker["top_left_mm"]
        s = marker["size_mm"]
        points.extend([(x, y, 0), (x+s, y, 0), (x+s, y+s, 0), (x, y+s, 0)])
    points = np.asarray(points, dtype=float)
    rotation = np.array(rotation, dtype=float)
    basis, _ = cv2.Rodrigues(rotation)
    center = points.mean(axis=0)
    axis_target = center + [*offset, 0]
    translation = np.array([0., 0., axial]) - basis @ axis_target
    matrix, distortion = np.asarray(lens.camera_matrix), np.asarray(lens.dist_coeffs)
    if lens.model == "fisheye":
        pixels, _ = cv2.fisheye.projectPoints(points[None, ...], rotation, translation, matrix, distortion)
    else:
        pixels, _ = cv2.projectPoints(points, rotation, translation, matrix, distortion)
    corners = {str(marker["id"]): corners for marker, corners in
               zip(chart["fiducials"], pixels.reshape(-1, 4, 2))}
    return corners, basis, translation


@pytest.mark.parametrize("model", ["pinhole", "fisheye"])
@pytest.mark.parametrize("axial", [18., 19., 20., 21., 22.])
def test_recovers_noiseless_metric_pose_at_known_distances(chart, model, axial):
    lens = calibrated_lens(model)
    corners, basis, translation = project_chart(chart, lens, axial)
    pose = estimate_chart_pose(chart, corners, lens)
    # Full-precision corner input and identical synthetic lens model. These
    # tolerances check numerical convergence rather than measurement accuracy.
    assert pose.camera_center_axial_distance_mm == pytest.approx(axial, abs=1e-7)
    assert pose.camera_center_perpendicular_distance_mm == pytest.approx(axial * basis[2, 2], abs=1e-7)
    assert pose.tilt_deg == pytest.approx(np.degrees(np.arccos(basis[2, 2])), abs=1e-7)
    assert pose.translation_mm == pytest.approx(translation, abs=1e-7)
    assert pose.reprojection_rms_px < 1e-6


def test_frontoparallel_centered_target(chart):
    lens = calibrated_lens("fisheye")
    corners, _, _ = project_chart(chart, lens, 20, rotation=(0, 0, 0), offset=(0, 0))
    pose = estimate_chart_pose(chart, corners, lens)
    assert pose.camera_center_axial_distance_mm == pytest.approx(20, abs=1e-7)
    assert pose.tilt_deg == pytest.approx(0, abs=1e-6)


def test_noisy_wide_angle_points_still_resolve_two_mm_change(chart):
    lens = calibrated_lens("fisheye")
    rng = np.random.default_rng(2026)
    measured = []
    for axial in (18, 20):
        corners, _, _ = project_chart(chart, lens, axial)
        for points in corners.values():
            points += rng.normal(0, 0.7, points.shape)
        pose = estimate_chart_pose(chart, corners, lens)
        assert 0.3 < pose.reprojection_rms_px < 1.5
        measured.append(pose.camera_center_axial_distance_mm)
    assert measured[1] - measured[0] > 1.5


def test_wrong_geometry_calibration_or_bad_corner_are_rejected(chart):
    lens = calibrated_lens("fisheye")
    corners, _, _ = project_chart(chart, lens, 20)
    wrong_chart = deepcopy(chart)
    wrong_chart["fiducials"][0]["top_left_mm"][0] += 1
    no_distortion = replace(lens, dist_coeffs=(0, 0, 0, 0))
    wrong_id = dict(corners, **{"0": corners["1"], "1": corners["0"]})
    hot_corner = deepcopy(corners)
    hot_corner["3"][0, 0] += 10
    for layout, image_corners, calibration in ((wrong_chart, corners, lens),
                                               (chart, corners, no_distortion),
                                               (chart, wrong_id, lens),
                                               (chart, hot_corner, lens)):
        with pytest.raises(ValueError, match="pose"):
            estimate_chart_pose(layout, image_corners, calibration)


def test_missing_nonfinite_mirrored_and_overlapping_marker_geometry(chart):
    lens = calibrated_lens("fisheye")
    corners, _, _ = project_chart(chart, lens, 20)
    bad = deepcopy(corners)
    bad["0"][0, 0] = np.nan
    mirror = {key: points * [-1, 1] + [4055, 0] for key, points in corners.items()}
    overlapping = deepcopy(chart)
    overlapping["fiducials"][0]["size_mm"] = 60
    with pytest.raises(ValueError, match="four unique"):
        estimate_chart_pose(chart, {key: points for key, points in corners.items() if key != "0"}, lens)
    with pytest.raises(ValueError, match="native sensor"):
        estimate_chart_pose(chart, bad, lens)
    with pytest.raises(ValueError, match="face the camera"):
        estimate_chart_pose(chart, mirror, lens)
    with pytest.raises(ValueError, match="overlap"):
        estimate_chart_pose(overlapping, corners, lens)


def test_rejects_two_nearly_indistinguishable_planar_poses(chart):
    lens = calibrated_lens("pinhole")
    # The board spans only a few dozen pixels. Weak perspective makes the
    # two tilt directions yield nearly identical distorted observations.
    corners, _, _ = project_chart(chart, lens, 2000, rotation=(0.45, -0.3, 0.015))
    with pytest.raises(ValueError, match="Ambiguous planar optical pose"):
        estimate_chart_pose(chart, corners, lens)


def test_rejects_pose_outside_plausible_tilt(chart):
    lens = calibrated_lens("pinhole")
    corners, _, _ = project_chart(chart, lens, 90, rotation=(1.34, 0, 0), offset=(0, 0))
    with pytest.raises(ValueError, match="physically plausible|reprojection fit failed"):
        estimate_chart_pose(chart, corners, lens)


def synthetic_reference(lens, chart, *, pixel_format="Mono8"):
    return OpticalReference(
        offset_mm=1.25, uncertainty_mm=0.08, lens_reference_plane="TEST lens plane",
        provenance="synthetic offset data", lens_sha256=lens_fingerprint(lens),
        marker_geometry_sha256=marker_geometry_fingerprint(chart), pixel_format=pixel_format,
        validation=OpticalDistanceValidation(checks_mm=((17, 18.29), (20, 21.22), (23, 24.22)),
            uncertainty_mm=0.2, max_tilt_deg=10, max_axis_offset_mm=8, max_reprojection_rms_px=2,
            provenance="synthetic held-out positions"))


def test_two_mm_discrepancy_requires_a_matching_independently_validated_reference(chart):
    lens = calibrated_lens("fisheye")
    reference = synthetic_reference(lens, chart)
    recipe = Recipe(pixel_format="Mono8", lens_calibration=lens, optical_reference=reference)
    corners, _, _ = project_chart(chart, lens, 19.25)
    registration = {"marker_corners_px": corners}
    comparison = dict(printer_wd=20, printer_reference={"lens_reference_plane": "TEST lens plane", "uncertainty_mm": .15})
    verified = verify_working_distance(chart, recipe, registration, **comparison)
    assert verified["optical_distance_status"] == "Warning" and verified["optical_distance_valid"]
    assert verified["printer_working_distance_mm"] == 20
    assert verified["optical_working_distance_mm"] == pytest.approx(18)
    assert verified["working_distance_difference_mm"] == pytest.approx(-2)
    assert verified["optical_discrepancy_threshold_mm"] == pytest.approx(.35)
    assert verified["optical_distance_uncertainty_mm"] == .2
    for change in ({"optical_reference": None}, {"optical_reference": replace(reference, validation=None)},
                   {"optical_reference": replace(reference, marker_geometry_sha256="0" * 64)},
                   {"optical_reference": replace(reference, validation=replace(reference.validation, max_tilt_deg=1))},
                   {"optical_reference": replace(reference, lens_sha256="0" * 64)}, {"pixel_format": "Mono16"}):
        result = verify_working_distance(chart, replace(recipe, **change), registration, **comparison)
        assert result["optical_distance_status"] == "Unavailable" and not result["optical_distance_valid"]
        assert result["optical_working_distance_mm"] is None and result["working_distance_difference_mm"] is None
        assert result["optical_pose"]["camera_center_axial_distance_mm"] == pytest.approx(19.25)
    disagrees = replace(recipe, optical_discrepancy_threshold_mm=2.5)
    result = verify_working_distance(chart, disagrees, registration, **comparison)
    assert result["optical_distance_status"] == "Valid"
    assert result["optical_discrepancy_threshold_mm"] == 2.5
    mismatched_plane = dict(comparison, printer_reference={"lens_reference_plane": "another plane", "uncertainty_mm": .15})
    result = verify_working_distance(chart, recipe, registration, **mismatched_plane)
    assert result["optical_distance_valid"] and result["working_distance_difference_mm"] is None
    assert "definitions differ" in result["optical_distance_reason"]


def test_recipe_loads_optical_reference_and_preserves_held_out_checks(chart, tmp_path):
    lens = calibrated_lens("pinhole")
    reference = synthetic_reference(lens, chart)
    with pytest.raises(ValueError, match="checks exceed"):
        replace(reference, offset_mm=1.7)
    (tmp_path / "reference.json").write_text(json.dumps(asdict(reference)))
    recipe_file = tmp_path / "recipe.toml"
    recipe_file.write_text('optical_reference = "reference.json"\nmeasure_optical_distance = true\n')
    recipe = load_recipe(recipe_file)
    assert recipe.optical_reference == reference and recipe.measure_optical_distance
    assert Recipe(**json.loads(json.dumps(recipe.to_dict()))) == recipe


@pytest.mark.parametrize("pixel_format", ["Mono8", "BayerRG8"])
def test_subpixel_comparison_on_low_contrast_defocused_frames(chart, pixel_format):
    lens = LensCalibration("SIMULATED", (1600, 1280), ((308, 0, 800), (0, 308, 640), (0, 0, 1)), (0, 0, 0, 0))
    recipe = Recipe(pixel_format=pixel_format, lens_calibration=lens)
    camera = SimCamera(SimStage(simulation_fixture()), recipe, chart)
    camera.offset_px = np.array([0.36, -0.42])
    original = camera.capture()
    pixels = cv2.GaussianBlur(original.pixels, (0, 0), 1.6)
    pixels = np.uint8(np.rint(pixels * 0.27 + 95))
    capture = Capture(pixels, original.metadata).validate()
    normal, _, _ = register_capture(capture, chart, recipe)
    refined, gray, pitch = register_capture(capture, chart, recipe, refine_optical_corners=True)
    np.testing.assert_array_equal(refined["homography"], normal["homography"])
    assert refined["refined_marker_corners_px"] != normal["marker_corners_px"]
    samples = pixels if pitch == 1 else pixels[::2, 1::2]
    np.testing.assert_array_equal(gray, samples / 255)
    for key in ("marker_corners_px", "refined_marker_corners_px"):
        pose = estimate_chart_pose(chart, refined[key], lens)
        assert pose.reprojection_rms_px < 3
        assert pose.camera_center_axial_distance_mm == pytest.approx(19.25, abs=0.3)
    if pitch == 2:
        unrefined = np.asarray(list(normal["marker_corners_px"].values()))
        assert np.all(unrefined[..., 0] % 2 == 1) and np.all(unrefined[..., 1] % 2 == 0)


def test_distance_from_wide_angle_fisheye_chart_images(chart):
    lens = LensCalibration("SIMULATED", (1600, 1280), ((680, 0, 800), (0, 680, 640), (0, 0, 1)),
                           (-0.04, 0.008, -0.002, 0.0004), model="fisheye")
    recipe = Recipe(pixel_format="Mono8", lens_calibration=lens)
    camera = SimCamera(SimStage(simulation_fixture()), recipe, chart)
    original = camera.capture()
    y, x = np.indices(original.pixels.shape, dtype=np.float32)
    pixels = np.dstack((x, y)).reshape(-1, 1, 2)
    rays = cv2.fisheye.undistortPoints(pixels, np.asarray(lens.camera_matrix), np.asarray(lens.dist_coeffs))
    rays = rays.reshape(*x.shape, 2)
    for depth in (18., 20.):
        # Intersect each sensor ray with the known plane, then sample the
        # simulator artwork at its known printed-coordinate scale.
        mapping = rays * (depth * camera.scale) + np.float32([800, 640])
        image = cv2.remap(original.pixels, mapping, None, cv2.INTER_LINEAR, borderValue=209)
        registration, _, _ = register_capture(Capture(image, original.metadata), chart, recipe,
                                               refine_optical_corners=True)
        for key in ("marker_corners_px", "refined_marker_corners_px"):
            pose = estimate_chart_pose(chart, registration[key], lens)
            assert pose.camera_center_axial_distance_mm == pytest.approx(depth, abs=0.15)


@pytest.fixture
def charted_rig(chart, verified_optical_fixture):
    lens = LensCalibration("SIMULATED", (1600, 1280), ((308, 0, 800), (0, 308, 640), (0, 0, 1)), (0, 0, 0, 0))
    fixture = verified_optical_fixture
    recipe = Recipe(wd_start_mm=19.8, wd_end_mm=20.2, step_mm=0.1, frames_per_position=1,
                    repetitions=1, settle_s=0, pixel_format="Mono8", lens_calibration=lens,
                    optical_reference=synthetic_reference(lens, chart), measure_optical_distance=True,
                    expected_width_px=1600, expected_height_px=1280)
    stage = SimStage(fixture)
    stage.simulated = False

    class StationaryChartCamera(SimCamera):
        simulated = False

        def capture(self):
            frame = super().capture()
            frame.metadata["simulated"] = False
            return frame

    return fixture, recipe, stage, StationaryChartCamera(stage, recipe, chart)


def test_scan_persists_optical_pose_and_reports_warning_without_relabelling_mtf(charted_rig, tmp_path):
    from focusfield.reports import analyze_run

    fixture, recipe, stage, camera = charted_rig
    controller = ScanController(recipe, SETTINGS, fixture, stage, camera, tmp_path,
        printer_state=lambda: "IDLE", checks=OperatorChecks(True, True, True, True, True), operator_confirmed=True)
    run = controller.run(analyze=False)
    assert run.state == "COMPLETED" and "scan potentially invalid" in run.message
    path = Path(run.path)
    manifest_path = path / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    assert manifest["optical_distance_potentially_invalid"]
    assert [c[1] for c in stage.calls if c[0] == "move"] == pytest.approx([99.8, 99.9, 100, 100.1, 100.2])
    assert Recipe(**json.loads((path / "config.json").read_text())) == recipe
    nominal = manifest["frames"][2]
    assert nominal["nominal_working_distance_mm"] == nominal["optical_distance"]["printer_working_distance_mm"] == 20
    assert nominal["optical_distance"]["optical_working_distance_mm"] == pytest.approx(18, abs=0.15)
    assert nominal["optical_distance"]["optical_pose"]["translation_mm"]
    # Exercise offline estimation from a frame with no acquisition-time distance.
    del nominal["optical_distance"]
    manifest_path.write_text(json.dumps(manifest))
    summary = analyze_run(path)
    assert summary["working_distance_verification"]["warning_frames"] == 5
    assert any("Distance discrepancy" in reason for reason in summary["measurement_rejection_reasons"])
    assert not summary["measurement_valid"]
    assert json.loads(manifest_path.read_text())["frames"][2]["optical_distance"]["optical_pose"]
    with (path / "working_distance.csv").open() as stream:
        optical = list(csv.DictReader(stream))[2]
    assert optical["optical_distance_status"] == "Warning" and float(optical["printer_working_distance_mm"]) == 20
    with (path / "sfr_results.csv").open() as stream:
        assert sorted({float(row["wd_mm"]) for row in csv.DictReader(stream)}) == pytest.approx(recipe.distances())
    assert "Working-distance verification" in (path / "report.html").read_text()


@pytest.mark.parametrize("printer", ["available", "offline", "stop"])
def test_check_chart_uses_a_cancellable_read_only_printer_query(charted_rig, qtbot, tmp_path, monkeypatch, printer):
    import threading

    import gantry_controls as gc
    from PySide6.QtCore import QSettings
    from pathprogrammer.ui import focusfield_panel
    from pathprogrammer.ui.main_window import MainWindow

    fixture, recipe, stage, camera = charted_rig
    calls = []
    queried, closed = threading.Event(), threading.Event()

    def send(command, *, cancel, timeout):
        calls.append(command)
        queried.set()
        if printer == "stop":
            assert cancel.wait(5)
            raise InterruptedError("STOP requested")
        if printer == "offline":
            raise gc.PrinterError("No printer connected")
        return "X:150 Y:150 Z:100\nok"

    window = MainWindow(QSettings(str(tmp_path / "settings.ini"), QSettings.IniFormat))
    qtbot.addWidget(window)
    try:
        monkeypatch.setattr(gc, "send_gcode", send)
        monkeypatch.setattr(camera, "close", closed.set)
        monkeypatch.setattr(focusfield_panel, "load_settings", lambda: SimpleNamespace(focusfield=fixture))
        monkeypatch.setattr(focusfield_panel, "CameraAdapter", lambda _: camera)
        panel = window.focusfield
        panel.simulate.setChecked(False)
        panel.apply_recipe(recipe)
        panel.check_preview()
        if printer == "stop":
            qtbot.waitUntil(queried.is_set, timeout=5000)
            window.stop()
        qtbot.waitUntil(lambda: panel.thread is None, timeout=10000)
        message = panel.status.toPlainText()
        if printer == "available":
            assert panel.last_preview["optical_distance"]["optical_distance_status"] == "Warning"
            assert "Working-distance verification" in message and "Printer working distance: 20.00 mm" in message
            assert "Optical working distance: 18." in message and "Distance discrepancy" in message
        elif printer == "offline":
            assert panel.last_preview["optical_distance"]["optical_distance_valid"]
            assert "Printer working distance: Unavailable" in message and "Optical working distance: 18." in message
        else:
            assert panel.last_preview is None and "ABORTED" in message
        assert calls == ["M114"] and not stage.calls and closed.is_set()
    finally:
        window.server.close()
