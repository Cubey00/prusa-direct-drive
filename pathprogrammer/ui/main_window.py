"""Main window: print plates, printer and calibration | Foto control + model preview + model control
(folded) | points (folded), force graph and run controls."""
import re
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QSettings, Qt, QThread, QTimer, Signal
from PySide6.QtWidgets import (QFileDialog, QGridLayout, QLabel, QMainWindow, QMessageBox, QPushButton,
                               QScrollArea, QSplitter, QVBoxLayout, QWidget)

from .collapsible import CollapsibleBox
from .photo_panel import PhotoPanel
from .sensor_offset import SensorOffsetDialog, offset_text
import machine_settings

from .. import calibration, config, program as prog_mod
from ..executor import ExecutorWorker
from ..face_render import ShadedTopRenderer
from ..hardware import PrinterHardware, SimHardware
from ..ipc import CalibratorServer
from ..model_io import Orientation, face_size, load_model, oriented_mesh
from ..program import Program, RunSettings, TouchMode
from .face_view import FaceView
from .calibration_wizard import CalibrationWizard
from .left_panel import LeftPanel
from .plates_panel import PlatesPanel
from .printer_box import PrinterBox
from .point_model import PointTableModel
from .point_table import PointTable
from .force_graph import ForceGraph
from .run_controls import RunControls
from .run_settings import RunSettingsBox


