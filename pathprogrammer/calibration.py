"""Face <-> machine similarity transform from three calibration point pairs. No GUI dependency.

The three points are the fiducial cones printed on every plate (slicing/): F1 front-left,
F2 front-right, F3 back-left of the usable bed area. On a plate 3MF their face coordinates come
from the plate manifest; the machine coordinates are the head positions sent by the calibrator.

Face points become w = x + i*(y_sign*y), machine points m = X + i*Y, and m = a*w + b, fitted by
least squares over the three pairs. The mirror is the y_sign convention, so a and b describe
rotation, scale and shift only. With three points the fit is overdetermined: each point's
residual shows whether a point was mistaken or measured badly.
"""
import cmath
import math
from dataclasses import dataclass, field
from pathlib import Path

from . import config

POINTS = ("F1", "F2", "F3")
LABELS = {"F1": "F1 front left", "F2": "F2 front right", "F3": "F3 back left"}


@dataclass
class Issue:
    level: str          # "error" or "warning"
    msg: str


@dataclass
class CalPoints:
    face: dict = field(default_factory=lambda: {p: None for p in POINTS})      # name -> (x, y)
    machine: dict = field(default_factory=lambda: {p: None for p in POINTS})   # name -> (X, Y)

    def complete(self):
        return all(self.face[p] is not None and self.machine[p] is not None for p in POINTS)

    def to_dict(self):
        return {"face": {p: list(self.face[p]) if self.face[p] else None for p in POINTS},
                "machine": {p: list(self.machine[p]) if self.machine[p] else None for p in POINTS}}

    @classmethod
    def from_dict(cls, d):
        """Points missing from d (e.g. a program saved with the old two-point calibration) stay unset."""
        c = cls()
        for p in POINTS:
            c.face[p] = tuple(d["face"][p]) if d.get("face", {}).get(p) else None
            c.machine[p] = tuple(d["machine"][p]) if d.get("machine", {}).get(p) else None
        return c


@dataclass
class Transform:
    a: complex
    b: complex
    y_sign: int = config.FACE_Y_TO_MACHINE_SIGN

    def face_to_machine(self, x, y):
        m = self.a * complex(x, self.y_sign * y) + self.b
        return m.real, m.imag

    def machine_to_face(self, X, Y):
        w = (complex(X, Y) - self.b) / self.a
        return w.real, self.y_sign * w.imag

    @property
    def scale(self):
        return abs(self.a)

    @property
    def rotation_deg(self):
        return math.degrees(cmath.phase(self.a))

    def to_dict(self):
        return {"a": [self.a.real, self.a.imag], "b": [self.b.real, self.b.imag], "y_sign": self.y_sign}

    @classmethod
    def from_dict(cls, d):
        return cls(complex(*d["a"]), complex(*d["b"]), int(d["y_sign"]))


def _dist(p, q):
    return math.hypot(p[0] - q[0], p[1] - q[1])


def _spread(pts):
    """Smallest height of the triangle (mm): near 0 means the points lie on one line."""
    (x1, y1), (x2, y2), (x3, y3) = pts
    area2 = abs((x2 - x1) * (y3 - y1) - (x3 - x1) * (y2 - y1))
    longest = max(_dist(pts[0], pts[1]), _dist(pts[1], pts[2]), _dist(pts[0], pts[2]))
    return area2 / longest if longest else 0.0


def validate(cal, y_sign=config.FACE_Y_TO_MACHINE_SIGN):
    """Issues for the current input; fit() only succeeds when there is no error."""
    if not cal.complete():
        return [Issue("error", f"Enter all {2 * len(POINTS)} calibration coordinates "
                               f"({', '.join(POINTS)} on the face and on the machine).")]
    issues = []
    for kind, pts in (("Face", cal.face), ("Machine", cal.machine)):
        for i, p in enumerate(POINTS):
            for q in POINTS[i + 1:]:
                if _dist(pts[p], pts[q]) < config.CAL_MIN_DISTANCE:
                    issues.append(Issue("error", f"{kind} points {p} and {q} are closer than "
                                                 f"{config.CAL_MIN_DISTANCE:g} mm."))
        if not issues and _spread([pts[p] for p in POINTS]) < config.CAL_MIN_DISTANCE:
            issues.append(Issue("error", f"{kind} points lie (almost) on one line."))
    if issues:
        return issues
    t = _solve(cal, y_sign)
    if abs(t.scale - 1) > config.CAL_SCALE_TOL:
        issues.append(Issue("warning", f"Scale {t.scale:.4f} is far from 1: wrong points or wrong model scale?"))
    for p, r in residuals(cal, t).items():
        if r > config.CAL_RESIDUAL_TOL:
            issues.append(Issue("warning", f"{p} is {r:.2f} mm off the fit: wrong point, swapped "
                                           f"points or a bad measurement?"))
    return issues


def _solve(cal, y_sign):
    w = [complex(x, y_sign * y) for x, y in (cal.face[p] for p in POINTS)]
    m = [complex(*cal.machine[p]) for p in POINTS]
    wc, mc = sum(w) / len(w), sum(m) / len(m)
    a = sum((mi - mc) * (wi - wc).conjugate() for wi, mi in zip(w, m)) / sum(abs(wi - wc) ** 2 for wi in w)
    return Transform(a, mc - a * wc, y_sign)


def residuals(cal, t):
    """Distance (mm) between each measured machine point and where the fit puts it."""
    return {p: _dist(t.face_to_machine(*cal.face[p]), cal.machine[p]) for p in POINTS}


def fit(cal, y_sign=config.FACE_Y_TO_MACHINE_SIGN):
    errors = [i.msg for i in validate(cal, y_sign) if i.level == "error"]
    if errors:
        raise ValueError(" ".join(errors))
    return _solve(cal, y_sign)


# ---------------- plates ----------------

def manifest_for(model_path):
    """The plate manifest next to a plate 3MF (plate_01.3mf -> plate_01.json), else None."""
    import json
    path = Path(model_path).with_suffix(".json")
    if not path.exists():
        return None
    try:
        manifest = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    return manifest if "fiducials" in manifest else None


def face_points_from_manifest(manifest, mesh_bounds):
    """Face (x, y) of the fiducial tips for the unrotated plate mesh with these bounds:
    face x = X - xmin, face y = ymax - Y (see model_io)."""
    lo, hi = mesh_bounds
    return {f["name"]: (float(f["x"] - lo[0]), float(hi[1] - f["y"]))
            for f in manifest["fiducials"] if f["name"] in POINTS}
