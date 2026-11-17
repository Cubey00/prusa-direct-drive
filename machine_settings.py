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
import math
import tomllib
from dataclasses import dataclass
from pathlib import Path

DEFAULT_PATH = Path(__file__).resolve().parent / "machine_settings.toml"
ENV_VAR = "PRINTERPROJECT_SETTINGS"
PROBE_CLEARANCE = 3.0   # mm kept free around the firmware's G28 probe points


class SettingsError(ValueError):
    pass


@dataclass(frozen=True)
class Printer:
    model: str
    bed_size: tuple
    z_max: float
    travel_x: tuple                 # head (nozzle) travel in machine coordinates
    travel_y: tuple

    @property
    def travel(self):
        return self.travel_x, self.travel_y


@dataclass(frozen=True)
class PrusaLinkSettings:
    host: str
    user: str


@dataclass(frozen=True)
class Printing:
    filament: str
    bed_probe_points: tuple       # ((x, y), ...) bed mm; () = probe the whole print area
    wait_for_bed_frame: bool


@dataclass(frozen=True)
class WorkArea:
    usable_x: tuple
    usable_y: tuple
    probe_points: tuple             # ((x, y), ...) where the firmware probes the bed on G28

    @property
    def usable(self):
        """((x min, x max), (y min, y max)) in bed coordinates."""
        return self.usable_x, self.usable_y


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
    hover_clearance: float

    @property
    def offset(self):
        return self.offset_x, self.offset_y, self.offset_z

    def head_xy(self, x, y):
        """Head (machine) X/Y that puts the sensor above bed point (x, y), before calibration."""
        return x - self.offset_x, y - self.offset_y

    def hover_z(self, height):
        """Machine Z that keeps the sensor `hover_clearance` above something `height` mm tall
        (higher Z = lower bed; the sensor tip is -offset_z below the nozzle)."""
        return height + self.hover_clearance - self.offset_z


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
    cone_F1: tuple                # (x, y, z) head position on that cone, or () if not known
    cone_F2: tuple
    cone_F3: tuple

    def cone_position(self, name):
        """Saved head position (x, y, z) on fiducial `name` (F1/F2/F3), or None."""
        return getattr(self, f"cone_{name}", ()) or None


@dataclass(frozen=True)
class FocusFixture:
    enabled: bool = False
    measured: bool = False
    fixture_id: str = "unconfigured"
    calibration_id: str = "unconfigured"
    camera_serial: str = ""
    firmware_id: str = ""
    safe_z_min_mm: float = 0.0
    safe_z_max_mm: float = 0.0
    reference_z_mm: float = 0.0
    reference_wd_mm: float = 20.0
    minimum_wd_mm: float = 5.0
    wd_uncertainty_mm: float = 0.0
    position_tolerance_mm: float = 0.02
    z_speed_mm_s: float = 1.0

    def z_for_wd(self, wd):
        return self.reference_z_mm + wd - self.reference_wd_mm

    def wd_for_z(self, z):
        return self.reference_wd_mm + z - self.reference_z_mm


