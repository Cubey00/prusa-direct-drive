"""Optical setup, preview, scan and offline review inside the existing application."""
from dataclasses import replace
import json
from pathlib import Path
import sys
import threading

import numpy as np
from PySide6.QtCore import QObject, QSignalBlocker, QThread, Qt, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout,
                               QGroupBox, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
                               QProgressBar, QPushButton, QScrollArea, QSpinBox,
                               QSplitter, QTextEdit, QVBoxLayout, QWidget)

from machine_settings import SETTINGS, load as load_settings
from focusfield.acquisition import FORMATS, CameraAdapter, check_cancel
from focusfield.chart import load_chart
from focusfield.config import (DEFAULT_RECIPE, centered_scan_bounds, load_recipe, nominal_on_grid,
                               simulation_fixture)
from focusfield.data import storage_required
from focusfield.calibration import XYCalibration, save_reference
from focusfield.lens import LensCalibration
from focusfield.controller import OperatorChecks, RunResult, ScanController
from focusfield.hardware import SimCamera, SimStage, ZStage
from focusfield.reports import analyze_run
from focusfield.setup import CenteringLimits, SetupController, simulation_xy_calibration
from .collapsible import CollapsibleBox
from .photo_panel import LiveView, to_qimage
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
        self.run_path = self.report_path = None
        self._analysis_cancel = None
        self._task_cancel = None
        self.last_preview = None
        self.xy_calibration = None
        self._sim_stage = None
        self.scout_proposal = None
        self.setup_history = []
        self.recipe = load_recipe()
        self.fixture_profile = SETTINGS.focusfield
        self._applying_recipe = False
        outer = QVBoxLayout(self)
        notice = QLabel("Fixed lens focus · variable target distance · apparent focus-field variation. "
                        "Simulation and RGB8 results are provisional. Raw pipelines require physical validation.")
        notice.setWordWrap(True)
        outer.addWidget(notice)
        split = QSplitter(Qt.Horizontal)
        setup = QWidget()
        form = QFormLayout(setup)
        self.simulate = QCheckBox("Simulation")
        self.simulate.setChecked(True)
        self.dry_run = QCheckBox("Motion-only dry run")
        self.preset = QComboBox()
        self.preset.addItems(["Quick check", "Measurement"])
        form.addRow(self.simulate, self.dry_run)
        form.addRow("Scan preset", self.preset)

        setup_options = QWidget()
        setup_form = QFormLayout(setup_options)
        load = QPushButton("Load scan recipe…")
        load.clicked.connect(self.load_recipe_dialog)
        setup_form.addRow(load)
        self.chart = QLineEdit(self.recipe.chart_path)
        self.chart.setReadOnly(True)
        choose = QPushButton("Change chart…")
        choose.clicked.connect(self.choose_chart)
        setup_form.addRow(choose, self.chart)

        advanced = QWidget()
        self.advanced_form = advanced_form = QFormLayout(advanced)
        self.manual_bounds = QCheckBox("Use manual scan bounds")
        advanced_form.addRow(self.manual_bounds)
        self.fields = {}
        for key, label, lo, hi, decimals in (
            ("wd_start_mm", "Start WD (mm)", 0.1, 500, 3), ("wd_end_mm", "End WD (mm)", 0.1, 500, 3),
            ("nominal_wd_mm", "Nominal distance (mm)", 0.1, 500, 3), ("step_mm", "Step (mm)", 0.001, 10, 3),
            ("settle_s", "Settle (s)", 0, 60, 2), ("exposure_us", "Exposure (µs)", 1, 10000000, 0),
            ("gain", "Gain", 0, 100, 2), ("fixed_frequency_cpp", "Frequency (cycles/sensor pixel)", 0.001, 0.249, 3)):
            widget = QDoubleSpinBox()
            widget.setRange(lo, hi)
            widget.setDecimals(decimals)
            widget.setValue(getattr(self.recipe, key))
            self.fields[key] = widget
            (form if key in {"nominal_wd_mm", "step_mm"} else advanced_form).addRow(label, widget)
        self.half_span = QDoubleSpinBox()
        self.half_span.setDecimals(3)
        self.half_span.setRange(0.002, 250)
        self.half_span.setSingleStep(0.05)
        self.half_span.setValue((self.recipe.wd_end_mm - self.recipe.wd_start_mm) / 2)
        form.insertRow(3, "Half-span (mm)", self.half_span)
        for key, label in (("frames_per_position", "Frames / position"), ("repetitions", "Sweeps")):
            widget = QSpinBox()
            widget.setRange(1, 100)
            widget.setValue(getattr(self.recipe, key))
            self.fields[key] = widget
            advanced_form.addRow(label, widget)
        self.reverse = QCheckBox("Alternate reverse sweeps (hysteresis check)")
        advanced_form.addRow(self.reverse)
        self.nominal_note = QLabel()
        self.nominal_note.setWordWrap(True)
        form.addRow(self.nominal_note)
        self.format = QComboBox()
        self.format.addItems((["RGB8", "Mono8", "Mono16", "BayerRG8", "BayerRG10p", "BayerRG12p"] if sys.platform == 'darwin'
                              else ["RGB8", "Mono8", "Mono10", "Mono12", "Mono16", "BayerRG8", "BayerRG10", "BayerRG12"]))
        advanced_form.addRow("Measurement pixel format", self.format)
        lens_buttons = QHBoxLayout()
        self.load_lens = load_lens = QPushButton("Load lens calibration…")
        load_lens.clicked.connect(self.load_lens_calibration)
        self.clear_lens = QPushButton("Clear lens calibration")
        self.clear_lens.clicked.connect(lambda: self.set_lens_calibration(None))
        lens_buttons.addWidget(load_lens)
        lens_buttons.addWidget(self.clear_lens)
        setup_form.addRow(lens_buttons)
        self.lens_label = QLabel()
        self.lens_label.setWordWrap(True)
        setup_form.addRow(self.lens_label)
        self.check_group = checks = QGroupBox("Physical setup checks (required for real hardware)")
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
        for widget in (self.wd_btn, self.xy_btn, self.xy_label):
            setup_form.addRow(widget)
        alignment = QHBoxLayout()
        alignment.addWidget(self.center_btn)
        alignment.addWidget(self.scout_btn)
        form.addRow(alignment)
        self.scout_note = QLabel()
        self.scout_note.setWordWrap(True)
        form.addRow(self.scout_note)
        self.review_scout = QPushButton("Review scout range…")
        self.review_scout.clicked.connect(lambda: self.start_scan(use_scout=True))
        form.addRow(self.review_scout)
        gen = QWidget()
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
        setup_form.addRow(CollapsibleBox("Generate chart", gen))
        form.addRow(CollapsibleBox("Setup", setup_options))
        form.addRow(CollapsibleBox("Advanced scan settings", advanced))
        scroll = QScrollArea()
        scroll.setWidget(setup)
        scroll.setWidgetResizable(True)
        split.addWidget(scroll)
        right = QWidget()
        layout = QVBoxLayout(right)
        self.preview = LiveView()
        self.preview.setText("Captured frame preview\nSimulation is enabled by default")
        self.preview.setMinimumSize(320, 240)
        layout.addWidget(self.preview, 1)
        self.status = QTextEdit()
        self.status.setReadOnly(True)
        layout.addWidget(self.status)
        self.progress = QProgressBar()
        layout.addWidget(self.progress)
        buttons = QHBoxLayout()
        self.plan_note = QLabel()
        self.plan_note.setWordWrap(True)
        layout.addWidget(self.plan_note)
        self.start_btn = QPushButton("Review scan…")
        self.preview_btn = QPushButton("Check chart")
        self.stop_btn = QPushButton("STOP")
        self.stop_btn.setStyleSheet("background:#c32626;color:white;font-weight:bold")
        self.stop_btn.setEnabled(False)
        self.reanalyze_btn = QPushButton("Analyze saved run…")
        self.report_btn = QPushButton("Open report")
        self.report_btn.setEnabled(False)
        for button in (self.preview_btn, self.start_btn, self.stop_btn):
            buttons.addWidget(button)
        results = QWidget()
        result_actions = QHBoxLayout(results)
        result_actions.addWidget(self.report_btn)
        result_actions.addWidget(self.reanalyze_btn)
        self.start_btn.clicked.connect(lambda: self.start_scan())
        self.preview_btn.clicked.connect(self.check_preview)
        self.stop_btn.clicked.connect(self.stop)
        self.reanalyze_btn.clicked.connect(self.reanalyze)
        self.report_btn.clicked.connect(self.open_report)
        layout.addLayout(buttons)
        self.results = CollapsibleBox("Results", results)
        layout.addWidget(self.results)
        split.addWidget(right)
        split.setSizes([440, 680])
        outer.addWidget(split, 1)
        self.setup = setup
        self.update_lens_label()
        self.simulate.toggled.connect(self.change_mode)
        self.dry_run.toggled.connect(self.update_controls)
        self.chart.textChanged.connect(self.invalidate_preview)
        self.format.currentTextChanged.connect(self.invalidate_preview)
        self.preset.currentIndexChanged.connect(self.invalidate_preview)
        self.manual_bounds.toggled.connect(self.range_changed)
        self.half_span.valueChanged.connect(self.range_changed)
        self.reverse.toggled.connect(self.update_controls)
        for key, widget in self.fields.items():
            if key in {"exposure_us", "gain"}:
                widget.valueChanged.connect(self.invalidate_preview)
            elif key in {"nominal_wd_mm", "step_mm", "wd_start_mm", "wd_end_mm", "fixed_frequency_cpp"}:
                widget.valueChanged.connect(self.range_changed)
            else:
                widget.valueChanged.connect(self.update_controls)
        self.apply_recipe(self.recipe, preset=0)

    def change_mode(self, simulated):
        if not simulated:
            try:
                self.fixture_profile = load_settings().focusfield
            except ValueError as exc:
                QMessageBox.warning(self, "Fixture settings", str(exc))
        with QSignalBlocker(self.preset):
            self.preset.setCurrentIndex(0 if simulated else 1)
        self.update_lens_label()
        self.invalidate_preview()

    def invalidate_preview(self, *args):
        self.last_preview = None
        self.scout_proposal = None
        self.update_controls()

    def range_changed(self, *args):
        if not self._applying_recipe:
            self.scout_proposal = None
        self.update_controls()

    def capture_format(self, fixture):
        if self.preset.currentIndex() == 0:
            return "RGB8"
        if not self.simulate.isChecked() and fixture.camera_pipeline_verified and fixture.verified_pixel_format:
            return fixture.verified_pixel_format
        return self.format.currentText()

    def scan_bounds(self):
        if self.manual_bounds.isChecked():
            return self.fields["wd_start_mm"].value(), self.fields["wd_end_mm"].value()
        return centered_scan_bounds(self.fields["nominal_wd_mm"].value(), self.half_span.value(),
                                    self.fields["step_mm"].value())

    def current_recipe(self, *, fixture=None, use_scout=False):
        fixture = fixture or self.fixture_profile
        values = {key: widget.value() for key, widget in self.fields.items()}
        if use_scout:
            draft = self.scout_proposal["suggested_recipe"]
            for key in ("wd_start_mm", "wd_end_mm", "step_mm"):
                values[key] = draft[key]
        else:
            values["wd_start_mm"], values["wd_end_mm"] = self.scan_bounds()
        if self.preset.currentIndex() == 0:
            values.update(frames_per_position=1, repetitions=1)
        pixel_format = self.capture_format(fixture)
        supported = [self.format.itemText(i) for i in range(self.format.count())]
        if pixel_format not in supported:
            if not self.dry_run.isChecked():
                raise ValueError(f"{pixel_format} is unsupported by this camera backend")
            pixel_format = self.format.currentText()
        size = self.recipe.expected_width_px, self.recipe.expected_height_px
        lens = None if self.simulate.isChecked() else self.recipe.lens_calibration
        if self.simulate.isChecked():
            chart = load_chart(self.chart.text())
            size = int(chart["page_width"] * SimCamera.scale), int(chart["page_height"] * SimCamera.scale)
        if lens:
            size = lens.image_size_px
        if (self.last_preview and self.last_preview.get("image_size_px")
                and self.last_preview.get("pixel_format") == pixel_format):
            size = self.last_preview["image_size_px"]
        return replace(self.recipe, **values, chart_path=self.chart.text(), pixel_format=pixel_format,
                       lens_calibration=lens, expected_width_px=size[0], expected_height_px=size[1],
                       separate_nominal_plane_required=not nominal_on_grid(
                           values["wd_start_mm"], values["wd_end_mm"], values["step_mm"], values["nominal_wd_mm"]),
                       reverse_alternate=values["repetitions"] > 1 and self.reverse.isChecked())

    def scan_summary(self, recipe, dry_run):
        per_sweep = len(recipe.distances())
        positions = per_sweep * recipe.repetitions
        frames = 0 if dry_run else positions * recipe.frames_per_position
        bits, channels = FORMATS[recipe.pixel_format]
        frame_bytes = recipe.expected_width_px * recipe.expected_height_px * channels * (1 if bits == 8 else 2)
        space = storage_required(frames, frame_bytes) / 1e9
        count = f"{positions} positions"
        if recipe.repetitions > 1:
            count += f" ({per_sweep} per sweep)"
        return (f"{recipe.wd_start_mm:g}–{recipe.wd_end_mm:g} mm · {count} · "
                f"{frames} frames · about {space:.2f} GB free disk required")

    def update_controls(self, *args):
        if self._applying_recipe:
            return
        dry_run, simulated = self.dry_run.isChecked(), self.simulate.isChecked()
        quick = self.preset.currentIndex() == 0
        idle = self.thread is None
        self.start_btn.setText("Run dry run…" if dry_run else "Run scan" if simulated else "Review scan…")
        self.preview_btn.setEnabled(idle and not dry_run)
        self.check_group.setVisible(not simulated)
        for name in ("lens_locked_and_chart_checked", "collision_free_dry_run"):
            self.checks[name].setVisible(not dry_run)
        self.half_span.setEnabled(not self.manual_bounds.isChecked())
        for key in ("wd_start_mm", "wd_end_mm"):
            self.fields[key].setEnabled(self.manual_bounds.isChecked())
            self.advanced_form.setRowVisible(self.fields[key], self.manual_bounds.isChecked())
        for key in ("frames_per_position", "repetitions"):
            self.fields[key].setEnabled(not quick)
            self.advanced_form.setRowVisible(self.fields[key], not quick)
        self.reverse.setVisible(not quick and self.fields["repetitions"].value() > 1)
        verified_format = (not simulated and self.fixture_profile.camera_pipeline_verified
                           and bool(self.fixture_profile.verified_pixel_format))
        self.format.setEnabled(not quick and not verified_format)
        self.advanced_form.setRowVisible(self.format, not quick and not verified_format)
        if not self.manual_bounds.isChecked():
            try:
                bounds = self.scan_bounds()
            except ValueError:
                pass
            else:
                for key, bound in zip(("wd_start_mm", "wd_end_mm"), bounds):
                    with QSignalBlocker(self.fields[key]):
                        self.fields[key].setValue(bound)
        valid_preview = bool(self.last_preview and self.last_preview.get("preview_registration", {})
                             .get("full_field_visible"))
        center_error = self.last_preview["preview_registration"].get("center_error_px") if valid_preview else None
        can_center = bool(valid_preview and (simulated or self.xy_calibration is not None) and center_error is not None
                          and np.linalg.norm(center_error) > CenteringLimits().tolerance_px)
        self.center_btn.setVisible(not dry_run and can_center)
        self.scout_btn.setVisible(not dry_run and valid_preview)
        self.center_btn.setEnabled(idle and not dry_run and can_center)
        self.scout_btn.setEnabled(idle and not dry_run and valid_preview)
        self.scout_note.setVisible(self.scout_proposal is not None)
        self.review_scout.setVisible(self.scout_proposal is not None)
        self.review_scout.setText("Dry run scout range…" if dry_run else "Review scout range…")
        needs_native = not simulated and not dry_run and not quick and self.capture_format(self.fixture_profile) == "RGB8"
        try:
            recipe = self.current_recipe()
            label = self.scan_summary(recipe, dry_run)
            if not dry_run:
                label += f" · {recipe.pixel_format}"
                if recipe.pixel_format == "RGB8":
                    label += " (provisional)"
            self.plan_note.setText(label)
            if needs_native:
                self.plan_note.setText(label + "\nSelect a verified native format in Advanced scan settings.")
            self.start_btn.setEnabled(idle and not needs_native)
            self.nominal_note.setText(
                f"Separate measurement at requested {recipe.nominal_wd_mm:g} mm is required. No extra move is scheduled."
                if recipe.separate_nominal_plane_required else
                f"Requested {recipe.nominal_wd_mm:g} mm is on the sweep grid")
        except (OSError, ValueError, KeyError, TypeError) as exc:
            self.plan_note.setText(str(exc))
            self.start_btn.setEnabled(False)
            self.nominal_note.clear()
        if self.scout_proposal:
            try:
                draft = self.current_recipe(use_scout=True)
                nominal = " · Separate nominal measurement required" if draft.separate_nominal_plane_required else ""
                self.scout_note.setText("Scout proposal · " + self.scan_summary(draft, dry_run) + nominal)
                self.review_scout.setEnabled(idle and not needs_native)
            except (OSError, ValueError, KeyError, TypeError) as exc:
                self.scout_note.setText(str(exc))
                self.review_scout.setEnabled(False)

    def update_lens_label(self):
        lens = self.recipe.lens_calibration
        self.clear_lens.setVisible(lens is not None)
        self.clear_lens.setEnabled(lens is not None)
        self.load_lens.setText("Replace lens calibration…" if lens else "Load lens calibration…")
        if lens is None:
            self.lens_label.setText("No lens correction")
        else:
            width, height = lens.image_size_px
            mode = " · Inactive in simulation" if self.simulate.isChecked() else ""
            self.lens_label.setText(f"Lens calibration for {lens.camera_serial} · {width} × {height} px{mode}")

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
            self.fixture_profile = save_reference(record, path, settings.path).focusfield
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
            QMessageBox.warning(self, "Setup needs images", "Centering/scouting require images. Turn off Motion only; use Run dry run for a supervised motion-only Z preflight.")
            return
        simulated = self.simulate.isChecked()
        if (not self.last_preview or self.last_preview["simulated"] != simulated
                or not self.last_preview["preview_registration"]["full_field_visible"]):
            QMessageBox.warning(self, "Preview required", "Run Check chart successfully before setup motion.")
            return
        if not simulated and (self.main.thread is not None or self.main.server.is_running() or self.main.printer._busy):
            QMessageBox.warning(self, "Printer in use", "Finish other printer/calibrator operations first.")
            return
        try:
            settings = load_settings()
            fixture = simulation_fixture() if simulated else settings.focusfield
            recipe = self.current_recipe(fixture=fixture)
            if recipe.pixel_format != self.last_preview.get("pixel_format"):
                raise ValueError("The capture format changed. Run Check chart again before setup motion.")
            checks = OperatorChecks(**{k: v.isChecked() for k, v in self.checks.items()})
            if not simulated:
                fixture.validate_physical()
                checks.validate()
            xy = self.xy_calibration
            if simulated:
                xy = simulation_xy_calibration(fixture)
                chart = load_chart(recipe.chart_path)
                width = int(chart["page_width"] * SimCamera.scale)
                height = int(chart["page_height"] * SimCamera.scale)
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
                scout = scout_recipe_dialog(self, fixture, recipe.nominal_wd_mm, physical=not simulated)
                if scout is None:
                    return
            if operation == "center" and not simulated and QMessageBox.question(
                    self, "Approve bounded optical setup motion", description,
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
                load_chart(recipe.chart_path)
                self.apply_recipe(recipe)
            except Exception as exc:
                QMessageBox.warning(self, "Recipe", str(exc))

    def apply_recipe(self, recipe, *, preset=1):
        self._applying_recipe = True
        try:
            self.recipe = recipe
            self.preset.setCurrentIndex(preset)
            for key, widget in self.fields.items():
                widget.setValue(getattr(recipe, key))
            self.half_span.setValue((recipe.wd_end_mm - recipe.wd_start_mm) / 2)
            try:
                centered = centered_scan_bounds(recipe.nominal_wd_mm, self.half_span.value(), recipe.step_mm)
            except ValueError:
                centered = None
            self.manual_bounds.setChecked(centered != (recipe.wd_start_mm, recipe.wd_end_mm))
            self.reverse.setChecked(recipe.reverse_alternate)
            self.format.setCurrentText(recipe.pixel_format)
            self.chart.setText(recipe.chart_path)
            self.update_lens_label()
        finally:
            self._applying_recipe = False
        self.invalidate_preview()

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

    def start_scan(self, *, use_scout=False):
        if self.thread is not None:
            return
        simulated = self.simulate.isChecked()
        if not simulated and (self.main.thread is not None or self.main.server.is_running() or self.main.printer._busy):
            QMessageBox.warning(self, "Printer in use", "Close the calibrator and finish other printer operations first.")
            return
        try:
            if use_scout and not self.scout_proposal:
                return
            dry_run = self.dry_run.isChecked()
            settings = load_settings()
            fixture = simulation_fixture() if simulated else settings.focusfield
            recipe = self.current_recipe(fixture=fixture, use_scout=use_scout)
            load_chart(recipe.chart_path)
            recipe.validate_fixture(fixture, settings.printer.z_max, simulation=simulated, dry_run=dry_run)
            checks = OperatorChecks(**{k: v.isChecked() for k, v in self.checks.items()})
            if not simulated:
                checks.validate(dry_run)
                if not dry_run and self.preset.currentIndex() == 1 and recipe.pixel_format == "RGB8":
                    raise ValueError("Select a verified native pixel format for Measurement, or choose Quick check.")
                nominal = (f"Separate measurement at {recipe.nominal_wd_mm:g} mm is required."
                           if recipe.separate_nominal_plane_required else
                           f"The sweep includes the requested {recipe.nominal_wd_mm:g} mm plane.")
                operation = "motion-only dry run" if dry_run else f"{self.preset.currentText()} image scan"
                capture = "" if dry_run else (f"\n{recipe.pixel_format} · {recipe.exposure_us:g} µs exposure "
                                               f"· {recipe.gain:g} gain")
                if QMessageBox.question(self, "Approve optical scan motion",
                        f"Run supervised {operation}?\n{self.scan_summary(recipe, dry_run)}{capture}\n"
                        f"Step {recipe.step_mm:g} mm · Absolute WD uncertainty {fixture.wd_uncertainty_mm:g} mm.\n{nominal}",
                        QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
                    return
            stage, camera = self.devices(recipe, fixture, motion_only=dry_run)
            # Construct PrusaLink from QSettings on the GUI thread; only network I/O runs in worker.
            link = link_from_settings(self.main.settings) if not simulated and self.main.printer.configured() else None
            self.controller = ScanController(recipe, settings, fixture, stage, camera,
                Path(self.main.runs_folder()) / "focusfield", checks=checks, dry_run=self.dry_run.isChecked(),
                operator_confirmed=not simulated,
                setup_provenance=dict(history=self.setup_history,
                                      scout_proposal=({**self.scout_proposal, 'approved': not simulated and not dry_run}
                                                      if use_scout else None),
                                      operator_approved_fine_scan=use_scout and not simulated and not dry_run),
                printer_state=(lambda: link.status().state) if link else (lambda: None))
            self.last_preview = None
            self._start_task(self.controller.run, physical=not simulated, borrow_camera=not simulated and not dry_run)
            self.controller.on_event = lambda kind, *args: self.worker.event.emit(kind, args)
            self.thread.start()
        except Exception as exc:
            QMessageBox.warning(self, "Cannot start optical scan", str(exc))

    def check_preview(self):
        if self.thread or self.dry_run.isChecked():
            return
        simulated = self.simulate.isChecked()
        if not simulated and (self.main.thread is not None or self.main.server.is_running() or self.main.printer._busy):
            QMessageBox.warning(self, "Devices in use", "Finish other printer/calibrator operations first.")
            return
        try:
            if not simulated:
                self.fixture_profile = load_settings().focusfield
            recipe = replace(self.recipe, chart_path=self.chart.text(), pixel_format=self.capture_format(self.fixture_profile),
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
                    height, width = capture.pixels.shape[:2]
                    frame_info = (f"Camera {capture.metadata['camera_serial']} · {width} × {height} px · "
                                  f"{capture.metadata['pixel_format']}")
                    try:
                        registration, gray, _ = register_capture(capture, chart, recipe, optical_center_px=calibrated_center)
                    except ValueError as exc:
                        raise ValueError(f"{frame_info}. {exc}") from exc
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
                               f"{frame_info}. "
                               f"Chart-center error {registration['center_error_px']} sensor pixels "
                               f"relative to {'calibrated optical center' if calibrated_center else 'image midpoint (diagnostic only)'}. "
                               "Registration does not authorize XY motion or establish absolute WD.")
                    check_cancel(cancel)
                    return RunResult("COMPLETED", "", message, details=dict(preview_registration=registration,
                        simulated=simulated, camera_serial=capture.metadata["camera_serial"],
                        image_size_px=(capture.pixels.shape[1], capture.pixels.shape[0]),
                        pixel_format=capture.metadata["pixel_format"]))
                except InterruptedError as exc:
                    return RunResult("ABORTED", "", str(exc))
                finally:
                    camera.close()
            self.last_preview = None
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
        self.update_controls()
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
            if array.dtype != np.uint8:
                array = np.ascontiguousarray(array / (2**record["bit_depth"]-1) * 255, dtype=np.uint8)
            self.preview.set_image(to_qimage(np.ascontiguousarray(array)))

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
        self.reanalyze_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        if result.path:
            self.run_path = Path(result.path)
            if (self.run_path / "report.html").is_file():
                self.report_path = self.run_path / "report.html"
                self.report_btn.setEnabled(True)
                self.results.set_expanded(True)
        path_message = f"\nRun folder {result.path}" if result.path else ""
        self.status.append(f"{result.state} · {result.message}{path_message}")
        if result.state == 'COMPLETED' and 'preview_registration' in result.details:
            self.last_preview = result.details
        if result.path and (Path(result.path) / 'setup_result.json').is_file() and result.state == 'COMPLETED':
            manifest = json.loads((Path(result.path) / 'manifest.json').read_text(encoding='utf-8'))
            details = manifest['setup_result']
            self.setup_history.append(dict(path=result.path, operation=manifest['operation'], result=details))
            if manifest['operation'] == 'scout':
                self.status.append(details['reasoning'] + f". Absolute WD uncertainty {details['absolute_wd_uncertainty_mm']:g} mm.")
                nominal_message = details.get('nominal_plane_message', '')
                if nominal_message:
                    self.status.append(nominal_message)
                self.scout_proposal = dict(path=result.path, **details)
        elif result.acquisition_complete or result.analysis_complete:
            self.status.append(f"Acquisition complete: {result.acquisition_complete}; analysis complete: "
                               f"{result.analysis_complete}; measurement valid: {result.measurement_valid}")

        self.update_controls()

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
        if self.report_path and self.report_path.is_file():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.report_path.resolve())))

    def shutdown(self):
        if self.thread:
            self.stop()
            # Keep the event loop alive until finished() owns thread cleanup.
            return False
        return True
