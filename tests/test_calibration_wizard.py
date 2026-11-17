"""The 'Start kalibratie' window, from an opened plate to 'Kalibratie geslaagd', without hardware."""
import json

import pytest
from PySide6.QtCore import QSettings

import machine_settings
from pathprogrammer.ui.main_window import MainWindow
from slicing import pipeline
from slicing.testset import write_mini_testset


class FakeServer:
    """Stands in for the calibrator link: records what is sent, never launches anything."""

    def __init__(self):
        self.gotos, self.homes, self.jogs, self.requests = [], [], [], []

    def is_running(self):
        return True

    def goto(self, x, y, z, label="", home_xy=False):
        self.gotos.append((label, round(x, 3), round(y, 3), round(z, 3)))
        self.homes.append(home_xy)
        return True

    def jog(self, dx, dy, dz, feed):
        self.jogs.append((round(dx, 3), round(dy, 3), round(dz, 3), feed))
        return True

    def request_position(self, point):
        self.requests.append(point)
        return True

    def launch(self, script):
        raise AssertionError("must not start the calibrator in a test")

    def close(self):
        pass

    def terminate(self):
        pass


@pytest.fixture
def plate(tmp_path):
    write_mini_testset(tmp_path / "set")
    pipeline.run(tmp_path / "set", tmp_path / "out", do_slice=False, on_status=lambda m: None)
    return tmp_path / "out" / "plate_01.3mf"


