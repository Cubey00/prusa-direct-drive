from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QMessageBox, QTabWidget
import pytest

from pathprogrammer.ui.main_window import MainWindow


@pytest.fixture
def window(qtbot, tmp_path):
    w = MainWindow(QSettings(str(tmp_path / "settings.ini"), QSettings.IniFormat))
    w.settings.setValue("runs_folder", str(tmp_path))
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


def test_nominal_plane_warning_is_persistent_and_preserves_user_distance(window):
    p = window.focusfield
    p.fields['wd_start_mm'].setValue(20.2)
    p.fields['wd_end_mm'].setValue(20.4)
    recipe = p.current_recipe()
    assert recipe.nominal_wd_mm == 20 and recipe.separate_nominal_plane_required
    assert 'Separate measurement at requested 20 mm' in p.nominal_note.text()
    p.fields['wd_start_mm'].setValue(19.9)
    p.fields['wd_end_mm'].setValue(20.1)
    assert not p.current_recipe().separate_nominal_plane_required
    assert 'on the sweep grid' in p.nominal_note.text()


@pytest.mark.parametrize('approved', [False, True])
def test_physical_motion_only_workflow_never_constructs_a_camera(window, qtbot, monkeypatch,
                                                               verified_optical_fixture, approved, tmp_path):
    from dataclasses import replace
    from focusfield.hardware import SimStage
    from machine_settings import SETTINGS
    from pathprogrammer.ui import focusfield_panel
    f = replace(verified_optical_fixture, camera_pipeline_verified=False, frame_timing_verified=False)
    settings = replace(SETTINGS, focusfield=f)
    stage = SimStage(f)
    stage.simulated = False
    monkeypatch.setattr(focusfield_panel, 'load_settings', lambda: settings)
    monkeypatch.setattr(focusfield_panel, 'ZStage', lambda *args: stage)
    def forbidden_camera(*args):
        raise AssertionError('A motion-only dry run must not construct a camera')
    monkeypatch.setattr(focusfield_panel, 'CameraAdapter', forbidden_camera)
    monkeypatch.setattr(window.photo, 'release', forbidden_camera)
    monkeypatch.setattr(window.photo, 'resume', forbidden_camera)
    monkeypatch.setattr(QMessageBox, 'question', lambda *args: QMessageBox.Yes if approved else QMessageBox.No)
    p = window.focusfield
    p.simulate.setChecked(False)
    p.dry_run.setChecked(True)
    p.fields['settle_s'].setValue(0)
    for name in ('fixture_and_clearance', 'increasing_z_lowers_bed', 'firmware_and_stop_verified', 'manual_printer_idle'):
        p.checks[name].setChecked(True)
    p.start_scan()
    if approved:
        qtbot.waitUntil(lambda: p.thread is None, timeout=10000)
        assert 'Motion-only dry run completed' in p.status.toPlainText()
        assert p.run_path.parent == tmp_path / 'focusfield'
        assert len([call for call in stage.calls if call[0] == 'move']) == 42
    else:
        assert p.thread is None and not any(call[0] == 'move' for call in stage.calls)


@pytest.mark.parametrize('finish', ['stop', 'window_close', 'disconnect'])
def test_physical_preview_cancel_disconnect_and_close_release_camera_in_worker(window, qtbot, monkeypatch, finish):
    import threading
    from device_session import DeviceLease
    from focusfield.acquisition import check_cancel
    from pathprogrammer.ui import focusfield_panel
    handoff = []
    monkeypatch.setattr(window.photo, 'release', lambda: handoff.append('released'))
    monkeypatch.setattr(window.photo, 'resume', lambda: handoff.append('resumed'))

    class Camera:
        def __init__(self, recipe):
            self.cancel = threading.Event()
            self.opened = threading.Event()
            self.closed = False
            self.lease = DeviceLease('camera')
        def open(self):
            assert handoff == ['released']
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
    assert handoff == ['released', 'resumed']
    assert ('FAILED' if finish == 'disconnect' else 'ABORTED') in p.status.toPlainText()
    with DeviceLease('camera'):
        pass
