"""Optical setup, preview, scan and offline review inside the existing application."""
from dataclasses import replace
import json
from pathlib import Path
import sys
import threading

import numpy as np
from PySide6.QtCore import QObject, QThread, Qt, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices, QImage, QPixmap
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout,
                               QGroupBox, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
                               QProgressBar, QPushButton, QScrollArea, QSpinBox,
                               QSplitter, QTextEdit, QVBoxLayout, QWidget)

from machine_settings import SETTINGS, load as load_settings
from focusfield.acquisition import CameraAdapter, check_cancel
from focusfield.chart import load_chart
from focusfield.config import DEFAULT_RECIPE, load_recipe, simulation_fixture
from focusfield.calibration import XYCalibration, save_reference
from focusfield.lens import LensCalibration
from focusfield.controller import OperatorChecks, RunResult, ScanController
from focusfield.hardware import SimCamera, SimStage, ZStage
from focusfield.reports import analyze_run
from focusfield.setup import CenteringLimits, SetupController, simulation_xy_calibration
from .printer_box import link_from_settings
from .optical_setup_dialogs import measured_wd_dialog, scout_recipe_dialog


class OpticalWorker(QObject):
    finished = Signal(object)
    event = Signal(str, object)

    def __init__(self, work):
        super().__init__()
        self.work = work

    @Slot()
    def run(self):
        try:
            result = self.work()
        except InterruptedError as exc:
            result = RunResult("ABORTED", "", str(exc))
        except Exception as exc:
            result = RunResult("FAILED", "", f"{type(exc).__name__}: {exc}")
        self.finished.emit(result)