@pytest.fixture
def win(qtbot, tmp_path, monkeypatch):
    settings_file = tmp_path / "machine_settings.toml"
    settings_file.write_text(machine_settings.DEFAULT_PATH.read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.setenv(machine_settings.ENV_VAR, str(settings_file))     # saving must not touch the repo
    w = MainWindow(QSettings(str(tmp_path / "s.ini"), QSettings.IniFormat))
    qtbot.addWidget(w)
    w.server.close()
    w.server = FakeServer()
    return w


def msg(w, kind, **kw):
    w.on_calibrator_message({"v": 1, "type": kind, **kw})


def fiducials(plate):
    return {f["name"]: f for f in json.loads(plate.with_suffix(".json").read_text())["fiducials"]}


def visible(wiz):
    """Which of the step controls the user can see right now."""
    return {name for name, w in (("go", wiz.go_btn), ("jogger", wiz.jogger), ("finish", wiz.finish_btn))
            if w.isVisible()}


def do_point(w, p, x, y):
    """Ga naar kegel -> calibrator arrives -> insturen -> calibrator answers with the position."""
    wiz = w.wizard
    wiz.go_btn.click()
    msg(w, "goto_done", label=p, ok=True, message=f"At {p}")
    wiz.send_btn.click()
    assert w.server.requests[-1] == p
    msg(w, "position", point=p, x=x, y=y, z=12.0)


def test_button_opens_the_wizard_and_asks_for_a_plate_first(win):
    assert win.left.calib_btn.text() == "Start kalibratie"
    win.left.calib_btn.click()
    wiz = win.wizard
    assert wiz.isVisible() and "Open eerst een plaat" in wiz.text_label.text()
    assert visible(wiz) == set()


def test_one_step_at_a_time_and_ends_with_position_known(win, plate):
    assert win.open_model(plate)
    win.start_calibration()
    wiz = win.wizard
    fids = fiducials(plate)

    # only one option: go to cone 1
    assert visible(wiz) == {"go"} and wiz.go_btn.text() == "Ga naar kegel 1"
    # sensor 40 mm in front of and 80 mm below the nozzle (settings file): head 40 mm further back,
    # travel height = tallest thing on the plate (5 mm) + 10 mm clearance + 80 mm
    wiz.go_btn.click()
    assert win.server.gotos == [("F1", fids["F1"]["x"], fids["F1"]["y"] + 40.0, 95.0)]
    assert not wiz.go_btn.isEnabled()                       # moving: nothing to click
    msg(win, "goto_done", label="F1", ok=True, message="At F1")

    # then the jogger, slow and small
    assert visible(wiz) == {"jogger"} and wiz.send_btn.text() == "Positie van kegel 1 insturen"
    wiz.jog_btns["X+"].click()
    wiz.step_group.button(0).setChecked(True)               # 1 mm
    wiz.jog_btns["Z-"].click()
    assert win.server.jogs == [(0.1, 0, 0, 120.0), (0, 0, -1.0, 120.0)]
    wiz.send_btn.click()
    assert win.server.requests == ["F1"] and not wiz.send_btn.isEnabled()
    # the head really ends up 0.6 mm right and 39.2 mm back of each cone (the true offset)
    msg(win, "position", point="F1", x=fids["F1"]["x"] - 0.6, y=fids["F1"]["y"] + 39.2, z=12.0)

    # next: only "go to cone 2"
    assert visible(wiz) == {"go"} and wiz.go_btn.text() == "Ga naar kegel 2"
    for p in ("F2", "F3"):
        do_point(win, p, fids[p]["x"] - 0.6, fids[p]["y"] + 39.2)

    assert visible(wiz) == {"finish"} and wiz.finish_btn.text() == "Afsluiten: positie bekend"
    assert win.server.homes == [True, False, False]          # X/Y homed once, before cone 1
    text = wiz.text_label.text()
    assert "Kalibratie geslaagd! De printer weet nu waar de sensorkop t.o.v. de objecten is." in text
    assert "Positie bekend" in text and "opgeslagen" in text
    assert win.transform is not None
    # the measured offset is kept for the next prediction
    assert machine_settings.load().sensor.offset[:2] == pytest.approx((0.6, -39.2))
    wiz.finish_btn.click()
    assert not wiz.isVisible()


def test_a_mixed_up_point_is_flagged_not_called_a_success(win, plate):
    win.open_model(plate)
    win.start_calibration()
    fids = fiducials(plate)
    do_point(win, "F1", fids["F1"]["x"], fids["F1"]["y"] + 40)
    do_point(win, "F2", fids["F2"]["x"], fids["F2"]["y"] + 40)
    do_point(win, "F3", fids["F3"]["x"] + 3.0, fids["F3"]["y"] + 40)   # 3 mm off
    wiz = win.wizard
    assert "geslaagd" not in wiz.text_label.text() and "Opnieuw beginnen" in wiz.text_label.text()
    assert visible(wiz) == set() and wiz.restart_btn.isVisible()
    assert machine_settings.load().sensor.offset[1] == -40.0          # nothing saved
    wiz.restart_btn.click()
    assert visible(wiz) == {"go"} and wiz.go_btn.text() == "Ga naar kegel 1"
    assert win.left.cal_points().machine["F1"] is None


def test_failed_move_and_failed_send_are_explained(win, plate):
    win.open_model(plate)
    win.start_calibration()
    wiz = win.wizard
    wiz.go_btn.click()
    msg(win, "goto_done", label="F1", ok=False, message="Refused: outside the travel limits")
    assert visible(wiz) == {"go"} and wiz.go_btn.isEnabled() and "Refused" in wiz.link_label.text()
    wiz.go_btn.click()
    msg(win, "goto_done", label="F1", ok=True, message="At F1")
    wiz.send_btn.click()
    msg(win, "status", ok=False, message="Send failed: the head is still moving")
    assert wiz.send_btn.isEnabled() and "still moving" in wiz.link_label.text()


def test_out_of_reach_cone_cannot_be_driven_to(win, plate):
    manifest = json.loads(plate.with_suffix(".json").read_text())
    for f in manifest["fiducials"]:
        if f["name"] == "F3":
            f["y"] = 200.0                  # the head would have to go to Y 240, past its stop at 221
    plate.with_suffix(".json").write_text(json.dumps(manifest))
    win.open_model(plate)
    win.start_calibration()
    wiz = win.wizard
    fids = fiducials(plate)
    do_point(win, "F1", fids["F1"]["x"], fids["F1"]["y"] + 40)
    do_point(win, "F2", fids["F2"]["x"], fids["F2"]["y"] + 40)
    assert not wiz.go_btn.isEnabled() and "buiten bereik" in wiz.text_label.text()


def test_no_answer_from_the_calibrator_is_explained(win, plate, qtbot, monkeypatch):
    from pathprogrammer.ui import calibration_wizard
    monkeypatch.setattr(calibration_wizard, "GOTO_ANSWER_MS", 100)
    monkeypatch.setattr(calibration_wizard, "HOME_ANSWER_MS", 100)
    win.open_model(plate)
    win.start_calibration()
    wiz = win.wizard
    wiz.go_btn.click()
    qtbot.waitUntil(lambda: "nog niets" in wiz.link_label.text(), timeout=3000)
    assert "scherm van de printer" in wiz.link_label.text() and wiz.go_btn.isEnabled()
    wiz.go_btn.click()                                  # an answer in time: no warning afterwards
    msg(win, "goto_done", label="F1", ok=True, message="At F1")
    qtbot.wait(300)
    assert "nog niets" not in wiz.link_label.text() and visible(wiz) == {"jogger"}
