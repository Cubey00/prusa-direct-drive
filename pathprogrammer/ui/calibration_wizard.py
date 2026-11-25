"""'Start calibration': the sensor on the plate's fiducials F1, F2, F3, one step at a time.

Per cone there is only one thing to do at a time:
  1. "Go to cone N": the calibrator drives the head to the predicted position (bed down to a
     safe travel height first, then X/Y), predicted from the sensor offset in machine_settings.toml.
     For cone 1 it homes X and Y first (G28 X Y, after lowering the bed): the head may have been
     moved by hand, and every later position counts on X/Y being right. Z is not homed (the
     nozzle probes the bed for that, and the sensor below it would hit the bed).
  2. The jogger: small, slow moves to put the sensor exactly on the tip.
  3. "Send position of cone N": the calibrator sends the head's position back.
After F3 the 3-point fit decides: "Calibration successful" or what to check. A good calibration saves
the head position on each cone (machine_settings.toml [calibration] cone_F1..3) and the sensor
offset measured from them; the next calibration drives straight to the saved spots, hover_clearance
(1 cm) higher, so only a short way down is left to jog.

"Home X and Y only" (third choice, for when the calibration is known to be right): no cone is
measured; the bed goes to the travel height, X/Y are homed and the saved cone positions are used.
"Go to point 1" then hands over to Measure, the calibrator stays open (X/Y stay homed).

"Only cone 2" (choice at the start): only F2 is measured. That gives the plate's shift but no
rotation or scale: F1 and F3 are filled in as F2 + their bed distance from F2 (the plate assumed
square on the bed, machine steps exact), so everything after it (fit, Measure, saving) works as usual.
"""
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QButtonGroup, QDialog, QGridLayout, QHBoxLayout, QLabel, QMessageBox,
                               QPushButton, QRadioButton, QVBoxLayout, QWidget)

import machine_settings
from .. import calibration, measure

PLACE = {"F1": "front left", "F2": "front right", "F3": "back left"}
GOTO_ANSWER_MS = 20000      # no reply from the calibrator within this time: tell the user why
HOME_ANSWER_MS = 90000      # the same when it homes X/Y first
JOG_STEPS = (5.0, 1.0, 0.5, 0.1, 0.05)  # mm
JOG_FEED = 120.0                        # mm/min = 2 mm/s: slow, for fine-tuning
OK_COLOR, WARN_COLOR, ERR_COLOR = "#1b7a1b", "#b36b00", "#c00000"
BIG = "font-size: 16px; padding: 10px;"

MODE_ALL, MODE_F2, MODE_HOME = "all", "F2", "home"
HOME_LABEL = "home"                      # goto_done label of "Home X and Y only"
SINGLE_POINT = "F2"
MODE_KEY = "calibration/mode"            # QSettings: last choice, per laptop

# states
NO_PLATE, GOTO, MOVING, JOG, SENDING, DONE = "no_plate", "goto", "moving", "jog", "sending", "done"


