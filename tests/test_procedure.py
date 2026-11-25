"""'Do whole measuring procedure': home X/Y, then every part of the plate (touch, N steps, a photo
at each), all in one folder."""
import json
import re
from pathlib import Path

import pytest

from tests.test_calibration_wizard import FakeServer, msg, plate, win   # noqa: F401 (fixtures)
from tests.test_measure import calibrate


@pytest.fixture
def run(win, plate, tmp_path, qtbot):
    """The window with a calibrated plate (2 parts), a fake printer and a fake camera; photos go to tmp."""
    calibrate(plate)
    win.open_model(plate)
    win.PROC_SETTLE_MS = 0
    s = win.server
    s.creeps, s.steps, s.halts = [], [], 0
    s.creep = lambda fast, mx: s.creeps.append((fast, mx)) or True
    s.step = lambda mm, g: s.steps.append((round(mm, 3), g)) or True
    s.halt = lambda: setattr(s, "halts", s.halts + 1) or True
    shots = []
    feed = win.photo.feed
    feed.connected = True
    feed.take_photo = lambda folder, name=None: shots.append(Path(folder) / name) or True
    win.photo.settings.setValue("photo_folder", str(tmp_path / "photos"))
    win._shots = shots
    return win


def wait(qtbot, cond):
    qtbot.waitUntil(lambda: bool(cond()), timeout=3000)


def save_photo(w, qtbot, n):
    """Wait for photo n to be asked for, then let the 'camera' save it."""
    wait(qtbot, lambda: len(w._shots) >= n)
    path = w._shots[n - 1]
    path.write_bytes(b"tif")
    w.photo.photoSaved.emit(path, None)
    return path


def start(w, steps, step_mm=0.1, max_g=300.0):
    w.measure.n_steps.setValue(steps)
    w.measure.step_mm.setValue(step_mm)
    w.measure.max_force.setValue(max_g)
    w.measure.proc_btn.click()
    assert w.server.home_only and w.server.home_only[-1][0] == "home"
    assert not w.measure.proc_btn.isEnabled() and not w.measure.hover_btn.isEnabled()
    msg(w, "goto_done", label="home", ok=True, message="X and Y homed.")


def reach_and_touch(w, qtbot, label, n_gotos, n_creeps, z0=93.2, force=8.0):
    """The head arrives above `label`, the creep finds the object at z0."""
    wait(qtbot, lambda: len(w.server.gotos) == n_gotos and w.server.gotos[-1][0] == label)
    assert w.server.homes[-1] is False                     # homed once at the start: not again
    msg(w, "goto_done", label=label, ok=True, message="")
    wait(qtbot, lambda: len(w.server.creeps) == n_creeps)
    assert w.server.creeps[-1] == (1.5, 8.0)
    msg(w, "touch_done", ok=True, status="ok", message="The sensor touches the object.", z=z0, force_g=force)
    msg(w, "force", g=force + 0.3)


def do_steps(w, qtbot, n, first_photo, z0=93.2, steps_before=0):
    for i in range(1, n + 1):
        wait(qtbot, lambda: len(w.server.steps) == steps_before + i)
        msg(w, "touch_done", ok=True, status="ok", message="", z=z0 - 0.1 * i, force_g=40.0 * i)
        msg(w, "force", g=40.0 * i + 0.5)
        save_photo(w, qtbot, first_photo + i)


def test_whole_procedure_measures_every_part_into_one_folder(run, qtbot):
    w = run
    start(w, steps=2)
    # point 1
    reach_and_touch(w, qtbot, "point 1", n_gotos=1, n_creeps=1)
    first = save_photo(w, qtbot, 1)
    folder = first.parent
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}", folder.name)       # date_start time
    assert folder.parent.name == "photos" and "_d0.000mm_F8.3g_" in first.name
    do_steps(w, qtbot, 2, first_photo=1)
    # on to point 2 (the calibrator lifts to the travel height first), the same again
    reach_and_touch(w, qtbot, "point 2", n_gotos=2, n_creeps=2, z0=92.7, force=6.0)
    save_photo(w, qtbot, 4)
    do_steps(w, qtbot, 2, first_photo=4, z0=92.7, steps_before=2)
    # done: back up to 3 mm above the last part
    wait(qtbot, lambda: len(w.server.gotos) == 3)
    assert w.server.gotos[-1][0] == "point 2"
    msg(w, "goto_done", label="point 2", ok=True, message="")
    wait(qtbot, lambda: w._proc is None)
    assert "All parts measured" in w.measure.status.text() and w.measure.proc_btn.isEnabled()
    assert len(w._shots) == 6 and all(p.parent == folder for p in w._shots)
    data = json.loads((folder / "procedure.json").read_text())
    assert [(r["point"], r["result"], r["steps_done"], r["photos"]) for r in data["points"]] == \
        [(1, "all 2 steps done", 2, 3), (2, "all 2 steps done", 2, 3)]
    assert [p["point"] for p in data["photos"]] == [1, 1, 1, 2, 2, 2]
    assert [p["indentation_mm"] for p in data["photos"]] == pytest.approx([0.0, 0.1, 0.2] * 2)
    assert [p["force_g"] for p in data["photos"]] == pytest.approx([8.3, 40.5, 80.5, 6.3, 40.5, 80.5])
    assert data["photos"][0]["object"] != data["photos"][3]["object"]
    assert data["photos"][2]["head_z"] == pytest.approx(93.0)
    assert (folder / "measurements.csv").read_text().count("\n") == 7            # header + 6 photos


