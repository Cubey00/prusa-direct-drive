import csv
from threading import Event
from unittest.mock import Mock

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QMessageBox

import gantry_controls as gc
from pathprogrammer.repeatability import HOME, MEASURE, MOVE, PrinterMotion, Step
from pathprogrammer.ui.main_window import MainWindow


@pytest.fixture
def panel(qtbot, tmp_path, monkeypatch):
    monkeypatch.setattr("pathprogrammer.ui.main_window.CalibratorServer", Mock(
        return_value=Mock(is_running=Mock(return_value=False))
    ))
    window = MainWindow(QSettings(str(tmp_path / "settings.ini"), QSettings.IniFormat))
    qtbot.addWidget(window)
    tab = window.repeatability
    window.centralWidget().setCurrentWidget(tab)
    window.show()
    tab.folder.setText(str(tmp_path))
    tab.run_name.setText("Bed fixture")
    tab.clearance.setText("100")
    for field, value in zip(tab.measurement, ("80", "90", "40")):
        field.setText(value)
    yield tab
    if tab.active:
        tab.stop_run()
        qtbot.waitUntil(lambda: not tab.active, timeout=5000)


def read_log(folder):
    path, = folder.glob("repeatability_*.csv")
    with path.open(newline="", encoding="utf-8") as stream:
        return list(csv.DictReader(stream))


@pytest.mark.parametrize("count", [1, 2, 3])
def test_sequence_pauses_for_exactly_the_configured_dials(panel, qtbot, tmp_path, monkeypatch, count):
    connect = Mock(side_effect=AssertionError("Simulation opened the printer"))
    monkeypatch.setattr(gc, "connect", connect)
    panel.cycles.setValue(2)
    panel.dial_count.setValue(count)
    panel._add_step(MOVE)
    for col, value in enumerate(("70", "90", "40"), 1):
        panel.steps.item(0, col).setText(value)
    panel._add_step(MEASURE)
    if count == 2:
        panel._add_step(HOME)
        panel._reorder(-1)
    if count == 3:
        panel.home_first.setChecked(True)
    panel.start.click()

    for cycle in (1, 2):
        qtbot.waitUntil(lambda: panel.prompt.isVisible() and f"Cycle {cycle} of" in panel.status.text())
        assert [field.isVisible() for field in panel.reading_fields] == [i < count for i in range(3)]
        panel.save.click()
        assert panel.prompt.isVisible(), "Blank readings must leave the sequence paused"
        for field, text in zip(panel.reading_fields[:count], ("-0,024", "0", "+0.011")):
            field.setText(text)
        panel.save.click()
    qtbot.waitUntil(lambda: not panel.active)
    rows = read_log(tmp_path)
    planned = [row["action"] for row in rows if row["event"] == "planned"]
    expected_plan = {1: [MOVE, MEASURE], 2: [MOVE, HOME, MEASURE], 3: [HOME, MOVE, MEASURE]}
    assert planned == expected_plan[count]
    home_cycles = [row["cycle"] for row in rows if row["action"] == HOME and row["event"] == "completed"]
    assert home_cycles == {1: [], 2: ["1", "2"], 3: ["0"]}[count]
    measurements = [row for row in rows if row["action"] == MEASURE and row["event"] == "completed"]
    assert [row["cycle"] for row in measurements] == ["1", "2"]
    for row in measurements:
        assert (row["mode"], row["test_name"], row["target_z_mm"]) == ("simulation", "Bed fixture", "40.0")
        expected = ["-0.024", "0.0", "0.011"][:count] + [""] * (3 - count)
        assert [row[f"dial_{n}_mm"] for n in range(1, 4)] == expected
    assert rows[-1]["event"] == "completed" and not rows[-1]["step"]
    connect.assert_not_called()


