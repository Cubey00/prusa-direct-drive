"""Independent front-lens reference and diagnostic WD comparison."""
from dataclasses import asdict, dataclass
from hashlib import sha256
import json
import math
from pathlib import Path
import re

import cv2
import numpy as np

from .chart import marker_corners_mm, register_capture


def _digest(values):
    return sha256(json.dumps(values, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def lens_fingerprint(lens):
    return _digest(asdict(lens))


def marker_geometry_fingerprint(chart):
    return _digest({str(marker["id"]): marker_corners_mm(marker).tolist() for marker in chart["fiducials"]})


@dataclass(frozen=True)
class OpticalDistanceValidation:
    # Independent (physical front-lens WD, fitted camera-centre axial distance) pairs.
    checks_mm: tuple
    uncertainty_mm: float
    max_tilt_deg: float
    max_axis_offset_mm: float
    max_reprojection_rms_px: float
    provenance: str

    def __post_init__(self):
        checks = tuple(tuple(pair) for pair in self.checks_mm)
        if (len(checks) < 2 or any(len(pair) != 2 or not all(map(math.isfinite, pair))
                                   or min(pair) <= 0 for pair in checks)
                or len({wd for wd, _ in checks}) < 2):
            raise ValueError("Optical validation requires independent checks at two or more distinct distances")
        if (not all(map(math.isfinite, (self.uncertainty_mm, self.max_tilt_deg,
                                      self.max_axis_offset_mm, self.max_reprojection_rms_px)))
                or self.uncertainty_mm <= 0 or not 0 <= self.max_tilt_deg < 90
                or self.max_axis_offset_mm < 0 or self.max_reprojection_rms_px <= 0
                or not isinstance(self.provenance, str) or not self.provenance.strip()):
            raise ValueError("Optical validation requires finite uncertainty, operating limits and provenance")
        object.__setattr__(self, "checks_mm", checks)

    def covers(self, pose, distance, marker_center):
        # Include the uncertainty band at the independently checked endpoints.
        depths = [wd for wd, _ in self.checks_mm]
        return (min(depths) - self.uncertainty_mm <= distance <= max(depths) + self.uncertainty_mm
                and pose.tilt_deg <= self.max_tilt_deg
                and np.linalg.norm(np.asarray(pose.axis_intersection_chart_xy_mm) - marker_center) <= self.max_axis_offset_mm
                and pose.reprojection_rms_px <= self.max_reprojection_rms_px)


@dataclass(frozen=True)
class OpticalReference:
    offset_mm: float
    uncertainty_mm: float
    lens_reference_plane: str
    provenance: str
    lens_sha256: str
    marker_geometry_sha256: str
    pixel_format: str
    refine_corners: bool = False
    validation: OpticalDistanceValidation | None = None

    def __post_init__(self):
        if (not math.isfinite(self.offset_mm) or not math.isfinite(self.uncertainty_mm)
                or self.uncertainty_mm <= 0 or type(self.refine_corners) is not bool
                or any(not isinstance(value, str) or not value.strip()
                       for value in (self.lens_reference_plane, self.provenance, self.pixel_format))
                or any(not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value)
                       for value in (self.lens_sha256, self.marker_geometry_sha256))):
            raise ValueError("Optical reference requires a measured signed offset, uncertainty, plane and provenance")
        if isinstance(self.validation, dict):
            object.__setattr__(self, "validation", OpticalDistanceValidation(**self.validation))
        if self.validation is not None:
            if not isinstance(self.validation, OpticalDistanceValidation):
                raise ValueError("Invalid independent optical WD validation")
            if (self.validation.uncertainty_mm < self.uncertainty_mm
                    or any(abs(axial - self.offset_mm - wd) > self.validation.uncertainty_mm
                           for wd, axial in self.validation.checks_mm)):
                raise ValueError("Independent optical WD checks exceed the stated uncertainty")

    @classmethod
    def load(cls, path):
        return cls(**json.loads(Path(path).read_text(encoding="utf-8")))

    def matches(self, lens, chart, pixel_format, refine_corners):
        return (self.lens_sha256 == lens_fingerprint(lens)
                and self.marker_geometry_sha256 == marker_geometry_fingerprint(chart)
                and self.pixel_format == pixel_format and self.refine_corners == refine_corners)


def verify_working_distance(chart, recipe, registration, *, printer_wd=None, printer_reference=None,
                            simulated=False, registration_failure="", pixel_format=None):
    """Return serializable diagnostics independently of scan motion and MTF."""
    result = dict(printer_working_distance_mm=printer_wd, optical_working_distance_mm=None,
                  working_distance_difference_mm=None, optical_distance_uncertainty_mm=None,
                  optical_pose_reprojection_rms_px=None, optical_pose_tilt_deg=None, optical_pose=None,
                  optical_discrepancy_threshold_mm=None, optical_distance_valid=False,
                  optical_distance_status="Unavailable", optical_distance_reason="")

    def status(reason, label="Unavailable"):
        return dict(result, optical_distance_status=label, optical_distance_reason=reason)

    lens = recipe.lens_calibration
    reference = recipe.optical_reference

    def unavailable_pose(reason):
        if reference is None:
            reason += " Absolute optical working distance is not calibrated."
        return status(reason)

    if lens is None:
        return status("Absolute optical working distance is not calibrated. Load a measured lens profile.")
    if registration is None:
        return unavailable_pose(registration_failure or "Chart marker observations are unavailable.")
    marker_key = "refined_marker_corners_px" if recipe.optical_refine_corners else "marker_corners_px"
    corners = registration.get(marker_key)
    if corners is None:
        return unavailable_pose("Subpixel detection did not recover all four chart markers.")
    from .pose import estimate_chart_pose

    try:
        pose = estimate_chart_pose(chart, corners, lens,
                                   max_reprojection_rms_px=recipe.quality.max_marker_reprojection_px)
    except (ValueError, cv2.error) as exc:
        return unavailable_pose(str(exc))
    result.update(optical_pose=asdict(pose), optical_pose_reprojection_rms_px=pose.reprojection_rms_px,
                  optical_pose_tilt_deg=pose.tilt_deg)
    if reference is None:
        return status("Absolute optical working distance is not calibrated. The physical lens offset is missing.")
    if simulated or not reference.matches(lens, chart, pixel_format or recipe.pixel_format, recipe.optical_refine_corners):
        return status("The optical reference does not match this camera, lens, chart or detection configuration.")
    optical = pose.camera_center_axial_distance_mm - reference.offset_mm
    if optical <= 0:
        return status("The physical lens reference is at or beyond the fitted target intersection.")
    validation = reference.validation
    if validation is None:
        return status("Absolute optical working distance has no independent scale validation.")
    marker_center = np.stack([marker_corners_mm(marker) for marker in chart["fiducials"]]).mean(axis=(0, 1))
    if not validation.covers(pose, optical, marker_center):
        return status("The optical estimate is outside the independently validated operating limits.")
    result.update(optical_working_distance_mm=optical,
                  optical_distance_uncertainty_mm=validation.uncertainty_mm, optical_distance_valid=True)
    if printer_wd is None or printer_reference is None:
        return status("Measured printer working distance is unavailable for comparison.")
    if printer_reference.get("lens_reference_plane") != reference.lens_reference_plane:
        return status("The optical and printer reference plane definitions differ.")
    # Add the two uncertainty bounds conservatively. The configured threshold
    # can demand a larger discrepancy than this joint bound.
    threshold = max(recipe.optical_discrepancy_threshold_mm or 0,
                    validation.uncertainty_mm + printer_reference["uncertainty_mm"])
    difference = optical - printer_wd
    result.update(working_distance_difference_mm=difference, optical_discrepancy_threshold_mm=threshold)
    if abs(difference) > threshold:
        return status(f"Distance discrepancy exceeds the {threshold:g} mm threshold.", "Warning")
    return status(f"Distances agree within the {threshold:g} mm threshold.", "Valid")


def measure_capture_distance(capture, chart, recipe, **comparison):
    if recipe.lens_calibration is None:
        return verify_working_distance(chart, recipe, None, **comparison)
    try:
        registration, _, _ = register_capture(capture, chart, recipe,
                                               refine_optical_corners=recipe.optical_refine_corners)
    except (ValueError, cv2.error) as exc:
        return verify_working_distance(chart, recipe, None, registration_failure=str(exc), **comparison)
    return verify_working_distance(chart, recipe, registration, pixel_format=capture.metadata["pixel_format"], **comparison)
