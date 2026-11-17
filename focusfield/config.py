"""Versioned recipes, separate from measured machine safety/calibration."""
from dataclasses import asdict, dataclass
from decimal import Decimal
import math
from pathlib import Path
import tomllib

from machine_settings import FocusFixture

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CHART = ROOT / "focusfield/charts/focus_field_chart_layout.json"
DEFAULT_RECIPE = ROOT / "experiments/focusfield_20mm.toml"


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

    def __post_init__(self):
        if self.schema_version != 1:
            raise ValueError("Unsupported recipe schema_version")
        for name in ("wd_start_mm", "wd_end_mm", "step_mm", "nominal_wd_mm", "settle_s",
                     "exposure_us", "gain", "fixed_frequency_cpp"):
            if not math.isfinite(getattr(self, name)):
                raise ValueError(f"{name} must be finite")
        if not 0 < self.wd_start_mm < self.wd_end_mm or self.step_mm < 0.001:
            raise ValueError("WD interval must increase, with step >= 0.001 mm")
        if not self.wd_start_mm <= self.nominal_wd_mm <= self.wd_end_mm:
            raise ValueError("Nominal WD must lie within scan")
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

    def validate_fixture(self, fixture, printer_z_max, *, simulation=False):
        if not simulation and not (fixture.enabled and fixture.measured):
            raise ValueError("Optical fixture is disabled or unmeasured in machine_settings.toml")
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
    return Recipe(**data)


def simulation_fixture():
    return FocusFixture(enabled=True, measured=True, fixture_id="SIMULATED", calibration_id="SIMULATED",
                        camera_serial="SIMULATED", firmware_id="SIMULATED", safe_z_min_mm=99,
                        safe_z_max_mm=101, reference_z_mm=100, wd_uncertainty_mm=0.01)
