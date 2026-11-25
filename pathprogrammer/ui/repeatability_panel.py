"""Edit and run a printer sequence that pauses for manual dial readings."""

import json
from datetime import datetime
from pathlib import Path
from queue import Queue
from threading import Event

from PySide6.QtCore import QIODevice, QSaveFile, QThread, Qt, Signal
from PySide6.QtWidgets import (QCheckBox, QFileDialog, QFormLayout, QGridLayout,
                               QGroupBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMessageBox,
                               QPushButton, QSpinBox, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

import machine_settings
from ..repeatability import (HOME, MEASURE, MOVE, SEQUENCE_FORMAT, Plan, PrinterMotion, RunLog, Step,
                             dial_value, load_sequence)
from .style import ACCENT, ACCENT_DARK, STOP, STOP_DARK, action_button

SEQUENCE_FILTER = "Repeatability sequences (*.json)"


class RepeatabilityWorker(QThread):
    progress = Signal(int, int, str)
    measuring = Signal(int, int)

    def __init__(self, plan, path, parent=None):
        super().__init__(parent)
        self.plan, self.path = plan, path
        self.cancel = Event()
        self.readings = Queue()
        self.motion = None if plan.simulate else PrinterMotion(self.cancel)
        self.result = ""

    def stop(self):
        if self.cancel.is_set():
            return
        self.cancel.set()
        if self.motion is not None:
            self.motion.interrupt()
        self.readings.put(None)

    def _check_stop(self):
        if self.cancel.is_set():
            raise InterruptedError("Stopped by operator.")

    def run(self):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("x", encoding="utf-8", newline="") as stream:
                log = RunLog(stream, self.plan)
                outcome, details = self._run_steps(log)
                log.record(outcome, details=details)
            self.result = f"{details}\nCSV saved to {self.path}"
        except OSError as exc:
            self.result = f"CSV error: {exc}. Run ended."

    def _run_steps(self, log):
        plan = self.plan
        motion = self.motion
        cycle, number, step = "", "", None
        step_started = False
        outcome, details = "completed", "Run completed."
        try:
            self._check_stop()
            if motion is not None:
                motion.open()
            for cycle, number, step in plan.sequence():
                self._check_stop()
                log.record("started", cycle=cycle, number=number, step=step)
                step_started = True
                self.progress.emit(cycle, number, step.action)
                if motion is not None:
                    motion.execute(step, plan.target(step), plan.clearance_z)
                self._check_stop()
                values = ()
                if step.action == MEASURE:
                    self.measuring.emit(cycle, number)
                    values = self.readings.get()
                    if values is None:
                        self._check_stop()
                log.record("completed", cycle=cycle, number=number, step=step, readings=values)
                step_started = False
        except Exception as exc:
            outcome = "stopped" if self.cancel.is_set() else "failed"
            details = "Stopped by operator." if outcome == "stopped" else f"Run failed: {exc}"
        finally:
            if motion is not None:
                try:
                    motion.close(abort=outcome != "completed")
                except Exception as exc:
                    outcome = "failed"
                    details += f" Printer shutdown error: {exc}"
        if outcome != "completed" and step_started:
            log.record(outcome, cycle=cycle, number=number, step=step, details=details)
        return outcome, details


class RepeatabilityPanel(QWidget):
    activeChanged = Signal(bool)

    def __init__(self, runs_folder, prepare_run, parent=None):
        super().__init__(parent)
        self.prepare_run = prepare_run
        self.worker = None
        self._sequence_folder = Path(runs_folder) / "repeatability"
        self._sequence_path = None
        self.run_name = QLineEdit()
        self.measurement = [QLineEdit() for _ in range(3)]
        self.clearance = QLineEdit()
        self.clearance.setPlaceholderText("mm")
        self.cycles = QSpinBox()
        self.cycles.setRange(1, 999)
        self.cycles.setValue(10)
        self.dial_count = QSpinBox()
        self.dial_count.setRange(1, 3)
        self.dial_count.setValue(2)
        self.dial_names = [QLineEdit(name) for name in ("X", "Y", "Z")]
        self.folder = QLineEdit(str(self._sequence_folder))
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse)
        self.simulate = QCheckBox("Simulate (no printer motion)")
        self.simulate.setChecked(True)
        self.home_first = QCheckBox("Home X/Y once before the first cycle")

        self.setup = QWidget()
        setup = QVBoxLayout(self.setup)
        setup.setContentsMargins(0, 0, 0, 0)
        files = QHBoxLayout()
        self.open_sequence_btn = QPushButton("Open sequence…")
        self.save_sequence_btn = QPushButton("Save sequence…")
        self.open_sequence_btn.clicked.connect(self._open_sequence)
        self.save_sequence_btn.clicked.connect(self._save_sequence)
        files.addWidget(self.open_sequence_btn)
        files.addWidget(self.save_sequence_btn)
        files.addStretch()
        setup.addLayout(files)
        settings = QGroupBox("Run setup")
        fields = QGridLayout(settings)
        fields.addWidget(QLabel("Test name"), 0, 0)
        fields.addWidget(self.run_name, 0, 1, 1, 3)
        fields.addWidget(QLabel("Measurement location (mm)"), 1, 0)
        for col, (axis, value) in enumerate(zip("XYZ", self.measurement), 1):
            coordinate = QHBoxLayout()
            label = QLabel(axis)
            label.setBuddy(value)
            coordinate.addWidget(label)
            coordinate.addWidget(value)
            fields.addLayout(coordinate, 1, col)
        fields.addWidget(QLabel("Clearance Z (mm)"), 2, 0)
        fields.addWidget(self.clearance, 2, 1)
        fields.addWidget(QLabel("Cycles"), 2, 2)
        fields.addWidget(self.cycles, 2, 3)
        fields.addWidget(QLabel("Number of dials"), 3, 0)
        fields.addWidget(self.dial_count, 3, 1)
        fields.addWidget(QLabel("Dial names"), 4, 0)
        for col, name in enumerate(self.dial_names, 1):
            fields.addWidget(name, 4, col)
        fields.addWidget(QLabel("CSV folder"), 5, 0)
        fields.addWidget(self.folder, 5, 1, 1, 2)
        fields.addWidget(browse, 5, 3)
        fields.addWidget(self.home_first, 6, 1, 1, 3)
        fields.addWidget(self.simulate, 7, 1, 1, 3)
        clearance_hint = QLabel("X/Y travel and homing use clearance Z. A larger Z lowers the bed.")
        clearance_hint.setWordWrap(True)
        fields.addWidget(clearance_hint, 8, 0, 1, 4)
        self.dial_count.valueChanged.connect(self._show_dials)
        self._show_dials(self.dial_count.value())
        setup.addWidget(settings)

        self.steps = QTableWidget(0, 4)
        self.steps.setHorizontalHeaderLabels(("Action", "X (mm)", "Y (mm)", "Z (mm)"))
        self.steps.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.steps.setSelectionBehavior(QTableWidget.SelectRows)
        self.steps.setSelectionMode(QTableWidget.SingleSelection)
        self.steps.setMinimumHeight(135)
        self.steps.setToolTip("Move rows use machine coordinates. Measure visits the saved location.")
        setup.addWidget(self.steps, 1)
        actions = QHBoxLayout()
        for title, action in (("Add move", MOVE), ("Add X/Y home", HOME), ("Add measurement", MEASURE)):
            button = QPushButton(title)
            button.clicked.connect(lambda checked=False, kind=action: self._add_step(kind))
            actions.addWidget(button)
        for title, handler in (("Move up", lambda: self._reorder(-1)),
                               ("Move down", lambda: self._reorder(1)), ("Remove", self._remove)):
            button = QPushButton(title)
            button.clicked.connect(handler)
            actions.addWidget(button)
        setup.addLayout(actions)

        self.prompt = QGroupBox("Enter dial readings")
        self.prompt_layout = QFormLayout(self.prompt)
        self.reading_fields = []
        for _ in range(3):
            field = QLineEdit()
            field.setPlaceholderText("Signed value in mm")
            self.prompt_layout.addRow(QLabel(), field)
            self.reading_fields.append(field)
        self.save = QPushButton("Save and continue")
        action_button(self.save, ACCENT, ACCENT_DARK)
        self.save.clicked.connect(self._submit)
        self.prompt_layout.addRow(self.save)
        self.prompt.hide()
        self.start = QPushButton("Start sequence")
        action_button(self.start, ACCENT, ACCENT_DARK)
        self.start.clicked.connect(self._start)
        self.stop = QPushButton("STOP")
        action_button(self.stop, STOP, STOP_DARK)
        self.stop.clicked.connect(self.stop_run)
        self.stop.setEnabled(False)
        self.status = QLabel()
        self.status.setTextFormat(Qt.PlainText)
        self.status.setWordWrap(True)
        self.status.setTextInteractionFlags(Qt.TextSelectableByMouse)

        buttons = QHBoxLayout()
        buttons.addWidget(self.start)
        buttons.addWidget(self.stop)
        buttons.addStretch()
        layout = QVBoxLayout(self)
        layout.addWidget(self.setup, 1)
        layout.addWidget(self.prompt)
        layout.addLayout(buttons)
        layout.addWidget(self.status)
        self._saved_sequence = self._sequence_data()

    @property
    def active(self):
        return self.worker is not None

    def _show_dials(self, count):
        for i, name in enumerate(self.dial_names):
            name.setVisible(i < count)

    def _browse(self):
        folder = QFileDialog.getExistingDirectory(self, "Choose the CSV folder", self.folder.text())
        if folder:
            self.folder.setText(folder)

    def _sequence_data(self):
        return {
            "format": SEQUENCE_FORMAT,
            "name": self.run_name.text(),
            "measurement": [field.text() for field in self.measurement],
            "clearance_z": self.clearance.text(),
            "cycles": self.cycles.value(),
            "dials": [field.text() for field in self.dial_names[:self.dial_count.value()]],
            "home_first": self.home_first.isChecked(),
            "steps": [[self.steps.item(row, col).text() for col in range(4)]
                      for row in range(self.steps.rowCount())],
        }

    def _save_sequence(self):
        default = self._sequence_path
        if default is None:
            folder = self._sequence_folder
            try:
                folder.mkdir(parents=True, exist_ok=True)
            except OSError:
                folder = Path.home()
            default = folder / "repeatability-sequence.json"
        dialog = QFileDialog(self, "Save repeatability sequence", str(default), SEQUENCE_FILTER)
        dialog.setAcceptMode(QFileDialog.AcceptSave)
        dialog.setDefaultSuffix("json")
        files = dialog.selectedFiles() if dialog.exec() == QFileDialog.Accepted else []
        dialog.deleteLater()
        if not files:
            return
        path = Path(files[0])
        data = self._sequence_data()
        payload = (json.dumps(data, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
        output = QSaveFile(str(path))
        if not output.open(QIODevice.WriteOnly) or output.write(payload) != len(payload) or not output.commit():
            self.status.setText(f"Could not save sequence: {output.errorString()}")
            return
        self._sequence_path, self._saved_sequence = path, data
        self.status.setText(f"Sequence saved to {path}")

    def _open_sequence(self):
        folder = self._sequence_path.parent if self._sequence_path else self._sequence_folder
        filename, _ = QFileDialog.getOpenFileName(self, "Open repeatability sequence", str(folder), SEQUENCE_FILTER)
        if not filename:
            return
        path = Path(filename)
        try:
            data = load_sequence(path)
        except (OSError, ValueError) as exc:
            self.status.setText(f"Could not open sequence: {exc}")
            return
        current = self._sequence_data()
        if current != self._saved_sequence and current != data and QMessageBox.question(
            self, "Open sequence", "Replace your unsaved sequence edits?",
            QMessageBox.Yes | QMessageBox.Cancel, QMessageBox.Cancel,
        ) != QMessageBox.Yes:
            return
        self.run_name.setText(data["name"])
        for field, value in zip(self.measurement, data["measurement"]):
            field.setText(value)
        self.clearance.setText(data["clearance_z"])
        self.cycles.setValue(data["cycles"])
        self.dial_count.setValue(len(data["dials"]))
        for index, field in enumerate(self.dial_names):
            field.setText(data["dials"][index] if index < len(data["dials"]) else "XYZ"[index])
        self.home_first.setChecked(data["home_first"])
        self.simulate.setChecked(True)
        self.steps.setRowCount(0)
        for action, *coordinates in data["steps"]:
            self._append_step(action, coordinates)
        self._sequence_path, self._saved_sequence = path, self._sequence_data()
        self.status.setText(f"Sequence opened from {path}")

    def _append_step(self, action, coordinates=("", "", "")):
        row = self.steps.rowCount()
        self.steps.insertRow(row)
        for col, text in enumerate((action, *coordinates)):
            item = QTableWidgetItem(text)
            if col == 0 or action != MOVE:
                item.setFlags(item.flags() & ~Qt.ItemIsEditable)
            self.steps.setItem(row, col, item)
        return row

    def _add_step(self, action):
        row = self._append_step(action)
        self.steps.selectRow(row)
        if action == MOVE:
            self.steps.editItem(self.steps.item(row, 1))

    def _remove(self):
        row = self.steps.currentRow()
        if row >= 0:
            self.steps.removeRow(row)

    def _reorder(self, offset):
        row = self.steps.currentRow()
        other = row + offset
        if row < 0 or not 0 <= other < self.steps.rowCount():
            return
        for col in range(self.steps.columnCount()):
            first = self.steps.takeItem(row, col)
            self.steps.setItem(row, col, self.steps.takeItem(other, col))
            self.steps.setItem(other, col, first)
        self.steps.selectRow(other)

    def _plan(self):
        try:
            measurement = tuple(round(dial_value(value.text()), 3) for value in self.measurement)
            clearance = round(dial_value(self.clearance.text()), 3)
        except ValueError:
            raise ValueError("Enter measurement X, Y, Z and clearance Z in millimetres.") from None
        steps = []
        for row in range(self.steps.rowCount()):
            action = self.steps.item(row, 0).text()
            position = None
            if action == MOVE:
                try:
                    position = tuple(round(dial_value(self.steps.item(row, col).text()), 3) for col in range(1, 4))
                except ValueError:
                    raise ValueError(f"Enter X, Y and Z in move row {row + 1}.") from None
            steps.append(Step(action, position))
        return Plan(self.run_name.text().strip(), measurement, clearance, self.cycles.value(),
                    tuple(name.text().strip() for name in self.dial_names[:self.dial_count.value()]),
                    tuple(steps), self.home_first.isChecked(), self.simulate.isChecked())

    def _start(self):
        if self.active:
            return
        try:
            plan = self._plan()
            plan.validate(machine_settings.load().printer)
            folder = self.folder.text().strip()
            if not folder:
                raise ValueError("Choose a CSV folder.")
            path = Path(folder).expanduser().resolve() / f"repeatability_{datetime.now():%Y%m%d_%H%M%S_%f}.csv"
        except (ValueError, OSError, RuntimeError, machine_settings.SettingsError) as exc:
            self.status.setText(str(exc))
            return
        axes = "Z is referenced" if plan.home_first or plan.steps[0].action == HOME else "X/Y/Z are referenced"
        if not plan.simulate and QMessageBox.question(
            self, "Start printer motion",
            f"Confirm that the printer is idle, {axes}, and the dial "
            f"fixture clears all moves and any X/Y homing. Lateral travel uses Z {plan.clearance_z:g} mm. "
            "An idle calibrator connection will be closed if needed.",
        ) != QMessageBox.Yes:
            return
        problem = self.prepare_run(plan.simulate)
        if problem:
            self.status.setText(problem)
            return
        self.worker = RepeatabilityWorker(plan, path, self)
        self.worker.progress.connect(self._progress)
        self.worker.measuring.connect(self._measure)
        self.worker.finished.connect(self._finished)
        self.setup.setEnabled(False)
        self.start.setEnabled(False)
        self.stop.setEnabled(True)
        self.status.setText(f"Starting {'simulation' if plan.simulate else 'printer sequence'}…")
        self.activeChanged.emit(True)
        self.worker.start()

    def _progress(self, cycle, number, action):
        if self.active and not self.worker.cancel.is_set():
            where = f"Cycle {cycle} of {self.worker.plan.cycles} · Step {number}" if cycle else "Before first cycle"
            self.status.setText(f"{where} · {action}")

    def _measure(self, cycle, number):
        if not self.active or self.worker.cancel.is_set():
            return
        names = self.worker.plan.dials
        for i, field in enumerate(self.reading_fields):
            label = self.prompt_layout.labelForField(field)
            label.setVisible(i < len(names))
            field.setVisible(i < len(names))
            if i < len(names):
                label.setText(f"{names[i]} (mm)")
                field.clear()
        self.status.setText(f"Cycle {cycle} of {self.worker.plan.cycles} · Step {number} · Waiting for readings")
        self.prompt.show()
        self.reading_fields[0].setFocus()

    def _submit(self):
        if self.worker is None or self.worker.cancel.is_set() or self.prompt.isHidden():
            return
        values = []
        for field in self.reading_fields[:len(self.worker.plan.dials)]:
            try:
                values.append(dial_value(field.text()))
            except ValueError:
                self.status.setText("Enter a numeric reading in every visible dial field.")
                field.setFocus()
                return
        self.prompt.hide()
        self.status.setText("Saving readings…")
        self.worker.readings.put(tuple(values))

    def stop_run(self):
        if self.worker is not None:
            self.worker.stop()
            self.prompt.hide()
            self.stop.setEnabled(False)
            self.status.setText("Stopping…")

    def _finished(self):
        worker, self.worker = self.worker, None
        self.status.setText(worker.result)
        worker.deleteLater()
        self.prompt.hide()
        self.setup.setEnabled(True)
        self.start.setEnabled(True)
        self.stop.setEnabled(False)
        self.activeChanged.emit(False)
