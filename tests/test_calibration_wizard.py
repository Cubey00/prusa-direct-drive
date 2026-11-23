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
        self.gotos, self.homes, self.finals, self.jogs, self.requests = [], [], [], [], []
        self.home_only = []

    def quit_calibrator(self):
        pass

    def is_running(self):
        return True

    def goto(self, x, y, z, label="", home_xy=False, z_final=None):
        self.gotos.append((label, round(x, 3), round(y, 3), round(z, 3)))
        self.homes.append(home_xy)
        self.finals.append(None if z_final is None else round(z_final, 3))
        return True

    def home(self, z, label="home"):
        self.home_only.append((label, round(z, 3)))
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
    # start from the shipped guesses, not from this laptop's last calibration
    machine_settings.save_values("sensor", {"offset_x": 0.0, "offset_y": -40.0, "offset_z": -80.0, "measured": False})
    machine_settings.save_values("calibration", {"cone_F1": [], "cone_F2": [], "cone_F3": []})
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


def do_point(w, p, x, y, z=85.2):
    """Ga naar kegel -> calibrator arrives -> insturen -> calibrator answers with the position."""
    wiz = w.wizard
    wiz.go_btn.click()
    msg(w, "goto_done", label=p, ok=True, message=f"At {p}")
    wiz.send_btn.click()
    assert w.server.requests[-1] == p
    msg(w, "position", point=p, x=x, y=y, z=z)


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
    wiz.step_group.button(1).setChecked(True)               # 1 mm
    wiz.jog_btns["Z-"].click()
    assert win.server.jogs == [(0.1, 0, 0, 120.0), (0, 0, -1.0, 120.0)]
    wiz.send_btn.click()
    assert win.server.requests == ["F1"] and not wiz.send_btn.isEnabled()
    # the head really ends up 0.6 mm right and 39.2 mm back of each cone (the true offset)
    msg(win, "position", point="F1", x=fids["F1"]["x"] - 0.6, y=fids["F1"]["y"] + 39.2, z=85.2)

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
    # the cone positions and the measured offset become the new standard
    saved = machine_settings.load()
    assert saved.sensor.offset == pytest.approx((0.6, -39.2, 5.0 - 85.2)) and saved.sensor.measured
    assert saved.calibration.cone_position("F2") == pytest.approx(
        (fids["F2"]["x"] - 0.6, fids["F2"]["y"] + 39.2, 85.2))
    wiz.finish_btn.click()
    assert not wiz.isVisible()

    # the next calibration drives straight to those spots, 1 cm (hover_clearance) above them
    win.start_calibration()
    assert wiz.go_btn.text() == "Ga naar kegel 1" and "vorige kalibratie" in wiz.text_label.text()
    wiz.go_btn.click()
    label, x, y, z = win.server.gotos[-1]
    assert (label, x, y) == ("F1", round(fids["F1"]["x"] - 0.6, 3), round(fids["F1"]["y"] + 39.2, 3))
    assert win.server.finals[-1] == 95.2 and z >= 95.2 and win.server.homes[-1] is True
    msg(win, "goto_done", label="F1", ok=True, message="At F1")
    assert "1 cm boven" in wiz.text_label.text()
    wiz.step_group.button(0).setChecked(True)               # 5 mm to come down fast
    wiz.jog_btns["Z-"].click()
    assert win.server.jogs[-1] == (0, 0, -5.0, 120.0)


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


def test_only_cone_2_gives_a_shift_calibration(win, plate):
    win.open_model(plate)
    win.start_calibration()
    wiz = win.wizard
    fids = fiducials(plate)
    assert wiz.mode_box.isVisible()
    wiz.mode_f2.setChecked(True)
    assert wiz.go_btn.text() == "Ga naar kegel 2" and "van" not in wiz.step_label.text()
    do_point(win, "F2", fids["F2"]["x"] - 0.6, fids["F2"]["y"] + 39.2)
    assert win.server.homes == [True]                         # X/Y homed before the only cone
    assert visible(wiz) == {"finish"} and "Alleen kegel 2" in wiz.text_label.text()
    cal = win.left.cal_points()
    for p in ("F1", "F3"):                                    # filled in as F2 + bed distance
        assert cal.machine[p] == pytest.approx((fids[p]["x"] - 0.6, fids[p]["y"] + 39.2))
    assert win.transform is not None
    saved = machine_settings.load()
    assert saved.sensor.offset == pytest.approx((0.6, -39.2, fids["F2"]["z"] - 85.2))
    assert saved.calibration.cone_position("F1") is None and saved.calibration.cone_position("F3") is None
    # the choice is remembered for the next calibration
    win.wizard = None
    win.start_calibration()
    assert win.wizard.mode_f2.isChecked() and win.wizard.go_btn.text() == "Ga naar kegel 2"


