import json

from pathprogrammer import program as pm
from pathprogrammer.calibration import CalPoints, Transform
from pathprogrammer.program import Program, TouchMode, TouchPoint


def sample():
    p = Program(points=[TouchPoint(10, 5, TouchMode.FORCE, 100, 1.0),
                        TouchPoint(20, 5, TouchMode.DEPTH, 0.05, 0.5)])
    p.model = pm.ModelRef("model.3mf", "abc", [[1, 0, 0], [0, 1, 0], [0, 0, 1]], (73.4, 11.7))
    c = CalPoints()
    c.face = {"F1": (0, 10), "F2": (70, 10), "F3": (0, 0)}
    c.machine = {"F1": (100, 140), "F2": (170, 140), "F3": (100, 150)}
    p.calibration, p.transform = c, Transform(1 + 0j, 100 + 150j, -1)
    return p


def test_json_roundtrip(tmp_path):
    p = sample()
    f = tmp_path / "prog.json"
    pm.save(p, f)
    q = pm.load(f)
    assert q.points == p.points
    assert q.settings == p.settings
    assert q.model == p.model
    assert q.calibration.to_dict() == p.calibration.to_dict()
    assert q.transform == p.transform
    d = json.loads(f.read_text())
    assert d["schema_version"] == 1 and d["points"][1]["mode"] == "depth"


def test_validate_bounds_and_limits():
    p = sample()
    assert pm.validate(p, (73.4, 11.7), p.transform) == []
    p.points.append(TouchPoint(80, 5))                       # outside the face
    p.points.append(TouchPoint(5, 5, TouchMode.DEPTH, 50))   # depth too large
    msgs = [i.msg for i in pm.validate(p, (73.4, 11.7))]
    assert any("outside the" in m for m in msgs) and any(m.startswith("Point 4") for m in msgs)


def test_validate_machine_bounds():
    p = sample()
    p.transform = Transform(1 + 0j, 240 + 100j, -1)          # pushes X past 250
    assert any("machine area" in i.msg for i in pm.validate(p, (73.4, 11.7), p.transform))


def test_compare_model():
    p = sample()
    assert pm.compare_model(p, "model.3mf", "abc", [[1, 0, 0], [0, 1, 0], [0, 0, 1]]) == []
    w = pm.compare_model(p, "other.3mf", "xyz", [[0, -1, 0], [1, 0, 0], [0, 0, 1]])
    assert len(w) == 2
