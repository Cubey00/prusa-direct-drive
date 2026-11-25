"""The nozzle -> sensor offset is shown above Start calibration; changing it by hand takes two steps."""
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QDialogButtonBox

import machine_settings
from pathprogrammer.ui.sensor_offset import SensorOffsetDialog, offset_text


def settings_copy(tmp_path, monkeypatch):
    f = tmp_path / "machine_settings.toml"
    f.write_text(machine_settings.DEFAULT_PATH.read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.setenv(machine_settings.ENV_VAR, str(f))      # never touch the repo's file
    return f


def test_offset_needs_the_box_and_the_word(qtbot, tmp_path, monkeypatch):
    settings_copy(tmp_path, monkeypatch)
    dlg = SensorOffsetDialog(machine_settings.load())
    qtbot.addWidget(dlg)
    save = dlg.buttons.button(QDialogButtonBox.Save)
    assert not save.isEnabled() and not dlg.fields[1].isEnabled()
    dlg.sure.setChecked(True)
    assert dlg.fields[1].isEnabled() and not save.isEnabled()
    dlg.fields[1].setValue(-39.5)
    dlg.word.setText("adjust")
    assert save.isEnabled()
    save.click()
    s = machine_settings.load()
    assert s.sensor.offset[1] == -39.5 and s.sensor.measured is False
    assert "-39.50" in offset_text(s.sensor) and "estimated" in offset_text(s.sensor)


def test_offset_the_sensor_cannot_work_with_is_refused(qtbot, tmp_path, monkeypatch):
    from pathprogrammer.ui import sensor_offset
    settings_copy(tmp_path, monkeypatch)
    shown = []
    monkeypatch.setattr(sensor_offset.QMessageBox, "warning", lambda *a: shown.append(a[2]))
    dlg = SensorOffsetDialog(machine_settings.load())
    qtbot.addWidget(dlg)
    dlg.sure.setChecked(True)
    dlg.fields[1].setValue(-45.0)                 # sensor can no longer reach the back of the plate
    dlg.word.setText("ADJUST")
    dlg.buttons.button(QDialogButtonBox.Save).click()
    assert shown and "sensor can reach" in shown[0] and dlg.saved is None
    assert machine_settings.load().sensor.offset[1] != -45.0


def test_main_window_shows_the_offset(qtbot, tmp_path, monkeypatch):
    from pathprogrammer.ui.main_window import MainWindow
    settings_copy(tmp_path, monkeypatch)
    w = MainWindow(QSettings(str(tmp_path / "s.ini"), QSettings.IniFormat))
    qtbot.addWidget(w)
    x, y, z = machine_settings.load().sensor.offset
    assert f"Y {y:+.2f}" in w.left.offset_label.text() and w.left.offset_btn.isVisible() is False  # not shown yet
    w.server.close()