class MainWindow(QMainWindow):
    calibratorMessage = Signal(dict)      # crosses from the IPC thread to the UI thread

    def __init__(self, settings=None):
        super().__init__()
        self.setWindowTitle("Touch path programmer")
        self.settings = settings or QSettings("TUDelft", "pathprogrammer")
        self.program = Program()
        self.model = None                 # LoadedModel
        self.orientation = Orientation()
        self.mesh = None                  # oriented mesh
        self.transform = None
        self.renderer = ShadedTopRenderer()
        self._render_cache = {}
        self.thread = self.worker = None
        self.wizard = None                # 'Start kalibratie' window, made on first use
        self.model_path = None            # file the current model was opened from

        self.points = PointTableModel(self.program.points)
        self.left = LeftPanel()
        self.plates = PlatesPanel(self.settings)
        self.printer = PrinterBox(self._selected_plate, self.settings)
        self.view = FaceView(self.points)
        self.table = PointTable(self.points)
        self.run = RunControls()
        self.run_settings = RunSettingsBox()
        self.graph = ForceGraph()
        self.photo = PhotoPanel(self.settings)
        self._running_row = None
        self.view.set_selection_model(self.table.selection_model())
        self.run_settings.set_settings(self.program.settings)

        # centre: Foto control, a small model preview and the (folded) model controls
        centre = QWidget()
        cl = QVBoxLayout(centre)
        cl.setContentsMargins(4, 4, 4, 4)
        cl.addWidget(self.photo, 3)
        view_box = QWidget()
        vl = QVBoxLayout(view_box)
        vl.setContentsMargins(0, 0, 0, 0)
        title = QLabel("Model")
        title.setStyleSheet("font-weight: 600; color: #334155;")
        vl.addWidget(title)
        vl.addWidget(self.view, 1)
        self.view.setMinimumHeight(160)
        cl.addWidget(view_box, 2)
        model_ctl = QWidget()
        mcl = QVBoxLayout(model_ctl)
        mcl.setContentsMargins(6, 0, 6, 6)
        ctl = QGridLayout()
        self.rot_btns = {}
        for key, text, r, c in (("up", "^", 0, 2), ("left", "<", 1, 1), ("spin", "rotate", 1, 2),
                                ("right", ">", 1, 3), ("down", "v", 2, 2)):
            b = QPushButton(text)
            b.setFixedWidth(70)
            ctl.addWidget(b, r, c)
            self.rot_btns[key] = b
            b.clicked.connect(lambda _=False, k=key: self.rotate(k))
        fit = QPushButton("Fit view")
        fit.clicked.connect(self.view.fit)
        ctl.addWidget(fit, 2, 0)
        ctl.setColumnStretch(4, 1)
        ctl.addWidget(self.run_settings, 0, 5, 3, 1)
        hint = QLabel("Click the face to add a point, drag to move it. Wheel zooms, middle-drag pans.")
        hint.setStyleSheet("color: gray;")
        mcl.addLayout(ctl)
        mcl.addWidget(hint)
        mcl.addWidget(self.left)
        self.model_box = CollapsibleBox("Model control", model_ctl, expanded=False,
                                        settings=self.settings, key="ui/model_control_open")
        cl.addWidget(self.model_box)

        # right: points (folded), force graph, run controls
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(4, 4, 4, 4)
        self.table.title_label.hide()
        self.points_box = CollapsibleBox("Points in ascending order", self.table, expanded=False,
                                         settings=self.settings, key="ui/points_open")
        rl.addWidget(self.points_box)
        rl.addWidget(self.graph, 1)
        run_box = QWidget()
        run_box.setObjectName("runBox")
        run_box.setStyleSheet("QWidget#runBox { background: #ffffff; border: 1px solid #dde2ea; "
                              "border-radius: 8px; }")
        QVBoxLayout(run_box).addWidget(self.run)
        rl.addWidget(run_box)

        # left: print plates, printer, calibration
        left_col = QWidget()
        lcl = QVBoxLayout(left_col)
        lcl.setContentsMargins(4, 4, 4, 4)
        lcl.addWidget(self.plates)
        lcl.addWidget(self.printer)
        lcl.addWidget(self.left.cal_box)
        lcl.addStretch()
        left_scroll = QScrollArea()
        left_scroll.setWidget(left_col)
        left_scroll.setWidgetResizable(True)
        left_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        split = QSplitter(Qt.Horizontal)
        for w in (left_scroll, centre, right):
            split.addWidget(w)
        split.setSizes([430, 760, 430])
        self.setCentralWidget(split)

        # wiring
        self.left.open_btn.clicked.connect(self.open_model_dialog)
        self.left.save_folder_btn.clicked.connect(self.choose_save_folder)
        self.left.runs_folder_btn.clicked.connect(self.choose_runs_folder)
        self.left.calib_btn.clicked.connect(self.start_calibration)
        self.left.offset_btn.clicked.connect(self.edit_sensor_offset)
        self.left.calibrationChanged.connect(self.refit)
        self.left.faceSizeChanged.connect(self.refit)
        self.view.faceClicked.connect(self.add_point_at)
        self.view.pointMoved.connect(self.points.set_xy)
        self.view.pointSelected.connect(self.table.select_row)
        # deferred: the marker that asked is destroyed by the rebuild
        self.view.deleteRequested.connect(lambda r: QTimer.singleShot(0, lambda: self.table.delete_row(r)))
        self.table.add_btn.clicked.connect(self.add_point_button)
        self.run.execute_btn.clicked.connect(self.execute)
        self.run.stop_btn.clicked.connect(self.stop)
        self.run.load_btn.clicked.connect(self.load_program_dialog)
        self.run.save_btn.clicked.connect(self.save_program_dialog)
        self.calibratorMessage.connect(self.on_calibrator_message)
        self.plates.openPlateRequested.connect(self.open_plate)
        self.plates.plate_list.currentRowChanged.connect(lambda _: self.printer.refresh())
        self.run_settings.changed.connect(self.update_graph_lines)
        self.table.selection_model().selectionChanged.connect(self.update_graph_lines)
        self.points.pointsChanged.connect(self.update_graph_lines)
        self.points.pointsChanged.connect(self._reset_progress_if_stale)

        self.server = CalibratorServer(self.calibratorMessage.emit)
        self.left.folder_label.setText(f"Programs folder: {self.save_folder()}")
        self.left.runs_folder_label.setText(f"Runs folder: {self.runs_folder()}")
        self.refit()
        self.update_graph_lines()
        self.plates.restore_last()        # the last plates again, the selected one opened as the model
        self.show_sensor_offset()
        geo = self.settings.value("geometry")
        if geo is not None:
            self.restoreGeometry(geo)
        else:
            self.resize(1600, 900)

    def _selected_plate(self):
        """(G-code path or None, label) of the plate selected in the Print plates box."""
        item = self.plates.plate_list.currentItem()
        if item is None or self.plates.result is None:
            return None, ""
        dataset = Path(self.plates.result["out"]).name
        return self.plates.selected_gcode(), f"{dataset} {item.text().split(':')[0].lower()}"

    # ---------------- model ----------------
    def save_folder(self):
        return self.settings.value("save_folder", str(config.PROJECT_DIR))

    def choose_save_folder(self):
        path = QFileDialog.getExistingDirectory(self, "Select programs folder", self.save_folder())
        if path:
            self.settings.setValue("save_folder", path)
            self.left.folder_label.setText(f"Programs folder: {path}")

    def runs_folder(self):
        return self.settings.value("runs_folder", str(config.RUNS_DIR))

    def choose_runs_folder(self):
        path = QFileDialog.getExistingDirectory(self, "Select runs folder", self.runs_folder())
        if path:
            self.settings.setValue("runs_folder", path)
            self.left.runs_folder_label.setText(f"Runs folder: {path}")

    def new_run_dir(self):
        """(folder, name): the typed name made filesystem-safe, a timestamp if empty, _2, _3... if taken."""
        name = re.sub(r'[<>:"/\|?*\x00-\x1f]', "_", self.left.run_name.text()).strip(" .")
        name = name or f"run_{datetime.now():%Y%m%d_%H%M%S}"
        base, n, d = name, 1, Path(self.runs_folder()) / name
        while d.exists() and any(d.iterdir()):
            n += 1
            name = f"{base}_{n}"
            d = Path(self.runs_folder()) / name
        return d, name

    def open_model_dialog(self):
        start = self.settings.value("model_folder", str(config.PROJECT_DIR))
        path, _ = QFileDialog.getOpenFileName(self, "Open model", start,
                                              "Models (*.3mf *.stl);;3MF models (*.3mf);;"
                                              "STL models (*.stl);;All files (*)")
        if path:
            self.settings.setValue("model_folder", str(Path(path).parent))
            self.open_model(path)

    def open_plate(self, path):
        """A plate selected in the Print plates box: open it, unless it already is."""
        if self.model is not None and self.model_path == Path(path):
            return True
        return self.open_model(path)

    def open_model(self, path, orientation=None):
        try:
            self.model = load_model(path)
        except Exception as e:
            QMessageBox.critical(self, "Open model", f"Could not load {path}:\n{e}")
            return False
        self._render_cache.clear()
        self.model_path = Path(path)
        self.orientation = orientation or Orientation()
        self.left.file_label.setText(self.model.filename)
        self.left.units_label.setText(self.model.units_note)
        self.update_face()
        if self.orientation == Orientation():
            self.fill_fiducials_from_manifest(path)
        if self.wizard is not None:
            self.wizard.refresh()
        return True

    def fill_fiducials_from_manifest(self, model_path):
        """A plate 3MF with its manifest next to it: put the fiducial tips in as face points
        (machine points are kept). Returns True if it did."""
        manifest = calibration.manifest_for(model_path)
        if manifest is None:
            return False
        face = calibration.face_points_from_manifest(manifest, self.model.mesh.bounds)
        cal = self.left.cal_points()
        cal.face.update(face)
        self.left.set_cal_points(cal)
        self.left.link_label.setText(f"Plaat geladen ({Path(model_path).with_suffix('.json').name}); "
                                     "klik Start kalibratie")
        return True

    def update_face(self):
        self.mesh = oriented_mesh(self.model.mesh, self.orientation)
        key = tuple(map(tuple, self.orientation.to_list()))
        if key not in self._render_cache:
            self._render_cache[key] = self.renderer.render(self.mesh)
        self.left.set_face_size(*face_size(self.mesh))
        self.view.set_face(self._render_cache[key])
        self.refit()

    def rotate(self, key):
        if self.model is None:
            return
        if self.points.rowCount():
            r = QMessageBox.question(self, "Rotate model",
                                     "Point coordinates belong to the current face. Rotating clears all points. Continue?")
            if r != QMessageBox.Yes:
                return
            self.points.clear()
        {"up": self.orientation.tilt_up, "down": self.orientation.tilt_down, "left": self.orientation.turn_left,
         "right": self.orientation.turn_right, "spin": self.orientation.spin}[key]()
        self.update_face()

    # ---------------- calibration ----------------
    def refit(self):
        cal = self.left.cal_points()
        for name in calibration.POINTS:
            self.view.set_cal_point(name, cal.face[name])
        issues = calibration.validate(cal)
        if any(i.level == "error" for i in issues):
            self.transform = None
            self.left.set_diagnostics("\n".join(i.msg for i in issues), "error")
            return
        self.transform = calibration.fit(cal)
        t = self.transform
        res = calibration.residuals(cal, t)
        text = (f"Scale {t.scale:.4f}, rotation {t.rotation_deg:.2f} deg\n"
                f"Face origin -> X{t.b.real:.2f} Y{t.b.imag:.2f}\n"
                "Residuals: " + ", ".join(f"{p} {r:.2f} mm" for p, r in res.items()))
        if issues:
            text += "\n" + "\n".join(i.msg for i in issues)
        self.left.set_diagnostics(text, "warning" if issues else "ok")

    def show_sensor_offset(self, settings=None):
        """Nozzle -> sensor offset from machine_settings.toml above the calibration button."""
        try:
            settings = settings or machine_settings.load()
        except machine_settings.SettingsError as e:
            self.left.offset_label.setText(f"<span style='color:#c00000'>machine_settings.toml: {e}</span>")
            return
        self.left.offset_label.setText(offset_text(settings.sensor))

    def edit_sensor_offset(self):
        try:
            settings = machine_settings.load()
        except machine_settings.SettingsError as e:
            QMessageBox.warning(self, "Sensorafstand", str(e))
            return
        dlg = SensorOffsetDialog(settings, self)
        if dlg.exec() and dlg.saved is not None:
            self.show_sensor_offset(dlg.saved)
            if self.wizard is not None:
                self.wizard.settings = dlg.saved
                self.wizard.refresh()

    def start_calibration(self):
        """'Start kalibratie': the step-by-step window; it starts the calibrator when needed."""
        if self.wizard is None:
            self.wizard = CalibrationWizard(self)
            self.wizard.restart()             # a new calibration: old machine points no longer count
        elif self.wizard.state == "done":
            self.wizard.restart()
        self.wizard.refresh()
        self.wizard.show()
        self.wizard.raise_()
        self.wizard.activateWindow()
        if not self.server.is_running() and self.thread is None:
            self.open_calibrator()

    def open_calibrator(self):
        if self.server.is_running():
            QMessageBox.information(self, "Calibrator", "The visual calibrator is already open.")
            return
        if self.thread is not None:
            QMessageBox.warning(self, "Calibrator", "A program is running; the printer is in use.")
            return
        try:
            self.server.launch(config.GUI_SCRIPT)
        except Exception as e:
            QMessageBox.critical(self, "Calibrator", f"Could not start {config.GUI_SCRIPT.name}:\n{e}")
            return
        self.left.link_label.setText("Calibrator starting...")

    def on_calibrator_message(self, msg):
        kind = msg.get("type")
        if kind == "hello":
            self.left.link_label.setText("Calibrator verbonden: volg de stappen in het kalibratievenster")
        elif kind == "bye":
            self.left.link_label.setText("Calibrator gesloten")
        elif kind == "position" and msg.get("point") in calibration.POINTS:
            self.left.set_machine_point(msg["point"], msg["x"], msg["y"])
            self.left.link_label.setText(f"{msg['point']} ontvangen: "
                                         f"X{msg['x']:.2f} Y{msg['y']:.2f} Z{msg['z']:.2f}")
        if self.wizard is not None:
            self.wizard.on_message(msg)
            if self.wizard.state == "done":
                self.show_sensor_offset(self.wizard.settings)    # a good calibration saves a new one

    # ---------------- points ----------------
    def add_point_at(self, x, y):
        row = self.points.add(x, y, self.table.template())
        self.table.select_row(row)

    def add_point_button(self):
        if self.model is None:
            QMessageBox.information(self, "Add point", "Open a model first.")
            return
        w, h = self.left.face_size()
        last = self.points.points[-1] if self.points.points else None
        x, y = (min(last.x + 5, w), last.y) if last else (w / 2, h / 2)
        self.add_point_at(x, y)

    # ---------------- program files ----------------
    def collect_program(self):
        p = self.program
        p.settings = RunSettings(**self.run_settings.settings_values())
        p.model = prog_mod.ModelRef(self.model.filename if self.model else "",
                                    self.model.sha256 if self.model else "",
                                    self.orientation.to_list(), tuple(self.left.face_size()))
        cal = self.left.cal_points()
        p.calibration = cal if any(v is not None for d in (cal.face, cal.machine) for v in d.values()) else None
        p.transform = self.transform
        return p

    def save_program_dialog(self):
        path, _ = QFileDialog.getSaveFileName(self, "Save program as", self.save_folder(), "Program (*.json)")
        if not path:
            return
        try:
            prog_mod.save(self.collect_program(), path)
        except Exception as e:
            QMessageBox.critical(self, "Save program", str(e))
            return
        self.run.status.setText(f"Saved {Path(path).name}")

    def load_program_dialog(self):
        path, _ = QFileDialog.getOpenFileName(self, "Load program", self.save_folder(), "Program (*.json)")
        if path:
            self.load_program(path)

    def load_program(self, path, ask=True):
        try:
            loaded = prog_mod.load(path)
        except Exception as e:
            QMessageBox.critical(self, "Load program", f"Could not read {path}:\n{e}")
            return
        warnings = []
        if self.model is None:
            candidate = Path(path).parent / loaded.model.filename
            if loaded.model.filename and candidate.exists():
                self.open_model(candidate)
        if self.model is not None:
            warnings = prog_mod.compare_model(loaded, self.model.filename, self.model.sha256,
                                              self.orientation.to_list())
            if loaded.model.orientation != self.orientation.to_list():
                self.orientation = Orientation.from_list(loaded.model.orientation)
                self.update_face()
        self.points.set_points(loaded.points)
        self.run_settings.set_settings(loaded.settings)
        if loaded.calibration is not None:
            use = True
            current = self.left.cal_points()
            if ask and current.complete() and current.to_dict() != loaded.calibration.to_dict():
                use = QMessageBox.question(self, "Calibration",
                                           "The program contains a calibration. Use it instead of the current one?"
                                           ) == QMessageBox.Yes
            if use:
                self.left.set_cal_points(loaded.calibration)
        if warnings and ask:
            QMessageBox.warning(self, "Load program", "\n".join(warnings))
        self.run.status.setText(f"Loaded {Path(path).name}")

    # ---------------- execution ----------------
    def preflight(self):
        errors = []
        if self.model is None:
            errors.append("No model loaded.")
        if self.transform is None:
            errors.append("Calibration is incomplete or invalid.")
        prog = self.collect_program()
        errors += [i.msg for i in prog_mod.validate(prog, self.left.face_size(), self.transform)]
        return prog, errors

    def execute(self):
        if self.thread is not None:
            return
        prog, errors = self.preflight()
        if errors:
            QMessageBox.warning(self, "Cannot execute", "\n".join(errors))
            return
        simulate = self.run.simulate.isChecked()
        if not simulate and self.server.is_running():
            r = QMessageBox.question(self, "Kalibratie open",
                                     "De kalibratie gebruikt de printerverbinding nog. Nu sluiten?")
            if r != QMessageBox.Yes:
                return
            self.server.quit_calibrator()
        xs, ys = zip(*(self.transform.face_to_machine(p.x, p.y) for p in prog.points))
        run_dir, run_name = self.new_run_dir()
        n_images = sum(p.n_images() for p in prog.points)
        msg = (f"Run {len(prog.points)} points{' (SIMULATION)' if simulate else ''}?\n\n"
               f"Run folder: {run_dir}\n{n_images} images planned.\n\n"
               f"Machine X {min(xs):.1f} - {max(xs):.1f} mm, Y {min(ys):.1f} - {max(ys):.1f} mm.\n"
               "The bed's CURRENT Z will be the travel height: make sure the head clears the part.")
        if QMessageBox.question(self, "Execute program", msg) != QMessageBox.Yes:
            return

        use_camera = not simulate and n_images > 0
        if use_camera:
            self.photo.release()              # one owner at a time: the run opens the camera itself
        hw = SimHardware(on_status=print) if simulate else PrinterHardware(on_status=print, use_camera=use_camera)
        self.points.clear_states()
        self.run.progress.setRange(0, len(prog.points))
        self.run.progress.setValue(0)
        self.set_running(True)
        self.thread = QThread(self)
        self.worker = ExecutorWorker(prog, self.transform, hw, run_dir, self.mesh, run_name)
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        self.worker.status.connect(self.run.status.setText)
        self.worker.pointStarted.connect(self.on_point_started)
        self.worker.pointFinished.connect(self.on_point_finished)
        self.worker.finished.connect(self.on_run_finished)
        self.graph.set_source(getattr(hw, "force_trace", None))
        self.thread.start()

    def update_graph_lines(self, *_):
        """Solid line at the next point's max force (force points only), dotted at the contact threshold."""
        pts = self.points.points
        row = self._running_row if self._running_row is not None else self.table.current_row()
        if row is None or not 0 <= row < len(pts):
            row = 0 if pts else None
        p = pts[row] if row is not None else None
        max_g = p.value if p is not None and p.mode is TouchMode.FORCE else None
        self.graph.set_lines(max_g, self.run_settings.contact.value())

    def _reset_progress_if_stale(self):
        """Editing the program (no run active) discards the previous run's progress."""
        if self.thread is None and not self.points.states:
            self.run.progress.setRange(0, 1)
            self.run.progress.setValue(0)

    def on_point_started(self, i):
        self._running_row = i
        self.update_graph_lines()
        self.points.set_state(i, "running")
        self.view.rebuild_points()

    def on_point_finished(self, i, res):
        self.points.set_state(i, "ok" if res.status == "ok" else "failed")
        self.run.progress.setValue(i + 1 if res.status == "ok" else i)
        self.view.rebuild_points()

    def on_run_finished(self, summary):
        self.thread.quit()
        self.thread.wait()
        self.thread = self.worker = None
        self.graph.set_source(None)
        self.photo.resume()
        self._running_row = None
        self.update_graph_lines()
        self.set_running(False)
        self.run.status.setText(f"{summary.outcome}: {summary.message}\nRun folder: {summary.log_path}")
        if summary.outcome != "completed":
            QMessageBox.warning(self, "Run ended", f"{summary.outcome}: {summary.message}")

    def stop(self):
        if self.worker is not None:
            self.worker.abort()
            self.run.status.setText("STOP requested")
        elif not self.run.simulate.isChecked() and not self.server.is_running():
            PrinterHardware().stop()           # nothing running here, but stop the printer anyway

    def set_running(self, on):
        self.run.set_running(on)
        self.table.set_editable(not on)
        self.view.set_editable(not on)
        self.left.setEnabled(not on)
        self.left.cal_box.setEnabled(not on)
        self.run_settings.setEnabled(not on)
        for b in self.rot_btns.values():
            b.setEnabled(not on)

    def closeEvent(self, e):
        if self.worker is not None:
            if QMessageBox.question(self, "Quit", "A program is running. Stop it and quit?") != QMessageBox.Yes:
                e.ignore()
                return
            self.worker.abort()
            self.thread.quit()
            self.thread.wait(5000)
        if self.plates.is_busy():
            if QMessageBox.question(self, "Quit", "Plates are still being sliced. Wait for it and quit?"
                                    ) != QMessageBox.Yes:
                e.ignore()
                return
            self.plates.shutdown()
        self.printer.shutdown()
        self.photo.shutdown()
        self.settings.setValue("geometry", self.saveGeometry())
        self.server.quit_calibrator()
        self.server.close()
        super().closeEvent(e)