def test_offset_outside_reach_still_saves_cones_and_height(win, plate):
    """Sensor measured 1.5 mm left of the nozzle: the reach check refuses that X offset, but the
    cone positions and the measured height must still be saved (Meten needs them)."""
    win.open_model(plate)
    win.start_calibration()
    wiz = win.wizard
    wiz.mode_f2.setChecked(True)
    fids = fiducials(plate)
    do_point(win, "F2", fids["F2"]["x"] + 1.5, fids["F2"]["y"] + 39.2)
    assert "Kalibratie geslaagd" in wiz.text_label.text() and "niet opgeslagen" in wiz.text_label.text()
    saved = machine_settings.load()
    assert saved.calibration.cone_position("F2") is not None
    assert saved.sensor.offset == pytest.approx((0.0, -40.0, fids["F2"]["z"] - 85.2))


def test_cone_2_with_a_big_x_offset_saves_and_meten_works(win, plate):
    """Real case: the sensor sits 13.6 mm right of the nozzle. F1/F3 calculated from F2 lie
    outside the head's travel; only F2 is saved, the height too, and Meten can go."""
    from pathprogrammer import measure
    win.open_model(plate)
    win.start_calibration()
    wiz = win.wizard
    wiz.mode_f2.setChecked(True)
    fids = fiducials(plate)
    do_point(win, "F2", fids["F2"]["x"] - 13.61, fids["F2"]["y"] + 37.89, z=64.5)
    saved = machine_settings.load()
    assert saved.calibration.cone_position("F2") == pytest.approx((fids["F2"]["x"] - 13.61, fids["F2"]["y"] + 37.89, 64.5))
    assert saved.sensor.offset[2] == pytest.approx(fids["F2"]["z"] - 64.5)
    manifest = json.loads(plate.with_suffix(".json").read_text())
    t = measure.hover_target(manifest, saved)                     # no session: from the saved F2 alone
    part = manifest["parts"][0]
    assert t.source == "opgeslagen positie van kegel 2"
    assert (t.x, t.y) == pytest.approx((part["x"] - 13.61, part["y"] + 37.89))


def test_home_only_needs_a_saved_calibration(win, plate):
    win.open_model(plate)
    win.start_calibration()
    wiz = win.wizard
    wiz.mode_home.setChecked(True)
    assert wiz.go_btn.text() == "Home X en Y" and not wiz.go_btn.isEnabled()
    assert "Dit kan nog niet" in wiz.text_label.text()
    wiz.go_btn.click()
    assert win.server.home_only == []


def test_home_only_then_go_to_point_1(win, plate):
    """Calibration known to be right: home X/Y only, then straight on to Meten's point 1."""
    fids = fiducials(plate)
    machine_settings.save_values("calibration", {"cone_F2": [fids["F2"]["x"] - 0.6, fids["F2"]["y"] + 39.2, 85.2]})
    machine_settings.save_values("sensor", {"offset_x": 0.6, "offset_y": -39.2,
                                            "offset_z": round(fids["F2"]["z"] - 85.2, 2), "measured": True})
    win.open_model(plate)
    win.start_calibration()
    wiz = win.wizard
    wiz.mode_home.setChecked(True)
    assert wiz.go_btn.isEnabled() and not wiz.jogger.isVisible()
    wiz.go_btn.click()
    assert win.server.home_only == [("home", round(wiz.travel_z(), 3))] and win.server.gotos == []
    assert not wiz.go_btn.isEnabled()
    msg(win, "goto_done", label="home", ok=True, message="X and Y homed.")
    assert wiz.state == "done" and wiz.point1_btn.isVisible() and not wiz.finish_btn.isVisible()
    assert "X en Y gehomed" in wiz.text_label.text()
    assert not win._needs_home
    wiz.point1_btn.click()
    assert not wiz.isVisible()
    label, x, y, _ = win.server.gotos[-1]
    part = json.loads(plate.with_suffix(".json").read_text())["parts"][0]
    assert label == "punt 1" and win.server.homes[-1] is False       # already homed
    assert (x, y) == pytest.approx((part["x"] - 0.6, part["y"] + 39.2), abs=1e-3)
    # remembered per laptop
    win.wizard = None
    win.start_calibration()
    assert win.wizard.mode_home.isChecked()


def test_home_only_failure_is_shown(win, plate):
    fids = fiducials(plate)
    machine_settings.save_values("calibration", {"cone_F2": [fids["F2"]["x"] - 0.6, fids["F2"]["y"] + 39.2, 85.2]})
    win.open_model(plate)
    win.start_calibration()
    wiz = win.wizard
    wiz.mode_home.setChecked(True)
    wiz.go_btn.click()
    msg(win, "goto_done", label="home", ok=False, message="Homing X/Y failed: boom")
    assert wiz.state == "goto" and "boom" in wiz.link_label.text() and win._needs_home
