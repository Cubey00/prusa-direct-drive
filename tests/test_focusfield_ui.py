from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QMessageBox, QTabWidget
import pytest

from pathprogrammer.ui.main_window import MainWindow


@pytest.fixture
def window(qtbot, tmp_path):
    w = MainWindow(QSettings(str(tmp_path / "settings.ini"), QSettings.IniFormat))
    w.settings.setValue("log_folder", str(tmp_path))
    qtbot.addWidget(w)
    yield w
    w.server.close()


def test_page_preserves_existing_workflows(window):
    tabs = window.centralWidget()
    assert isinstance(tabs, QTabWidget)
    assert tabs.tabText(1) == "Focus-Field MTF"
    assert window.plates is not None and window.printer is not None
    assert window.left.calib_btn is not None and window.run.execute_btn is not None
    assert window.focusfield.simulate.isChecked()


def test_simulated_preview_runs_in_worker_without_devices(window, qtbot):
    p = window.focusfield
    p.check_preview()
    qtbot.waitUntil(lambda: p.thread is None, timeout=10000)
    assert "All four chart markers detected; 124 local edges" in p.status.toPlainText()
    assert not p.preview.pixmap().isNull()
    assert not p.hardware_active


def test_hardware_activity_blocks_touch_calibration_and_printer_actions(window, monkeypatch):
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: warnings.append(args[2]))
    window.focusfield.hardware_active = True
    window._optical_activity(True)
    window.start_calibration()
    window.execute()
    window.open_calibrator()
    window.printer._action(lambda link: None, "test")
    assert len(warnings) == 4
    assert window.wizard is None and window.thread is None
    window.focusfield.hardware_active = False
    window._optical_activity(False)


def test_unmeasured_physical_scan_cannot_start(window, monkeypatch):
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: warnings.append(args[2]))
    window.focusfield.simulate.setChecked(False)
    window.focusfield.start_scan()
    assert window.focusfield.thread is None
    assert "disabled or unmeasured" in warnings[0]
