"""Measure a local optical XY mapping with supervised, fixed-Z moves."""
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
import uuid

import cv2
import numpy as np

from .calibration import XYCalibration
from .chart import load_chart
from .controller import RunResult
from .data import atomic_json
from .flat_calibration import StationaryCalibrationController, checkerboard_center, checkerboard_corners


OFFSETS = np.array([(0, 0), (1, 0), (-1, 0), (0, 1), (0, -1),
                    (0, 0), (.5, .5), (-.5, -.5), (0, 0)])
MAX_ERROR_PX = 3.0


def _fit_mapping(xy, pixels):
    # Fit the four axis moves with the initial image as the local origin.
    # Diagonal moves test the fit, and two returns test repeatability.
    machine = xy[1:] - xy[0]
    image = pixels[1:] - pixels[0]
    axes, _, rank, _ = np.linalg.lstsq(machine[:4], image[:4], rcond=None)
    if rank != 2 or np.linalg.cond(axes) > 100 or np.linalg.svd(axes, compute_uv=False)[-1] < 1:
        raise ValueError("XY movement did not produce a distinguishable response on both image axes")
    errors = np.linalg.norm(machine @ axes - image, axis=1)
    residual = float(np.max(errors[[0, 1, 2, 3, 5, 6]]))
    uncertainty = float(np.max(np.linalg.norm(image[[4, 7]], axis=1)))
    if residual + uncertainty > MAX_ERROR_PX:
        raise ValueError(f"XY mapping validation failed: {residual:.2f} px residual + "
                         f"{uncertainty:.2f} px return error exceeds {MAX_ERROR_PX:g} px")
    hull = cv2.convexHull(np.float32(image[:4])).reshape(-1, 2)
    radius = float(cv2.pointPolygonTest(hull, (0., 0.), True)) - residual - uncertainty
    if radius <= 0:
        raise ValueError("XY samples did not enclose a usable image region")
    return axes.T.tolist(), radius, residual, uncertainty


class XYMappingController(StationaryCalibrationController):
    def __init__(self, *args, clearance, operator, step_mm, **kwargs):
        super().__init__(*args, **kwargs)
        self.clearance, self.operator, self.step_mm = clearance, operator, step_mm

    def _measure(self, board):
        start = self._position()
        tolerance = self.fixture.position_tolerance_mm
        if abs(start[2] - self.fixture.z_for_wd(self.recipe.nominal_wd_mm)) > tolerance:
            raise ValueError("Set the printer to the requested nominal working distance before measuring XY")
        targets = np.round(start + np.column_stack((OFFSETS * self.step_mm, np.zeros(len(OFFSETS)))), 3)
        targets[:, 2] = start[2]
        # The verified machine rectangle is convex. Check every endpoint
        # before moving, then check actual positions again at each move.
        for target in targets:
            self.stage.check_xy(*target)

        measured, centers = [], []
        size = None
        for index, target in enumerate(targets):
            self._check_abort()
            self._printer_guard()
            current = self._position()
            if abs(current[2] - start[2]) > tolerance:
                raise RuntimeError("Z changed during fixed-Z XY calibration")
            if index:
                self.stage.move_xy_absolute(*target[:2], speed_mm_s=1.)
            self._check_abort()
            if np.max(np.abs(self._position() - target)) > tolerance:
                raise RuntimeError("Printer readback differs from the XY calibration position")
            frame, reported = self._capture_at_position()
            if np.max(np.abs(reported - target)) > tolerance:
                raise RuntimeError("Printer drifted from the XY calibration position")
            frame_size = (frame.pixels.shape[1], frame.pixels.shape[0])
            if size is None:
                size = frame_size
            elif size != frame_size:
                raise ValueError("Camera image dimensions changed during XY calibration")
            center = checkerboard_center(checkerboard_corners(frame, board), board)
            if not np.isfinite(center).all():
                raise ValueError("Invalid checkerboard center")
            if index == 0 and np.any(np.abs(center - np.array(size)/2) > .1 * np.array(size)):
                raise ValueError("Center the checkerboard in the camera preview before measuring XY")
            measured.append(reported[:2])
            centers.append(center)
            self.on_event("progress", index+1, len(targets))

        matrix, radius, residual, uncertainty = _fit_mapping(np.asarray(measured), np.asarray(centers))
        now = datetime.now(timezone.utc)
        mapping = XYCalibration(
            calibration_id=f"xy-{now:%Y%m%dT%H%M%S}-{uuid.uuid4().hex[:8]}",
            fixture_id=self.fixture.fixture_id, camera_serial=self.fixture.camera_serial, measured=True,
            jacobian_px_per_mm=matrix, optical_center_px=tuple(centers[0].tolist()), image_size_px=size,
            valid_radius_px=radius, uncertainty_px=uncertainty, residual_px=residual, max_residual_px=MAX_ERROR_PX,
            method="Fixed-Z checkerboard, four axis moves, two diagonal checks and two returns",
            operator=self.operator, measured_at=now.isoformat(),
            sample_center_px=tuple(centers[0].tolist()), sample_z_mm=float(start[2]),
            **asdict(self.clearance)).validate(self.fixture)
        return mapping, tuple(measured[-1].tolist())

    def run(self):
        if self.state != "IDLE":
            raise RuntimeError("A calibration controller runs only once")
        outcome, message, details = "FAILED", "", {}
        try:
            self._state("PREFLIGHT")
            self._check_abort()
            if self.dry_run:
                raise ValueError("XY calibration needs camera frames. Turn off Motion only to measure XY.")
            if not (np.isfinite(self.step_mm) and .25 <= self.step_mm <= 1 and self.operator.strip()):
                raise ValueError("Enter an operator and an XY test displacement from 0.25 to 1 mm")
            if self.step_mm <= 2 * self.fixture.position_tolerance_mm:
                raise ValueError("Use an XY test displacement larger than twice the printer position tolerance")
            self.clearance.validate()
            if not self.simulated:
                if self.operator_confirmed is not True:
                    raise ValueError("Approve supervised fixed-Z motion before measuring the XY mapping")
                self.fixture.validate_physical()
                self.checks.validate()
            chart = load_chart(self.recipe.chart_path)
            if "checkerboard" not in chart:
                raise ValueError("Select the combined target layout with a checkerboard first")
            self._printer_guard()
            self._check_abort()
            self.stage.open()
            self._check_abort()
            self.camera.open()
            self._state("CALIBRATING", "Measuring checkerboard movement at fixed Z")
            mapping, checkerboard_xy = self._measure(chart["checkerboard"])
            self._check_abort()
            outcome = "COMPLETED"
        except InterruptedError as exc:
            outcome, message = "ABORTED", str(exc)
        except Exception as exc:
            message = str(exc)
        finally:
            outcome, message = self._close_devices(outcome, message)
        if outcome == "COMPLETED":
            try:
                self._check_abort()
                path = Path(self.output_root) / mapping.calibration_id / "optical_xy_mapping.json"
                path.parent.mkdir(parents=True)
                atomic_json(path, asdict(mapping))
                details.update(xy_calibration=mapping, xy_path=str(path),
                               panel_position=("checkerboard", checkerboard_xy))
                message = (f"Saved {path}. XY residual {mapping.residual_px:.2f} px. "
                           "Checkerboard XY recorded. Calibrate the lens or record the MTF panel first.")
            except InterruptedError as exc:
                outcome, message = "ABORTED", str(exc)
            except (OSError, ValueError) as exc:
                outcome, message = "FAILED", f"Could not save XY mapping: {exc}"
        self._state(outcome, message)
        return RunResult(outcome, "", message, details=details)
