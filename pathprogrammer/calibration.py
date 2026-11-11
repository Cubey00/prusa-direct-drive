"""Face <-> machine similarity transform from two calibration point pairs. No GUI dependency.

Face points become w = x + i*(y_sign*y), machine points m = X + i*Y, and m = a*w + b.
Two points cannot tell a mirror from a rotation, so the mirror is the y_sign convention.
A third point would call for a least-squares (Umeyama) fit instead; not needed yet.
"""
import cmath
import math
from dataclasses import dataclass, field

from . import config

POINTS = ("back_left", "front_right")


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
        c = cls()
        for p in POINTS:
            c.face[p] = tuple(d["face"][p]) if d["face"].get(p) else None
            c.machine[p] = tuple(d["machine"][p]) if d["machine"].get(p) else None
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


def validate(cal, y_sign=config.FACE_Y_TO_MACHINE_SIGN):
    """Issues for the current input; fit() only succeeds when there is no error."""
    if not cal.complete():
        return [Issue("error", "Enter all four calibration coordinates.")]
    issues = []
    f1, f2 = cal.face["back_left"], cal.face["front_right"]
    m1, m2 = cal.machine["back_left"], cal.machine["front_right"]
    if _dist(f1, f2) < config.CAL_MIN_DISTANCE:
        issues.append(Issue("error", f"Face points are closer than {config.CAL_MIN_DISTANCE:g} mm."))
    if _dist(m1, m2) < config.CAL_MIN_DISTANCE:
        issues.append(Issue("error", f"Machine points are closer than {config.CAL_MIN_DISTANCE:g} mm."))
    if issues:
        return issues
    t = _solve(cal, y_sign)
    if abs(t.scale - 1) > config.CAL_SCALE_TOL:
        issues.append(Issue("warning", f"Scale {t.scale:.4f} is far from 1: wrong points or wrong model scale?"))
    return issues


def _solve(cal, y_sign):
    w1, w2 = (complex(x, y_sign * y) for x, y in (cal.face["back_left"], cal.face["front_right"]))
    m1, m2 = (complex(*cal.machine["back_left"]), complex(*cal.machine["front_right"]))
    a = (m2 - m1) / (w2 - w1)
    return Transform(a, m1 - a * w1, y_sign)


def fit(cal, y_sign=config.FACE_Y_TO_MACHINE_SIGN):
    errors = [i.msg for i in validate(cal, y_sign) if i.level == "error"]
    if errors:
        raise ValueError(" ".join(errors))
    return _solve(cal, y_sign)
