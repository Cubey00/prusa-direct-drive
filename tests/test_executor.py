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
    s = run_program(prog(), T, hw, lambda *a: events.append(a), run_dir=tmp_path / "run")
    assert s.outcome == "completed" and s.done == 3
    moves = [c for c in hw.calls if c[0] == "move_xy"]
    assert [(round(c[1]), round(c[2])) for c in moves] == [(100, 145), (110, 145), (120, 145)]
    assert hw.calls[0] == ("open",) and hw.calls[-1] == ("close",)
    assert [e[1] for e in events if e[0] == "point_started"] == [0, 1, 2]
    log = json.loads(open(s.log_path + "/run_log.json").read())
    assert log["outcome"] == "completed" and len(log["points"]) == 3
    assert log["points"][0]["machine"] == [100, 145]


def test_stops_on_failure(tmp_path):
    hw = SimHardware(time_scale=0, fail_at=2)
    s = run_program(prog(4), T, hw, run_dir=tmp_path / "run")
    assert s.outcome == "failed" and s.done == 1
    assert len([c for c in hw.calls if c[0] == "touch"]) == 2
    assert hw.calls[-1] == ("close",)


def test_abort(tmp_path):
    hw = SimHardware(time_scale=0)
    s = run_program(prog(), T, hw, should_abort=lambda: True, run_dir=tmp_path / "run")
    assert s.outcome == "aborted" and s.done == 0


def test_run_folder_images_and_heightmap(tmp_path):
    import trimesh
    from pathprogrammer.model_io import Orientation, oriented_mesh
    mesh = oriented_mesh(trimesh.creation.box(extents=(40, 30, 3)), Orientation())
    p = prog(2)
    p.points[1].image_step = 25.0            # point 2 is a 100 g force point -> 4 images
    hw = SimHardware(time_scale=0)
    s = run_program(p, T, hw, run_dir=tmp_path / "r", mesh=mesh, run_name="r")
    d = tmp_path / "r"
    assert s.outcome == "completed"
    for f in ("run_log.json", "images.json", "heightmap.csv", "heightmap.png"):
        assert (d / f).exists()
    idx = json.loads((d / "images.json").read_text())["images"]
    assert [i["file"] for i in idx] == [f"images/point_02_0{k}.tiff" for k in range(1, 5)]
    assert [i["force_g"] for i in idx] == [25, 50, 75, 100]
    # point 2 is at face (10, 5); the head stands at the machine position it maps to
    assert (idx[0]["x"], idx[0]["y"]) == (10, 5) and idx[0]["machine"] == [110, 145]
    assert all((d / i["file"]).exists() for i in idx)
    assert json.loads((d / "run_log.json").read_text())["points"][1]["images"][0] == idx[0]["file"]
