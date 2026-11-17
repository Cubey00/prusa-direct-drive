"""'Start kalibratie': the sensor on the plate's fiducials F1, F2, F3, one step at a time.

Per cone there is only one thing to do at a time:
  1. "Ga naar kegel N": the calibrator drives the head to the predicted position (bed down to a
     safe travel height first, then X/Y), predicted from the sensor offset in machine_settings.toml.
     For cone 1 it homes X and Y first (G28 X Y, after lowering the bed): the head may have been
     moved by hand, and every later position counts on X/Y being right. Z is not homed (the
     nozzle probes the bed for that, and the sensor below it would hit the bed).
  2. The jogger: small, slow moves to put the sensor exactly on the tip.
  3. "Positie van kegel N insturen": the calibrator sends the head's position back.
After F3 the 3-point fit decides: "Kalibratie geslaagd" (and the measured sensor offset is saved
for the next prediction) or what to check.
"""
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QButtonGroup, QDialog, QGridLayout, QHBoxLayout, QLabel, QMessageBox,
                               QPushButton, QRadioButton, QVBoxLayout, QWidget)

import machine_settings
from .. import calibration

PLACE = {"F1": "linksvoor", "F2": "rechtsvoor", "F3": "linksachter"}
GOTO_ANSWER_MS = 20000      # no reply from the calibrator within this time: tell the user why
HOME_ANSWER_MS = 90000      # the same when it homes X/Y first
JOG_STEPS = (1.0, 0.5, 0.1, 0.05)       # mm
JOG_FEED = 120.0                        # mm/min = 2 mm/s: slow, for fine-tuning
OK_COLOR, WARN_COLOR, ERR_COLOR = "#1b7a1b", "#b36b00", "#c00000"
BIG = "font-size: 16px; padding: 10px;"

# states
NO_PLATE, GOTO, MOVING, JOG, SENDING, DONE = "no_plate", "goto", "moving", "jog", "sending", "done"


