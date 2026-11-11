import json

from pathprogrammer.calibration import Transform
from pathprogrammer.executor import run_program
from pathprogrammer.hardware import SimHardware
from pathprogrammer.program import Program, TouchMode, TouchPoint

T = Transform(1 + 0j, 100 + 150j, -1)


def prog(n=3):
    return Program(points=[TouchPoint(10 * i, 5, TouchMode.FORCE if i % 2 else TouchMode.DEPTH,
                                      100 if i % 2 else 2, 1.0) for i in range(n)])


def test_runs_all_points_in_order(tmp_path):
    hw, events = SimHardware(time_scale=0), []
    s = run_program(prog(), T, hw, lambda *a: events.append(a), log_dir=tmp_path)
    assert s.outcome == "completed" and s.done == 3
    moves = [c for c in hw.calls if c[0] == "move_xy"]
    assert [(round(c[1]), round(c[2])) for c in moves] == [(100, 145), (110, 145), (120, 145)]
    assert hw.calls[0] == ("open",) and hw.calls[-1] == ("close",)
    assert [e[1] for e in events if e[0] == "point_started"] == [0, 1, 2]
    log = json.loads(open(s.log_path).read())
    assert log["outcome"] == "completed" and len(log["points"]) == 3
    assert log["points"][0]["machine"] == [100, 145]


def test_stops_on_failure(tmp_path):
    hw = SimHardware(time_scale=0, fail_at=2)
    s = run_program(prog(4), T, hw, log_dir=tmp_path)
    assert s.outcome == "failed" and s.done == 1
    assert len([c for c in hw.calls if c[0] == "touch"]) == 2
    assert hw.calls[-1] == ("close",)


def test_abort(tmp_path):
    hw = SimHardware(time_scale=0)
    s = run_program(prog(), T, hw, should_abort=lambda: True, log_dir=tmp_path)
    assert s.outcome == "aborted" and s.done == 0
