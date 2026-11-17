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


def test_preview_reports_center_error_without_starting_motion(window, qtbot):
    p = window.focusfield
    assert not p.center_btn.isEnabled() and not p.scout_btn.isEnabled()
    p.check_preview()
    qtbot.waitUntil(lambda: p.thread is None, timeout=10000)
    assert 'Chart-center error' in p.status.toPlainText()
    assert p.last_preview and p.center_btn.isEnabled() and p.scout_btn.isEnabled()
    assert not any(c[0] in {'move', 'move_xy'} for c in p._sim_stage.calls)
    p.simulate.setChecked(False)
    assert p.last_preview is None and not p.center_btn.isEnabled()


def test_setup_requires_new_preview_and_never_moves_from_registration_alone(window, monkeypatch):
    messages = []
    monkeypatch.setattr(QMessageBox, 'warning', lambda *args: messages.append(args[2]))
    window.focusfield.start_setup('center')
    assert 'preview' in messages[0].lower()
    assert window.focusfield.thread is None


def test_loading_unsupported_backend_recipe_does_not_silently_select_rgb(window, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QFileDialog
    p = window.focusfield
    supported = [p.format.itemText(i) for i in range(p.format.count())]
    unsupported = next(fmt for fmt in ['Mono12', 'BayerRG12p'] if fmt not in supported)
    recipe = tmp_path / 'unsupported.toml'
    recipe.write_text(f'pixel_format = "{unsupported}"\n', encoding='utf-8')
    warnings = []
    old = p.recipe
    monkeypatch.setattr(QFileDialog, 'getOpenFileName', lambda *args: (str(recipe), ''))
    monkeypatch.setattr(QMessageBox, 'warning', lambda *args: warnings.append(args[2]))
    p.load_recipe_dialog()
    assert 'not supported' in warnings[0]
    assert p.recipe is old and p.format.currentText() == 'RGB8'


@pytest.mark.parametrize('finish', ['stop', 'window_close', 'disconnect'])
def test_physical_preview_cancel_disconnect_and_close_release_camera_in_worker(window, qtbot, monkeypatch, finish):
    import threading
    from device_session import DeviceLease
    from focusfield.acquisition import check_cancel
    from pathprogrammer.ui import focusfield_panel
    class Camera:
        def __init__(self, recipe):
            self.cancel = threading.Event()
            self.opened = threading.Event()
            self.closed = False
            self.lease = DeviceLease('camera')
        def open(self):
            self.lease.acquire()
            self.opened.set()
        def fresh(self):
            if finish == 'disconnect':
                raise RuntimeError('Camera disconnected')
            self.cancel.wait(3)
            check_cancel(self.cancel)
        def capture(self):
            raise AssertionError('Capture must not run after STOP')
        def close(self):
            self.closed = True
            self.lease.release()
    instances = []
    def factory(recipe):
        camera = Camera(recipe)
        instances.append(camera)
        return camera
    monkeypatch.setattr(focusfield_panel, 'CameraAdapter', factory)
    p = window.focusfield
    p.simulate.setChecked(False)
    p.check_preview()
    camera = instances[0]
    qtbot.waitUntil(camera.opened.is_set, timeout=5000)
    if finish == 'window_close':
        assert not window.close()
    elif finish == 'stop':
        window.stop()  # global STOP, including the preview mode
    qtbot.waitUntil(lambda: p.thread is None, timeout=10000)
    assert camera.closed and not p.hardware_active
    assert ('FAILED' if finish == 'disconnect' else 'ABORTED') in p.status.toPlainText()
    with DeviceLease('camera'):
        pass
