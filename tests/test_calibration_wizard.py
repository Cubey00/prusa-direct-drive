"""The 'Start kalibratie' window, from an opened plate to 'Kalibratie geslaagd', without hardware."""
import json

import pytest
from PySide6.QtCore import QSettings

import machine_settings
from pathprogrammer.ui.main_window import MainWindow
from slicing import pipeline
from slicing.testset import write_mini_testset


class FakeServer:
    """Stands in for the calibrator link: records goto's, never launches anything."""

    def __init__(self):
        self.gotos = []

    def is_running(self):
        return True

    def goto(self, x, y, z, label=""):
        self.gotos.append((label, round(x, 3), round(y, 3), round(z, 3)))
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


def send_position(w, point, x, y):
    w.on_calibrator_message({"v": 1, "type": "position", "point": point, "x": x, "y": y, "z": 95.0})


def test_button_opens_the_wizard_and_asks_for_a_plate_first(win):
    assert win.left.calib_btn.text() == "Start kalibratie"
    win.left.calib_btn.click()
    wiz = win.wizard
    assert wiz.isVisible() and "Open eerst een plaat" in wiz.plate_label.text()
    assert not any(b.isEnabled() for b in wiz.go_btns.values())


def test_full_calibration_ends_with_success(win, plate):
    assert win.open_model(plate)
    win.start_calibration()
    wiz = win.wizard
    fids = {f["name"]: f for f in json.loads(plate.with_suffix(".json").read_text())["fiducials"]}
    # sensor 40 mm in front of and 80 mm below the nozzle (settings file): head 40 mm further back,
    # travel height = tallest thing on the plate (5 mm) + 10 mm clearance + 80 mm
    wiz.go_btns["F1"].click()
    assert win.server.gotos == [("F1", fids["F1"]["x"], fids["F1"]["y"] + 40.0, 95.0)]
    assert "nu deze" in wiz.state_labels["F1"].text()

    # the head really ends up 0.6 mm right and 39.2 mm back of each cone (the true offset)
    for p in ("F1", "F2", "F3"):
        send_position(win, p, fids[p]["x"] - 0.6, fids[p]["y"] + 39.2)
        assert "✓" in wiz.state_labels[p].text()
    text = wiz.result_label.text()
    assert "Kalibratie geslaagd! De printer weet nu waar de sensorkop t.o.v. de objecten is." in text
    assert win.transform is not None

    # the measured offset can be taken over and saved into the settings file
    assert wiz.take_offset_btn.isEnabled()
    wiz.take_offset_btn.click()
    assert (wiz.off_x.value(), wiz.off_y.value()) == pytest.approx((0.6, -39.2))
    wiz.save_off_btn.click()
    assert "Opgeslagen" in wiz.off_status.text()
    assert machine_settings.load().sensor.offset[:2] == pytest.approx((0.6, -39.2))


def test_a_mixed_up_point_is_flagged_not_called_a_success(win, plate):
    win.open_model(plate)
    win.start_calibration()
    fids = {f["name"]: f for f in json.loads(plate.with_suffix(".json").read_text())["fiducials"]}
    send_position(win, "F1", fids["F1"]["x"], fids["F1"]["y"] + 40)
    send_position(win, "F2", fids["F2"]["x"], fids["F2"]["y"] + 40)
    send_position(win, "F3", fids["F3"]["x"] + 3.0, fids["F3"]["y"] + 40)   # 3 mm off
    text = win.wizard.result_label.text()
    assert "geslaagd" not in text and "controleer" in text and "F3" in text


def test_offset_out_of_reach_is_refused_and_disables_the_point(win, plate):
    win.open_model(plate)
    win.start_calibration()
    wiz = win.wizard
    wiz.off_y.setValue(-60.0)                    # F3 would need the head beyond Y 221
    assert not wiz.go_btns["F3"].isEnabled() and "buiten bereik" in wiz.target_labels["F3"].text()
    wiz.save_off_btn.click()
    assert "Niet opgeslagen" in wiz.off_status.text()
    assert machine_settings.load().sensor.offset[1] == -40.0