@dataclass(frozen=True)
class Settings:
    path: Path
    printer: Printer
    prusalink: PrusaLinkSettings
    printing: Printing
    work_area: WorkArea
    raster: Raster
    fiducials: Fiducials
    sensor: Sensor
    camera: Camera
    forces: Forces
    calibration: Calibration
    focusfield: FocusFixture = FocusFixture()

    def unmeasured(self):
        """Sections whose values are still first guesses (measured = false)."""
        return [name for name, sec in (("sensor", self.sensor), ("camera", self.camera))
                if not sec.measured]


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
    if kind == "points":
        if (not isinstance(value, list) or any(not isinstance(p, list) or len(p) != 2 for p in value)
                or any(isinstance(v, bool) or not isinstance(v, (int, float)) for p in value for v in p)):
            raise SettingsError(f"{where}: [{section}] {key} must be a list of [x, y] pairs, got {value!r}")
        return tuple((float(x), float(y)) for x, y in value)
    if kind == "xyz_or_empty":
        if (not isinstance(value, list) or len(value) not in (0, 3)
                or any(isinstance(v, bool) or not isinstance(v, (int, float)) for v in value)):
            raise SettingsError(f"{where}: [{section}] {key} must be [] or [x, y, z], got {value!r}")
        return tuple(float(v) for v in value)
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

    if not s.prusalink.host.strip() or not s.prusalink.user.strip():
        fail("[prusalink] host and user cannot be empty")
    bw, bh = s.printer.bed_size
    for name, (lo, hi), limit in (("usable_x", s.work_area.usable_x, bw), ("usable_y", s.work_area.usable_y, bh)):
        if not 0 <= lo < hi <= limit:
            fail(f"[work_area] {name} {lo:g}..{hi:g} must lie inside the bed 0..{limit:g}")
    (ux0, ux1), (uy0, uy1) = s.work_area.usable
    for px, py in s.work_area.probe_points:
        m = PROBE_CLEARANCE
        if ux0 - m < px < ux1 + m and uy0 - m < py < uy1 + m:
            fail(f"[work_area] probe point ({px:g}, {py:g}) is within {m:g} mm of the usable area: the "
                 "nozzle probes the bed there on every G28, so keep usable_x / usable_y clear of it")
    (tx0, tx1), (ty0, ty1) = s.printer.travel
    for px, py in s.printing.bed_probe_points:
        if not (tx0 <= px <= tx1 and ty0 <= py <= ty1):
            fail(f"[printing] bed probe point ({px:g}, {py:g}) is outside what the nozzle can reach "
                 f"(X {tx0:g}..{tx1:g}, Y {ty0:g}..{ty1:g})")
    for name in ("F1", "F2", "F3"):
        pos = s.calibration.cone_position(name)
        if pos and not (tx0 <= pos[0] <= tx1 and ty0 <= pos[1] <= ty1 and 0 <= pos[2] <= s.printer.z_max):
            fail(f"[calibration] cone_{name} {list(pos)} is outside the printer's travel")
    se = s.sensor
    reach = {"nozzle": (s.printer.travel_x, s.printer.travel_y),
             "sensor": ((s.printer.travel_x[0] + se.offset_x, s.printer.travel_x[1] + se.offset_x),
                        (s.printer.travel_y[0] + se.offset_y, s.printer.travel_y[1] + se.offset_y))}
    for what, ((rx0, rx1), (ry0, ry1)) in reach.items():
        if not (rx0 <= ux0 and ux1 <= rx1 - 1 and ry0 + 1 <= uy0 and uy1 <= ry1 - 1):
            fail(f"[work_area] usable X {ux0:g}..{ux1:g}, Y {uy0:g}..{uy1:g} is not inside what the "
                 f"{what} can reach (X {rx0:g}..{rx1:g}, Y {ry0:g}..{ry1:g}, 1 mm margin); "
                 "shrink usable_x / usable_y or check [printer] travel and [sensor] offset")
    if not 0 < -se.offset_z + s.fiducials.height + se.hover_clearance < s.printer.z_max:
        fail("[sensor] offset_z and hover_clearance put the travel height outside 0..z_max")
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
    op = s.focusfield
    numeric = (op.safe_z_min_mm, op.safe_z_max_mm, op.reference_z_mm, op.reference_wd_mm,
               op.minimum_wd_mm, op.wd_uncertainty_mm, op.position_tolerance_mm, op.z_speed_mm_s)
    if not all(math.isfinite(x) for x in numeric):
        fail("[focusfield] all distances and speeds must be finite")
    if not 0 <= op.safe_z_min_mm <= op.reference_z_mm <= op.safe_z_max_mm <= s.printer.z_max:
        fail("[focusfield] safe Z bounds and reference must lie inside 0..printer.z_max")
    if not (0 < op.minimum_wd_mm <= op.wd_for_z(op.safe_z_min_mm)
            and op.wd_uncertainty_mm >= 0 and 0 < op.position_tolerance_mm <= 0.1
            and 0 < op.z_speed_mm_s <= 10):
        fail("[focusfield] invalid clearance, uncertainty, tolerance or speed")
    if op.enabled and (not op.measured or op.safe_z_min_mm == op.safe_z_max_mm
                       or not op.camera_serial.strip() or not op.firmware_id.strip()
                       or op.fixture_id == "unconfigured" or op.calibration_id == "unconfigured"
                       or op.wd_uncertainty_mm <= 0):
        fail("[focusfield] enabling requires a measured fixture, camera, firmware and WD calibration")


