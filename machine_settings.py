"""
machine_settings.py
Reads machine_settings.toml: every value that depends on the physical setup (stops, sensor
offset, camera scale, fiducials, force limits) in one file. The config modules
(slicing/config.py, pathprogrammer/config.py, gantry_controls) take their values from SETTINGS,
so the TOML file is the only place to change them.

PRINTERPROJECT_SETTINGS=<path> loads another file (e.g. for a second printer or a test).
Stdlib only, no Qt.
"""
import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

DEFAULT_PATH = Path(__file__).resolve().parent / "machine_settings.toml"
ENV_VAR = "PRINTERPROJECT_SETTINGS"


class SettingsError(ValueError):
    pass


@dataclass(frozen=True)
class Printer:
    model: str
    bed_size: tuple
    z_max: float


@dataclass(frozen=True)
class WorkArea:
    usable_x: tuple
    usable_y: tuple
    machine_x: tuple
    machine_y: tuple
    measured: bool

    @property
    def usable(self):
        """((x min, x max), (y min, y max)) in bed coordinates."""
        return self.usable_x, self.usable_y

    @property
    def machine(self):
        return self.machine_x, self.machine_y


@dataclass(frozen=True)
class Raster:
    margin_x: float
    margin_y: float
    cell_pitch: float
    min_gap: float
    max_part_height: float


@dataclass(frozen=True)
class Fiducials:
    inset: float
    base_diameter: float
    height: float


@dataclass(frozen=True)
class Sensor:
    mount: str
    offset_x: float
    offset_y: float
    offset_z: float
    measured: bool
    housing_diameter: float
    polymer_protrusion: float

    @property
    def offset(self):
        return self.offset_x, self.offset_y, self.offset_z


@dataclass(frozen=True)
class Camera:
    mm_per_px: float
    centre_px: tuple
    measured: bool


@dataclass(frozen=True)
class Forces:
    contact_g: float
    max_force_g: float
    guard_trip_g: float
    guard_release_g: float


@dataclass(frozen=True)
class Calibration:
    min_distance: float
    scale_tol: float
    residual_tol: float


@dataclass(frozen=True)
class Settings:
    path: Path
    printer: Printer
    work_area: WorkArea
    raster: Raster
    fiducials: Fiducials
    sensor: Sensor
    camera: Camera
    forces: Forces
    calibration: Calibration

    def unmeasured(self):
        """Sections whose values are still first guesses (measured = false)."""
        return [name for name, sec in (("work_area", self.work_area), ("sensor", self.sensor),
                                       ("camera", self.camera)) if not sec.measured]


# ---------------- parsing ----------------

def _get(data, section, key, kind, where):
    try:
        value = data[section][key]
    except KeyError:
        raise SettingsError(f"{where}: missing [{section}] {key}") from None
    if kind is float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise SettingsError(f"{where}: [{section}] {key} must be a number, got {value!r}")
        return float(value)
    if kind is bool:
        if not isinstance(value, bool):
            raise SettingsError(f"{where}: [{section}] {key} must be true or false, got {value!r}")
        return value
    if kind is str:
        if not isinstance(value, str):
            raise SettingsError(f"{where}: [{section}] {key} must be text, got {value!r}")
        return value
    if kind == "pair":
        if (not isinstance(value, list) or len(value) != 2
                or any(isinstance(v, bool) or not isinstance(v, (int, float)) for v in value)):
            raise SettingsError(f"{where}: [{section}] {key} must be two numbers like [0.0, 250.0], "
                                f"got {value!r}")
        return float(value[0]), float(value[1])
    raise TypeError(kind)


def _build(cls, data, section, where, **kinds):
    return cls(**{key: _get(data, section, key, kind, where) for key, kind in kinds.items()})


def _check(s, where):
    def fail(msg):
        raise SettingsError(f"{where}: {msg}")

    bw, bh = s.printer.bed_size
    for name, (lo, hi), limit in (("usable_x", s.work_area.usable_x, bw), ("usable_y", s.work_area.usable_y, bh)):
        if not 0 <= lo < hi <= limit:
            fail(f"[work_area] {name} {lo:g}..{hi:g} must lie inside the bed 0..{limit:g}")
    for name, (lo, hi) in (("machine_x", s.work_area.machine_x), ("machine_y", s.work_area.machine_y)):
        if not lo < hi:
            fail(f"[work_area] {name} must go from low to high")
    r = s.raster
    if r.cell_pitch <= r.min_gap:
        fail("[raster] cell_pitch must be larger than min_gap")
    f = s.fiducials
    (x0, x1), (y0, y1) = s.work_area.usable
    radius = f.base_diameter / 2
    if f.inset < radius or 2 * f.inset > min(x1 - x0, y1 - y0):
        fail("[fiducials] inset must be at least half the base diameter and fit in the usable area")
    if f.inset + radius > r.margin_x:
        fail(f"[fiducials] cones (inset {f.inset:g} + radius {radius:g}) do not fit in "
             f"[raster] margin_x {r.margin_x:g}")
    if f.height <= 0 or f.base_diameter <= 0:
        fail("[fiducials] height and base_diameter must be positive")
    fo = s.forces
    if not 0 < fo.contact_g < fo.max_force_g < fo.guard_trip_g:
        fail("[forces] needs 0 < contact_g < max_force_g < guard_trip_g")
    if not 0 < fo.guard_release_g < fo.guard_trip_g:
        fail("[forces] needs 0 < guard_release_g < guard_trip_g")
    if s.camera.mm_per_px < 0:
        fail("[camera] mm_per_px cannot be negative")
    if s.camera.measured and s.camera.mm_per_px == 0:
        fail("[camera] measured = true needs mm_per_px")


def parse(data, where="settings"):
    s = Settings(
        path=Path(where),
        printer=_build(Printer, data, "printer", where, model=str, bed_size="pair", z_max=float),
        work_area=_build(WorkArea, data, "work_area", where, usable_x="pair", usable_y="pair",
                         machine_x="pair", machine_y="pair", measured=bool),
        raster=_build(Raster, data, "raster", where, margin_x=float, margin_y=float, cell_pitch=float,
                      min_gap=float, max_part_height=float),
        fiducials=_build(Fiducials, data, "fiducials", where, inset=float, base_diameter=float,
                         height=float),
        sensor=_build(Sensor, data, "sensor", where, mount=str, offset_x=float, offset_y=float,
                      offset_z=float, measured=bool, housing_diameter=float, polymer_protrusion=float),
        camera=_build(Camera, data, "camera", where, mm_per_px=float, centre_px="pair", measured=bool),
        forces=_build(Forces, data, "forces", where, contact_g=float, max_force_g=float,
                      guard_trip_g=float, guard_release_g=float),
        calibration=_build(Calibration, data, "calibration", where, min_distance=float, scale_tol=float,
                           residual_tol=float),
    )
    _check(s, where)
    return s


def load(path=None):
    path = Path(path or os.environ.get(ENV_VAR) or DEFAULT_PATH)
    try:
        with open(path, "rb") as f:
            data = tomllib.load(f)
    except FileNotFoundError:
        raise SettingsError(f"Settings file {path} not found") from None
    except tomllib.TOMLDecodeError as e:
        raise SettingsError(f"{path.name}: {e}") from None
    return parse(data, str(path))


SETTINGS = load()
