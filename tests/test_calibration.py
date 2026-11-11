import cmath
import math

import pytest

from pathprogrammer.calibration import CalPoints, Transform, fit, validate


def make_cal(face_bl, face_fr, t):
    c = CalPoints()
    c.face["back_left"], c.face["front_right"] = face_bl, face_fr
    c.machine["back_left"] = t.face_to_machine(*face_bl)
    c.machine["front_right"] = t.face_to_machine(*face_fr)
    return c


@pytest.mark.parametrize("deg,b", [(0, 50 + 80j), (3, 120 + 10j), (-5, 0j)])
def test_fit_recovers_transform(deg, b):
    truth = Transform(cmath.exp(1j * math.radians(deg)), b, -1)
    t = fit(make_cal((2, 3), (60, 40), truth))
    assert t.scale == pytest.approx(1.0)
    assert t.rotation_deg == pytest.approx(deg)
    for x, y in [(0, 0), (10, 25), (70, 5)]:
        assert t.face_to_machine(x, y) == pytest.approx(truth.face_to_machine(x, y))
        assert t.machine_to_face(*t.face_to_machine(x, y)) == pytest.approx((x, y))


def test_face_front_is_machine_minus_y():
    t = fit(make_cal((0, 0), (50, 30), Transform(1 + 0j, 100 + 100j, -1)))
    X0, Y0 = t.face_to_machine(0, 0)
    X1, Y1 = t.face_to_machine(0, 10)
    assert X1 == pytest.approx(X0) and Y1 == pytest.approx(Y0 - 10)


def test_degenerate_rejected():
    c = make_cal((5, 5), (6, 5), Transform(1 + 0j, 0j, -1))
    assert any(i.level == "error" for i in validate(c))
    with pytest.raises(ValueError):
        fit(c)


def test_incomplete_rejected():
    assert validate(CalPoints())[0].level == "error"


def test_scale_warning():
    c = make_cal((0, 0), (50, 30), Transform(1.05 + 0j, 0j, -1))
    assert any("Scale" in i.msg for i in validate(c))
    c = make_cal((0, 0), (50, 30), Transform(0.3 + 0.954j, 0j, -1))   # rotation alone is not flagged
    assert validate(c) == []


def test_transform_dict_roundtrip():
    t = Transform(0.99 + 0.02j, 3 - 4j, -1)
    assert Transform.from_dict(t.to_dict()) == t
