"""Measured OpenCV pinhole intrinsics in native capture pixel coordinates."""
from dataclasses import dataclass
from functools import lru_cache
import json
from pathlib import Path

import cv2
import numpy as np


@dataclass(frozen=True)
class LensCalibration:
    camera_serial: str
    image_size_px: tuple
    camera_matrix: tuple
    dist_coeffs: tuple
    calibration_rms_px: float | None = None
    validation_rms_px: tuple = ()

    def __post_init__(self):
        matrix = np.asarray(self.camera_matrix, dtype=float)
        coeffs = np.asarray(self.dist_coeffs, dtype=float)
        size = tuple(self.image_size_px)
        errors = tuple(self.validation_rms_px)
        if not isinstance(self.camera_serial, str) or not self.camera_serial.strip():
            raise ValueError("Lens calibration requires a camera serial")
        if len(size) != 2 or any(type(n) is not int or n <= 0 for n in size):
            raise ValueError("Lens calibration requires native image width and height")
        if (matrix.shape != (3, 3) or not np.isfinite(matrix).all()
                or matrix[0, 0] <= 0 or matrix[1, 1] <= 0
                or matrix[0, 1] != 0 or matrix[1, 0] != 0
                or not np.array_equal(matrix[2], [0, 0, 1])
                or not 0 <= matrix[0, 2] < size[0] or not 0 <= matrix[1, 2] < size[1]):
            raise ValueError("Invalid OpenCV lens camera matrix")
        if coeffs.shape not in {(4,), (5,)} or not np.isfinite(coeffs).all():
            raise ValueError("Lens calibration requires 4 or 5 finite OpenCV pinhole coefficients")
        all_errors = errors if self.calibration_rms_px is None else (*errors, self.calibration_rms_px)
        for error in all_errors:
            if not np.isfinite(error) or error < 0:
                raise ValueError("Lens calibration errors must be finite and nonnegative")
        object.__setattr__(self, "image_size_px", size)
        object.__setattr__(self, "camera_matrix", tuple(map(tuple, matrix.tolist())))
        object.__setattr__(self, "dist_coeffs", tuple(coeffs.tolist()))
        object.__setattr__(self, "validation_rms_px", errors)

    @classmethod
    def load(cls, path):
        return cls(**json.loads(Path(path).read_text(encoding="utf-8")))

    def validate_capture(self, capture):
        if str(capture.metadata["camera_serial"]) != self.camera_serial:
            raise ValueError(f"Lens calibration is for camera {self.camera_serial}; "
                             f"this frame is from {capture.metadata['camera_serial']}")
        size = (capture.pixels.shape[1], capture.pixels.shape[0])
        if size != self.image_size_px:
            raise ValueError(f"Lens calibration frame size {self.image_size_px} differs from capture {size}")

    def analysis_matrix(self, pitch, offset):
        matrix = np.asarray(self.camera_matrix).copy()
        matrix[:2, 2] -= offset
        matrix[:2] /= pitch
        return matrix


def distort_points(points, ideal_matrix, raw_matrix, coeffs):
    rays = (points - ideal_matrix[:2, 2]) / ideal_matrix.diagonal()[:2]
    rays = np.column_stack((rays, np.ones(len(rays))))
    projected, _ = cv2.projectPoints(rays, np.zeros(3), np.zeros(3), raw_matrix, coeffs)
    return projected.reshape(-1, 2)


@lru_cache(maxsize=1)
def rectification_maps(calibration, pitch, offset, size):
    """Lazily cache the maps for the marker-only fallback."""
    matrix = calibration.analysis_matrix(pitch, offset)
    coeffs = np.asarray(calibration.dist_coeffs)
    ideal, _ = cv2.getOptimalNewCameraMatrix(matrix, coeffs, size, 1, size)
    maps = cv2.initUndistortRectifyMap(matrix, coeffs, None, ideal, size, cv2.CV_16SC2)
    return ideal, maps


def calibrate_lens(images, validation_images, *, board_size, square_mm, camera_serial, max_rms_px):
    """Fit intrinsics from static checkerboard views and check separate views."""
    images = [Path(path).resolve() for path in images]
    validation_images = [Path(path).resolve() for path in validation_images]
    all_images = images + validation_images
    if len(images) < 8 or len(validation_images) < 2 or len(set(all_images)) != len(all_images):
        raise ValueError("Provide at least 8 distinct calibration images and 2 separate validation images")
    if len(board_size) != 2 or min(board_size) < 3 or not np.isfinite(square_mm) or square_mm <= 0:
        raise ValueError("Checkerboard needs at least 3 inner corners along each axis and positive square spacing")
    cols, rows = board_size
    object_points = np.zeros((cols * rows, 3), dtype=np.float32)
    object_points[:, :2] = np.mgrid[:cols, :rows].T.reshape(-1, 2) * square_mm
    size = None
    image_points = []
    corner_criteria = (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_MAX_ITER, 40, 0.001)
    for path in all_images:
        image = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
        if image is None or image.dtype not in (np.uint8, np.uint16) or image.ndim not in (2, 3):
            raise ValueError(f"Cannot read {path.name} as an 8-bit or 16-bit camera image")
        plane = image if image.ndim == 2 else np.ascontiguousarray(image[..., 1])
        frame_size = (plane.shape[1], plane.shape[0])
        if size is None:
            size = frame_size
        if frame_size != size:
            raise ValueError(f"Calibration frame {path.name} has size {frame_size}, expected {size}")
        if plane.dtype != np.uint8:
            plane = cv2.normalize(plane, None, 0, 255, cv2.NORM_MINMAX, cv2.CV_8U)
        found, corners = cv2.findChessboardCorners(plane, board_size)
        if not found:
            raise ValueError(f"Checkerboard not detected in {path.name}")
        cv2.cornerSubPix(plane, corners, (5, 5), (-1, -1), corner_criteria)
        image_points.append(corners)
    rms, matrix, coeffs, _, _ = cv2.calibrateCamera([object_points] * len(images),
                                                   image_points[:len(images)], size, None, None)
    errors = []
    for path, corners in zip(validation_images, image_points[len(images):]):
        found, rotation, translation = cv2.solvePnP(object_points, corners, matrix, coeffs)
        if not found:
            raise ValueError(f"Could not estimate checkerboard pose in {path.name}")
        predicted, _ = cv2.projectPoints(object_points, rotation, translation, matrix, coeffs)
        error = float(np.sqrt(np.mean(np.sum((predicted - corners)**2, axis=2))))
        if not np.isfinite(error) or error > max_rms_px:
            raise ValueError(f"Validation image {path.name} has {error:.3f} px RMS, above {max_rms_px:g} px")
        errors.append(error)
    if not np.isfinite(rms) or rms > max_rms_px:
        raise ValueError(f"Checkerboard calibration has {rms:.3f} px RMS, above {max_rms_px:g} px")
    return LensCalibration(camera_serial, size, matrix, coeffs.ravel(), float(rms), tuple(errors))
