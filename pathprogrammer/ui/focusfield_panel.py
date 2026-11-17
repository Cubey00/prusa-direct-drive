"""Optical setup, preview, scan and offline review inside the existing application."""
from dataclasses import replace
from pathlib import Path

import numpy as np
from PySide6.QtCore import QObject, QThread, Qt, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices, QImage, QPixmap
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout,
                               QGroupBox, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
                               QProgressBar, QPushButton, QScrollArea, QSpinBox,
                               QSplitter, QTextEdit, QVBoxLayout, QWidget)

from machine_settings import SETTINGS, load as load_settings
from focusfield.acquisition import CameraAdapter
from focusfield.chart import load_chart
from focusfield.config import DEFAULT_RECIPE, load_recipe, simulation_fixture
from focusfield.controller import OperatorChecks, RunResult, ScanController
from focusfield.hardware import SimCamera, SimStage, ZStage
from focusfield.reports import analyze_run
from .printer_box import link_from_settings


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
        self.run_path = None
        self._analysis_cancel = None
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
        self.format = QComboBox()
        self.format.addItems(["RGB8", "Mono8", "Mono10", "Mono12", "Mono16", "BayerRG8", "BayerRG10",
                              "BayerRG12", "BayerRG10p", "BayerRG12p"])
        form.addRow("Capture pixel format", self.format)
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
                          ("lens_locked_and_chart_checked", "Lens locked, target scale and chart visibility checked"),
                          ("collision_free_dry_run", "Complete collision-free dry run already verified"),
                          ("manual_printer_idle", "If PrusaLink is unavailable: I verified printer idle manually")):
            checkbox = QCheckBox(text)
            self.checks[key] = checkbox
            check_layout.addWidget(checkbox)
        form.addRow(checks)
        form.addRow(QLabel("No automatic homing, touch, or recovery motion. Configure [focusfield] in machine settings."))
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
                self.recipe = recipe
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
            recipe = replace(self.recipe, **{key: w.value() for key, w in self.fields.items()},
                             chart_path=self.chart.text(), pixel_format=self.format.currentText(),
                             reverse_alternate=self.reverse.isChecked())
            chart = load_chart(recipe.chart_path)
            settings = load_settings()
            fixture = simulation_fixture() if simulated else settings.focusfield
            recipe.validate_fixture(fixture, settings.printer.z_max, simulation=simulated)
            checks = OperatorChecks(**{k: v.isChecked() for k, v in self.checks.items()})
            if not simulated:
                checks.validate(self.dry_run.isChecked())
            stage = SimStage(fixture) if simulated else ZStage(fixture, settings.printer.z_max)
            camera = SimCamera(stage, recipe, chart) if simulated else CameraAdapter(recipe)
            # Construct PrusaLink from QSettings on the GUI thread; only network I/O runs in worker.
            link = link_from_settings(self.main.settings) if not simulated and self.main.printer.configured() else None
            self.controller = ScanController(recipe, settings, fixture, stage, camera,
                Path(self.main.log_folder()) / "focusfield", checks=checks, dry_run=self.dry_run.isChecked(),
                printer_state=(lambda: link.status().state) if link else (lambda: None))
            self._start_task(self.controller.run, physical=not simulated)
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
                             exposure_us=self.fields["exposure_us"].value(), gain=self.fields["gain"].value())
            chart = load_chart(recipe.chart_path)
            if simulated:
                stage = SimStage(simulation_fixture())
                camera = SimCamera(stage, recipe, chart)
            else:
                camera = CameraAdapter(recipe)
            def preview():
                from focusfield.acquisition import analysis_plane
                from focusfield.chart import detect_chart, edge_rois
                try:
                    camera.open()
                    camera.fresh()
                    capture = camera.capture()
                    self.worker.event.emit("frame", (capture.metadata, capture.pixels))
                    gray, pitch = analysis_plane(capture.pixels, capture.metadata)
                    h, error = detect_chart(gray, chart)
                    local_count = 0
                    for square in chart["slanted_squares"]:
                        try:
                            local_count += len(edge_rois(gray, h, square))
                        except ValueError:
                            pass
                    message = (f"All four chart markers detected; {local_count} local edges. "
                               f"Marker reprojection RMS {error:.2f} analysis pixels. "
                               f"Camera {capture.metadata['camera_serial']}; {capture.metadata['pixel_format']}. "
                               "This preview does not validate physical scale or camera processing.")
                    return RunResult("COMPLETED", "", message)
                finally:
                    camera.close()
            self._start_task(preview, physical=not simulated)
            self.thread.start()
        except Exception as exc:
            QMessageBox.warning(self, "Preview", str(exc))

    def _start_task(self, work, *, physical=False):
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
        if self.hardware_active:
            self.hardware_active = False
            self.hardwareActivityChanged.emit(False)
        self.setup.setEnabled(True)
        self.start_btn.setEnabled(True)
        self.preview_btn.setEnabled(True)
        self.reanalyze_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.run_path = Path(result.path) if result.path else None
        self.report_btn.setEnabled(bool(self.run_path and (self.run_path / "report.html").exists()))
        self.status.append(f"{result.state}: {result.message}\nRun: {result.path}")

    def stop(self):
        if self.controller:
            self.controller.stop()
        if self._analysis_cancel:
            self._analysis_cancel.set()
        self.status.append("STOP requested")

    def reanalyze(self):
        path = QFileDialog.getExistingDirectory(self, "Select optical run folder", str(Path(self.main.log_folder()) / "focusfield"))
        if not path:
            return
        import json
        import threading
        try:
            manifest = json.loads((Path(path) / "manifest.json").read_text(encoding="utf-8"))
            partial = manifest["state"] != "COMPLETED"
            if partial and QMessageBox.question(self, "Partial run", "Analyze this incomplete run with explicit partial-data flags?") != QMessageBox.Yes:
                return
            self._analysis_cancel = threading.Event()
            cancel = self._analysis_cancel
            def analyze():
                summary = analyze_run(path, allow_partial=partial, should_abort=cancel.is_set,
                                      on_progress=lambda *args: self.worker.event.emit("progress", args))
                return RunResult("COMPLETED", path, f"{summary['valid_focus_fits']} valid focus fits")
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
