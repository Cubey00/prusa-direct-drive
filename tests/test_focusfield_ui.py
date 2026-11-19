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
    assert window.focusfield.preset.currentText() == "Measurement"


def test_simulated_preview_runs_in_worker_without_devices(window, qtbot):
    p = window.focusfield
    p.check_preview()
    qtbot.waitUntil(lambda: p.thread is None, timeout=10000)
    assert "All four chart markers detected; 124 local edges" in p.status.toPlainText()
    assert not p.preview.pixmap().isNull()
    assert not p.hardware_active
    assert "1600 × 1280 px · RGB8" in p.status.toPlainText()
    assert "Review the sweep" in p.chart_check_note.text()
    p.fields["nominal_wd_mm"].setValue(20.1)
    assert "Check the simulated chart" in p.chart_check_note.text()


def test_preview_keeps_frame_edges_visible_when_panel_shrinks(window, qtbot):
    import numpy as np

    panel = window.focusfield
    window.centralWidget().setCurrentWidget(panel)
    window.show()
    panel.preview.setFixedSize(640, 480)
    frame = np.full((600, 800, 3), 25, dtype=np.uint8)
    frame[:, :80] = [255, 0, 0]
    frame[:, -80:] = [0, 255, 0]
    panel.on_event("frame", ({"bit_depth": 8}, frame))
    assert panel.preview.pixmap().width() == 640
    panel.preview.setFixedSize(340, 240)
    qtbot.waitUntil(lambda: panel.preview.pixmap().width() <= 340)
    visible = panel.preview.grab().toImage()
    ratio = visible.devicePixelRatio()          # grab() is in device pixels (2x on Retina)
    assert panel.preview.pixmap().size().toTuple() == (320, 240)
    assert visible.pixelColor(round(14 * ratio), round(120 * ratio)).getRgb()[:3] == (255, 0, 0)
    assert visible.pixelColor(round(326 * ratio), round(120 * ratio)).getRgb()[:3] == (0, 255, 0)


def test_loading_and_clearing_lens_profile_invalidates_preview(window, tmp_path, monkeypatch):
    from dataclasses import asdict
    import json
    from PySide6.QtWidgets import QFileDialog
    from focusfield.lens import LensCalibration

    lens = LensCalibration("TEST", (160, 120), ((120, 0, 80), (0, 120, 60), (0, 0, 1)),
                           (-0.2, 0.04, 0, 0))
    path = tmp_path / "lens.json"
    path.write_text(json.dumps(asdict(lens)))
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *args: (str(path), ""))
    panel = window.focusfield
    assert "checkerboard calibration test" in panel.lens_label.text()
    panel.last_preview = {"previous": True}
    panel.load_lens_calibration()
    assert panel.last_preview is None and "TEST" in panel.lens_label.text()
    assert panel.current_recipe().lens_calibration is None
    panel.simulate.setChecked(False)
    assert panel.current_recipe().lens_calibration == lens
    assert "checkerboard calibration test" not in panel.lens_label.text()
    panel.clear_lens.click()
    assert panel.current_recipe().lens_calibration is None
    assert "checkerboard calibration test" in panel.lens_label.text()


def test_combined_chart_is_selected_and_validated_lens_opens_scan_review(qtbot, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QFileDialog
    from focusfield.controller import RunResult
    from focusfield.lens import LensCalibration
    from pathprogrammer.ui.focusfield_panel import FocusFieldPanel

    panel = FocusFieldPanel(None)
    qtbot.addWidget(panel)
    chart = tmp_path / "targets.svg"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *args: (str(chart), ""))
    panel.generate_chart()
    assert chart.is_file()
    assert panel.chart.text() == str(tmp_path / "targets_layout.json")
    # 60 x 36 mm field (3.2 mm lens at 20 mm): squares = floor(10 * 36.2 / 12) / 10
    assert panel.checkerboard == {"inner_corners": [7, 5], "square_mm": 3.0}
    panel.simulate.setChecked(False)
    panel.format.setCurrentText("Mono16")
    assert not panel.start_btn.isEnabled()

    lens = LensCalibration("TEST", (160, 120), ((90, 0, 80), (0, 90, 60), (0, 0, 1)),
                           (-0.09, 0.04, 0, 0), model="fisheye")
    reviews = []
    monkeypatch.setattr(panel, "start_scan", lambda: reviews.append(panel.current_recipe()))
    details = {
        "lens_calibration": lens,
        "lens_path": str(tmp_path / "lens.json"),
        "preview_registration": {"full_field_visible": True},
        "pixel_format": "Mono16",
        "image_size_px": lens.image_size_px,
    }
    panel._start_task(lambda: RunResult("COMPLETED", "", "Validated", details=details))
    panel.thread.start()
    qtbot.waitUntil(lambda: bool(reviews))
    assert reviews[0].lens_calibration == lens
    assert reviews[0].chart_path == panel.chart.text()


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
    assert 'check chart' in messages[0].lower()
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
    p.manual_bounds.setChecked(True)
    p.fields['wd_start_mm'].setValue(20.2)
    p.fields['wd_end_mm'].setValue(20.4)
    recipe = p.current_recipe()
    assert recipe.nominal_wd_mm == 20 and recipe.separate_nominal_plane_required
    assert 'Separate measurement at requested 20 mm' in p.nominal_note.text()
    p.fields['wd_start_mm'].setValue(19.9)
    p.fields['wd_end_mm'].setValue(20.1)
    assert not p.current_recipe().separate_nominal_plane_required
    assert 'on the sweep grid' in p.nominal_note.text()


