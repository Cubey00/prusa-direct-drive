from unittest.mock import Mock

from PySide6.QtCore import QSettings

from pathprogrammer.ui.main_window import MainWindow
from pathprogrammer.ui.style import APP_STYLESHEET


def test_camera_status_leaves_print_and_calibration_controls_visible(qapp, qtbot, tmp_path, monkeypatch):
    monkeypatch.setattr("pathprogrammer.ui.main_window.CalibratorServer", Mock())
    old_stylesheet = qapp.styleSheet()
    qapp.setStyleSheet(APP_STYLESHEET)
    try:
        window = MainWindow(QSettings(str(tmp_path / "settings.ini"), QSettings.IniFormat))
        qtbot.addWidget(window)
        window.resize(1280, 850)
        window.show()
        split = window.centralWidget().currentWidget()
        left = split.widget(0)

        window.photo.stateChanged.emit(
            False, "No camera: the macOS camera backend could not start. "
            "Check the Aravis installation and verify that the camera is connected before retrying."
        )
        qapp.processEvents()
        assert left.viewport().width() >= left.widget().width()
        status = window.photo.state_label
        assert status.height() > status.fontMetrics().height()
        assert window.photo.rect().contains(status.geometry())

        split.setSizes([0, 800, 480])
        qapp.processEvents()
        assert left.viewport().width() >= left.widget().width()
    finally:
        qapp.setStyleSheet(old_stylesheet)