def test_stop_at_a_prompt_preserves_previous_readings(panel, qtbot, tmp_path):
    panel.dial_count.setValue(1)
    panel.cycles.setValue(2)
    panel._add_step(MEASURE)
    panel.start.click()
    qtbot.waitUntil(panel.prompt.isVisible)
    panel.reading_fields[0].setText("0.007")
    panel.save.click()
    qtbot.waitUntil(lambda: "Cycle 2 of" in panel.status.text() and panel.prompt.isVisible())
    panel.stop.click()
    qtbot.waitUntil(lambda: not panel.active)
    rows = read_log(tmp_path)
    assert [row["dial_1_mm"] for row in rows if row["dial_1_mm"]] == ["0.007"]
    assert [(row["cycle"], row["event"]) for row in rows if row["event"] == "stopped"] == [
        ("2", "stopped"), ("", "stopped")
    ]


def test_invalid_clearance_prevents_start(panel, tmp_path):
    panel._add_step(MEASURE)
    panel.clearance.setText("20")
    panel.start.click()
    assert not panel.active
    assert "Clearance Z" in panel.status.text()
    assert not list(tmp_path.glob("*.csv"))


def test_printer_error_ends_run_before_next_action(panel, qtbot, tmp_path, monkeypatch):
    motion = Mock()
    motion.execute.side_effect = OSError("Printer disconnected")
    monkeypatch.setattr("pathprogrammer.ui.repeatability_panel.PrinterMotion", Mock(return_value=motion))
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *args: QMessageBox.Yes))
    panel.simulate.setChecked(False)
    panel._add_step(HOME)
    panel._add_step(MEASURE)
    panel.start.click()
    qtbot.waitUntil(lambda: not panel.active)
    assert motion.execute.call_count == 1
    assert motion.execute.call_args.args[0] == Step(HOME)
    motion.close.assert_called_once_with(abort=True)
    assert not panel.prompt.isVisible()
    rows = read_log(tmp_path)
    assert rows[-1]["event"] == "failed"
    assert "Printer disconnected" in rows[-1]["details"]


def test_stop_during_motion_cancels_before_measurement(panel, qtbot, tmp_path, monkeypatch):
    entered = Event()
    motion = Mock()

    def make_motion(cancel):
        def execute(*_):
            entered.set()
            if cancel.wait(5):
                raise InterruptedError("Move cancelled")
        motion.execute.side_effect = execute
        return motion

    monkeypatch.setattr("pathprogrammer.ui.repeatability_panel.PrinterMotion", make_motion)
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *args: QMessageBox.Yes))
    panel.simulate.setChecked(False)
    panel.home_first.setChecked(True)
    panel._add_step(MEASURE)
    panel.start.click()
    qtbot.waitUntil(entered.is_set)
    panel.stop.click()
    qtbot.waitUntil(lambda: not panel.active)
    motion.interrupt.assert_called_once()
    motion.close.assert_called_once_with(abort=True)
    assert motion.execute.call_count == 1
    assert not panel.prompt.isVisible()
    assert read_log(tmp_path)[-1]["event"] == "stopped"


def test_printer_motion_preserves_z_approach_and_only_homes_when_requested(monkeypatch):
    commands = Mock()
    commands.read_position.return_value = (80.0, 90.0, 42.0)
    for name in ("connect", "send_gcode", "read_position", "move_absolute", "home_xy", "disconnect"):
        monkeypatch.setattr(gc, name, getattr(commands, name))
    motion = PrinterMotion(Event())
    motion.open()
    motion.execute(Step(MEASURE), (80, 90, 40), 100)
    assert [call.kwargs.get("z") for call in commands.move_absolute.call_args_list] == [40]
    motion.execute(Step(MOVE), (82, 90, 50), 100)
    calls = commands.move_absolute.call_args_list[1:]
    assert [call.kwargs.get("z") for call in calls] == [100, None, 50]
    assert calls[1].kwargs["x"] == 82 and calls[1].kwargs["y"] == 90
    commands.home_xy.assert_not_called()
    motion.execute(Step(HOME), None, 100)
    assert commands.move_absolute.call_args.kwargs["z"] == 100
    commands.home_xy.assert_called_once_with(cancel=motion.cancel)
    motion.close(abort=False)
    commands.disconnect.assert_called_once()
