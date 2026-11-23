from dataclasses import replace
from pathlib import Path

import cv2
import numpy as np
import pytest

from focusfield.acquisition import Capture, capture_metadata
from focusfield.calibration import XYCalibration, XYClearance
from focusfield.chart import load_chart, preview_marker_corners
from focusfield.config import DEFAULT_CHART, Recipe
from focusfield.controller import OperatorChecks
from focusfield.generate_chart import build_parser, generate_chart
from focusfield.hardware import SimStage, ZStage
from focusfield.lens import LensCalibration
from focusfield.xy_calibration import OFFSETS, XYMappingController, _fit_mapping
from machine_settings import SETTINGS


JACOBIAN = np.array([[-64., 8.], [5., 60.]])
SIZE = (640, 480)


def checkerboard_image():
    image = np.full(SIZE[::-1], 230, dtype=np.uint8)
    for y in range(6):
        for x in range(8):
            if (x + y) % 2 == 0:
                image[132+y*36:132+(y+1)*36, 176+x*36:176+(x+1)*36] = 25
    return image


@pytest.fixture
def mapping_rig(tmp_path, verified_optical_fixture):
    fixture = verified_optical_fixture
    output = tmp_path / "sheet.svg"
    generate_chart(build_parser().parse_args(["--output", str(output), "--with-checkerboard"]))
    recipe = Recipe(chart_path=str(tmp_path / "sheet_layout.json"), pixel_format="Mono8", settle_s=0)
    clearance = XYClearance((120., 180.), (120., 180.), (99., 101.), 1., 20., 1., 1., True)
    guard = ZStage(fixture, SETTINGS.printer.z_max, printer_travel=SETTINGS.printer.travel,
                   xy_clearance=clearance)
    stage = SimStage(fixture)
    stage.simulated = False
    stage.check_xy = guard.check_xy

    class Camera:
        simulated = False
        count = 0
        blank_at = None
        stop_at = None

        def open(self):
            pass

        def close(self):
            pass

        def fresh(self):
            pass

        def capture(self):
            self.count += 1
            if self.count == self.stop_at:
                controller.stop()
            displacement = JACOBIAN @ (np.array(stage.position_xyz()[:2]) - 150)
            matrix = np.float32([[1, 0, displacement[0]], [0, 1, displacement[1]]])
            image = (np.full(SIZE[::-1], 230, dtype=np.uint8) if self.count == self.blank_at else
                     cv2.warpAffine(checkerboard_image(), matrix, SIZE, borderValue=230))
            meta = capture_metadata("Mono8", fixture.camera_serial, recipe.exposure_us, recipe.gain,
                                    device_frame_id=self.count)
            return Capture(image, meta)

    camera = Camera()
    controller = XYMappingController(recipe, SETTINGS, fixture, stage, camera, tmp_path,
        clearance=clearance, operator="TEST", step_mm=.5, printer_state=lambda: "IDLE",
        checks=OperatorChecks(True, True, True, True, True), operator_confirmed=True)
    return controller, camera, stage, guard


def test_measures_and_saves_mapping_without_lens_or_aruco(mapping_rig):
    controller, _, stage, _ = mapping_rig
    result = controller.run()
    assert result.state == "COMPLETED", result.message
    path = Path(result.details["xy_path"])
    assert path.name == "optical_xy_mapping.json"
    profile = XYCalibration.load(path).validate(controller.fixture)
    np.testing.assert_allclose(profile.jacobian_px_per_mm, JACOBIAN, atol=.2)
    assert profile.residual_px < .3
    assert profile.uncertainty_px < .3
    assert profile.sample_z_mm == 100
    assert profile.valid_radius_px > 10
    assert result.details["panel_position"] == ("checkerboard", (150., 150.))
    assert stage.position_xyz() == (150, 150, 100)
    assert all(call[0] != "move" for call in stage.calls)
    assert profile.for_centering(None) is None
    lens = LensCalibration(profile.camera_serial, SIZE, ((300, 0, 321), (0, 300, 240), (0, 0, 1)), (0, 0, 0, 0))
    centered = profile.for_centering(lens)
    assert centered.optical_center_px == (321, 240) and centered.valid_radius_px < profile.valid_radius_px
    assert profile.for_centering(replace(lens, camera_serial="OTHER")) is None


@pytest.mark.parametrize("failure", ["approval", "motion_only", "bounds", "working_distance", "no_target", "target_lost", "stop"])
def test_mapping_guard_and_stop_do_not_save_or_recover(mapping_rig, tmp_path, failure):
    controller, camera, stage, guard = mapping_rig
    if failure == "approval":
        controller.operator_confirmed = False
    elif failure == "motion_only":
        controller.dry_run = True
    elif failure == "bounds":
        guard.xy_clearance = controller.clearance = replace(controller.clearance, safe_x_mm=(148., 151.3))
    elif failure == "working_distance":
        stage.z += .15
    elif failure == "no_target":
        camera.blank_at = 1
    elif failure == "target_lost":
        camera.blank_at = 2
    else:
        camera.stop_at = 2
    result = controller.run()
    assert result.state == ("ABORTED" if failure == "stop" else "FAILED"), result.message
    assert not list(tmp_path.glob("*/optical_xy_mapping.json"))
    moves = [call for call in stage.calls if call[0].startswith("move")]
    assert moves == ([("move_xy", 150.5, 150.)] if failure in ("target_lost", "stop") else [])


def test_mapping_rejects_independent_diagonal_error():
    xy = OFFSETS * .5
    pixels = xy @ JACOBIAN.T + [320, 240]
    pixels[6] += [4, 0]
    with pytest.raises(ValueError, match="XY mapping validation failed"):
        _fit_mapping(xy, pixels)


@pytest.mark.parametrize("pixel_format", ["Mono8", "BayerRG8"])
def test_marker_framing_returns_native_corners_without_lens_profile(pixel_format):
    chart = load_chart(DEFAULT_CHART)
    image = np.full((600, 800), 215, dtype=np.uint8)
    dictionary = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
    for marker_id, (x, y) in enumerate(((100, 100), (600, 100), (600, 400), (100, 400))):
        image[y:y+90, x:x+90] = cv2.aruco.generateImageMarker(dictionary, marker_id, 90)
    capture = Capture(image, capture_metadata(pixel_format, "TEST", 1000, 1))
    result = preview_marker_corners(capture, chart, 3.2, 1.55)
    assert set(result) == {0, 1, 2, 3}
    assert result[0][0] == pytest.approx([100, 100], abs=2)
    assert result[2][0] == pytest.approx([600, 400], abs=2)
