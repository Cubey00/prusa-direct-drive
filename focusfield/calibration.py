"""Auditable, operator-measured WD references and local image-to-machine mappings.

Nothing in this module connects to or moves hardware.
"""
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
import json
from pathlib import Path
import uuid

import numpy as np


@dataclass(frozen=True)
class WDReference:
    fixture_id: str
    camera_serial: str
    reference_z_mm: float
    reference_wd_mm: float
    check_z_mm: float
    check_wd_mm: float
    uncertainty_mm: float
    lens_reference_plane: str
    patterned_surface_notes: str
    method: str
    operator: str
    measured_at: str
    calibration_id: str

    def validate(self):
        for key in ("fixture_id", "camera_serial", "lens_reference_plane", "patterned_surface_notes",
                    "method", "operator", "measured_at", "calibration_id"):
            if not str(getattr(self, key)).strip() or getattr(self, key) == "unconfigured":
                raise ValueError(f"WD reference requires {key}")
        if datetime.fromisoformat(self.measured_at.replace("Z", "+00:00")).tzinfo is None:
            raise ValueError("WD reference date must include a timezone")
        values = [self.reference_z_mm, self.reference_wd_mm, self.check_z_mm, self.check_wd_mm, self.uncertainty_mm]
        if (any(type(v) not in (int, float) for v in values) or not np.isfinite(values).all()
                or min(self.reference_z_mm, self.check_z_mm) < 0
                or min(self.reference_wd_mm, self.check_wd_mm, self.uncertainty_mm) <= 0):
            raise ValueError("WD reference needs positive finite measurements and uncertainty")
        dz, dwd = self.check_z_mm-self.reference_z_mm, self.check_wd_mm-self.reference_wd_mm
        if abs(dz) < 0.05 or dz*dwd <= 0 or abs(dz-dwd) > 2*self.uncertainty_mm:
            raise ValueError("Independently measured second point must verify increasing Z increases WD with unit slope")
        return self

    @classmethod
    def measured(cls, **values):
        return cls(**values, measured_at=datetime.now(timezone.utc).isoformat(),
                   calibration_id="wd-" + uuid.uuid4().hex).validate()


def validate_reference(fixture):
    path = Path(fixture.wd_reference_record)
    if not fixture.wd_reference_record or not path.is_file():
        raise ValueError("A saved, independently measured WD reference is required before physical motion")
    record = WDReference(**json.loads(path.read_text(encoding="utf-8"))).validate()
    if (record.fixture_id != fixture.fixture_id or record.camera_serial != fixture.camera_serial
            or record.calibration_id != fixture.calibration_id
            or any(abs(a-b) > 1e-9 for a, b in ((record.reference_z_mm, fixture.reference_z_mm),
                (record.reference_wd_mm, fixture.reference_wd_mm), (record.uncertainty_mm, fixture.wd_uncertainty_mm)))
            or not fixture.safe_z_min_mm <= record.check_z_mm <= fixture.safe_z_max_mm):
        raise ValueError("WD reference identity, measurements or sign-check position differ from fixture configuration")
    return asdict(record)


def save_reference(record, output, settings_path):
    from .data import atomic_json
    from machine_settings import save_values
    record.validate()
    path = Path(output).resolve()
    if path.exists():
        raise ValueError("Choose a new WD reference filename; calibration records are immutable")
    atomic_json(path, asdict(record))
    # A new reference never enables motion or attests a safe mechanical envelope.
    return save_values("focusfield", dict(enabled=False, fixture_id=record.fixture_id,
        camera_serial=record.camera_serial, calibration_id=record.calibration_id,
        reference_z_mm=record.reference_z_mm, reference_wd_mm=record.reference_wd_mm,
        wd_uncertainty_mm=record.uncertainty_mm, wd_reference_record=str(path)), path=settings_path)


@dataclass(frozen=True)
class XYClearance:
    """Operator-verified mechanical envelope, independent of camera measurements."""
    safe_x_mm: tuple
    safe_y_mm: tuple
    safe_z_mm: tuple
    housing_radius_mm: float
    housing_below_nozzle_mm: float
    target_height_mm: float
    clearance_margin_mm: float
    clearance_verified: bool

    def validate(self):
        if self.clearance_verified is not True:
            raise ValueError("Verify the mechanical clearance before authorizing XY motion")
        for pair in (self.safe_x_mm, self.safe_y_mm, self.safe_z_mm):
            if len(pair) != 2 or not np.isfinite(pair).all() or pair[0] >= pair[1]:
                raise ValueError("XY clearance requires explicit finite machine-coordinate safety bounds")
        scalars = [self.housing_radius_mm, self.housing_below_nozzle_mm,
                   self.target_height_mm, self.clearance_margin_mm]
        if (not np.isfinite(scalars).all() or min(scalars) < 0
                or self.housing_radius_mm <= 0 or self.clearance_margin_mm <= 0):
            raise ValueError("Enter measured housing and target dimensions and a positive clearance margin")
        return self