class FocusFieldPanel(QWidget):
    hardwareActivityChanged = Signal(bool)

    def __init__(self, main_window):
        super().__init__()
        self.main = main_window
        self.thread = self.worker = self.controller = None
        self.hardware_active = False
        self._borrowed_camera = False
        self.run_path = None
        self._analysis_cancel = None
        self._task_cancel = None
        self.last_preview = None
        self.xy_calibration = None
        self._sim_stage = None
        self.scout_proposal = None
        self.setup_history = []
        self.recipe = load_recipe()
        outer = QVBoxLayout(self)
        notice = QLabel("Fixed lens focus · variable target distance · apparent focus-field variation. "
                        "Simulation and RGB8 results are provisional. Raw pipelines require physical validation.")
        notice.setWordWrap(True)
        outer.addWidget(notice)
        split = QSplitter(Qt.Horizontal)
        setup = QWidget()
        form = QFormLayout(setup)
        load = QPushButton("Load scan recipe…")
        load.clicked.connect(self.load_recipe_dialog)
        form.addRow(load)
        self.chart = QLineEdit(self.recipe.chart_path)
        choose = QPushButton("Select chart layout…")
        choose.clicked.connect(self.choose_chart)
        form.addRow(choose, self.chart)
        self.fields = {}
        for key, label, lo, hi, decimals in (
            ("wd_start_mm", "Start WD (mm)", 0.1, 500, 3), ("wd_end_mm", "End WD (mm)", 0.1, 500, 3),
            ("step_mm", "Step (mm)", 0.001, 10, 3), ("nominal_wd_mm", "Nominal plane (mm)", 0.1, 500, 3),
            ("settle_s", "Settle (s)", 0, 60, 2), ("exposure_us", "Exposure (µs)", 1, 10000000, 0),
            ("gain", "Gain", 0, 100, 2), ("fixed_frequency_cpp", "Frequency (cycles/sensor pixel)", 0.001, 0.249, 3)):
            widget = QDoubleSpinBox()
            widget.setRange(lo, hi)
            widget.setDecimals(decimals)
            widget.setValue(getattr(self.recipe, key))
            self.fields[key] = widget
            form.addRow(label, widget)
        for key, label in (("frames_per_position", "Frames / position"), ("repetitions", "Sweeps")):
            widget = QSpinBox()
            widget.setRange(1, 100)
            widget.setValue(getattr(self.recipe, key))
            self.fields[key] = widget
            form.addRow(label, widget)
        self.reverse = QCheckBox("Alternate reverse sweeps (hysteresis check)")
        form.addRow(self.reverse)
        self.nominal_note = QLabel()
        self.nominal_note.setWordWrap(True)
        form.addRow(self.nominal_note)
        self.format = QComboBox()
        self.format.addItems((["RGB8", "Mono8", "Mono16", "BayerRG8", "BayerRG10p", "BayerRG12p"] if sys.platform == 'darwin'
                              else ["RGB8", "Mono8", "Mono10", "Mono12", "Mono16", "BayerRG8", "BayerRG10", "BayerRG12"]))
        form.addRow("Capture pixel format", self.format)
        lens_buttons = QHBoxLayout()
        load_lens = QPushButton("Load lens calibration…")
        load_lens.clicked.connect(self.load_lens_calibration)
        self.clear_lens = QPushButton("Clear lens calibration")
        self.clear_lens.clicked.connect(lambda: self.set_lens_calibration(None))
        lens_buttons.addWidget(load_lens)
        lens_buttons.addWidget(self.clear_lens)
        form.addRow(lens_buttons)
        self.lens_label = QLabel()
        self.lens_label.setWordWrap(True)
        form.addRow(self.lens_label)
        self.simulate = QCheckBox("Simulate stage and camera")
        self.simulate.setChecked(True)
        self.dry_run = QCheckBox("Motion only (collision-free dry run; no images)")
        form.addRow(self.simulate)
        form.addRow(self.dry_run)
        checks = QGroupBox("Physical setup checks (required for real hardware)")
        check_layout = QVBoxLayout(checks)
        self.checks = {}
        for key, text in (("fixture_and_clearance", "Measured fixture and complete travel clearance verified"),
                          ("increasing_z_lowers_bed", "Verified: increasing Z lowers bed / increases WD"),
                          ("firmware_and_stop_verified", "Mounted firmware and global STOP tested"),
                          ("lens_locked_and_chart_checked", "Image operations: lens locked, target scale and chart visibility checked"),
                          ("collision_free_dry_run", "Complete collision-free dry run already verified"),
                          ("manual_printer_idle", "If PrusaLink is unavailable: I verified printer idle manually")):
            checkbox = QCheckBox(text)
            self.checks[key] = checkbox
            check_layout.addWidget(checkbox)
        form.addRow(checks)
        form.addRow(QLabel("No automatic homing, touch, or recovery motion. Configure [focusfield] in machine settings."))
        optical_setup = QGroupBox("Separate optical setup operations")
        setup_form = QFormLayout(optical_setup)
        self.wd_btn = QPushButton("Record measured WD reference… (no motion)")
        self.wd_btn.clicked.connect(self.record_wd_reference)
        self.xy_btn = QPushButton("Load measured optical XY mapping…")
        self.xy_btn.clicked.connect(self.load_xy_calibration)
        self.xy_label = QLabel("No optical XY mapping selected")
        self.center_btn = QPushButton("Center chart… (bounded XY motion)")
        self.center_btn.setEnabled(False)
        self.center_btn.clicked.connect(lambda: self.start_setup('center'))
        self.scout_btn = QPushButton("Scout focus interval… (bounded Z motion)")
        self.scout_btn.setEnabled(False)
        self.scout_btn.clicked.connect(lambda: self.start_setup('scout'))
        for widget in (self.wd_btn, self.xy_btn, self.xy_label, self.center_btn, self.scout_btn):
            setup_form.addRow(widget)
        form.addRow(optical_setup)
        gen = QGroupBox("Generate optical test chart")
        gen_form = QFormLayout(gen)
        self.field_width, self.field_height = QDoubleSpinBox(), QDoubleSpinBox()
        for widget, value in ((self.field_width, 60), (self.field_height, 45)):
            widget.setRange(20, 300)
            widget.setValue(value)
        gen_form.addRow("Measured visible field width (mm)", self.field_width)
        gen_form.addRow("Measured visible field height (mm)", self.field_height)
        generate = QPushButton("Save SVG + physical layout…")
        generate.clicked.connect(self.generate_chart)
        gen_form.addRow(generate)
        form.addRow(gen)
        scroll = QScrollArea()
        scroll.setWidget(setup)
        scroll.setWidgetResizable(True)
        split.addWidget(scroll)
        right = QWidget()
        layout = QVBoxLayout(right)
        self.preview = QLabel("Captured frame preview\nSimulation is enabled by default")
        self.preview.setAlignment(Qt.AlignCenter)
        self.preview.setMinimumSize(320, 240)
        layout.addWidget(self.preview, 1)
        self.status = QTextEdit()
        self.status.setReadOnly(True)
        layout.addWidget(self.status)
        self.progress = QProgressBar()
        layout.addWidget(self.progress)
        buttons = QHBoxLayout()
        self.start_btn = QPushButton("Start scan")
        self.preview_btn = QPushButton("Check preview & chart")
        self.stop_btn = QPushButton("STOP")
        self.stop_btn.setStyleSheet("background:#c32626;color:white;font-weight:bold")
        self.stop_btn.setEnabled(False)
        self.reanalyze_btn = QPushButton("Analyze saved run…")
        self.report_btn = QPushButton("Open report")
        self.report_btn.setEnabled(False)
        for button in (self.preview_btn, self.start_btn, self.stop_btn, self.reanalyze_btn, self.report_btn):
            buttons.addWidget(button)
        self.start_btn.clicked.connect(self.start_scan)
        self.preview_btn.clicked.connect(self.check_preview)
        self.stop_btn.clicked.connect(self.stop)
        self.reanalyze_btn.clicked.connect(self.reanalyze)
        self.report_btn.clicked.connect(self.open_report)
        layout.addLayout(buttons)
        split.addWidget(right)
        outer.addWidget(split, 1)
        self.setup = setup
        self.update_lens_label()
        self.simulate.toggled.connect(self.invalidate_preview)
        self.chart.textChanged.connect(self.invalidate_preview)
        self.format.currentTextChanged.connect(self.invalidate_preview)
        for widget in self.fields.values():
            widget.valueChanged.connect(self.invalidate_preview)
        self.update_nominal_note()

    def invalidate_preview(self, *args):
        self.last_preview = None
        self.scout_proposal = None
        self.center_btn.setEnabled(False)
        self.scout_btn.setEnabled(False)
        self.update_nominal_note()

    def update_nominal_note(self):
        lo, hi, step, nominal = (self.fields[k].value() for k in
                                ('wd_start_mm', 'wd_end_mm', 'step_mm', 'nominal_wd_mm'))
        if not lo <= nominal <= hi:
            message = (f"Separate measurement at requested {nominal:g} mm WD is required: "
                       "this focus sweep does not include that plane. No extra move is scheduled.")
        elif abs((nominal-lo)/step-round((nominal-lo)/step)) > 1e-7:
            message = (f"Requested {nominal:g} mm WD is not on the sweep grid. Align bounds/step or "
                       "take a separate nominal-plane measurement; interpolation is not a measurement.")
        else:
            message = f"Requested nominal {nominal:g} mm WD is on the sweep grid; absolute WD retains calibration uncertainty."
        self.nominal_note.setText(message)

    def current_recipe(self):
        values = {key: w.value() for key, w in self.fields.items()}
        return replace(self.recipe, **values,
                       chart_path=self.chart.text(), pixel_format=self.format.currentText(),
                       lens_calibration=None if self.simulate.isChecked() else self.recipe.lens_calibration,
                       separate_nominal_plane_required=not values['wd_start_mm'] <= values['nominal_wd_mm'] <= values['wd_end_mm'],
                       reverse_alternate=self.reverse.isChecked())

    def update_lens_label(self):
        lens = self.recipe.lens_calibration
        self.clear_lens.setEnabled(lens is not None)
        if lens is None:
            self.lens_label.setText("No lens correction")
        else:
            width, height = lens.image_size_px
            self.lens_label.setText(f"Lens calibration for {lens.camera_serial} · {width} × {height} px")

    def set_lens_calibration(self, lens):
        self.recipe = replace(self.recipe, lens_calibration=lens)
        self.update_lens_label()
        self.invalidate_preview()

    def load_lens_calibration(self):
        path, _ = QFileDialog.getOpenFileName(self, "Measured lens calibration", "", "Calibration (*.json)")
        if path:
            try:
                self.set_lens_calibration(LensCalibration.load(path))
            except (OSError, TypeError, ValueError) as exc:
                QMessageBox.warning(self, "Lens calibration", str(exc))

    def devices(self, recipe, fixture, *, xy=None, motion_only=False):
        if self.simulate.isChecked():
            if self._sim_stage is None:
                self._sim_stage = SimStage(fixture)
            self._sim_stage.stopped = False
            if motion_only:
                return self._sim_stage, None
            camera = SimCamera(self._sim_stage, recipe, load_chart(recipe.chart_path))
            camera.offset_px = np.array([12., -8.])
            return self._sim_stage, camera
        settings = load_settings()
        return (ZStage(fixture, settings.printer.z_max, xy, settings.printer.travel),
                None if motion_only else CameraAdapter(recipe))

    def record_wd_reference(self):
        try:
            settings = load_settings()
            record = measured_wd_dialog(self, settings.focusfield)
            if record is None:
                return
            path, _ = QFileDialog.getSaveFileName(self, "Save immutable measured WD reference", "wd_reference.json", "JSON (*.json)")
            if not path:
                return
            save_reference(record, path, settings.path)
            self.invalidate_preview()
            self.status.append(f"Measured WD reference saved to {path}. Physical motion remains disabled; verify all other calibration and safety checks.")
        except Exception as exc:
            QMessageBox.warning(self, "WD reference", str(exc))

    def load_xy_calibration(self):
        path, _ = QFileDialog.getOpenFileName(self, "Measured optical XY mapping", "", "JSON (*.json)")
        if path:
            try:
                c = XYCalibration.load(path)
                c.validate(load_settings().focusfield)
                self.xy_calibration = c
                self.xy_label.setText(f"XY calibration: {c.calibration_id}")
                self.invalidate_preview()
            except Exception as exc:
                QMessageBox.warning(self, "Optical XY mapping", str(exc))

    def start_setup(self, operation):
        if self.thread is not None:
            return
        if self.dry_run.isChecked():
            QMessageBox.warning(self, "Setup needs images", "Centering/scouting require images. Turn off Motion only; use Start scan for a supervised motion-only Z preflight.")
            return
        simulated = self.simulate.isChecked()
        if not self.last_preview or self.last_preview['simulated'] != simulated:
            QMessageBox.warning(self, "Preview required", "Run Check preview & chart successfully before setup motion.")
            return
        if not simulated and (self.main.thread is not None or self.main.server.is_running() or self.main.printer._busy):
            QMessageBox.warning(self, "Printer in use", "Finish other printer/calibrator operations first.")
            return
        try:
            recipe = self.current_recipe()
            settings = load_settings()
            fixture = simulation_fixture() if simulated else settings.focusfield
            checks = OperatorChecks(**{k: v.isChecked() for k, v in self.checks.items()})
            if not simulated:
                fixture.validate_physical()
                checks.validate()
            xy = self.xy_calibration
            if simulated:
                xy = simulation_xy_calibration(fixture)
                chart = load_chart(recipe.chart_path)
                width, height = int(chart['page_width']*16), int(chart['page_height']*16)
                xy = replace(xy, optical_center_px=(width/2, height/2), image_size_px=(width, height))
            limits = CenteringLimits()
            scout = None
            if operation == 'center':
                if xy is None:
                    raise ValueError("Load a measured optical XY mapping; ArUco registration alone cannot authorize motion")
                xy.validate(fixture)
                description = (f"Center using mapping {xy.calibration_id}: at most {limits.max_iterations} XY steps, "
                    f"{limits.max_step_mm:g} mm per step, {limits.max_total_mm:g} mm total, "
                    f"{limits.speed_mm_s:g} mm/s. No Z adjustment or homing. Approve this supervised operation?")
            else:
                scout = scout_recipe_dialog(self, fixture)
                if scout is None:
                    return
                description = (f"Scout Z {scout.z_start_mm:g}–{scout.z_end_mm:g} mm in {scout.step_mm:g} mm steps; "
                    f"may extend once within verified Z {fixture.safe_z_min_mm:g}–{fixture.safe_z_max_mm:g} mm; "
                    f"maximum {scout.max_positions} positions. Fine scan requires separate approval. Approve this supervised scout?")
            if not simulated and QMessageBox.question(self, "Approve bounded optical setup motion", description,
                    QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
                return
            stage, camera = self.devices(recipe, fixture, xy=xy)
            link = link_from_settings(self.main.settings) if not simulated and self.main.printer.configured() else None
            kwargs = dict(operation=operation, operator_confirmed=not simulated, xy_calibration=xy,
                          centering_limits=limits)
            if scout:
                kwargs['scout_recipe'] = scout
            self.controller = SetupController(recipe, settings, fixture, stage, camera,
                Path(self.main.runs_folder()) / 'focusfield', checks=checks,
                printer_state=(lambda: link.status().state) if link else (lambda: None), **kwargs)
            self._start_task(self.controller.run, physical=not simulated, borrow_camera=not simulated)
            self.controller.on_event = lambda kind, *args: self.worker.event.emit(kind, args)
            self.invalidate_preview()
            self.thread.start()
        except Exception as exc:
            QMessageBox.warning(self, "Cannot start optical setup", str(exc))

    def choose_chart(self):
        path, _ = QFileDialog.getOpenFileName(self, "Chart physical layout", self.chart.text(), "Layout (*.json)")
        if path:
            try:
                load_chart(path)
                self.chart.setText(path)
            except Exception as exc:
                QMessageBox.warning(self, "Chart", str(exc))

    def load_recipe_dialog(self):
        path, _ = QFileDialog.getOpenFileName(self, "Scan recipe", str(DEFAULT_RECIPE), "Recipe (*.toml)")
        if path:
            try:
                recipe = load_recipe(path)
                if recipe.pixel_format not in [self.format.itemText(i) for i in range(self.format.count())]:
                    raise ValueError("Recipe pixel format is not supported by this camera backend; choose an explicitly supported native format")
                self.recipe = recipe
                self.update_lens_label()
                self.invalidate_preview()
                for key, widget in self.fields.items():
                    widget.setValue(getattr(recipe, key))
                self.reverse.setChecked(recipe.reverse_alternate)
                self.format.setCurrentText(recipe.pixel_format)
                self.chart.setText(recipe.chart_path)
            except Exception as exc:
                QMessageBox.warning(self, "Recipe", str(exc))

    def generate_chart(self):
        path, _ = QFileDialog.getSaveFileName(self, "Save optical chart", "focus_field_chart.svg", "SVG (*.svg)")
        if not path:
            return
        try:
            from focusfield.generate_chart import build_parser, generate_chart
            w, h = self.field_width.value(), self.field_height.value()
            args = build_parser().parse_args(["--output", path, "--field-width", str(w), "--field-height", str(h),
                                              "--page-width", str(max(100, w+40)), "--page-height", str(max(80, h+35))])
            generate_chart(args)
            self.chart.setText(str(Path(path).with_name(Path(path).stem + "_layout.json")))
            self.status.append(f"Chart saved: {path}. Print at 100%; verify the 10 mm scale and full marker visibility.")
        except Exception as exc:
            QMessageBox.warning(self, "Chart generation", str(exc))

    def start_scan(self):
        if self.thread is not None:
            return
        simulated = self.simulate.isChecked()
        if not simulated and (self.main.thread is not None or self.main.server.is_running() or self.main.printer._busy):
            QMessageBox.warning(self, "Printer in use", "Close the calibrator and finish other printer operations first.")
            return
        try:
            recipe = self.current_recipe()
            chart = load_chart(recipe.chart_path)
            settings = load_settings()
            fixture = simulation_fixture() if simulated else settings.focusfield
            recipe.validate_fixture(fixture, settings.printer.z_max, simulation=simulated,
                                    dry_run=self.dry_run.isChecked())
            checks = OperatorChecks(**{k: v.isChecked() for k, v in self.checks.items()})
            if not simulated:
                checks.validate(self.dry_run.isChecked())
                if QMessageBox.question(self, "Approve optical scan motion",
                        f"Run supervised {'motion-only dry run' if self.dry_run.isChecked() else 'image scan'} at calibrated WD "
                        f"{recipe.wd_start_mm:g}–{recipe.wd_end_mm:g} mm, step {recipe.step_mm:g} mm? "
                        f"Absolute WD uncertainty: {fixture.wd_uncertainty_mm:g} mm. "
                        f"{self.nominal_note.text()} "
                        "This is a separate operation from scouting and centering.",
                        QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
                    return
            stage, camera = self.devices(recipe, fixture, motion_only=self.dry_run.isChecked())
            # Construct PrusaLink from QSettings on the GUI thread; only network I/O runs in worker.
            link = link_from_settings(self.main.settings) if not simulated and self.main.printer.configured() else None
            self.controller = ScanController(recipe, settings, fixture, stage, camera,
                Path(self.main.runs_folder()) / "focusfield", checks=checks, dry_run=self.dry_run.isChecked(),
                operator_confirmed=not simulated,
                setup_provenance=dict(history=self.setup_history,
                                      scout_proposal=({**self.scout_proposal, 'approved': not simulated} if self.scout_proposal else None),
                                      operator_approved_fine_scan=bool(self.scout_proposal and not simulated)),
                printer_state=(lambda: link.status().state) if link else (lambda: None))
            self._start_task(self.controller.run, physical=not simulated,
                             borrow_camera=not simulated and not self.dry_run.isChecked())
            self.controller.on_event = lambda kind, *args: self.worker.event.emit(kind, args)
            self.thread.start()
        except Exception as exc:
            QMessageBox.warning(self, "Cannot start optical scan", str(exc))

    def check_preview(self):
        if self.thread:
            return
        simulated = self.simulate.isChecked()
        if not simulated and (self.main.thread is not None or self.main.server.is_running() or self.main.printer._busy):
            QMessageBox.warning(self, "Devices in use", "Finish other printer/calibrator operations first.")
            return
        try:
            recipe = replace(self.recipe, chart_path=self.chart.text(), pixel_format=self.format.currentText(),
                             lens_calibration=None if simulated else self.recipe.lens_calibration,
                             exposure_us=self.fields["exposure_us"].value(), gain=self.fields["gain"].value())
            chart = load_chart(recipe.chart_path)
            if simulated:
                stage, camera = self.devices(recipe, simulation_fixture())
            else:
                camera = CameraAdapter(recipe)
            cancel = threading.Event()
            if hasattr(camera, 'cancel'):
                camera.cancel = cancel
            calibrated_center = self.xy_calibration.optical_center_px if self.xy_calibration and not simulated else None
            def preview():
                from focusfield.chart import register_capture, edge_rois
                try:
                    check_cancel(cancel)
                    camera.open()
                    check_cancel(cancel)
                    camera.fresh()
                    check_cancel(cancel)
                    capture = camera.capture().validate()
                    check_cancel(cancel)
                    self.worker.event.emit("frame", (capture.metadata, capture.pixels))
                    try:
                        registration, gray, _ = register_capture(capture, chart, recipe, optical_center_px=calibrated_center)
                    except ValueError as exc:
                        height, width = capture.pixels.shape[:2]
                        serial = capture.metadata["camera_serial"]
                        raise ValueError(f"Camera {serial} · {width} × {height} px. {exc}") from exc
                    h, error = registration.pop('homography'), registration['reprojection_rms_px']
                    project_chart = registration.pop("project_chart")
                    local_count = 0
                    for square in chart["slanted_squares"]:
                        check_cancel(cancel)
                        try:
                            local_count += len(edge_rois(gray, h, square, project_chart=project_chart))
                        except ValueError:
                            pass
                    message = (f"All four chart markers detected; {local_count} local edges. "
                               f"Marker reprojection RMS {error:.2f} sensor pixels. "
                               f"Lens correction {'on' if registration['lens_corrected'] else 'off'}. "
                               f"Camera {capture.metadata['camera_serial']}; {capture.metadata['pixel_format']}. "
                               f"Chart-center error {registration['center_error_px']} sensor pixels "
                               f"relative to {'calibrated optical center' if calibrated_center else 'image midpoint (diagnostic only)'}. "
                               "Registration does not authorize XY motion or establish absolute WD.")
                    check_cancel(cancel)
                    return RunResult("COMPLETED", "", message, details=dict(preview_registration=registration,
                        simulated=simulated, camera_serial=capture.metadata['camera_serial']))
                except InterruptedError as exc:
                    return RunResult("ABORTED", "", str(exc))
                finally:
                    camera.close()
            self.invalidate_preview()
            self._start_task(preview, physical=not simulated, borrow_camera=not simulated, task_cancel=cancel)
            self.thread.start()
        except Exception as exc:
            QMessageBox.warning(self, "Preview", str(exc))

    def _start_task(self, work, *, physical=False, borrow_camera=False, task_cancel=None):
        if borrow_camera:
            self.main.photo.release()
        self._borrowed_camera = borrow_camera
        self._task_cancel = task_cancel or threading.Event()
        self.thread = QThread(self)
        self.worker = OpticalWorker(work)
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        self.worker.event.connect(self.on_event)
        self.worker.finished.connect(self.finished)
        self.worker.finished.connect(self.thread.quit, Qt.DirectConnection)
        self.worker.finished.connect(self.worker.deleteLater)
        self.hardware_active = physical
        if physical:
            self.hardwareActivityChanged.emit(True)
        self.setup.setEnabled(False)
        self.start_btn.setEnabled(False)
        self.preview_btn.setEnabled(False)
        self.reanalyze_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.report_btn.setEnabled(False)
        self.center_btn.setEnabled(False)
        self.scout_btn.setEnabled(False)
        self.status.clear()

    @Slot(str, object)
    def on_event(self, kind, args):
        if kind == "state":
            self.status.append(f"{args[0]} {args[1]}")
        elif kind == "progress":
            self.progress.setRange(0, args[1])
            self.progress.setValue(args[0])
        elif kind == "frame":
            record, array = args
            if array.ndim == 2:
                array = np.repeat(array[..., None], 3, axis=2)
            array = np.ascontiguousarray(array / (2**record["bit_depth"]-1) * 255, dtype=np.uint8)
            image = QImage(array.data, array.shape[1], array.shape[0], array.strides[0], QImage.Format_RGB888).copy()
            self.preview.setPixmap(QPixmap.fromImage(image).scaled(640, 480, Qt.KeepAspectRatio, Qt.SmoothTransformation))

    @Slot(object)
    def finished(self, result):
        self.thread.wait()
        self.thread.deleteLater()
        self.thread = self.worker = self.controller = None
        self._analysis_cancel = None
        self._task_cancel = None
        if self.hardware_active:
            self.hardware_active = False
            self.hardwareActivityChanged.emit(False)
        if self._borrowed_camera:
            self._borrowed_camera = False
            self.main.photo.resume()
        self.setup.setEnabled(True)
        self.start_btn.setEnabled(True)
        self.preview_btn.setEnabled(True)
        self.reanalyze_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.run_path = Path(result.path) if result.path else None
        self.report_btn.setEnabled(bool(self.run_path and (self.run_path / "report.html").exists()))
        self.status.append(f"{result.state}: {result.message}\nRun: {result.path}")
        if result.state == 'COMPLETED' and 'preview_registration' in result.details:
            self.last_preview = result.details
        usable_preview = bool(self.last_preview and self.last_preview['preview_registration']['full_field_visible'])
        self.center_btn.setEnabled(usable_preview)
        self.scout_btn.setEnabled(usable_preview)
        if result.path and (Path(result.path) / 'setup_result.json').is_file() and result.state == 'COMPLETED':
            manifest = json.loads((Path(result.path) / 'manifest.json').read_text(encoding='utf-8'))
            details = manifest['setup_result']
            self.setup_history.append(dict(path=result.path, operation=manifest['operation'], result=details))
            if manifest['operation'] == 'scout':
                self.status.append(details['reasoning'] + f". Absolute WD uncertainty {details['absolute_wd_uncertainty_mm']:g} mm.")
                nominal_message = details.get('nominal_plane_message', '')
                if nominal_message:
                    self.status.append(nominal_message)
                if QMessageBox.question(self, 'Proposed fine scan — no automatic motion',
                        f"{details['reasoning']}. Proposed Z {details['z_start_mm']:g}–{details['z_end_mm']:g} mm. "
                        f"{nominal_message} "
                        "Load the proposed recipe into the editor? Start scan requires a separate action and approval.",
                        QMessageBox.Yes | QMessageBox.No, QMessageBox.No) == QMessageBox.Yes:
                    from focusfield.config import Recipe
                    profile = self.recipe.lens_calibration
                    self.recipe = Recipe(**details['suggested_recipe'])
                    if self.simulate.isChecked():
                        self.recipe = replace(self.recipe, lens_calibration=profile)
                    self.update_lens_label()
                    for key, widget in self.fields.items():
                        widget.setValue(getattr(self.recipe, key))
                    self.scout_proposal = dict(path=result.path, **details)
        elif result.acquisition_complete or result.analysis_complete:
            self.status.append(f"Acquisition complete: {result.acquisition_complete}; analysis complete: "
                               f"{result.analysis_complete}; measurement valid: {result.measurement_valid}")

    def stop(self):
        if self.controller:
            self.controller.stop()
        if self._analysis_cancel:
            self._analysis_cancel.set()
        if self._task_cancel:
            self._task_cancel.set()
        self.status.append("STOP requested; waiting for any current camera call to return before cleanup (frame timeout 5 s)")

    def reanalyze(self):
        path = QFileDialog.getExistingDirectory(self, "Select optical run folder", str(Path(self.main.runs_folder()) / "focusfield"))
        if not path:
            return
        import json
        import threading
        try:
            manifest = json.loads((Path(path) / "manifest.json").read_text(encoding="utf-8"))
            if manifest.get('operation') in {'center', 'scout'}:
                raise ValueError("This is a setup log. Inspect setup_result.json and manifest.json; acquire a fine scan for field analysis.")
            partial = manifest["state"] != "COMPLETED"
            if partial and QMessageBox.question(self, "Partial run", "Analyze this incomplete run with explicit partial-data flags?") != QMessageBox.Yes:
                return
            self._analysis_cancel = threading.Event()
            cancel = self._analysis_cancel
            def analyze():
                summary = analyze_run(path, allow_partial=partial, should_abort=cancel.is_set,
                                      on_progress=lambda *args: self.worker.event.emit("progress", args))
                return RunResult("COMPLETED", path, f"{summary['valid_focus_fits']} valid focus fits",
                                 summary['acquisition_complete'], summary['analysis_complete'], summary['measurement_valid'])
            self._start_task(analyze)
            self.thread.start()
        except Exception as exc:
            QMessageBox.warning(self, "Analysis", str(exc))

    def open_report(self):
        if self.run_path:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str((self.run_path / "report.html").resolve())))

    def shutdown(self):
        if self.thread:
            self.stop()
            # Keep the event loop alive until finished() owns thread cleanup.
            return False
        return True
