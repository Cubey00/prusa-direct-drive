"""Metric pose of the printed chart from 16 native-sensor marker corners."""
from dataclasses import dataclass

import cv2
import numpy as np
from scipy.optimize import least_squares

from .chart import marker_corners_mm
from .lens import distort_points


@dataclass(frozen=True)
class ChartPose:
    rotation_vector_rad: tuple
    translation_mm: tuple
    camera_center_axial_distance_mm: float
    camera_center_perpendicular_distance_mm: float
    axis_intersection_chart_xy_mm: tuple
    tilt_deg: float
    reprojection_rms_px: float
    max_corner_error_px: float


def _correspondences(chart, observed, image_size):
    markers = chart["fiducials"]
    ids = {str(marker["id"]) for marker in markers}
    if len(markers) != 4 or ids != {"0", "1", "2", "3"} or set(observed) != ids:
        raise ValueError("Optical pose requires all four unique markers 0,1,2,3")
    if chart["units"] != "mm" or any(marker["size_mm"] <= 0 for marker in markers):
        raise ValueError("Invalid metric chart marker geometry")
    xy = np.stack([marker_corners_mm(marker) for marker in markers])
    pixels = np.asarray([observed[str(marker["id"])] for marker in markers], dtype=float)
    if (pixels.shape != (4, 4, 2) or not np.isfinite(pixels).all()
            or np.any(pixels < 0) or np.any(pixels >= image_size)):
        raise ValueError("Invalid native sensor marker corners")
    if not np.isfinite(xy).all():
        raise ValueError("Invalid metric chart marker geometry")
    for index, a in enumerate(xy):
        for b in xy[index+1:]:
            if np.all(np.maximum(a[0], b[0]) < np.minimum(a[2], b[2])):
                raise ValueError("Metric chart markers overlap")
    centers = np.float32(xy.mean(axis=1) - xy.mean(axis=(0, 1)))
    if len(cv2.convexHull(centers)) != 4:
        raise ValueError("Metric chart markers must form four distinct field corners")
    for corners in pixels:
        contour = np.float32(corners)
        if not cv2.isContourConvex(contour) or cv2.contourArea(contour, oriented=True) <= 0:
            raise ValueError("Marker corners must be convex and face the camera")
    return np.column_stack((xy.reshape(-1, 2), np.zeros(16))), pixels.reshape(-1, 2)


def estimate_chart_pose(chart, marker_corners_px, lens, *, max_reprojection_rms_px=3.0,
                        ambiguity_gap_px=0.25, max_tilt_deg=75.0):
    """Fit planar IPPE candidates, then score/refine them in distorted sensor pixels.

    Chart X/Y follow the print right/down; its +Z points away from the camera
    when the printed face is directly in front of the lens.
    """
    if not (np.isfinite([max_reprojection_rms_px, ambiguity_gap_px, max_tilt_deg]).all()
            and max_reprojection_rms_px > 0 and ambiguity_gap_px >= 0 and 0 < max_tilt_deg < 90):
        raise ValueError("Invalid optical pose quality limits")
    points, pixels = _correspondences(chart, marker_corners_px, lens.image_size_px)
    origin = points.mean(axis=0)
    points -= origin
    matrix, coeffs = np.asarray(lens.camera_matrix), np.asarray(lens.dist_coeffs)
    fisheye = lens.model == "fisheye"
    ideal = lens.undistort(pixels, matrix)
    redistorted = distort_points(ideal, matrix, matrix, coeffs, model=lens.model)
    if not np.isfinite(redistorted).all() or np.max(np.linalg.norm(redistorted - pixels, axis=1)) > 0.5:
        raise ValueError("Lens calibration could not invert the optical pose corners")
    # Use normalized rays for both models. This also uses the lens profile's
    # iterative pinhole inversion instead of the short default inside PnP.
    observations = ((ideal - matrix[:2, 2]) / matrix.diagonal()[:2]).reshape(-1, 1, 2)
    identity = np.eye(3)
    _, rotations, translations, _ = cv2.solvePnPGeneric(
        points, observations, identity, None, flags=cv2.SOLVEPNP_IPPE)

    def residual(parameters):
        rvec, tvec = parameters[:3], parameters[3:]
        if fisheye:
            projected, _ = cv2.fisheye.projectPoints(points.reshape(1, -1, 3), rvec, tvec, matrix, coeffs)
        else:
            projected, _ = cv2.projectPoints(points, rvec, tvec, matrix, coeffs)
        return (projected.reshape(-1, 2) - pixels).ravel()

    def geometry(parameters):
        rotation, _ = cv2.Rodrigues(parameters[:3])
        normal = rotation[:, 2]
        center = parameters[3:]
        depth = points @ rotation[2] + center[2]
        perpendicular = float(normal @ center)
        if (normal[2] < np.cos(np.deg2rad(max_tilt_deg)) or np.min(depth) <= 0
                or perpendicular <= 0):
            return None
        axial = perpendicular / normal[2]
        tilt = float(np.rad2deg(np.arccos(np.clip(normal[2], -1, 1))))
        translation = center - rotation @ origin
        if not np.isfinite([axial, perpendicular, tilt, *translation]).all():
            return None
        return rotation, translation, float(axial), perpendicular, tilt

    seeds = [np.r_[r.ravel(), t.ravel()] for r, t in zip(rotations, translations)]
    if not any(np.isfinite(seed).all() for seed in seeds):
        # IPPE can be singular for an exactly fronto-parallel noiseless view.
        found, rvec, tvec = cv2.solvePnP(points, observations, identity, None)
        if found:
            seeds.append(np.r_[rvec.ravel(), tvec.ravel()])
    candidates = []
    for seed in seeds:
        if not np.isfinite(seed).all() or not np.isfinite(residual(seed)).all():
            continue
        fit = least_squares(residual, seed, method="lm", max_nfev=100)
        fitted_geometry = geometry(fit.x) if fit.success and np.isfinite(fit.x).all() else None
        if fitted_geometry is not None:
            errors = np.linalg.norm(fit.fun.reshape(-1, 2), axis=1)
            rms = float(np.sqrt(np.mean(errors**2)))
            if np.isfinite(rms):
                candidates.append((rms, float(errors.max()), fitted_geometry, fit.x))
    if not candidates:
        raise ValueError("Optical pose has no physically plausible solution")
    candidates.sort(key=lambda candidate: candidate[0])
    rms, maximum, (rotation, translation, axial, perpendicular, tilt), parameters = candidates[0]
    if rms > max_reprojection_rms_px or maximum > 2 * max_reprojection_rms_px:
        raise ValueError(f"Optical pose reprojection fit failed ({rms:.2f} px RMS, {maximum:.2f} px maximum)")
    for alternative_rms, _, (other_rotation, _, other_axial, _, _), _ in candidates[1:]:
        # Distinct physical minima count as ambiguous only when the sensor data
        # cannot distinguish them. Both IPPE seeds may refine to the same minimum.
        relative = cv2.Rodrigues(other_rotation @ rotation.T)[0]
        distinct = np.linalg.norm(relative) > np.deg2rad(0.5) or abs(other_axial - axial) > 0.002 * axial
        if distinct and alternative_rms - rms <= ambiguity_gap_px:
            raise ValueError("Ambiguous planar optical pose")
    intersection = rotation.T @ (np.array([0, 0, axial]) - translation)
    return ChartPose(tuple(parameters[:3].tolist()), tuple(translation.tolist()),
                     axial, perpendicular, tuple(intersection[:2].tolist()), tilt, rms, maximum)