@dataclass(frozen=True)
class XYCalibration:
    calibration_id: str
    fixture_id: str
    camera_serial: str
    measured: bool
    clearance_verified: bool
    jacobian_px_per_mm: tuple
    optical_center_px: tuple
    image_size_px: tuple
    valid_radius_px: float
    uncertainty_px: float
    residual_px: float
    max_residual_px: float
    safe_x_mm: tuple
    safe_y_mm: tuple
    safe_z_mm: tuple
    housing_radius_mm: float
    housing_below_nozzle_mm: float
    target_height_mm: float
    clearance_margin_mm: float
    method: str
    operator: str
    measured_at: str
    sample_center_px: tuple | None = None
    sample_z_mm: float | None = None

    def clearance(self):
        return XYClearance(self.safe_x_mm, self.safe_y_mm, self.safe_z_mm, self.housing_radius_mm,
                           self.housing_below_nozzle_mm, self.target_height_mm, self.clearance_margin_mm,
                           self.clearance_verified)

    def validate(self, fixture):
        j = np.asarray(self.jacobian_px_per_mm, dtype=float)
        if j.shape != (2, 2) or not np.isfinite(j).all() or np.linalg.cond(j) > 100:
            raise ValueError("XY mapping must be finite, nonsingular and locally well conditioned")
        if not (self.measured is True and self.clearance_verified is True and self.calibration_id.strip()
                and self.fixture_id == fixture.fixture_id and self.camera_serial == fixture.camera_serial
                and self.method.strip() and self.operator.strip() and self.measured_at.strip()):
            raise ValueError("XY centering requires a measured mapping, matching fixture/camera and verified clearance")
        if datetime.fromisoformat(self.measured_at.replace("Z", "+00:00")).tzinfo is None:
            raise ValueError("XY calibration date must include a timezone")
        self.clearance().validate()
        scalars = [self.valid_radius_px, self.uncertainty_px, self.residual_px, self.max_residual_px]
        if (not np.isfinite(scalars).all() or min(scalars) < 0 or self.valid_radius_px <= 0
                or self.max_residual_px <= 0 or self.residual_px + self.uncertainty_px > self.max_residual_px):
            raise ValueError("XY mapping uncertainty or residual is invalid")
        if (len(self.optical_center_px) != 2 or len(self.image_size_px) != 2
                or not np.isfinite([*self.optical_center_px, *self.image_size_px]).all()
                or any(not 0 <= p < n for p, n in zip(self.optical_center_px, self.image_size_px))):
            raise ValueError("Invalid calibrated image dimensions or optical center")
        if self.sample_center_px is not None and (
                len(self.sample_center_px) != 2 or not np.isfinite(self.sample_center_px).all()
                or any(not 0 <= p < n for p, n in zip(self.sample_center_px, self.image_size_px))
                or self.sample_z_mm is None or not np.isfinite(self.sample_z_mm)
                or not self.safe_z_mm[0] <= self.sample_z_mm <= self.safe_z_mm[1]):
            raise ValueError("Invalid XY calibration sampling position")
        return self

    def for_centering(self, lens):
        """Use the calibrated principal point within the sampled image region."""
        if self.sample_center_px is None:
            return self
        if (lens is None or lens.camera_serial != self.camera_serial
                or lens.image_size_px != tuple(self.image_size_px)):
            return None
        center = (lens.camera_matrix[0][2], lens.camera_matrix[1][2])
        radius = self.valid_radius_px - np.linalg.norm(np.subtract(center, self.sample_center_px))
        if radius <= 0:
            return None
        return replace(self, optical_center_px=center, valid_radius_px=float(radius))

    @classmethod
    def load(cls, path):
        return cls(**json.loads(Path(path).read_text(encoding="utf-8")))

    def correction(self, error_px, max_step_mm):
        error = np.asarray(error_px, float)
        if error.shape != (2,) or not np.isfinite(error).all() or np.linalg.norm(error) > self.valid_radius_px:
            raise ValueError("Center error outside the locally calibrated image region; position manually")
        delta = np.linalg.solve(self.jacobian_px_per_mm, -error)
        norm = np.linalg.norm(delta)
        return delta * min(1, max_step_mm/max(norm, 1e-12))
