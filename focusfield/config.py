"""Versioned recipes, separate from measured machine safety/calibration."""
from dataclasses import asdict, dataclass, field
from decimal import Decimal
import math
from pathlib import Path
import tomllib

from machine_settings import FocusFixture
from .lens import LensCalibration
from .quality import QualityThresholds

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CHART = ROOT / "focusfield/charts/focus_field_chart_layout.json"
DEFAULT_RECIPE = ROOT / "experiments/focusfield_20mm.toml"


def centered_scan_bounds(nominal, half_span, step):
    """Fit whole steps on both sides of nominal without exceeding the requested span."""
    nominal, half_span, step = map(lambda value: Decimal(str(value)), (nominal, half_span, step))
    if step < Decimal("0.001"):
        raise ValueError("Step must be at least 0.001 mm")
    intervals = half_span // step
    if intervals < 2:
        raise ValueError("Half-span must cover at least two steps")
    return float(nominal - intervals * step), float(nominal + intervals * step)


def nominal_on_grid(start, end, step, nominal):
    start, end, step, nominal = map(lambda value: Decimal(str(value)), (start, end, step, nominal))
    return start <= nominal <= end and step > 0 and (nominal - start) % step == 0


@dataclass(frozen=True)
class Recipe:
    schema_version: int = 1
    wd_start_mm: float = 19.5
    wd_end_mm: float = 20.5
    step_mm: float = 0.05
    nominal_wd_mm: float = 20.0
    frames_per_position: int = 3
    repetitions: int = 2
    reverse_alternate: bool = False
    settle_s: float = 0.3
    discard_frames: int = 2
    exposure_us: float = 10000.0
    gain: float = 1.0
    fixed_frequency_cpp: float = 0.1
    pixel_format: str = "RGB8"
    chart_path: str = str(DEFAULT_CHART)
    expected_width_px: int = 4056
    expected_height_px: int = 3040
    quality: QualityThresholds = field(default_factory=QualityThresholds)
    separate_nominal_plane_required: bool = False
    lens_calibration: LensCalibration | None = None

    def __post_init__(self):
        if isinstance(self.quality, dict):
            object.__setattr__(self, "quality", QualityThresholds(**self.quality))
        if not isinstance(self.quality, QualityThresholds):
            raise ValueError("quality must contain data-quality thresholds")
        if isinstance(self.lens_calibration, dict):
            object.__setattr__(self, "lens_calibration", LensCalibration(**self.lens_calibration))
        if self.lens_calibration is not None and not isinstance(self.lens_calibration, LensCalibration):
            raise ValueError("lens_calibration must contain a measured lens profile")
        for size in (self.expected_width_px, self.expected_height_px):
            if type(size) is not int or not 1 <= size <= 65536:
                raise ValueError("Expected image dimensions must be integers in 1..65536")
        if self.schema_version != 1:
            raise ValueError("Unsupported recipe schema_version")
        for name in ("wd_start_mm", "wd_end_mm", "step_mm", "nominal_wd_mm", "settle_s",
                     "exposure_us", "gain", "fixed_frequency_cpp"):
            if not math.isfinite(getattr(self, name)):
                raise ValueError(f"{name} must be finite")
        if not 0 < self.wd_start_mm < self.wd_end_mm or self.step_mm < 0.001:
            raise ValueError("WD interval must increase, with step >= 0.001 mm")
        if type(self.separate_nominal_plane_required) is not bool or self.nominal_wd_mm <= 0:
            raise ValueError("Nominal WD must be positive and separate_nominal_plane_required boolean")
        nominal_inside = self.wd_start_mm <= self.nominal_wd_mm <= self.wd_end_mm
        if not nominal_inside and not self.separate_nominal_plane_required:
            raise ValueError("Nominal WD must lie within scan")
        if self.separate_nominal_plane_required and nominal_on_grid(
                self.wd_start_mm, self.wd_end_mm, self.step_mm, self.nominal_wd_mm):
            raise ValueError("Separate nominal-plane flag requires a nominal plane outside the sweep grid")
        for key in ("frames_per_position", "repetitions", "discard_frames"):
            v = getattr(self, key)
            if type(v) is not int or not 1 <= v <= 100:
                raise ValueError(f"{key} must be an integer in 1..100")
        if type(self.reverse_alternate) is not bool:
            raise ValueError("reverse_alternate must be boolean")
        if not (0 <= self.settle_s <= 60 and self.exposure_us > 0 and self.gain >= 0
                and 0 < self.fixed_frequency_cpp < 0.25):
            raise ValueError("Invalid settling, exposure, gain or frequency (must be < 0.25 cycles/sensor pixel)")
        if self.pixel_format not in {"RGB8", "Mono8", "Mono10", "Mono12", "Mono16",
                                     "BayerRG8", "BayerRG10", "BayerRG12", "BayerRG10p", "BayerRG12p"}:
            raise ValueError("Unsupported measurement pixel format")
        steps = (Decimal(str(self.wd_end_mm)) - Decimal(str(self.wd_start_mm))) / Decimal(str(self.step_mm))
        if steps != steps.to_integral_value() or not 4 <= steps <= 2000:
            raise ValueError("Range must contain 5..2001 positions and an integral number of steps")
        if len(self.distances()) * self.frames_per_position * self.repetitions > 100000:
            raise ValueError("Recipe exceeds 100000 frames")

    def distances(self):
        lo, hi, step = map(lambda x: Decimal(str(x)), (self.wd_start_mm, self.wd_end_mm, self.step_mm))
        return [float(lo + i * step) for i in range(int((hi - lo) / step) + 1)]

    def schedule(self, fixture):
        result = []
        for rep in range(self.repetitions):
            distances = self.distances()
            if self.reverse_alternate and rep % 2:
                distances.reverse()
            result.extend((rep, wd, fixture.z_for_wd(wd)) for wd in distances)
        return result

    def validate_fixture(self, fixture, printer_z_max, *, simulation=False, dry_run=False):
        if not simulation and not (fixture.enabled and fixture.measured):
            raise ValueError("Optical fixture is disabled or unmeasured in machine_settings.toml")
        if not simulation:
            if dry_run:
                fixture.validate_motion_safety()
            else:
                fixture.validate_physical()
        for _, wd, z in self.schedule(fixture):
            if not (math.isfinite(z) and 0 <= fixture.safe_z_min_mm <= z <= fixture.safe_z_max_mm <= printer_z_max):
                raise ValueError(f"Z {z:g} is outside measured fixture / printer bounds")
            if wd - fixture.wd_uncertainty_mm < fixture.minimum_wd_mm:
                raise ValueError("Working distance including uncertainty violates minimum clearance")
            if abs(z - round(z, 3)) > 1e-8:
                raise ValueError("Setpoint exceeds driver's 0.001 mm command precision")

    def to_dict(self):
        return asdict(self)


def load_recipe(path=DEFAULT_RECIPE):
    path = Path(path).resolve()
    with path.open("rb") as stream:
        data = tomllib.load(stream)
    if "chart_path" in data:
        data["chart_path"] = str((path.parent / data["chart_path"]).resolve())
    if isinstance(data.get("lens_calibration"), str):
        data["lens_calibration"] = LensCalibration.load(path.parent / data["lens_calibration"])
    return Recipe(**data)


def simulation_fixture():
    return FocusFixture(enabled=True, measured=True, fixture_id="SIMULATED", calibration_id="SIMULATED",
                        camera_serial="SIMULATED", firmware_id="SIMULATED", safe_z_min_mm=99,
                        safe_z_max_mm=101, reference_z_mm=100, wd_uncertainty_mm=0.01,
                        motion_safety_verified=True, camera_pipeline_verified=True,
                        frame_timing_verified=True, wd_reference_record="SIMULATED")