def test_max_force_ends_that_part_and_goes_on(run, qtbot):
    w = run
    start(w, steps=10, step_mm=0.2, max_g=150.0)
    reach_and_touch(w, qtbot, "point 1", n_gotos=1, n_creeps=1)
    save_photo(w, qtbot, 1)
    wait(qtbot, lambda: len(w.server.steps) == 1)
    assert w.server.steps[-1] == (0.2, 150.0)
    msg(w, "touch_done", ok=False, status="limit", message="Stopped: the force went above 150 g.",
        z=93.05, force_g=151.0)
    save_photo(w, qtbot, 2)                                 # a photo at the max force too
    reach_and_touch(w, qtbot, "point 2", n_gotos=2, n_creeps=2)
    assert len(w.server.steps) == 1                         # no more steps on point 1
    data = json.loads((w._shots[0].parent / "procedure.json").read_text())
    assert data["points"][0]["result"] == "max force 150 g reached" and data["photos"][-1]["at_max_force"]


def test_a_part_without_contact_is_skipped(run, qtbot):
    w = run
    start(w, steps=1)
    wait(qtbot, lambda: len(w.server.gotos) == 1)
    msg(w, "goto_done", label="point 1", ok=True, message="")
    wait(qtbot, lambda: len(w.server.creeps) == 1)
    msg(w, "touch_done", ok=False, status="no_contact", message="No contact within 15 mm")
    reach_and_touch(w, qtbot, "point 2", n_gotos=2, n_creeps=2)
    save_photo(w, qtbot, 1)
    do_steps(w, qtbot, 1, first_photo=1)
    wait(qtbot, lambda: len(w.server.gotos) == 3)
    msg(w, "goto_done", label="point 2", ok=True, message="")
    wait(qtbot, lambda: w._proc is None)
    assert "1 part(s) skipped" in w.measure.status.text()
    data = json.loads((w._shots[0].parent / "procedure.json").read_text())
    assert data["points"][0]["result"].startswith("skipped") and data["points"][1]["photos"] == 2


def test_stop_ends_the_procedure_without_moving(run, qtbot):
    w = run
    start(w, steps=5)
    reach_and_touch(w, qtbot, "point 1", n_gotos=1, n_creeps=1)
    save_photo(w, qtbot, 1)
    wait(qtbot, lambda: len(w.server.steps) == 1)
    w.stop()
    assert w._proc is None and w.server.halts == 1
    msg(w, "touch_done", ok=False, status="aborted", message="Stopped.", z=93.15, force_g=20.0)
    assert w.measure.proc_btn.isEnabled() and len(w.server.gotos) == 1      # not lifted by itself


def test_procedure_needs_a_camera_and_a_plate(win, plate):
    win.measure.proc_btn.click()
    assert "No camera connected" in win.measure.status.text() and not win.server.home_only
    win.photo.feed.connected = True
    win.measure.proc_btn.click()
    assert "Open a plate first" in win.measure.status.text() and not win.server.home_only


def test_settings_are_remembered(run):
    w = run
    w.measure.max_force.setValue(250.0)
    w.measure.step_mm.setValue(0.05)
    w.measure.n_steps.setValue(50)
    w.measure.max_force.setValue(1.0)          # clamped to the minimum (10 g)
    w._load_procedure_settings()
    assert (w.measure.max_force.value(), w.measure.step_mm.value(), w.measure.n_steps.value()) == (10.0, 0.05, 50)
