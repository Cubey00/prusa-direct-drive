"""Calibrate a fixed-focus wide-angle lens from flat targets and measured Z motion."""
from dataclasses import asdict, replace
from pathlib import Path
import tempfile
import time

import cv2
import numpy as np
from scipy.optimize import least_squares

from .acquisition import analysis_plane
from .chart import load_chart, register_capture
from .controller import RunResult, ScanController
from .data import atomic_json
from .lens import LensCalibration, distort_points


FIT_PLACEMENTS = 5
PLACEMENTS = FIT_PLACEMENTS + 2


def checkerboard_corners(capture, board):
    plane, pitch = analysis_plane(capture.pixels, capture.metadata)
    image = np.uint8(plane * 255)
    size = tuple(board["inner_corners"])
    found, corners = cv2.findChessboardCorners(image, size, cv2.CALIB_CB_ADAPTIVE_THRESH |
                                              cv2.CALIB_CB_NORMALIZE_IMAGE | cv2.CALIB_CB_FAST_CHECK)
    if found:
        cv2.cornerSubPix(image, corners, (5, 5), (-1, -1), (3, 40, 0.001))
    else:
        found, corners = cv2.findChessboardCornersSB(image, size, cv2.CALIB_CB_NORMALIZE_IMAGE)
    if not found:
        raise ValueError("Checkerboard not found. Frame the entire board with a white margin and check exposure.")
    points = corners.reshape(-1, 2).astype(float) * pitch
    if pitch == 2:
        points[:, 0] += 1  # The Bayer green analysis lattice starts at sensor column one.
    return points


