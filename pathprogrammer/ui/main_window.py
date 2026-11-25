"""Main window: print plates, printer and calibration | Foto control + model preview + model control
(folded) | points (folded), force graph and run controls."""
import json
import re
from datetime import datetime
from collections import deque
from pathlib import Path

from PySide6.QtCore import QSettings, Qt, QThread, QTimer, Signal
from PySide6.QtWidgets import (QFileDialog, QGridLayout, QLabel, QMainWindow, QMessageBox, QPushButton,
                               QScrollArea, QSplitter, QTabWidget, QVBoxLayout, QWidget)

from .collapsible import CollapsibleBox
from .photo_panel import PhotoPanel
from .measure_box import MeasureBox
from .. import measure
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
from .calibration_wizard import HOME_LABEL, CalibrationWizard
from ..ipc import GUARD_STATUS
from .left_panel import LeftPanel
from .plates_panel import PlatesPanel
from .printer_box import PrinterBox
from .point_model import PointTableModel
from .point_table import PointTable
from .force_graph import ForceGraph
from .run_controls import RunControls
from .run_settings import RunSettingsBox
from .focusfield_panel import FocusFieldPanel


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
        self.wizard = None                # 'Start calibration' window, made on first use
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
        self._live_force = deque(maxlen=300)   # calibrator's force stream: 30 s at 10/s
        self.graph.set_source(self._live_source)
        self.photo = PhotoPanel(self.settings)
        self.measure = MeasureBox()
        self._pending_goto = None         # goto for Measure, sent once the calibrator says hello
        self._measuring = None            # label of the Measure move under way
        self._needs_home = True           # X/Y position unknown until the calibrator homed them
        self._hovering = False            # the sensor hovers above the point (nothing moved since)
        self._contact = None              # first touch: {"object", "z0", "depth_mm", "force_g"}
        self._proc = None                 # 'Do whole measuring procedure' under way: its state (dict)
        self._timeout_token = 0           # only the newest Measure timeout counts
        self._last_touch_status = None    # status of the last touch_done (ok / limit / ...)
        self._point_index = None          # Measure: index of the part the sensor was last sent to (reached)
        self._target_index = 0
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
        rl.addWidget(self.measure)
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
        # Keep the controls within the viewport even when the vertical scrollbar is shown.
        left_scroll.setMinimumWidth(left_col.minimumSizeHint().width()
                                    + left_scroll.verticalScrollBar().sizeHint().width()
                                    + 2 * left_scroll.frameWidth())
        split = QSplitter(Qt.Horizontal)
        for w in (left_scroll, centre, right):
            split.addWidget(w)
        split.setCollapsible(0, False)
        split.setSizes([430, 760, 430])
        self.focusfield = FocusFieldPanel(self)
        self.focusfield.hardwareActivityChanged.connect(self._optical_activity)
        tabs = QTabWidget()
        tabs.addTab(split, "Print, calibration & touch")
        tabs.addTab(self.focusfield, "Focus-Field MTF")
        self.setCentralWidget(tabs)

        # wiring
        self.left.open_btn.clicked.connect(self.open_model_dialog)
        self.left.save_folder_btn.clicked.connect(self.choose_save_folder)
        self.left.runs_folder_btn.clicked.connect(self.choose_runs_folder)
        self.left.calib_btn.clicked.connect(self.start_calibration)
        self.left.offset_btn.clicked.connect(self.edit_sensor_offset)
        self.measure.hover_btn.clicked.connect(lambda: self.hover_above_point(0))
        self.measure.touch_btn.clicked.connect(self.creep_to_object)
        self.measure.step_btn.clicked.connect(self.press_deeper)
        self.measure.next_btn.clicked.connect(self.next_point)
        self.measure.proc_btn.clicked.connect(self.start_procedure)
        self.photo.photoSaved.connect(self._on_procedure_photo)
        self._load_procedure_settings()
        for w in (self.measure.max_force, self.measure.step_mm, self.measure.n_steps):
            w.valueChanged.connect(self._save_procedure_settings)
        self.photo.tag_provider = self._photo_tag
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
        self._point_index = None
        self._update_next_button(self._measuring is None)
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
        self.left.link_label.setText(f"Plate loaded ({Path(model_path).with_suffix('.json').name}); "
                                     "click Start calibration")
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
            QMessageBox.warning(self, "Sensor offset", str(e))
            return
        dlg = SensorOffsetDialog(settings, self)
        if dlg.exec() and dlg.saved is not None:
            self.show_sensor_offset(dlg.saved)
            if self.wizard is not None:
                self.wizard.settings = dlg.saved
                self.wizard.refresh()

    def start_calibration(self):
        """'Start calibration': the step-by-step window; it starts the calibrator when needed."""
        if self.focusfield.hardware_active:
            QMessageBox.warning(self, "Printer in use", "An optical scan owns the printer and camera.")
            return
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
        if self.focusfield.hardware_active:
            QMessageBox.warning(self, "Printer in use", "An optical scan owns the printer and camera.")
            return
        if self.server.is_running():
            QMessageBox.information(self, "Calibrator", "The visual calibrator is already open.")
            return
        if self.thread is not None:
            QMessageBox.warning(self, "Calibrator", "A program is running; the printer is in use.")
            return
        try:
            self.server.launch(config.GUI_SCRIPT)
            self._needs_home = True           # a new calibrator: home X/Y before driving anywhere
        except Exception as e:
            QMessageBox.critical(self, "Calibrator", f"Could not start {config.GUI_SCRIPT.name}:\n{e}")
            return
        self.left.link_label.setText("Calibrator starting...")

    # ---------------- Measure ----------------
    MEASURE_ANSWER_MS = 120000            # homing X/Y + the move

    @staticmethod
    def _point_label(index):
        return f"point {index + 1}"

    def hover_above_point(self, index=0):
        """'Hover 3 mm above point 1' (index 0) / 'Go to next point': sensor measure.HOVER_MM above part
        `index` of the opened plate. The calibrator lifts to the travel height (clear of every
        part and cone) before it drives X/Y, then goes down to just above the part."""
        if self.focusfield.hardware_active or self.thread is not None or self._measuring:
            return
        manifest = calibration.manifest_for(self.model_path) if self.model_path else None
        try:
            settings = machine_settings.load()
            t = measure.hover_target(manifest, settings, self.left.cal_points(), part_index=index)
        except (measure.MeasureError, machine_settings.SettingsError) as e:
            self.measure.set_status(str(e), "error")
            return
        self._hover_target = t
        self._target_index = index
        label = self._point_label(index)
        args = (t.x, t.y, t.travel_z, label)
        self._measuring = label
        self._set_measure_buttons(False)
        self._hovering = False
        self._set_contact(None)           # a new spot: the indentation starts again at the next touch
        self._arm_measure_timeout(self.MEASURE_ANSWER_MS)
        where = f"{label} ({t.part_id}, bed X{t.bed_x:g} Y{t.bed_y:g})"
        if self.server.is_running() and self._send_measure_goto(args):
            self.measure.set_status(f"On the way to {where}...", "busy")
            return
        self._pending_goto = args
        if not self.server.is_running():
            self.open_calibrator()
        self.measure.set_status(f"Starting the printer connection; then the head goes to {where}.", "busy")

    CREEP_MAX_MM = measure.HOVER_MM + 5.0  # never more than this far up (the hover gap + 5 mm)
    CREEP_FREE_MM = measure.CREEP_FREE_MM  # of the hover gap: known free, a bit faster
    CREEP_ANSWER_MS = 240000

    def creep_to_object(self):
        """'Move very slowly until the sensor touches the object': the calibrator creeps the bed up
        and stops on the load cell's first contact."""
        if self.focusfield.hardware_active or self.thread is not None or self._measuring:
            return
        if not self.server.is_running():
            self.measure.set_status(f"Hover {measure.HOVER_TEXT} above point 1 first: there is no connection to "
                                    "the printer yet.",
                                    "error")
            return
        fast = self.CREEP_FREE_MM if self._hovering else 0.0
        if not self.server.creep(fast, self.CREEP_MAX_MM):
            self.measure.set_status("The printer connection does not respond; try again.", "error")
            return
        self._measuring = "touch"
        self._set_measure_buttons(False)
        self._arm_measure_timeout(self.CREEP_ANSWER_MS)
        self.measure.set_status("The bed rises very slowly until the sensor feels something... (STOP stops it)",
                                "busy")

    PRESS_STEP_MM = 0.1
    PRESS_STOP_G = 300.0
    STEP_ANSWER_MS = 30000

    def press_deeper(self):
        """'Go 0.1 mm down': the sensor presses 0.1 mm deeper, stopping above 300 g.
        Only after a first touch: the indentation counts from there (0 at the first contact)."""
        if self.focusfield.hardware_active or self.thread is not None or self._measuring:
            return
        if self._contact is None:
            self.measure.set_status("Touch the object first with 'Move very slowly until the sensor touches the "
                                    "object': the indentation counts from that first touch (0 mm).", "error")
            return
        self._start_step(self.PRESS_STEP_MM, self.PRESS_STOP_G)

    def _start_step(self, mm, stop_g):
        if not self.server.is_running() or not self.server.step(mm, stop_g):
            self.measure.set_status("The printer connection does not respond; try again.", "error")
            if self._proc is not None:
                self._procedure_finish("The printer connection does not respond.", "error")
            return False
        self._step_mm = mm
        self._measuring = "step"
        self._set_measure_buttons(False)
        self._arm_measure_timeout(self.STEP_ANSWER_MS)
        self.measure.set_status(f"{mm:g} mm deeper... (stops above {stop_g:g} g)", "busy")
        return True

    # ---------------- whole measuring procedure ----------------
    PROC_SETTLE_MS = 500                  # after a move: let the bed stand still before the photo
    PHOTO_ANSWER_MS = 20000
    PROC_KEYS = (("measure/max_force_g", "max_force", 300.0), ("measure/step_mm", "step_mm", 0.1),
                 ("measure/steps", "n_steps", 10))

    def _load_procedure_settings(self):
        for key, attr, default in self.PROC_KEYS:
            w = getattr(self.measure, attr)
            try:
                w.setValue(type(w.value())(self.settings.value(key, default)))
            except (TypeError, ValueError):
                w.setValue(default)

    def _save_procedure_settings(self, *_):
        for key, attr, _default in self.PROC_KEYS:
            self.settings.setValue(key, getattr(self.measure, attr).value())

    def start_procedure(self):
        """'Do whole measuring procedure': home X/Y (the saved calibration is used), then for EVERY
        part of the plate in manifest order: just above it, first touch (indentation 0, photo),
        `steps` x `step_mm` deeper with a photo at each step, stopping early at the max force, then
        up to the travel height and on to the next part. A part without contact or out of reach is
        skipped (noted in procedure.json). At the end the sensor goes back to just above the last
        part. All photos, measurements.csv and procedure.json go into one new folder
        <photo folder>/<date_start time>."""
        if self.focusfield.hardware_active or self.thread is not None or self._measuring or self._proc:
            return
        if not self.photo.feed.connected:
            self.measure.set_status("No camera connected: the procedure takes a photo at every step.", "error")
            return
        manifest = calibration.manifest_for(self.model_path) if self.model_path else None
        try:
            settings = machine_settings.load()
            t = measure.hover_target(manifest, settings, self.left.cal_points(), part_index=0)
        except measure.MeasureError as e:
            if not (manifest and manifest.get("parts")) or "out of reach" not in str(e):
                self.measure.set_status(str(e), "error")
                return
            t = None
        except machine_settings.SettingsError as e:
            self.measure.set_status(str(e), "error")
            return
        # which parts can the sensor reach? the others are skipped
        points, skipped = [], []
        for i, part in enumerate(manifest["parts"]):
            try:
                ti = measure.hover_target(manifest, settings, self.left.cal_points(), part_index=i)
                points.append(i)
                t = t or ti
            except measure.MeasureError as e:
                skipped.append({"point": i + 1, "object": part["id"], "result": f"skipped: {e}"})
        if not points:
            self.measure.set_status("No part of this plate is within reach of the sensor.", "error")
            return
        stop_g = self.measure.max_force.value()
        if stop_g >= settings.forces.max_force_g:
            self.measure.set_status(f"Max force must stay below {settings.forces.max_force_g:g} g "
                                    "([forces] max_force_g).", "error")
            return
        start = datetime.now()
        self._proc = {"stage": "home", "start": start, "travel_z": t.travel_z, "source": t.source,
                      "folder": self.photo.folder() / start.strftime("%Y-%m-%d_%H-%M-%S"),
                      "stop_g": stop_g, "step_mm": self.measure.step_mm.value(),
                      "steps": self.measure.n_steps.value(), "queue": points, "n_points": len(points),
                      "point": None, "done": 0, "photos": [], "points": list(skipped),
                      "photo_name": None, "last_depth": None, "at_limit": False, "wait_hello": False}
        self._set_measure_buttons(False)
        self._set_contact(None)
        self._measuring = HOME_LABEL
        self._arm_measure_timeout(self.MEASURE_ANSWER_MS)
        if self.server.is_running():
            self._procedure_send_home()
        else:
            self._proc["wait_hello"] = True
            self.open_calibrator()
            self.measure.set_status("Procedure: starting the printer connection...", "busy")

    def _procedure_send_home(self):
        p = self._proc
        if self.server.home(p["travel_z"], HOME_LABEL):
            self.measure.set_status("Procedure: bed to a safe height, then homing X and Y...", "busy")
        else:
            self._measure_done("The printer connection does not respond.", "error")

    def _procedure_where(self):
        p = self._proc
        done = p["n_points"] - len(p["queue"])
        obj = self._hover_target.part_id if getattr(self, "_hover_target", None) is not None else ""
        return f"Part {done} of {p['n_points']} (point {p['point'] + 1}, {obj})"

    def _procedure_next_point(self):
        """On to the next part (the calibrator lifts to the travel height first), or the end."""
        p = self._proc
        if not p["queue"]:
            self._procedure_finish("All parts measured.", "ok")
            return
        p["point"] = p["queue"].pop(0)
        p.update(stage="hover", done=0, last_depth=None, at_limit=False)
        p["points"].append({"point": p["point"] + 1, "object": None, "result": "started", "steps_done": 0,
                            "photos": 0})
        self.hover_above_point(p["point"])
        if self._measuring is None:
            self._procedure_finish(f"Could not go to point {p['point'] + 1}.", "error", retract=False)
        else:
            self.measure.set_status(f"{self._procedure_where()}: to {measure.HOVER_TEXT} above it...", "busy")

    def _procedure_point_done(self, result):
        p = self._proc
        rec = p["points"][-1]
        rec.update(result=result, steps_done=p["done"])
        self._write_procedure_log(p, "running")
        self._procedure_next_point()

    def _procedure_next(self, level, text):
        """Called after every move of the procedure (and its result)."""
        p = self._proc
        if p is None:
            return
        stage = p["stage"]
        if stage == "step" and level == "error" and self._last_touch_status == "limit":
            p["at_limit"] = True                  # max force reached: photo of this spot (if it moved), next part
            if self._contact is not None and p["last_depth"] is not None \
                    and abs(self._contact["depth_mm"] - p["last_depth"]) < 1e-4:
                self._procedure_point_done(f"max force {p['stop_g']:g} g reached")
            else:
                self._procedure_photo()
            return
        if stage == "touch" and level == "error" and self._last_touch_status == "no_contact":
            self._procedure_point_done(f"skipped: {text}")     # e.g. a part that did not print
            return
        if level == "error":
            self._procedure_finish(f"Stopped ({stage}): {text}", "error", retract=False)
            return
        if stage == "home":
            self._procedure_next_point()
        elif stage == "hover":
            p["points"][-1]["object"] = self._hover_target.part_id
            p["stage"] = "touch"
            self.creep_to_object()
            if self._measuring is None:
                self._procedure_finish("Could not start the touch.", "error", retract=False)
            else:
                self.measure.set_status(f"{self._procedure_where()}: very slowly up until the sensor "
                                        "touches it...", "busy")
        elif stage == "touch":
            if self._contact is None:
                self._procedure_finish("No first touch.", "error", retract=False)
                return
            self._procedure_photo()
        elif stage == "step":
            p["done"] += 1
            self._procedure_photo()
        elif stage == "retract":
            self._procedure_finish(p.get("result", "Done."), p.get("result_level", "ok"), retract=False)

    def _procedure_photo(self):
        self._proc["stage"] = "photo"
        QTimer.singleShot(self.PROC_SETTLE_MS, self, self._procedure_snap)

    def _procedure_snap(self):
        p = self._proc
        if p is None or p["stage"] != "photo":
            return
        tag = self._photo_tag()
        name = self.photo.take_tagged(p["folder"], tag) if tag else None
        if name is None:
            self._procedure_finish("No camera connected: photo not taken.", "error")
            return
        p["photo_name"], p["last_depth"] = name, tag["depth_mm"]
        c, t = self._contact or {}, self._hover_target
        z0 = c.get("z0")
        p["pending"] = {
            "file": name, "time": datetime.now().isoformat(timespec="milliseconds"),
            "point": p["point"] + 1, "object": tag["object"], "step": p["done"],
            "indentation_mm": round(tag["depth_mm"], 4),
            "force_g": round(tag["force_g"], 2), "force_settled_g": round(c.get("force_g", 0.0), 2),
            "head_x": round(t.x, 3), "head_y": round(t.y, 3),
            "head_z": None if z0 is None else round(z0 - tag["depth_mm"], 4),
            "contact_z": None if z0 is None else round(z0, 4),
            "bed_x": t.bed_x, "bed_y": t.bed_y, "part_top_z": t.top_z,
            "at_max_force": bool(p["at_limit"])}
        token = self._timeout_token = self._timeout_token + 1
        QTimer.singleShot(self.PHOTO_ANSWER_MS, self, lambda: self._procedure_photo_timed_out(token))

    def _procedure_photo_timed_out(self, token):
        p = self._proc
        if p is not None and p["stage"] == "photo" and token == self._timeout_token:
            self._procedure_finish("The camera did not save the photo in time.", "error")

    def _on_procedure_photo(self, path, error):
        p = self._proc
        if p is None or p["stage"] != "photo" or p["photo_name"] is None:
            return
        if error:
            self._procedure_finish(f"Photo not saved: {error}", "error")
            return
        if Path(path).name != p["photo_name"]:
            return
        self._timeout_token += 1
        p["photos"].append(dict(p.pop("pending", None) or {"file": p["photo_name"]}, path=str(path)))
        p["photo_name"] = None
        p["points"][-1]["photos"] += 1
        self._write_procedure_log(p, "running")
        if p["at_limit"]:
            self._procedure_point_done(f"max force {p['stop_g']:g} g reached")
        elif p["done"] >= p["steps"]:
            self._procedure_point_done(f"all {p['steps']} steps done")
        else:
            p["stage"] = "step"
            if self._start_step(p["step_mm"], p["stop_g"]):
                self.measure.set_status(f"{self._procedure_where()}: step {p['done'] + 1} of {p['steps']} "
                                        f"({len(p['photos'])} photos so far)...", "busy")

    def _procedure_finish(self, result, level, retract=True):
        """End of the procedure: write procedure.json, then (normally) lift the sensor back to just
        above the last part; the result is shown once that move is done."""
        p = self._proc
        if p is None:
            return
        if retract and p["stage"] != "retract" and p["point"] is not None and self.server.is_running():
            p["stage"], p["result"], p["result_level"] = "retract", result, level
            self._write_procedure_log(p, result)
            self.hover_above_point(p["point"])
            if self._measuring is not None:
                self.measure.set_status(f"{result} Lifting the sensor back to {measure.HOVER_TEXT} above the part...", "busy")
                return
        self._proc = None
        self._set_measure_buttons(self._measuring is None)
        self._write_procedure_log(p, result)
        skipped = sum(1 for r in p["points"] if str(r.get("result", "")).startswith("skipped"))
        extra = f", {skipped} part(s) skipped" if skipped else ""
        self.measure.set_status(f"Procedure: {result} {len(p['photos'])} photos{extra} in {p['folder']}", level)

    def _write_procedure_log(self, p, result):
        try:
            p["folder"].mkdir(parents=True, exist_ok=True)
            (p["folder"] / "procedure.json").write_text(json.dumps({
                "start": p["start"].isoformat(timespec="seconds"),
                "end": datetime.now().isoformat(timespec="seconds"),
                "plate": str(self.model_path) if self.model_path else None,
                "calibration": p["source"], "max_force_g": p["stop_g"], "step_mm": p["step_mm"],
                "steps_per_part": p["steps"], "result": result,
                "points": sorted(p["points"], key=lambda r: r["point"]),
                "photos": p["photos"]}, indent=2), encoding="utf-8")
        except OSError:
            pass

    def _set_measure_buttons(self, on):
        on = on and self._proc is None
        for b in (self.measure.hover_btn, self.measure.touch_btn, self.measure.step_btn, self.measure.proc_btn):
            b.setEnabled(on)
        self._update_next_button(on)

    def _part_count(self):
        manifest = calibration.manifest_for(self.model_path) if self.model_path else None
        return len(manifest.get("parts", [])) if manifest else 0

    def _update_next_button(self, on=True):
        """'Go to next point' once a point has been visited; it names the point it goes to."""
        i = self._point_index
        n = self._part_count()
        has_next = i is not None and i + 1 < n
        self.measure.next_btn.setEnabled(bool(on and has_next))
        if i is None:
            self.measure.next_btn.setText("Go to next point")
        elif has_next:
            self.measure.next_btn.setText(f"Go to next point (point {i + 2} of {n})")
        else:
            self.measure.next_btn.setText(f"Last point reached (point {i + 1} of {n})")

    def next_point(self):
        """'Go to next point': up to the travel height, over to the next part, just above it."""
        if self._point_index is None:
            self.measure.set_status("Go to point 1 first.", "error")
            return
        if self._point_index + 1 >= self._part_count():
            self.measure.set_status("This was the last point of the plate.", "info")
            return
        self.hover_above_point(self._point_index + 1)

    def _set_contact(self, contact):
        self._contact = contact
        if contact is None:
            self.measure.depth_label.setText("")
        else:
            self.measure.depth_label.setText(f"{contact['object']}: indentation {contact['depth_mm']:.3f} mm, "
                                             f"force {contact['force_g']:.1f} g")

    def _photo_tag(self):
        """For 'Take photo' while measuring: object, indentation from the first touch and the force now."""
        if self._contact is None:
            return None
        force = self._live_force[-1] if self._live_force else self._contact["force_g"]
        return {"object": self._contact["object"], "depth_mm": self._contact["depth_mm"], "force_g": force}

    def _send_measure_goto(self, args):
        x, y, z, label = args
        return self.server.goto(x, y, z, label, home_xy=self._needs_home, z_final=self._hover_target.z)

    def _measure_done(self, text, level):
        self._measuring = None
        self._pending_goto = None
        self._timeout_token += 1
        self.measure.set_status(text, level)
        if self._proc is not None:            # the procedure decides what comes next
            QTimer.singleShot(0, self, lambda: self._procedure_next(level, text))
            return
        self._set_measure_buttons(True)

    def _arm_measure_timeout(self, ms):
        self._timeout_token += 1
        token = self._timeout_token
        QTimer.singleShot(ms, self, lambda: self._measure_timed_out(token))

    def _measure_timed_out(self, token=None):
        if token is not None and token != self._timeout_token:
            return                            # a later move armed its own timeout
        if self._measuring:
            self._measure_done("No answer from the printer. Is it switched on and is the USB cable plugged in?", "error")

    def _measure_message(self, msg):
        kind = msg.get("type")
        if kind == "touch_done":
            self._last_touch_status = msg.get("status")
        if kind == "hello" and self._proc is not None and self._proc.get("wait_hello"):
            self._proc["wait_hello"] = False
            self._procedure_send_home()
        elif kind == "goto_done" and self._measuring == HOME_LABEL and msg.get("label") == HOME_LABEL:
            if msg.get("ok"):
                self._needs_home = False
                self._measure_done("X and Y homed.", "ok")
            else:
                self._measure_done(f"Homing X and Y failed: {msg.get('message', '')}", "error")
        elif kind == "hello" and self._pending_goto is not None:
            args, self._pending_goto = self._pending_goto, None
            if self._send_measure_goto(args):
                self.measure.set_status(f"On the way to {self._measuring}...", "busy")
            else:
                self._measure_done("The printer connection does not respond; try again.", "error")
        elif kind == "goto_done" and msg.get("label") == self._measuring:
            if msg.get("ok"):
                self._needs_home = False
                self._hovering = True
                t = self._hover_target
                self._point_index = self._target_index
                label = self._measuring
                self._measure_done(f"The sensor hovers {measure.HOVER_TEXT} above {label} ({t.part_id}). Head X{t.x:.2f} Y{t.y:.2f} "
                                   f"Z{t.z:.2f} (from the {t.source}).", "ok")
            else:
                self._measure_done(f"Failed: {msg.get('message', '')}", "error")
        elif kind == "touch_done" and self._measuring == "step":
            z, force = msg.get("z"), msg.get("force_g")
            c = self._contact
            if z is not None and c is not None:
                c["depth_mm"] = max(0.0, c["z0"] - float(z))
            if force is not None and c is not None:
                c["force_g"] = float(force)
            self._set_contact(c)
            depth = f"Indentation {c['depth_mm']:.3f} mm, force {c['force_g']:.1f} g." if c else ""
            status = msg.get("status")
            if status == "ok":
                self._measure_done(f"{getattr(self, '_step_mm', self.PRESS_STEP_MM):g} mm deeper. {depth}", "ok")
            elif status == "limit":
                self._measure_done(f"{msg.get('message', '')} {depth}", "error")
            else:
                self._measure_done(f"{msg.get('message', '') or 'Failed.'} {depth}", "error")
        elif kind == "touch_done" and self._measuring == "touch":
            self._hovering = False
            if msg.get("ok") and msg.get("z") is not None:
                obj = getattr(self, "_hover_target", None)
                self._set_contact({"object": obj.part_id if obj is not None else "object",
                                   "z0": float(msg["z"]), "depth_mm": 0.0,
                                   "force_g": float(msg.get("force_g") or 0.0)})
            else:
                self._set_contact(None)
            if msg.get("ok"):
                self._measure_done(f"{msg.get('message', '')} Head Z{msg.get('z') or 0:.3f}, "
                                   f"force {msg.get('force_g') or 0:.1f} g.", "ok")
            else:
                self._measure_done(msg.get("message", "") or "Failed.", "error")
        elif kind == "status" and not msg.get("ok") and self._measuring:
            self._measure_done(msg.get("message", ""), "error")
        elif kind == "bye":
            self._needs_home = True
            self._set_contact(None)
            if self._measuring:
                self._measure_done("The printer connection was closed.", "error")

    def on_calibrator_message(self, msg):
        self._measure_message(msg)
        first = self.wizard.sequence()[0] if self.wizard is not None else calibration.POINTS[0]
        if msg.get("type") == "goto_done" and msg.get("ok") and msg.get("label") in (first, HOME_LABEL):
            self._needs_home = False          # the wizard homed X/Y on its way to its first cone
        kind = msg.get("type")
        if kind == "force":
            try:
                self._live_force.append(float(msg["g"]))
            except (KeyError, TypeError, ValueError):
                pass
            return
        if kind == "bye":
            self._live_force.clear()
        if kind == "hello":
            self.left.link_label.setText("Calibrator connected: follow the steps in the calibration window")
        elif kind == "bye":
            self.left.link_label.setText("Calibrator closed")
        elif kind == "position" and msg.get("point") in calibration.POINTS:
            self.left.set_machine_point(msg["point"], msg["x"], msg["y"])
            self.left.link_label.setText(f"{msg['point']} received: "
                                         f"X{msg['x']:.2f} Y{msg['y']:.2f} Z{msg['z']:.2f}")
        elif kind == "status" and str(msg.get("message", "")).startswith(GUARD_STATUS):
            self.left.link_label.setText(msg["message"])          # the hidden calibrator dropped the bed
            self.measure.set_status(msg["message"], "error")
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
        if self.focusfield.hardware_active:
            QMessageBox.warning(self, "Printer in use", "An optical scan owns the printer and camera.")
            return
        if self.thread is not None:
            return
        prog, errors = self.preflight()
        if errors:
            QMessageBox.warning(self, "Cannot execute", "\n".join(errors))
            return
        simulate = self.run.simulate.isChecked()
        if not simulate and self.server.is_running():
            r = QMessageBox.question(self, "Calibration open",
                                     "The calibration is still using the printer connection. Close it now?")
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

    def _live_source(self):
        """The graph outside runs: the force the calibrator streams (None when it isn't running)."""
        return list(self._live_force) or None

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
        self.graph.set_source(self._live_source)
        self.photo.resume()
        self._running_row = None
        self.update_graph_lines()
        self.set_running(False)
        self.run.status.setText(f"{summary.outcome}: {summary.message}\nRun folder: {summary.log_path}")
        if summary.outcome != "completed":
            QMessageBox.warning(self, "Run ended", f"{summary.outcome}: {summary.message}")

    def stop(self):
        if self._proc is not None:
            self._procedure_finish("Stopped with STOP. The sensor stays where it is.", "error", retract=False)
        if self._measuring and self.server.is_running():
            self.server.halt()                 # Measure: stop the creep or move now
            self.measure.set_status("STOP sent.", "error")
        if self.focusfield.thread is not None:
            self.focusfield.stop()
            if self.focusfield.hardware_active:
                return
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
        self.measure.setEnabled(not on)
        self.run_settings.setEnabled(not on)
        for b in self.rot_btns.values():
            b.setEnabled(not on)

    def _optical_activity(self, active):
        self.set_running(active)
        self.printer.hardware_busy = active
        self.printer.refresh()

    def closeEvent(self, e):
        if not self.focusfield.shutdown():
            self.focusfield.status.append("Stopping before closing; close again once the run has ended.")
            e.ignore()
            return
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