def test_quick_and_measurement_presets_derive_a_grid_and_count(window, monkeypatch):
    from dataclasses import replace
    from machine_settings import SETTINGS
    from pathprogrammer.ui import focusfield_panel

    panel = window.focusfield
    assert "126 frames" in panel.plan_note.text()
    panel.preset.setCurrentText("Quick check")
    assert "21 frames" in panel.plan_note.text()
    assert panel.current_recipe().expected_width_px == 1600
    panel.fields["nominal_wd_mm"].setValue(20.013)
    panel.half_span.setValue(0.24)
    recipe = panel.current_recipe()
    assert recipe.distances() == [19.813, 19.863, 19.913, 19.963, 20.013, 20.063, 20.113, 20.163, 20.213]
    panel.manual_bounds.setChecked(True)
    panel.fields["wd_start_mm"].setValue(19.825)
    panel.fields["wd_end_mm"].setValue(20.225)
    assert panel.current_recipe().separate_nominal_plane_required
    profile = replace(SETTINGS.focusfield, camera_pipeline_verified=True, verified_pixel_format="Mono16")
    monkeypatch.setattr(focusfield_panel, "load_settings", lambda: replace(SETTINGS, focusfield=profile))
    panel.simulate.setChecked(False)
    assert panel.preset.currentText() == "Quick check"
    panel.preset.setCurrentText("Measurement")
    assert panel.current_recipe().pixel_format == "Mono16" and not panel.format.isEnabled()
    assert "54 frames" in panel.plan_note.text()
    panel.fields["repetitions"].setValue(1)
    assert panel.reverse.isHidden() and "27 frames" in panel.plan_note.text()
    panel.dry_run.setChecked(True)
    assert "0 frames" in panel.plan_note.text() and panel.start_btn.text().startswith("Run dry run")
    assert panel.chart_group.isHidden() and panel.chart_check_group.isHidden()
    panel.dry_run.setChecked(False)
    assert not panel.chart_group.isHidden() and not panel.chart_check_group.isHidden()


def test_preview_and_report_survive_unrelated_settings(window, qtbot, tmp_path):
    from focusfield.controller import RunResult

    panel = window.focusfield
    report = tmp_path / "report.html"
    report.write_text("<p>Earlier scan report</p>")
    panel._start_task(lambda: RunResult("COMPLETED", str(tmp_path)))
    panel.thread.start()
    qtbot.waitUntil(lambda: panel.thread is None, timeout=10000)
    panel.check_preview()
    qtbot.waitUntil(lambda: panel.thread is None, timeout=10000)
    assert panel.report_path == report and panel.report_btn.isEnabled()
    previous = panel.last_preview
    panel.fields["frames_per_position"].setValue(4)
    assert panel.last_preview is previous
    panel.dry_run.setChecked(True)
    assert not panel.center_btn.isEnabled() and not panel.scout_btn.isEnabled()
    assert panel.checks["lens_locked_and_chart_checked"].isHidden()
    panel.dry_run.setChecked(False)
    assert panel.center_btn.isEnabled() and panel.scout_btn.isEnabled()
    panel.fields["exposure_us"].setValue(12000)
    assert panel.last_preview is None


def test_scout_draft_reuses_capture_settings_without_overwriting_manual_range(window):
    panel = window.focusfield
    original = panel.current_recipe()
    panel.scout_proposal = {"suggested_recipe": dict(wd_start_mm=19.975, wd_end_mm=20.125, step_mm=0.025)}
    panel.update_controls()
    draft = panel.current_recipe(use_scout=True)
    assert draft.distances() == [19.975, 20, 20.025, 20.05, 20.075, 20.1, 20.125]
    assert draft.frames_per_position == original.frames_per_position and draft.nominal_wd_mm == original.nominal_wd_mm
    assert panel.current_recipe() == original
    assert "14 positions (7 per sweep)" in panel.scout_note.text()


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


def test_visible_field_follows_lens_and_working_distance(window):
    from focusfield.lens import LensCalibration

    panel = window.focusfield
    assert panel.field_width.value() == pytest.approx(59.9, abs=0.1)
    assert panel.field_height.value() == pytest.approx(36.2, abs=0.1)
    assert not panel.field_width.isEnabled() and "3.2 mm lens" in panel.field_note.text()
    panel.fields["nominal_wd_mm"].setValue(10)
    assert panel.field_width.value() == pytest.approx(30.0, abs=0.1)
    panel.lens_focal.setValue(6.4)
    assert panel.field_width.value() < 15
    f = 3.2 / 0.00155
    panel.set_lens_calibration(LensCalibration("CAL", (4056, 3040), ((f, 0, 2028), (0, f, 1520), (0, 0, 1)),
                                               (0, 0, 0, 0), model="fisheye"))
    assert panel.field_width.value() == pytest.approx(30.0, abs=0.1)
    assert "CAL" in panel.field_note.text() and not panel.lens_focal.isEnabled()
    panel.manual_field.setChecked(True)
    panel.field_width.setValue(42)
    panel.fields["nominal_wd_mm"].setValue(20)
    assert panel.field_width.isEnabled() and panel.field_width.value() == 42


def test_xy_mapping_and_safety_checks_are_not_folded_away(window):
    from PySide6.QtWidgets import QGroupBox

    panel = window.focusfield
    assert panel.chart_group.isAncestorOf(panel.xy_btn)
    assert not panel.alignment_box.isAncestorOf(panel.xy_btn)
    assert isinstance(panel.check_group, QGroupBox) and panel.check_group.isHidden()   # simulation
    panel.simulate.setChecked(False)
    assert not panel.check_group.isHidden() and not panel.xy_controls.isHidden()
    assert all(panel.check_group.isAncestorOf(box) for box in panel.checks.values())