class CalibrationWizard(QDialog):
    def __init__(self, main, parent=None):
        super().__init__(parent or main)
        self.main = main
        self.setWindowTitle("Calibration")
        self.setFixedWidth(520)
        self.settings = machine_settings.load()
        self.index = 0                 # which cone: 0, 1, 2 -> F1, F2, F3
        self.state = NO_PLATE
        self.success = False
        self.sent = {}                 # fiducial -> (x, y, z) head position the calibrator sent
        stored = str(self._qsettings().value(MODE_KEY, MODE_ALL))
        self.mode = stored if stored in (MODE_ALL, MODE_F2, MODE_HOME) else MODE_ALL

        self.step_label = QLabel("")
        self.step_label.setStyleSheet("font-size: 18px; font-weight: bold;")
        self.text_label = QLabel("")
        self.text_label.setWordWrap(True)
        self.text_label.setTextFormat(Qt.RichText)
        self.text_label.setMinimumHeight(60)

        # --- which cones: all three or only cone 2 ---
        self.mode_box = QWidget()
        ml = QVBoxLayout(self.mode_box)
        ml.setContentsMargins(0, 0, 0, 0)
        self.mode_group = QButtonGroup(self)
        self.mode_all = QRadioButton("All 3 cones (most accurate: shift, rotation and scale)")
        self.mode_f2 = QRadioButton("Only cone 2, front right (faster: shift only, the plate is "
                                    "assumed straight)")
        self.mode_home = QRadioButton("Home X and Y only (the calibration is already right: the saved "
                                      "cone position is used)")
        for i, rb in enumerate((self.mode_all, self.mode_f2, self.mode_home)):
            self.mode_group.addButton(rb, i)
            ml.addWidget(rb)
        {MODE_F2: self.mode_f2, MODE_HOME: self.mode_home}.get(self.mode, self.mode_all).setChecked(True)
        self.mode_group.idToggled.connect(self._mode_toggled)

        self.go_btn = QPushButton("")
        self.go_btn.setStyleSheet(BIG)
        self.go_btn.clicked.connect(self.go)

        # --- jogger ---
        self.jogger = QWidget()
        jl = QVBoxLayout(self.jogger)
        jl.setContentsMargins(0, 0, 0, 0)
        steps = QHBoxLayout()
        steps.addWidget(QLabel("Step size:"))
        self.step_group = QButtonGroup(self)
        for i, s in enumerate(JOG_STEPS):
            rb = QRadioButton(f"{s:g} mm")
            rb.setChecked(s == 0.1)
            self.step_group.addButton(rb, i)
            steps.addWidget(rb)
        steps.addStretch()
        jl.addLayout(steps)
        grid = QGridLayout()
        self.jog_btns = {}

        def jog_btn(key, text, d, r, c):
            b = QPushButton(text)
            b.setMinimumHeight(40)
            b.setAutoRepeat(False)
            b.clicked.connect(lambda: self.jog(*d))
            grid.addWidget(b, r, c)
            self.jog_btns[key] = b
        jog_btn("Y+", "▲ back (Y+)", (0, 1, 0), 0, 1)
        jog_btn("X-", "◀ left (X−)", (-1, 0, 0), 1, 0)
        jog_btn("X+", "right (X+) ▶", (1, 0, 0), 1, 2)
        jog_btn("Y-", "▼ front (Y−)", (0, -1, 0), 2, 1)
        jog_btn("Z-", "Bed up (Z−)", (0, 0, -1), 0, 3)
        jog_btn("Z+", "Bed down (Z+)", (0, 0, 1), 2, 3)
        jl.addLayout(grid)

        self.send_btn = QPushButton("")
        self.send_btn.setStyleSheet(BIG)
        self.send_btn.clicked.connect(self.send_position)
        jl.addWidget(self.send_btn)

        self.link_label = QLabel("")
        self.link_label.setWordWrap(True)
        self.link_label.setTextFormat(Qt.RichText)

        self.finish_btn = QPushButton("Close: position known")
        self.finish_btn.setStyleSheet(BIG)
        self.finish_btn.clicked.connect(self.accept)
        self.point1_btn = QPushButton("Go to point 1")
        self.point1_btn.setStyleSheet(BIG)
        self.point1_btn.clicked.connect(self.go_to_point1)

        self.restart_btn = QPushButton("Start over")
        self.restart_btn.clicked.connect(self.restart)
        close = QPushButton("Close")
        close.clicked.connect(self.close)
        bottom = QHBoxLayout()
        bottom.addWidget(self.restart_btn)
        bottom.addStretch()
        bottom.addWidget(close)

        lay = QVBoxLayout(self)
        lay.addWidget(self.step_label)
        lay.addWidget(self.text_label)
        lay.addWidget(self.mode_box)
        lay.addWidget(self.go_btn)
        lay.addWidget(self.jogger)
        lay.addWidget(self.finish_btn)
        lay.addWidget(self.point1_btn)
        lay.addWidget(self.link_label)
        lay.addStretch()
        lay.addLayout(bottom)
        self.refresh()

    # ---------------- plate / prediction ----------------
    def manifest(self):
        model = self.main.model
        return calibration.manifest_for(model.path) if model is not None else None

    def fiducials(self):
        m = self.manifest()
        return {f["name"]: f for f in m["fiducials"]} if m else {}

    def _qsettings(self):
        return getattr(self.main, "settings", None) or _NoSettings()

    def _mode_toggled(self, button_id, checked):
        if not checked or self.index > 0 or self.state not in (GOTO, NO_PLATE):
            return
        self.mode = (MODE_ALL, MODE_F2, MODE_HOME)[button_id] if 0 <= button_id <= 2 else MODE_ALL
        self._qsettings().setValue(MODE_KEY, self.mode)
        self.refresh()

    def saved_calibration(self):
        """(ok, reason): is there a saved calibration Measure can use without measuring a cone?"""
        if not self.settings.sensor.measured and not any(self.saved(p) for p in calibration.POINTS):
            return False, "no successful calibration has been saved yet"
        points, reason = measure.machine_points(None, self.settings, self.manifest())
        return points is not None, reason

    def home_only(self):
        return self.mode == MODE_HOME

    def sequence(self):
        """The cones to measure, in order."""
        return (SINGLE_POINT,) if self.mode == MODE_F2 else calibration.POINTS

    def point(self):
        seq = self.sequence()
        return seq[min(self.index, len(seq) - 1)]

    def cone_number(self, name=None):
        return calibration.POINTS.index(name or self.point()) + 1

    def travel_z(self):
        """Machine Z that keeps the sensor clear of the cones and the tallest part."""
        m = self.manifest()
        tallest = max([f["z"] for f in m["fiducials"]] + [p["size"][2] for p in m["parts"]]) if m else 0
        return tallest + self.settings.sensor.hover_clearance - self.settings.sensor.offset[2]

    def saved(self, name):
        """Head position (x, y, z) on this cone from the last good calibration, or None."""
        return self.settings.calibration.cone_position(name)

    def target(self, name):
        """(x, y, travel z, final z or None, saved?) for fiducial `name`: the saved head position
        hover_clearance higher if there is one, else predicted from the sensor offset at the
        travel height."""
        pos = self.saved(name)
        if pos:
            z_final = pos[2] + self.settings.sensor.hover_clearance
            return pos[0], pos[1], max(self.travel_z(), z_final), z_final, True
        f = self.fiducials()[name]
        ox, oy, _ = self.settings.sensor.offset
        return f["x"] - ox, f["y"] - oy, self.travel_z(), None, False

    def predicted_head(self, name):
        """Where the head ends up for fiducial `name` (x, y, z)."""
        x, y, z, z_final, _ = self.target(name)
        return x, y, z if z_final is None else z_final

    def reachable(self, x, y, *zs):
        (x0, x1), (y0, y1) = self.settings.printer.travel
        return x0 <= x <= x1 and y0 <= y <= y1 and all(0 <= z <= self.settings.printer.z_max for z in zs)

    # ---------------- view ----------------
    def refresh(self):
        if self.manifest() is None:
            self.state = NO_PLATE
        elif self.state == NO_PLATE:
            self.state = GOTO
        p = self.point()
        n = self.cone_number(p)
        step, total = self.index + 1, len(self.sequence())
        self.mode_box.setVisible(self.state == GOTO and self.index == 0)
        self.point1_btn.setVisible(self.state == DONE and self.success and self.home_only())
        if self.home_only() and self.state != NO_PLATE:
            self._refresh_home()
            return
        self.go_btn.setVisible(self.state in (GOTO, MOVING))
        self.jogger.setVisible(self.state in (JOG, SENDING))
        self.finish_btn.setVisible(self.state == DONE and self.success)
        self.restart_btn.setVisible(self.state not in (NO_PLATE,) and (self.index > 0 or self.state == DONE))
        self.go_btn.setText(f"Go to cone {n}")
        self.send_btn.setText(f"Send position of cone {n}")
        self.go_btn.setEnabled(self.state == GOTO)
        self.send_btn.setEnabled(self.state == JOG)

        if self.state == NO_PLATE:
            self.step_label.setText("Calibration")
            self.text_label.setText(f"<span style='color:{ERR_COLOR}'><b>Open a plate first:</b> choose the "
                                    "printed plate in <i>Print plates</i> and click <i>Open plate in "
                                    "programmer</i>.</span>")
        elif self.state in (GOTO, MOVING):
            self.step_label.setText(f"Cone {n} ({p}, {PLACE[p]})" if total == 1 else
                                    f"Cone {n} of {total} ({p}, {PLACE[p]})")
            x, y, z, z_final, from_saved = self.target(p)
            where = ("It goes to the spot of the previous calibration, 1 cm above it." if from_saved else
                     "It goes to the spot the sensor offset predicts (no saved spot yet).")
            if not self.reachable(x, y, z, *([z_final] if z_final is not None else [])):
                self.go_btn.setEnabled(False)
                self.text_label.setText(
                    f"<span style='color:{ERR_COLOR}'>Cone {n} is out of reach (head X{x:.1f} Y{y:.1f} "
                    f"Z{z:.1f}). Check the sensor offset and the saved cone positions in "
                    "machine_settings.toml.</span>")
            elif self.state == GOTO and self.homes():
                self.text_label.setText(
                    "Leave the printed plate on the bed and mount the sensor on the head. Do not switch "
                    "the printer off: that way the height (Z) of the print stays known.<br><br>"
                    "Click the button: the bed first lowers to a safe height, then the head finds X and Y "
                    "again (homing, the head goes to the front right) and then drives to the cone. "
                    f"{where} Z is not homed: the printer does that with the nozzle on the bed, and the "
                    "sensor now sits below the nozzle.")
            elif self.state == GOTO:
                self.text_label.setText("Click the button: the bed first lowers to a safe height, "
                                        f"then the head drives to the cone. {where}")
            else:
                self.text_label.setText(f"Head drives to cone {n}: first Z{z:.1f} (bed down), "
                                        + ("then home X/Y, " if self.homes() else "")
                                        + f"then X{x:.1f} Y{y:.1f} ...")
        elif self.state in (JOG, SENDING):
            self.step_label.setText(f"Cone {n}: fine adjustment" if total == 1 else
                                    f"Cone {step} of {total}: fine adjustment")
            down = ("The sensor hovers 1 cm above the previous spot: raise the bed about 10 mm "
                    "(once 5 mm with <b>Bed up</b>, then with 1 mm and smaller steps: the plate may "
                    "lie a little higher than last time). "
                    if self.saved(p) else "")
            self.text_label.setText(down + "Put the centre of the sensor exactly above the tip of the cone. "
                                    "Start coarse, finish with 0.1 or 0.05 mm. Raise the bed gently "
                                    "until just below the sensor. Then click <b>Send position of cone "
                                    f"{n}</b>.")
        elif self.state == DONE:
            self.step_label.setText("Calibration done" if self.success else "Calibration failed")

    def _refresh_home(self):
        """The view for "Home X and Y only"."""
        self.go_btn.setVisible(self.state in (GOTO, MOVING))
        self.jogger.setVisible(False)
        self.finish_btn.setVisible(False)
        self.restart_btn.setVisible(self.state == DONE)
        self.go_btn.setText("Home X and Y")
        ok, reason = self.saved_calibration()
        self.go_btn.setEnabled(self.state == GOTO and ok)
        if self.state in (GOTO, MOVING):
            self.step_label.setText("Home X and Y only")
        if self.state == GOTO and not ok:
            self.text_label.setText(f"<span style='color:{ERR_COLOR}'>Not possible yet: {reason}. Choose "
                                    "<i>All 3 cones</i> or <i>Only cone 2</i>.</span>")
        elif self.state == GOTO:
            self.text_label.setText(
                "Only use this when the calibration is already right: the plate is still in the same "
                "place and the printer has not been switched off (then Z stays known).<br><br>Click the "
                "button: the bed first lowers to a safe height, then the head finds X and Y again (homing, "
                "the head goes to the front right). Z is not homed. After that, Measure uses the saved "
                "cone position.")
        elif self.state == MOVING:
            self.text_label.setText(f"Bed to Z{self.travel_z():.1f}, then homing X and Y ...")
        elif self.state == DONE and self.success:
            self.step_label.setText("X and Y homed")
            self.text_label.setText(
                f"<span style='color:{OK_COLOR}; font-size: 16px;'><b>X and Y homed.</b></span> The "
                "saved calibration is used. Click <b>Go to point 1</b>: the sensor will hover 1 cm "
                "above the first part.")

    def go_to_point1(self):
        """After "Home X and Y only": close this window, keep the calibrator (X/Y stay homed) and
        let Measure drive to point 1."""
        self.hide()                       # not close(): that would quit the calibrator
        self.main.hover_above_point(0)

    def homes(self):
        """The first cone starts from a fresh X/Y home: the head may have been moved by hand."""
        return self.index == 0

    def set_state(self, state):
        self.state = state
        self.refresh()

    # ---------------- actions ----------------
    def ensure_calibrator(self):
        if not self.main.server.is_running():
            self.main.open_calibrator()
            self.link_label.setText("Starting the calibrator. Wait until it is connected, then click "
                                    "again.")
            return False
        return True

    def go(self):
        if self.state != GOTO or not self.ensure_calibrator():
            return
        if self.home_only():
            self.go_home()
            return
        p = self.point()
        x, y, z, z_final, _ = self.target(p)
        if not self.main.server.goto(x, y, z, p, home_xy=self.homes(), z_final=z_final):
            self.link_label.setText("The calibrator is not connected yet. Wait a moment and try again.")
            return
        self.link_label.setText("")
        self.set_state(MOVING)
        # tied to this window: the timer dies with it instead of firing into a deleted object
        wait = HOME_ANSWER_MS if self.homes() else GOTO_ANSWER_MS
        QTimer.singleShot(wait, self, lambda: self.goto_timed_out(p, wait))

    def go_home(self):
        if not self.saved_calibration()[0]:
            return
        if not self.main.server.home(self.travel_z(), HOME_LABEL):
            self.link_label.setText("The calibrator is not connected yet. Wait a moment and try again.")
            return
        self.link_label.setText("")
        self.set_state(MOVING)
        QTimer.singleShot(HOME_ANSWER_MS, self, self.home_timed_out)

    def home_timed_out(self):
        if self.state != MOVING or not self.home_only():
            return
        self.link_label.setText(
            f"<span style='color:{ERR_COLOR}'>No word from the calibrator after {HOME_ANSWER_MS // 1000} s. "
            "Check whether the printer's screen is asking a question; then click again.</span>")
        self.set_state(GOTO)

    def goto_timed_out(self, name, wait):
        if self.state != MOVING or self.point() != name:
            return
        self.link_label.setText(
            f"<span style='color:{ERR_COLOR}'>No word from the calibrator after {wait // 1000} s. "
            "Check whether the printer's screen is asking a question (e.g. to confirm the metrics "
            "address) and whether the calibrator window responds; then click again.</span>")
        self.set_state(GOTO)

    def jog_step(self):
        return JOG_STEPS[max(self.step_group.checkedId(), 0)]

    def jog(self, dx, dy, dz):
        if self.state != JOG:
            return
        s = self.jog_step()
        if not self.main.server.jog(dx * s, dy * s, dz * s, JOG_FEED):
            self.link_label.setText(f"<span style='color:{ERR_COLOR}'>Calibrator not connected.</span>")

    def send_position(self):
        if self.state != JOG:
            return
        if self.main.server.request_position(self.point()):
            self.set_state(SENDING)
        else:
            self.link_label.setText(f"<span style='color:{ERR_COLOR}'>Calibrator not connected.</span>")

    def on_message(self, msg):
        kind = msg.get("type")
        if kind == "hello":
            self.link_label.setText("Calibrator connected.")
        elif kind == "bye":
            self.link_label.setText(f"<span style='color:{ERR_COLOR}'>The calibrator was closed.</span>")
            if self.state in (MOVING, JOG, SENDING):
                self.set_state(GOTO)
        elif kind == "goto_done" and self.state == MOVING and self.home_only() and msg.get("label") == HOME_LABEL:
            if msg.get("ok"):
                self.link_label.setText("")
                self.success = True
                self.set_state(DONE)
            else:
                self.link_label.setText(f"<span style='color:{ERR_COLOR}'>{msg.get('message', '')}</span>")
                self.set_state(GOTO)
        elif kind == "goto_done" and self.state == MOVING and msg.get("label") == self.point():
            if msg.get("ok"):
                self.link_label.setText("")
                self.set_state(JOG)
            else:
                self.link_label.setText(f"<span style='color:{ERR_COLOR}'>{msg.get('message', '')}</span>")
                self.set_state(GOTO)
        elif kind == "status" and not msg.get("ok"):
            self.link_label.setText(f"<span style='color:{ERR_COLOR}'>{msg.get('message', '')}</span>")
            if self.state == SENDING:
                self.set_state(JOG)
        elif kind == "position" and msg.get("point") == self.point() and self.state != DONE:
            self.sent[msg["point"]] = (float(msg["x"]), float(msg["y"]), float(msg.get("z", 0.0)))
            self.link_label.setText(f"Cone {self.cone_number()} sent: X{msg['x']:.2f} Y{msg['y']:.2f}.")
            self.index += 1
            if self.index >= len(self.sequence()):
                self.finish()
            else:
                self.set_state(GOTO)

    def fill_from_single(self):
        """Only cone 2 measured: F1 and F3 = F2 + their bed distance from F2 (shift only)."""
        fids = self.fiducials()
        x2, y2, z2 = self.sent[SINGLE_POINT]
        f2 = fids[SINGLE_POINT]
        for p in calibration.POINTS:
            if p == SINGLE_POINT or p not in fids:
                continue
            f = fids[p]
            x, y = x2 + f["x"] - f2["x"], y2 + f["y"] - f2["y"]
            self.sent[p] = (x, y, z2 + f["z"] - f2["z"])        # head Z = tip Z - offset_z
            self.main.left.set_machine_point(p, x, y)

    def finish(self):
        self.index = len(self.sequence()) - 1
        single = self.mode == MODE_F2
        if single and SINGLE_POINT in self.sent:
            self.fill_from_single()
        cal = self.main.left.cal_points()
        issues = calibration.validate(cal)
        errors = [i.msg for i in issues if i.level == "error"]
        self.success = not issues
        if errors:
            text = (f"<span style='color:{ERR_COLOR}'><b>Calibration failed:</b> {' '.join(errors)}</span>"
                    "<br>Click <b>Start over</b>.")
        else:
            t = calibration.fit(cal)
            res = calibration.residuals(cal, t)
            details = ("Residuals: " + ", ".join(f"cone {i + 1} {r:.2f} mm" for i, r in enumerate(res.values()))
                       + f"; rotation {t.rotation_deg:.2f}°.")
            if single:
                details = ("Only cone 2 measured: the plate is shifted, not rotated (cones 1 and 3 "
                           "calculated from their distance to cone 2).")
            if issues:
                text = (f"<span style='color:{WARN_COLOR}'><b>The points do not fit well:</b> "
                        + " ".join(i.msg for i in issues) + f"</span><br>{details}<br>"
                        "Click <b>Start over</b>.")
            else:
                saved = self.save_calibration()
                text = (f"<span style='color:{OK_COLOR}; font-size: 16px;'><b>Calibration successful! The printer "
                        "now knows where the sensor head is relative to the objects.</b></span><br><br>"
                        f"Position known. {details}" + (f"<br>{saved}" if saved else ""))
        self.text_label.setText(text)
        self.set_state(DONE)

    def measured_offset(self):
        """Sensor-nozzle offset from this calibration: the nozzle printed each cone at its bed
        position, and the head stood at the measured machine position with the sensor on it."""
        cal = self.main.left.cal_points()
        fids = self.fiducials()
        pts = [(fids[p]["x"] - cal.machine[p][0], fids[p]["y"] - cal.machine[p][1])
               for p in calibration.POINTS if cal.machine[p] is not None and p in fids]
        if len(pts) != len(calibration.POINTS):
            return None
        return sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts)

    def measured_offset_z(self):
        """Sensor-nozzle Z offset: the sensor was jogged onto each cone tip (bed Z of the tip)."""
        fids = self.fiducials()
        zs = [fids[p]["z"] - self.sent[p][2] for p in calibration.POINTS if p in self.sent and p in fids]
        return sum(zs) / len(zs) if len(zs) == len(calibration.POINTS) else None

    def save_calibration(self):
        """Keep the head positions on the cones (the next calibration drives straight there) and
        the sensor offset measured from them, as the new standard in machine_settings.toml."""
        if any(p not in self.sent for p in calibration.POINTS):
            return ""
        off, off_z = self.measured_offset(), self.measured_offset_z()
        xy_refused = ""
        # only cone 2 measured: save only that one. F1/F3 were calculated and may lie where the
        # sensor can't reach (outside the head's travel); Measure calculates them again from F2.
        measured = (SINGLE_POINT,) if self.mode == MODE_F2 else calibration.POINTS
        cones = {f"cone_{p}": ([round(v, 3) for v in self.sent[p]] if p in measured else [])
                 for p in calibration.POINTS}
        try:
            self.settings = machine_settings.save_values("calibration", cones)
            if off is not None and off_z is not None:
                try:
                    self.settings = machine_settings.save_values(
                        "sensor", {"offset_x": round(off[0], 2), "offset_y": round(off[1], 2),
                                   "offset_z": round(off_z, 2), "measured": True})
                except machine_settings.SettingsError as e:
                    # X/Y outside what the reach check allows: keep at least the measured height,
                    # Measure needs it to stay clear of the parts (X/Y come from the cone positions)
                    self.settings = machine_settings.save_values("sensor", {"offset_z": round(off_z, 2)})
                    xy_refused = (f"Measured sensor offset X {off[0]:+.2f}, Y {off[1]:+.2f} not saved "
                                  f"({e}); height Z {off_z:+.2f} mm was saved.")
        except machine_settings.SettingsError as e:
            return f"Not saved: {e}"
        text = ("Position of cone 2 saved" if self.mode == MODE_F2 else "Positions of the cones saved")
        text += " in machine_settings.toml: the next calibration drives straight there, 1 cm above."
        if xy_refused:
            text += " " + xy_refused
        elif off is not None and off_z is not None:
            text += (f" Measured sensor offset: X {off[0]:+.2f}, Y {off[1]:+.2f}, Z {off_z:+.2f} mm "
                     "(also saved).")
        return text

    def restart(self):
        cal = self.main.left.cal_points()
        for p in calibration.POINTS:
            cal.machine[p] = None
        self.main.left.set_cal_points(cal)
        self.index = 0
        self.success = False
        self.sent = {}
        self.link_label.setText("")
        self.set_state(GOTO if self.manifest() is not None else NO_PLATE)

    def closeEvent(self, e):
        if self.state != DONE and (self.index > 0 or self.state in (MOVING, JOG, SENDING)):
            if QMessageBox.question(self, "Calibration", "The calibration is not finished. Close anyway?") \
                    != QMessageBox.Yes:
                e.ignore()
                return
        if self.home_only() and self.state == DONE and self.success:
            super().closeEvent(e)                # X/Y just homed: keep the calibrator for Measure
            return
        self.main.server.quit_calibrator()     # it runs without a window: close it with the wizard
        super().closeEvent(e)


class _NoSettings:
    def value(self, key, default=None):
        return default

    def setValue(self, key, value):
        pass
