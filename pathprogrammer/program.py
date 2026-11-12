"""Point series ("program") data, validation and JSON (de)serialisation. No GUI dependency."""
import json
from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import Enum

from . import config
from .calibration import CalPoints, Issue, Transform

SCHEMA_VERSION = 1


class TouchMode(Enum):
    FORCE = "force"     # press until |force| reaches value grams
    DEPTH = "depth"     # press value mm past contact

    @property
    def unit(self):
        return "g" if self is TouchMode.FORCE else "mm"

    @property
    def label(self):
        return "M" if self is TouchMode.FORCE else "d"

    @property
    def value_range(self):
        return config.FORCE_RANGE if self is TouchMode.FORCE else config.DEPTH_RANGE


@dataclass
class TouchPoint:
    x: float
    y: float
    mode: TouchMode = TouchMode.FORCE
    value: float = 100.0
    speed: float = config.TOUCH_SPEED      # mm/s after contact
    image_step: float = 0.0                # take an image every this much (g or mm, like value); 0 = none

    def n_images(self):
        return 0 if self.image_step <= 0 else int(self.value / self.image_step + 1e-9)

    def describe(self):
        return f"{self.mode.label} = {self.value:g}{self.mode.unit}"


@dataclass
class RunSettings:
    approach_speed: float = config.APPROACH_SPEED
    max_travel: float = config.MAX_TRAVEL
    contact_g: float = config.CONTACT_G
    travel_speed: float = config.TRAVEL_SPEED


@dataclass
class ModelRef:
    filename: str = ""
    sha256: str = ""
    orientation: list = field(default_factory=lambda: [[1, 0, 0], [0, 1, 0], [0, 0, 1]])
    face_size_mm: tuple = (0.0, 0.0)


@dataclass
class Program:
    points: list = field(default_factory=list)
    settings: RunSettings = field(default_factory=RunSettings)
    model: ModelRef = field(default_factory=ModelRef)
    calibration: CalPoints = None
    transform: Transform = None


def _in(v, rng):
    return rng[0] <= v <= rng[1]


def validate(program, face_size, transform=None):
    issues = []
    w, h = face_size
    if not program.points:
        issues.append(Issue("error", "The program has no points."))
    for i, p in enumerate(program.points, 1):
        if not (0 <= p.x <= w and 0 <= p.y <= h):
            issues.append(Issue("error", f"Point {i} ({p.x:.2f}, {p.y:.2f}) lies outside the {w:.1f} x {h:.1f} mm face."))
        if not _in(p.value, p.mode.value_range):
            lo, hi = p.mode.value_range
            issues.append(Issue("error", f"Point {i}: {p.describe()} outside {lo:g}-{hi:g} {p.mode.unit}."))
        if p.image_step < 0 or p.image_step > p.value:
            issues.append(Issue("error", f"Point {i}: image step {p.image_step:g} {p.mode.unit} outside 0-{p.value:g}."))
        elif p.n_images() > config.MAX_IMAGES_PER_POINT:
            issues.append(Issue("error", f"Point {i}: {p.n_images()} images exceeds {config.MAX_IMAGES_PER_POINT}."))
        if not _in(p.speed, config.TOUCH_SPEED_RANGE):
            issues.append(Issue("error", f"Point {i}: speed {p.speed:g} mm/s outside {config.TOUCH_SPEED_RANGE}."))
        if transform is not None:
            X, Y = transform.face_to_machine(p.x, p.y)
            (x0, x1), (y0, y1) = config.MACHINE_XY_BOUNDS
            if not (x0 <= X <= x1 and y0 <= Y <= y1):
                issues.append(Issue("error", f"Point {i} maps to X{X:.2f} Y{Y:.2f}, outside the machine area."))
    s = program.settings
    for name, v, rng in (("approach speed", s.approach_speed, config.APPROACH_SPEED_RANGE),
                         ("travel speed", s.travel_speed, config.TRAVEL_SPEED_RANGE),
                         ("max travel", s.max_travel, config.MAX_TRAVEL_RANGE)):
        if not _in(v, rng):
            issues.append(Issue("error", f"{name} {v:g} outside {rng}."))
    return issues


# ---------------- JSON ----------------

def to_dict(program):
    m = program.model
    return {
        "schema_version": SCHEMA_VERSION,
        "saved_at": datetime.now().isoformat(timespec="seconds"),
        "model": {"filename": m.filename, "sha256": m.sha256, "orientation": m.orientation,
                  "face_size_mm": list(m.face_size_mm)},
        "calibration": None if program.calibration is None else {
            **program.calibration.to_dict(),
            "transform": program.transform.to_dict() if program.transform else None},
        "settings": asdict(program.settings),
        "points": [{"x": p.x, "y": p.y, "mode": p.mode.value, "value": p.value, "speed": p.speed,
                    "image_step": p.image_step}
                   for p in program.points],
    }


def from_dict(d):
    if d.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"Unsupported schema version {d.get('schema_version')}")
    m = d["model"]
    prog = Program(
        points=[TouchPoint(p["x"], p["y"], TouchMode(p["mode"]), p["value"], p["speed"],
                            p.get("image_step", 0.0)) for p in d["points"]],
        settings=RunSettings(**d["settings"]),
        model=ModelRef(m["filename"], m["sha256"], m["orientation"], tuple(m["face_size_mm"])),
    )
    cal = d.get("calibration")
    if cal:
        prog.calibration = CalPoints.from_dict(cal)
        if cal.get("transform"):
            prog.transform = Transform.from_dict(cal["transform"])
    return prog


def save(program, path):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(to_dict(program), f, indent=2)


def load(path):
    with open(path, encoding="utf-8") as f:
        return from_dict(json.load(f))


def compare_model(program, filename, sha256, orientation_list):
    """Warnings when a loaded program doesn't match the open model."""
    warnings = []
    m = program.model
    if m.sha256 and sha256 and m.sha256 != sha256:
        warnings.append(f"Program was made for '{m.filename}' (different file contents) - open model is '{filename}'.")
    elif m.filename and m.filename != filename:
        warnings.append(f"Program was made for '{m.filename}', open model is '{filename}'.")
    if m.orientation != orientation_list:
        warnings.append("Program was saved with a different orientation; the stored one has been applied.")
    return warnings