def parse(data, where="settings"):
    s = Settings(
        path=Path(where),
        printer=_build(Printer, data, "printer", where, model=str, bed_size="pair", z_max=float,
                       travel_x="pair", travel_y="pair"),
        prusalink=_build(PrusaLinkSettings, data, "prusalink", where, host=str, user=str),
        printing=_build(Printing, data, "printing", where, filament=str, bed_probe_points="points",
                        wait_for_bed_frame=bool),
        work_area=_build(WorkArea, data, "work_area", where, usable_x="pair", usable_y="pair",
                         probe_points="points"),
        raster=_build(Raster, data, "raster", where, margin_x=float, margin_y=float, cell_pitch=float,
                      min_gap=float, max_part_height=float),
        fiducials=_build(Fiducials, data, "fiducials", where, inset=float, base_diameter=float,
                         height=float),
        sensor=_build(Sensor, data, "sensor", where, mount=str, offset_x=float, offset_y=float,
                      offset_z=float, measured=bool, housing_diameter=float, polymer_protrusion=float,
                      hover_clearance=float),
        camera=_build(Camera, data, "camera", where, mm_per_px=float, centre_px="pair", measured=bool),
        forces=_build(Forces, data, "forces", where, contact_g=float, max_force_g=float,
                      guard_trip_g=float, guard_release_g=float),
        calibration=_build(Calibration, data, "calibration", where, min_distance=float, scale_tol=float,
                           residual_tol=float, cone_F1="xyz_or_empty", cone_F2="xyz_or_empty",
                           cone_F3="xyz_or_empty"),
        focusfield=(_build(FocusFixture, data, "focusfield", where,
                          **{k: type(v) for k, v in vars(FocusFixture()).items()})
                    if "focusfield" in data else FocusFixture()),
    )
    _check(s, where)
    return s


def save_values(section, values, path=None):
    """Change `key = value` lines of one [section] in the TOML file, keeping every comment. The new
    file is validated first; on a SettingsError nothing is written. Returns the new Settings
    (modules that copied values at import keep the old ones until the app restarts)."""
    import re
    path = Path(path or os.environ.get(ENV_VAR) or DEFAULT_PATH)
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    current, todo = None, dict(values)
    for i, line in enumerate(lines):
        head = re.match(r"\s*\[([^\]]+)\]", line)
        if head:
            current = head.group(1).strip()
            continue
        m = re.match(r"(\s*)([A-Za-z_][A-Za-z0-9_]*)(\s*=\s*)([^#\n]*?)(\s*(#.*)?)(\r?\n)?$", line)
        if current == section and m and m.group(2) in todo:
            value = todo.pop(m.group(2))
            if isinstance(value, bool):
                text = "true" if value else "false"
            elif isinstance(value, str):
                text = f'"{value}"'
            elif isinstance(value, (list, tuple)):
                text = "[" + ", ".join(f"{float(v):.3f}" for v in value) + "]"
            else:
                text = repr(float(value))
            lines[i] = f"{m.group(1)}{m.group(2)}{m.group(3)}{text}{m.group(5) or ''}{m.group(7) or ''}"
    if todo:
        raise SettingsError(f"{path.name}: no [{section}] {', '.join(todo)} to change")
    new_text = "".join(lines)
    try:
        data = tomllib.loads(new_text)
    except tomllib.TOMLDecodeError as e:
        raise SettingsError(f"{path.name}: {e}") from None
    settings = parse(data, str(path))          # raises before anything is written
    path.write_text(new_text, encoding="utf-8")
    return settings


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