class CalibrationWizard(QDialog):
    def __init__(self, main, parent=None):
        super().__init__(parent or main)
        self.main = main
        self.setWindowTitle("Kalibratie")
        self.setFixedWidth(520)
        self.settings = machine_settings.load()
        self.index = 0                 # which cone: 0, 1, 2 -> F1, F2, F3
        self.state = NO_PLATE
        self.success = False

        self.step_label = QLabel("")
        self.step_label.setStyleSheet("font-size: 18px; font-weight: bold;")
        self.text_label = QLabel("")
        self.text_label.setWordWrap(True)
        self.text_label.setTextFormat(Qt.RichText)
        self.text_label.setMinimumHeight(60)

        self.go_btn = QPushButton("")
        self.go_btn.setStyleSheet(BIG)
        self.go_btn.clicked.connect(self.go)

        # --- jogger ---
        self.jogger = QWidget()
        jl = QVBoxLayout(self.jogger)
        jl.setContentsMargins(0, 0, 0, 0)
        steps = QHBoxLayout()
        steps.addWidget(QLabel("Stapgrootte:"))
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
        jog_btn("Y+", "▲ naar achter (Y+)", (0, 1, 0), 0, 1)
        jog_btn("X-", "◀ links (X−)", (-1, 0, 0), 1, 0)
        jog_btn("X+", "rechts (X+) ▶", (1, 0, 0), 1, 2)
        jog_btn("Y-", "▼ naar voren (Y−)", (0, -1, 0), 2, 1)
        jog_btn("Z-", "Bed omhoog (Z−)", (0, 0, -1), 0, 3)
        jog_btn("Z+", "Bed omlaag (Z+)", (0, 0, 1), 2, 3)
        jl.addLayout(grid)

        self.send_btn = QPushButton("")
        self.send_btn.setStyleSheet(BIG)
        self.send_btn.clicked.connect(self.send_position)
        jl.addWidget(self.send_btn)

        self.link_label = QLabel("")
        self.link_label.setWordWrap(True)
        self.link_label.setTextFormat(Qt.RichText)

        self.finish_btn = QPushButton("Afsluiten: positie bekend")
        self.finish_btn.setStyleSheet(BIG)
        self.finish_btn.clicked.connect(self.accept)

        self.restart_btn = QPushButton("Opnieuw beginnen")
        self.restart_btn.clicked.connect(self.restart)
        close = QPushButton("Sluiten")
        close.clicked.connect(self.close)
        bottom = QHBoxLayout()
        bottom.addWidget(self.restart_btn)
        bottom.addStretch()
        bottom.addWidget(close)

        lay = QVBoxLayout(self)
        lay.addWidget(self.step_label)
        lay.addWidget(self.text_label)
        lay.addWidget(self.go_btn)
        lay.addWidget(self.jogger)
        lay.addWidget(self.finish_btn)
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

    def point(self):
        return calibration.POINTS[min(self.index, len(calibration.POINTS) - 1)]

    def travel_z(self):
        """Machine Z that keeps the sensor clear of the cones and the tallest part."""
        m = self.manifest()
        tallest = max([f["z"] for f in m["fiducials"]] + [p["size"][2] for p in m["parts"]]) if m else 0
        return tallest + self.settings.sensor.hover_clearance - self.settings.sensor.offset[2]

    def predicted_head(self, name):
        """Head X/Y/Z that puts the sensor above fiducial `name`, at the travel height."""
        f = self.fiducials()[name]
        ox, oy, _ = self.settings.sensor.offset
        return f["x"] - ox, f["y"] - oy, self.travel_z()

    def reachable(self, x, y, z):
        (x0, x1), (y0, y1) = self.settings.printer.travel
        return x0 <= x <= x1 and y0 <= y <= y1 and 0 <= z <= self.settings.printer.z_max

    # ---------------- view ----------------
    def refresh(self):
        if self.manifest() is None:
            self.state = NO_PLATE
        elif self.state == NO_PLATE:
            self.state = GOTO
        p = self.point()
        n = self.index + 1
        total = len(calibration.POINTS)
        self.go_btn.setVisible(self.state in (GOTO, MOVING))
        self.jogger.setVisible(self.state in (JOG, SENDING))
        self.finish_btn.setVisible(self.state == DONE and self.success)
        self.restart_btn.setVisible(self.state not in (NO_PLATE,) and (self.index > 0 or self.state == DONE))
        self.go_btn.setText(f"Ga naar kegel {n}")
        self.send_btn.setText(f"Positie van kegel {n} insturen")
        self.go_btn.setEnabled(self.state == GOTO)
        self.send_btn.setEnabled(self.state == JOG)

        if self.state == NO_PLATE:
            self.step_label.setText("Kalibratie")
            self.text_label.setText(f"<span style='color:{ERR_COLOR}'><b>Open eerst een plaat:</b> kies in "
                                    "<i>Print plates</i> de geprinte plaat en klik <i>Open plate in "
                                    "programmer</i>.</span>")
        elif self.state in (GOTO, MOVING):
            self.step_label.setText(f"Kegel {n} van {total} ({p}, {PLACE[p]})")
            x, y, z = self.predicted_head(p)
            if not self.reachable(x, y, z):
                self.go_btn.setEnabled(False)
                self.text_label.setText(
                    f"<span style='color:{ERR_COLOR}'>Kegel {n} ligt buiten bereik (kop X{x:.1f} Y{y:.1f} "
                    f"Z{z:.1f}). Controleer de sensorafstand in machine_settings.toml.</span>")
            elif self.state == GOTO and self.homes():
                self.text_label.setText(
                    "Laat de geprinte plaat op het bed liggen en zet de sensor op de kop. Zet de printer "
                    "niet uit: dan blijft de hoogte (Z) van de print bekend.<br><br>"
                    "Klik op de knop: het bed zakt eerst naar een veilige hoogte, dan zoekt de kop "
                    "X en Y opnieuw op (homen, de kop gaat naar rechtsvoor) en rijdt daarna naar de kegel. "
                    "Z wordt niet gehomed: dat doet de printer met de nozzle op het bed, en daar zit "
                    "nu de sensor onder.")
            elif self.state == GOTO:
                self.text_label.setText("Klik op de knop: het bed zakt eerst naar een veilige hoogte, "
                                        "daarna rijdt de kop naar de kegel.")
            else:
                self.text_label.setText(f"Kop rijdt naar kegel {n}: eerst Z{z:.1f} (bed omlaag), "
                                        + ("dan X/Y homen, " if self.homes() else "")
                                        + f"dan X{x:.1f} Y{y:.1f} ...")
        elif self.state in (JOG, SENDING):
            self.step_label.setText(f"Kegel {n} van {total}: fijn afstellen")
            self.text_label.setText("Zet het midden van de sensor precies boven de top van de kegel. "
                                    "Begin met 1 mm, eindig met 0,1 of 0,05 mm. Breng het bed rustig "
                                    "omhoog tot vlak onder de sensor. Klik daarna op <b>Positie van kegel "
                                    f"{n} insturen</b>.")
        elif self.state == DONE:
            self.step_label.setText("Kalibratie klaar" if self.success else "Kalibratie niet gelukt")

    def homes(self):
        """Cone 1 starts from a fresh X/Y home: the head may have been moved by hand."""
        return self.index == 0

    def set_state(self, state):
        self.state = state
        self.refresh()

    # ---------------- actions ----------------
    def ensure_calibrator(self):
        if not self.main.server.is_running():
            self.main.open_calibrator()
            self.link_label.setText("De calibrator wordt gestart. Wacht tot hij verbonden is en klik dan "
                                    "opnieuw.")
            return False
        return True

    def go(self):
        if self.state != GOTO or not self.ensure_calibrator():
            return
        p = self.point()
        x, y, z = self.predicted_head(p)
        if not self.main.server.goto(x, y, z, p, home_xy=self.homes()):
            self.link_label.setText("De calibrator is nog niet verbonden. Wacht even en probeer opnieuw.")
            return
        self.link_label.setText("")
        self.set_state(MOVING)
        # tied to this window: the timer dies with it instead of firing into a deleted object
        wait = HOME_ANSWER_MS if self.homes() else GOTO_ANSWER_MS
        QTimer.singleShot(wait, self, lambda: self.goto_timed_out(p, wait))

    def goto_timed_out(self, name, wait):
        if self.state != MOVING or self.point() != name:
            return
        self.link_label.setText(
            f"<span style='color:{ERR_COLOR}'>De calibrator meldt na {wait // 1000} s nog niets. "
            "Kijk of er op het scherm van de printer een vraag openstaat (bijv. het metrics-adres "
            "bevestigen) en of het calibratorvenster reageert; klik daarna opnieuw.</span>")
        self.set_state(GOTO)

    def jog_step(self):
        return JOG_STEPS[max(self.step_group.checkedId(), 0)]

    def jog(self, dx, dy, dz):
        if self.state != JOG:
            return
        s = self.jog_step()
        if not self.main.server.jog(dx * s, dy * s, dz * s, JOG_FEED):
            self.link_label.setText(f"<span style='color:{ERR_COLOR}'>Calibrator niet verbonden.</span>")

    def send_position(self):
        if self.state != JOG:
            return
        if self.main.server.request_position(self.point()):
            self.set_state(SENDING)
        else:
            self.link_label.setText(f"<span style='color:{ERR_COLOR}'>Calibrator niet verbonden.</span>")

    def on_message(self, msg):
        kind = msg.get("type")
        if kind == "hello":
            self.link_label.setText("Calibrator verbonden.")
        elif kind == "bye":
            self.link_label.setText(f"<span style='color:{ERR_COLOR}'>De calibrator is gesloten.</span>")
            if self.state in (MOVING, JOG, SENDING):
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
            self.link_label.setText(f"Kegel {self.index + 1} ingestuurd: X{msg['x']:.2f} Y{msg['y']:.2f}.")
            self.index += 1
            if self.index >= len(calibration.POINTS):
                self.finish()
            else:
                self.set_state(GOTO)

    def finish(self):
        self.index = len(calibration.POINTS) - 1
        cal = self.main.left.cal_points()
        issues = calibration.validate(cal)
        errors = [i.msg for i in issues if i.level == "error"]
        self.success = not issues
        if errors:
            text = (f"<span style='color:{ERR_COLOR}'><b>Kalibratie mislukt:</b> {' '.join(errors)}</span>"
                    "<br>Klik <b>Opnieuw beginnen</b>.")
        else:
            t = calibration.fit(cal)
            res = calibration.residuals(cal, t)
            details = (f"Restfouten: " + ", ".join(f"kegel {i + 1} {r:.2f} mm" for i, r in enumerate(res.values()))
                       + f"; rotatie {t.rotation_deg:.2f}°.")
            if issues:
                text = (f"<span style='color:{WARN_COLOR}'><b>De punten kloppen niet goed:</b> "
                        + " ".join(i.msg for i in issues) + f"</span><br>{details}<br>"
                        "Klik <b>Opnieuw beginnen</b>.")
            else:
                saved = self.save_measured_offset()
                text = (f"<span style='color:{OK_COLOR}; font-size: 16px;'><b>Kalibratie geslaagd! De printer "
                        "weet nu waar de sensorkop t.o.v. de objecten is.</b></span><br><br>"
                        f"Positie bekend. {details}" + (f"<br>{saved}" if saved else ""))
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

    def save_measured_offset(self):
        """Keep the measured X/Y offset, so the next calibration starts right above the cones."""
        off = self.measured_offset()
        if off is None:
            return ""
        try:
            self.settings = machine_settings.save_values("sensor", {"offset_x": round(off[0], 2),
                                                                    "offset_y": round(off[1], 2)})
        except machine_settings.SettingsError as e:
            return f"Gemeten sensorafstand niet opgeslagen: {e}"
        return (f"Gemeten sensorafstand (X {off[0]:+.2f} mm, Y {off[1]:+.2f} mm) opgeslagen in "
                "machine_settings.toml.")

    def restart(self):
        cal = self.main.left.cal_points()
        for p in calibration.POINTS:
            cal.machine[p] = None
        self.main.left.set_cal_points(cal)
        self.index = 0
        self.success = False
        self.link_label.setText("")
        self.set_state(GOTO if self.manifest() is not None else NO_PLATE)

    def closeEvent(self, e):
        if self.state != DONE and (self.index > 0 or self.state in (MOVING, JOG, SENDING)):
            if QMessageBox.question(self, "Kalibratie", "De kalibratie is nog niet af. Toch sluiten?") \
                    != QMessageBox.Yes:
                e.ignore()
                return
        super().closeEvent(e)
