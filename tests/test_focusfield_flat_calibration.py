from dataclasses import replace

import cv2
import numpy as np
import pytest

from focusfield.acquisition import Capture, capture_metadata
from focusfield.chart import load_chart, register_chart
from focusfield.config import DEFAULT_CHART, Recipe, simulation_fixture
from focusfield.flat_calibration import FlatCalibrationController, fit_flat_lens
from focusfield.generate_chart import build_parser, generate_chart
from focusfield.hardware import SimCamera, SimStage
from focusfield.lens import LensCalibration
from focusfield.setup import simulation_xy_calibration
from machine_settings import SETTINGS


BOARD = {"inner_corners": [7, 5], "square_mm": 3.7}
SIZE = (1600, 1200)
MATRIX = np.array([[780., 0., 835.], [0., 780., 565.], [0., 0., 1.]])
COEFFS = np.array([-.045, .012, -.002, .0006])


def projected_board(xy, z):
    grid = np.mgrid[:7, :5].T.reshape(-1, 2).astype(float)
    grid = (grid - grid.mean(axis=0)) * BOARD["square_mm"]
    points = np.column_stack((grid + xy, np.full(len(grid), 20 + z - 100)))
    projected, _ = cv2.fisheye.projectPoints(points.reshape(1, -1, 3), np.array([.025, -.018, .12]),
                                             np.zeros(3), MATRIX, COEFFS)
    return projected.reshape(-1, 2)


def test_fit_flat_lens_validates_different_xy_positions_and_depths():
    offsets = [(0, 0), (-12, 0), (12, 0), (0, -10), (0, 10), (-9, -8), (9, 8)]
    groups = [[(z, projected_board(xy, z)) for z in (99.5, 100., 100.5)] for xy in offsets]
    lens = fit_flat_lens(groups, BOARD, SIZE, "TEST", 20., 3.)
    assert lens.model == "fisheye"
    np.testing.assert_allclose(lens.camera_matrix, MATRIX, atol=.01)
    np.testing.assert_allclose(lens.dist_coeffs, COEFFS, atol=.0001)
    assert max(lens.validation_rms_px) < .01
    groups[-1][-1][1][:, 0] += 10
    with pytest.raises(ValueError, match="Held-out checkerboard view"):
        fit_flat_lens(groups, BOARD, SIZE, "TEST", 20., 3.)


def test_fisheye_profile_registers_the_slanted_edge_target():
    fixture = simulation_fixture()
    chart = load_chart(DEFAULT_CHART)
    ideal = SimCamera(SimStage(fixture), Recipe(), chart).capture().pixels[..., 1]
    height, width = ideal.shape
    y, x = np.indices((height, width), dtype=np.float64)
    lens = LensCalibration(fixture.camera_serial, (width, height), MATRIX, COEFFS, model="fisheye")
    source = lens.undistort(np.dstack((x, y)).reshape(-1, 2), MATRIX)
    raw = cv2.remap(ideal, source.reshape(height, width, 2).astype(np.float32), None, cv2.INTER_LINEAR)
    registration = register_chart(raw.astype(float)/255, chart, lens_calibration=lens)
    assert registration["full_field_visible"]
    assert registration["reprojection_rms_px"] < 3


@pytest.mark.parametrize(("stop_during_capture", "transfer"), [(False, True), (True, True), (False, False)])
def test_combined_controller_fits_before_mtf_and_stops_without_transfer(
        tmp_path, monkeypatch, stop_during_capture, transfer):
    output = tmp_path / "sheet.svg"
    generate_chart(build_parser().parse_args(["--output", str(output), "--with-checkerboard"]))
    recipe = Recipe(chart_path=str(tmp_path / "sheet_layout.json"))
    fixture = simulation_fixture()
    xy = replace(simulation_xy_calibration(fixture), image_size_px=SIZE,
                 jacobian_px_per_mm=((39., 0.), (0., 39.)))

    class SafeStage(SimStage):
        def check_xy(self, x, y, z):
            assert 100 < x < 200 and 100 < y < 200 and 99 <= z <= 101

    stage = SafeStage(fixture)

    class Camera:
        simulated = True

        def open(self):
            pass

        def close(self):
            pass

        def fresh(self):
            pass

        def capture(self):
            return Capture(np.zeros((SIZE[1], SIZE[0], 3), dtype=np.uint8),
                           capture_metadata("RGB8", fixture.camera_serial, recipe.exposure_us, recipe.gain))

    def checkerboard(capture, board):
        point = projected_board((stage.x - 150., stage.y - 150.), stage.z)
        if stop_during_capture and stage.z < 100:
            controller.stop()
        return point[::-1] if stage.z > 100 else point

    def check_mtf(capture, chart, active_recipe):
        assert transfer
        assert stage.position_xyz() == (185., 150., 100.)
        assert active_recipe.lens_calibration.model == "fisheye"
        assert list(tmp_path.glob("lens-*/lens.json"))
        return {"full_field_visible": True, "homography": None, "project_chart": None,
                "reprojection_rms_px": .71}, None, 1

    monkeypatch.setattr("focusfield.flat_calibration.checkerboard_corners", checkerboard)
    monkeypatch.setattr("focusfield.flat_calibration.register_capture", check_mtf)
    positions = {"checkerboard": (150., 150.)}
    if transfer:
        positions["mtf"] = (185., 150.)
    controller = FlatCalibrationController(recipe, SETTINGS, fixture, stage, Camera(), tmp_path,
                                           xy_calibration=xy, panel_positions=positions)
    result = controller.run()
    if stop_during_capture:
        assert result.state == "ABORTED", result.message
        assert not list(tmp_path.glob("lens-*/lens.json"))
        assert ("move_xy", 185., 150.) not in stage.calls
    else:
        assert result.state == "COMPLETED", result.message
        lens = LensCalibration.load(result.details["lens_path"])
        assert lens == result.details["lens_calibration"]
        assert lens.camera_serial == fixture.camera_serial
        assert max(lens.validation_rms_px) < .01
        if transfer:
            assert result.details["preview_registration"]["full_field_visible"]
        else:
            assert "preview_registration" not in result.details
            assert ("move_xy", 185., 150.) not in stage.calls