def checkerboard_center(points, board):
    cols, rows = board["inner_corners"]
    grid = points.reshape(rows, cols, 2)
    return grid[(rows-1)//2:rows//2+1, (cols-1)//2:cols//2+1].mean(axis=(0, 1))


def fit_flat_lens(groups, board, image_size, serial, nominal_wd_mm, max_rms_px, *, check_abort=lambda: None):
    """Each group has a fixed XY placement and near, middle and far (Z, corners) observations."""
    if len(groups) != PLACEMENTS or any(len(group) != 3 for group in groups):
        raise ValueError("Calibration needs three depths at five fit positions and two validation positions")
    depths = np.array([[z for z, _ in group] for group in groups])
    if not np.isfinite(depths).all() or np.any(np.diff(depths, axis=1) <= 0):
        raise ValueError("Calibration requires three distinct measured depths at every position")
    depths -= depths[0, 1]
    pixels = np.array([[points for _, points in group] for group in groups], dtype=float)
    cols, rows = board["inner_corners"]
    square = board["square_mm"]
    grid = np.mgrid[:cols, :rows].T.reshape(-1, 2).astype(float)
    grid = (grid - grid.mean(axis=0)) * square
    size = np.asarray(image_size)
    if pixels.shape != (PLACEMENTS, 3, len(grid), 2) or not np.isfinite(pixels).all():
        raise ValueError("Calibration contains invalid checkerboard observations")
    fit_pixels = pixels[:FIT_PLACEMENTS].reshape(-1, 2)
    if np.any(fit_pixels.min(axis=0) > .23 * size) or np.any(fit_pixels.max(axis=0) < .77 * size):
        raise ValueError("Checkerboard fit positions did not cover both sides of each sensor axis")

    centered = pixels[0, 1].reshape(rows, cols, 2)
    dx = centered[rows // 2, cols // 2 + 1] - centered[rows // 2, cols // 2]
    focal = np.linalg.norm(dx) / square * nominal_wd_mm
    origin = size / 2
    center_angle = np.arctan2(dx[1], dx[0])
    cos, sin = np.cos(center_angle), np.sin(center_angle)
    offsets = (pixels[:, 1].mean(axis=1) - origin) @ np.array([[cos, -sin], [sin, cos]])
    offsets *= nominal_wd_mm / focal

    def unpack(params):
        f, cx, cy = params[:3]
        matrix = np.array([[f, 0, cx], [0, f, cy], [0, 0, 1]])
        return matrix, params[3:7], params[7:10], params[10]

    def predict(params, xy, z):
        matrix, coeffs, rotation, base_depth = unpack(params)
        points = np.column_stack((grid + xy, np.full(len(grid), base_depth + z)))
        projected, _ = cv2.fisheye.projectPoints(points.reshape(1, -1, 3),
                                                  rotation, np.zeros(3), matrix, coeffs)
        return projected.reshape(-1, 2)

    def residual(params):
        check_abort()
        xy = params[11:].reshape(FIT_PLACEMENTS, 2)
        return np.concatenate([(predict(params, offset, z) - actual).ravel()
                               for offset, group_z, group_px in zip(xy, depths, pixels)
                               for z, actual in zip(group_z, group_px)])

    start = np.r_[focal, origin, np.zeros(4), 0., 0., center_angle, nominal_wd_mm,
                  offsets[:FIT_PLACEMENTS].ravel()]
    low = np.r_[1., 0., 0., np.full(7, -np.inf), max(.01, .01-depths.min()),
                np.full(2 * FIT_PLACEMENTS, -np.inf)]
    high = np.r_[np.inf, size-1., np.full(8 + 2 * FIT_PLACEMENTS, np.inf)]
    result = least_squares(residual, start, bounds=(low, high), x_scale="jac", loss="soft_l1",
                           f_scale=1., max_nfev=300)
    if not result.success:
        raise ValueError("Lens fit did not converge. Check checkerboard coverage and the separation between Z positions")
    fit_error = float(np.sqrt(np.mean(np.sum(residual(result.x).reshape(-1, 2)**2, axis=1))))
    if fit_error > max_rms_px:
        raise ValueError(f"Checkerboard fit is {fit_error:.2f} px RMS, above the {max_rms_px:g} px limit")

    errors = []
    for group_z, group_px, offset in zip(depths[FIT_PLACEMENTS:], pixels[FIT_PLACEMENTS:],
                                         offsets[FIT_PLACEMENTS:]):
        check_abort()
        # The middle frame determines only placement. The held-out depths check scale
        # and distortion with the camera parameters frozen.
        placement = least_squares(lambda xy: (predict(result.x, xy, group_z[1])-group_px[1]).ravel(),
                                  offset).x
        for z, points in zip(group_z, group_px):
            error = float(np.sqrt(np.mean(np.sum((predict(result.x, placement, z)-points)**2, axis=1))))
            if not np.isfinite(error) or error > max_rms_px:
                raise ValueError(f"Held-out checkerboard view is {error:.2f} px RMS, above the {max_rms_px:g} px limit")
            errors.append(error)

    matrix, coeffs, _, _ = unpack(result.x)
    lens = LensCalibration(serial, tuple(image_size), matrix, coeffs, fit_error, tuple(errors), model="fisheye")
    raw = pixels.reshape(-1, 2)
    ideal = lens.undistort(raw, matrix)
    round_trip = distort_points(ideal, matrix, matrix, coeffs, model=lens.model)
    if not np.isfinite(round_trip).all() or np.max(np.linalg.norm(raw-round_trip, axis=1)) > .5:
        raise ValueError("The fitted lens correction cannot be inverted across the sampled field")
    return lens


class StationaryCalibrationController(ScanController):
    """Capture fresh frames with checked XY/Z readback during calibration."""
    def _position(self):
        xyz = self.stage.position_xyz()
        self.stage.check_xy(*xyz)
        return np.asarray(xyz)

    def _capture_at_position(self):
        self._check_abort()
        self._printer_guard()
        before = self._position()
        if self.cancel.wait(0 if self.simulated else self.recipe.settle_s):
            self._check_abort()
        settled = time.monotonic_ns()
        self.camera.fresh()
        self._check_abort()
        capture = self.camera.capture()
        self._capture_check(capture, settled)
        reported = self._position()
        if np.max(np.abs(reported-before)) > self.fixture.position_tolerance_mm:
            raise RuntimeError("Printer position drifted during the calibration capture")
        self.on_event("frame", capture.metadata, capture.pixels)
        return capture, reported

    def _close_devices(self, outcome, message):
        if outcome != "COMPLETED":
            self.stop()
        if self._stop_thread:
            self._stop_thread.join()
        for device in (self.camera, self.stage):
            try:
                device.close()
            except Exception as exc:
                outcome, message = "FAILED", f"{message}. Device close failed: {exc}"
        if self._stop_error:
            outcome, message = "FAILED", f"{message}. STOP failed: {self._stop_error}"
        return outcome, message


class FlatCalibrationController(StationaryCalibrationController):
    """Capture and validate a flat checkerboard, then position and check the MTF target."""

    def __init__(self, *args, panel_positions, xy_calibration, **kwargs):
        super().__init__(*args, **kwargs)
        self.panel_positions = panel_positions
        self.xy_calibration = xy_calibration

    def _move(self, x, y, z):
        self._check_abort()
        self._printer_guard()
        cx, cy, cz = self._position()
        self.stage.check_xy(cx, cy, z)
        self.stage.check_xy(x, y, z)
        tolerance = self.fixture.position_tolerance_mm
        if abs(cz - z) > .0005:
            self.stage.move_z_absolute(z)
            self._check_abort()
            if abs(self._position()[2] - z) > tolerance:
                raise RuntimeError("Calibration Z disagrees with the commanded position")
        if max(abs(cx - x), abs(cy - y)) > .0005:
            self._check_abort()
            self.stage.move_xy_absolute(x, y, speed_mm_s=2.)
        self._check_abort()
        if np.max(np.abs(self._position() - [x, y, z])) > tolerance:
            raise RuntimeError("Calibration XY/Z disagrees with the commanded position")

    def _sample(self, board, direction):
        capture, xyz = self._capture_at_position()
        points = checkerboard_corners(capture, board)
        # The detector may reverse the order on a symmetric board between frames.
        if (points[-1] - points[0]) @ direction < 0:
            points = points[::-1].copy()
        return float(xyz[2]), points

    def _calibrate(self, chart):
        board = chart["checkerboard"]
        distances = self.recipe.distances()
        middle = min(distances[1:-1], key=lambda wd: abs(wd-self.recipe.nominal_wd_mm))
        near, mid, far = (round(self.fixture.z_for_wd(wd), 3) for wd in (distances[0], middle, distances[-1]))
        if min(mid-near, far-mid) <= 2*self.fixture.position_tolerance_mm:
            raise ValueError("Increase the sweep span so the measured calibration depths can be distinguished")
        checker = np.asarray(self.panel_positions["checkerboard"])
        mtf = self.panel_positions.get("mtf")
        if mtf is not None and np.linalg.norm(checker - mtf) <= self.fixture.position_tolerance_mm:
            raise ValueError("Record a different XY position for the MTF panel")
        for center in self.panel_positions.values():
            for z in (near, mid, far):
                self.stage.check_xy(*center, z)
        self._move(*checker, mid)
        frame, xyz = self._capture_at_position()
        corners = checkerboard_corners(frame, board)
        direction = corners[-1] - corners[0]
        size = (frame.pixels.shape[1], frame.pixels.shape[0])
        if size != tuple(self.xy_calibration.image_size_px):
            raise ValueError("Camera image dimensions differ from the measured XY mapping")
        shift = (.44*np.asarray(size) - .625*np.ptp(corners, axis=0))
        if np.any(shift < .08*np.asarray(size)):
            raise ValueError("Checkerboard fills too much of the frame to sample the edges")
        offsets = np.array([(0, 0), (-1, 0), (1, 0), (0, -1), (0, 1), (-.8, -.8), (.8, .8)]) * shift
        positions = np.round(checker + np.linalg.solve(self.xy_calibration.jacobian_px_per_mm, offsets.T).T, 3)
        for x, y in positions:
            for depth in (near, mid, far):
                self.stage.check_xy(x, y, depth)
        groups = []
        for index, (x, y) in enumerate(positions):
            self._state("CALIBRATING", f"Checkerboard position {index+1} of {PLACEMENTS}")
            self._move(x, y, mid)
            at_mid = self._sample(board, direction) if index else (float(xyz[2]), corners)
            self._move(x, y, near)
            at_near = self._sample(board, direction)
            self._move(x, y, far)
            at_far = self._sample(board, direction)
            groups.append((at_near, at_mid, at_far))
            self._move(x, y, mid)
            self.on_event("progress", index+1, PLACEMENTS)
        self._state("FITTING", "Fitting the lens and checking independent positions")
        lens = fit_flat_lens(groups, board, size, str(frame.metadata["camera_serial"]), middle,
                             self.recipe.quality.max_marker_reprojection_px, check_abort=self._check_abort)
        return lens, mid

    def run(self):
        if self.state != "IDLE":
            raise RuntimeError("A calibration controller runs only once")
        outcome, message, details = "FAILED", "", {}
        try:
            self._state("PREFLIGHT")
            self.recipe.validate_fixture(self.fixture, self.settings.printer.z_max, simulation=self.simulated)
            if not self.simulated:
                if self.operator_confirmed is not True:
                    raise ValueError("Approve supervised XY/Z motion before calibration")
                self.checks.validate()
            self.xy_calibration.validate(self.fixture)
            chart = load_chart(self.recipe.chart_path)
            self._printer_guard()
            self._check_abort()
            self.stage.open()
            self.camera.open()
            lens, mid = self._calibrate(chart)
            self._check_abort()
            root = Path(self.output_root)
            root.mkdir(parents=True, exist_ok=True)
            path = Path(tempfile.mkdtemp(prefix="lens-", dir=root)) / "lens.json"
            atomic_json(path, asdict(lens))
            details = {"lens_calibration": lens, "lens_path": str(path)}
            message = f"Saved {path}. Lens validation {max(lens.validation_rms_px):.2f} px RMS."
            if "mtf" in self.panel_positions:
                self._state("CHECKING", "Moving to the MTF target for a corrected chart check")
                self._move(*self.panel_positions["mtf"], mid)
                capture, _ = self._capture_at_position()
                recipe = replace(self.recipe, lens_calibration=lens)
                registration, _, _ = register_capture(capture, chart, recipe)
                if not registration["full_field_visible"]:
                    raise ValueError("MTF chart is clipped. Check its saved position before scanning.")
                registration.pop("homography")
                registration.pop("project_chart")
                details.update(preview_registration=registration, pixel_format=recipe.pixel_format,
                               image_size_px=lens.image_size_px, camera_serial=lens.camera_serial,
                               simulated=self.simulated)
                message += f" MTF chart check {registration['reprojection_rms_px']:.2f} px RMS."
            else:
                message += " Center the MTF panel and run Check chart before scanning."
            self._check_abort()
            outcome = "COMPLETED"
        except InterruptedError as exc:
            outcome, message = "ABORTED", str(exc)
        except Exception as exc:
            outcome, message = "FAILED", str(exc)
        finally:
            outcome, message = self._close_devices(outcome, message)
            self._state(outcome, message)
        return RunResult(outcome, "", message, details=details)
