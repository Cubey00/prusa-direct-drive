import cmath
import math

import pytest

from pathprogrammer.calibration import (CalPoints, Transform, face_points_from_manifest, fit, residuals,
                                        validate)

# face coordinates of F1 (front left), F2 (front right), F3 (back left) on a plate face
FACE = {"F1": (10, 90), "F2": (240, 90), "F3": (10, 10)}


def make_cal(face, t, noise=None):
    c = CalPoints()
    for p, xy in face.items():
        c.face[p] = xy
        X, Y = t.face_to_machine(*xy)
        dx, dy = (noise or {}).get(p, (0, 0))
        c.machine[p] = (X + dx, Y + dy)
    return c


@pytest.mark.parametrize("deg,b", [(0, 50 + 80j), (3, 120 + 10j), (-5, 0j)])
def test_fit_recovers_transform(deg, b):
    truth = Transform(cmath.exp(1j * math.radians(deg)), b, -1)
    t = fit(make_cal(FACE, truth))
    assert t.scale == pytest.approx(1.0)
    assert t.rotation_deg == pytest.approx(deg)
    for x, y in [(0, 0), (10, 25), (70, 5)]:
        assert t.face_to_machine(x, y) == pytest.approx(truth.face_to_machine(x, y))
        assert t.machine_to_face(*t.face_to_machine(x, y)) == pytest.approx((x, y))


def test_face_front_is_machine_minus_y():
    t = fit(make_cal(FACE, Transform(1 + 0j, 100 + 100j, -1)))
    X0, Y0 = t.face_to_machine(0, 0)
    X1, Y1 = t.face_to_machine(0, 10)
    assert X1 == pytest.approx(X0) and Y1 == pytest.approx(Y0 - 10)


def test_least_squares_spreads_small_errors_and_reports_residuals():
    truth = Transform(1 + 0j, 5 + 60j, -1)
    c = make_cal(FACE, truth, noise={"F1": (0.1, 0), "F2": (-0.1, 0.05), "F3": (0, -0.05)})
    t = fit(c)
    res = residuals(c, t)
    assert max(res.values()) < 0.15
    assert validate(c) == []


def test_bad_point_warns_with_its_name():
    c = make_cal(FACE, Transform(1 + 0j, 0j, -1), noise={"F3": (3, 0)})
    msgs = [i.msg for i in validate(c) if i.level == "warning"]
    assert any(m.startswith("F3") for m in msgs)


def test_points_on_one_line_rejected():
    c = make_cal({"F1": (0, 0), "F2": (100, 0), "F3": (200, 1)}, Transform(1 + 0j, 0j, -1))
    assert any("one line" in i.msg for i in validate(c))
    with pytest.raises(ValueError):
        fit(c)


def test_points_too_close_rejected():
    c = make_cal({"F1": (5, 5), "F2": (6, 5), "F3": (5, 50)}, Transform(1 + 0j, 0j, -1))
    assert any(i.level == "error" for i in validate(c))


def test_incomplete_rejected():
    assert validate(CalPoints())[0].level == "error"
    c = make_cal(FACE, Transform(1 + 0j, 0j, -1))
    c.machine["F3"] = None
    assert validate(c)[0].level == "error"


def test_scale_warning():
    c = make_cal(FACE, Transform(1.05 + 0j, 0j, -1))
    assert any("Scale" in i.msg for i in validate(c))
    c = make_cal(FACE, Transform(0.3 + 0.954j, 0j, -1))   # rotation alone is not flagged
    assert validate(c) == []


def test_transform_dict_roundtrip():
    t = Transform(0.99 + 0.02j, 3 - 4j, -1)
    assert Transform.from_dict(t.to_dict()) == t


def test_old_two_point_calibration_loads_as_unset():
    old = {"face": {"back_left": [0, 0], "front_right": [50, 30]},
           "machine": {"back_left": [100, 100], "front_right": [150, 70]}}
    c = CalPoints.from_dict(old)
    assert not c.complete() and all(v is None for v in c.face.values())


def test_face_points_from_manifest():
    manifest = {"fiducials": [{"name": "F1", "x": 10, "y": 70, "z": 5},
                              {"name": "F2", "x": 240, "y": 70, "z": 5},
                              {"name": "F3", "x": 10, "y": 150, "z": 5}]}
    bounds = ((6.0, 66.0, 0.0), (244.0, 154.0, 5.0))       # cone bases reach 4 mm past the tips
    face = face_points_from_manifest(manifest, bounds)
    assert face == {"F1": (4.0, 84.0), "F2": (234.0, 84.0), "F3": (4.0, 4.0)}
    # bed X/Y -> face -> machine with an identity bed placement gives the bed coordinates back
    t = Transform(1 + 0j, complex(6, 154), -1)
    assert t.face_to_machine(*face["F2"]) == pytest.approx((240, 70))
