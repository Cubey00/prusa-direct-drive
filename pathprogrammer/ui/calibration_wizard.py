"""'Start kalibratie': step-by-step calibration of the sensor on the plate's fiducials F1, F2, F3.

The sensor's offset from the nozzle (machine_settings.toml [sensor]) predicts where the head must
go to put the sensor above each cone. "Rijd erheen" sends the calibrator there (bed to a safe
travel height first, then X/Y); the user fine-jogs onto the tip and sends the position from the
calibrator. After the third point the 3-point fit says whether the calibration succeeded.
"""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog, QDoubleSpinBox, QFormLayout, QGridLayout, QGroupBox, QHBoxLayout,
                               QLabel, QMessageBox, QPushButton, QVBoxLayout)

import machine_settings
from .. import calibration

PLACE = {"F1": "linksvoor", "F2": "rechtsvoor", "F3": "linksachter"}
OK_COLOR, WARN_COLOR, ERR_COLOR, TODO_COLOR = "#1b7a1b", "#b36b00", "#c00000", "gray"


def _spin(value, lo=-300.0, hi=300.0):
    s = QDoubleSpinBox()
    s.setRange(lo, hi)
    s.setDecimals(1)
    s.setSuffix(" mm")
    s.setSingleStep(0.5)
    s.setValue(value)
    return s


class CalibrationWizard(QDialog):
    def __init__(self, main, parent=None):
        super().__init__(parent or main)
        self.main = main
        self.setWindowTitle("Kalibratie: sensor op F1, F2 en F3")
        self.setMinimumWidth(560)
        self.done_points = set()
        self.settings = machine_settings.load()

        intro = QLabel(
            "<b>Zo kalibreer je</b> (na het printen en de wissel van printkop naar sensor):<ol>"
            "<li>Laat de geprinte plaat op het bed liggen en zet de sensor op de kop. "
            "<b>Niet homen (G28)</b> met de sensor erop.</li>"
            "<li>Controleer hieronder de afstand van de sensor tot de nozzle.</li>"
            "<li>Per kegel: klik <b>Rijd erheen</b>. Het bed zakt eerst naar een veilige hoogte, "
            "daarna rijdt de kop naar de voorspelde plek.</li>"
            "<li>Jog in de calibrator met kleine stappen (1 tot 0,1 mm) tot het midden van de sensor "
            "precies boven de top van de kegel staat. Laat het bed langzaam omhoog komen "
            "(Z omlaag) tot vlak boven de top.</li>"
            "<li>Klik in de calibrator op de knop van die kegel (bijv. <b>F1 front left</b>). "
            "Het venster gaat dan door naar de volgende kegel.</li></ol>")
        intro.setWordWrap(True)
        intro.setTextFormat(Qt.RichText)

        # --- sensor offset ---
        off_box = QGroupBox("Afstand sensor t.o.v. nozzle (machine_settings.toml [sensor])")
        ox, oy, oz = self.settings.sensor.offset
        self.off_x, self.off_y, self.off_z = _spin(ox), _spin(oy), _spin(oz)
        form = QFormLayout()
        form.addRow("X (+ = rechts van de nozzle)", self.off_x)
        form.addRow("Y (- = vóór de nozzle, naar de deur)", self.off_y)
        form.addRow("Z (- = onder de nozzle)", self.off_z)
        self.save_off_btn = QPushButton("Opslaan in machine_settings.toml")
        self.off_status = QLabel("")
        self.off_status.setWordWrap(True)
        ol = QVBoxLayout(off_box)
        ol.addLayout(form)
        row = QHBoxLayout()
        row.addWidget(self.save_off_btn)
        row.addWidget(self.off_status, 1)
        ol.addLayout(row)

        # --- steps ---
        steps = QGroupBox("Kegels")
        g = QGridLayout(steps)
        self.plate_label = QLabel("")
        self.plate_label.setWordWrap(True)
        g.addWidget(self.plate_label, 0, 0, 1, 4)
        self.target_labels, self.state_labels, self.go_btns = {}, {}, {}
        for r, p in enumerate(calibration.POINTS, start=1):
            g.addWidget(QLabel(f"<b>{p}</b> {PLACE[p]}"), r, 0)
            self.target_labels[p] = QLabel("")
            self.state_labels[p] = QLabel("")
            self.go_btns[p] = QPushButton("Rijd erheen")
            self.go_btns[p].clicked.connect(lambda _=False, name=p: self.go_to(name))
            g.addWidget(self.target_labels[p], r, 1)
            g.addWidget(self.go_btns[p], r, 2)
            g.addWidget(self.state_labels[p], r, 3)
        g.setColumnStretch(1, 1)

        self.link_label = QLabel("")
        self.link_label.setWordWrap(True)
        self.result_label = QLabel("")
        self.result_label.setWordWrap(True)
        self.result_label.setTextFormat(Qt.RichText)

        self.restart_btn = QPushButton("Opnieuw beginnen")
        self.take_offset_btn = QPushButton("Gemeten afstand overnemen")
        self.take_offset_btn.setToolTip("Zet de afstand sensor-nozzle die uit deze kalibratie volgt in de "
                                        "velden hierboven (daarna Opslaan)")
        self.take_offset_btn.setEnabled(False)
        close = QPushButton("Sluiten")
        close.clicked.connect(self.close)
        buttons = QHBoxLayout()
        buttons.addWidget(self.restart_btn)
        buttons.addWidget(self.take_offset_btn)
        buttons.addStretch()
        buttons.addWidget(close)

        lay = QVBoxLayout(self)
        lay.addWidget(intro)
        lay.addWidget(off_box)
        lay.addWidget(steps)
        lay.addWidget(self.link_label)
        lay.addWidget(self.result_label)
        lay.addLayout(buttons)

        for s in (self.off_x, self.off_y, self.off_z):
            s.valueChanged.connect(self.refresh)
        self.save_off_btn.clicked.connect(self.save_offset)
        self.restart_btn.clicked.connect(self.restart)
        self.take_offset_btn.clicked.connect(self.take_measured_offset)
        # word-wrapped rich text reports too small a height in a layout: reserve what it needs
        self.setFixedWidth(640)
        for label in (intro,):
            label.setMinimumHeight(label.heightForWidth(600) + 8)
        self.result_label.setMinimumHeight(70)
        self.refresh()

    # ---------------- state ----------------
    def manifest(self):
        model = self.main.model
        return calibration.manifest_for(model.path) if model is not None else None

    def offset(self):
        return self.off_x.value(), self.off_y.value(), self.off_z.value()

    def fiducials(self):
        m = self.manifest()
        return {f["name"]: f for f in m["fiducials"]} if m else {}

    def travel_z(self):
        """Machine Z that keeps the sensor clear of the cones and the tallest part."""
        m = self.manifest()
        tallest = max([f["z"] for f in m["fiducials"]] + [p["size"][2] for p in m["parts"]]) if m else 0
        return tallest + self.settings.sensor.hover_clearance - self.off_z.value()

    def predicted_head(self, name):
        """Head X/Y/Z that puts the sensor above fiducial `name`, at the travel height."""
        f = self.fiducials()[name]
        ox, oy, _ = self.offset()
        return f["x"] - ox, f["y"] - oy, self.travel_z()

    def refresh(self):
        m = self.manifest()
        if m is None:
            self.plate_label.setText("<span style='color:%s'><b>Open eerst een plaat:</b> kies in "
                                     "<i>Print plates</i> de geprinte plaat en klik <i>Open plate in "
                                     "programmer</i>.</span>" % ERR_COLOR)
            for p in calibration.POINTS:
                self.target_labels[p].setText("")
                self.go_btns[p].setEnabled(False)
            return
        self.plate_label.setText(f"Plaat: <b>{self.main.model.filename}</b>. Voorspelde kopposities "
                                 "met de afstand hierboven (nog niet gekalibreerd):")
        (x0, x1), (y0, y1) = self.settings.printer.travel
        nxt = self.next_point()
        for p in calibration.POINTS:
            x, y, z = self.predicted_head(p)
            reachable = x0 <= x <= x1 and y0 <= y <= y1 and 0 <= z <= self.settings.printer.z_max
            self.target_labels[p].setText(f"kop X{x:.1f} Y{y:.1f} Z{z:.1f}" if reachable else
                                          f"<span style='color:{ERR_COLOR}'>X{x:.1f} Y{y:.1f} Z{z:.1f}: "
                                          "buiten bereik, controleer de afstand</span>")
            self.go_btns[p].setEnabled(reachable)
            if p in self.done_points:
                xy = self.main.left.cal_points().machine[p]
                self.state_labels[p].setText(f"<span style='color:{OK_COLOR}'>✓ X{xy[0]:.2f} Y{xy[1]:.2f}</span>")
            elif p == nxt:
                self.state_labels[p].setText(f"<span style='color:{WARN_COLOR}'>&larr; nu deze</span>")
            else:
                self.state_labels[p].setText(f"<span style='color:{TODO_COLOR}'>wacht</span>")
        self.show_result()

    def next_point(self):
        return next((p for p in calibration.POINTS if p not in self.done_points), None)

    # ---------------- actions ----------------
    def ensure_calibrator(self):
        if not self.main.server.is_running():
            self.main.open_calibrator()
            self.link_label.setText("Calibrator wordt gestart. Wacht tot 'Calibrator connected' "
                                    "verschijnt en klik dan opnieuw op Rijd erheen.")
            return False
        return True

    def go_to(self, name):
        if not self.ensure_calibrator():
            return
        x, y, z = self.predicted_head(name)
        if self.main.server.goto(x, y, z, name):
            self.link_label.setText(f"Kop rijdt naar {name}: eerst Z{z:.1f} (bed omlaag), dan X{x:.1f} Y{y:.1f} ...")
        else:
            self.link_label.setText("De calibrator is nog niet verbonden. Wacht even en probeer opnieuw.")

    def on_message(self, msg):
        kind = msg.get("type")
        if kind == "goto_done":
            color = OK_COLOR if msg.get("ok") else ERR_COLOR
            self.link_label.setText(f"<span style='color:{color}'>{msg.get('message', '')}</span>")
        elif kind == "position" and msg.get("point") in calibration.POINTS:
            self.done_points.add(msg["point"])
            nxt = self.next_point()
            self.link_label.setText(f"{msg['point']} ontvangen." +
                                    (f" Volgende: <b>{nxt}</b> ({PLACE[nxt]}): klik Rijd erheen." if nxt else ""))
            self.refresh()

    def restart(self):
        cal = self.main.left.cal_points()
        for p in calibration.POINTS:
            cal.machine[p] = None
        self.main.left.set_cal_points(cal)
        self.done_points.clear()
        self.result_label.setText("")
        self.take_offset_btn.setEnabled(False)
        self.refresh()

    def save_offset(self):
        x, y, z = self.offset()
        try:
            self.settings = machine_settings.save_values("sensor", {"offset_x": x, "offset_y": y, "offset_z": z})
        except machine_settings.SettingsError as e:
            self.off_status.setStyleSheet(f"color: {ERR_COLOR};")
            self.off_status.setText(f"Niet opgeslagen: {e}")
            return
        self.off_status.setStyleSheet(f"color: {OK_COLOR};")
        self.off_status.setText("Opgeslagen.")
        self.refresh()

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

    def take_measured_offset(self):
        off = self.measured_offset()
        if off is not None:
            self.off_x.setValue(off[0])
            self.off_y.setValue(off[1])
            self.off_status.setStyleSheet("")
            self.off_status.setText("Gemeten X/Y overgenomen; klik Opslaan om ze te bewaren.")

    def show_result(self):
        if self.done_points != set(calibration.POINTS):
            self.result_label.setText("")
            self.take_offset_btn.setEnabled(False)
            return
        cal = self.main.left.cal_points()
        issues = calibration.validate(cal)
        errors = [i.msg for i in issues if i.level == "error"]
        if errors:
            self.result_label.setText(f"<span style='color:{ERR_COLOR}'><b>Kalibratie mislukt:</b> "
                                      f"{' '.join(errors)} Klik Opnieuw beginnen.</span>")
            return
        t = calibration.fit(cal)
        res = calibration.residuals(cal, t)
        off = self.measured_offset()
        self.take_offset_btn.setEnabled(off is not None)
        details = (f"Rotatie {t.rotation_deg:.2f}°, schaal {t.scale:.4f}, restfouten "
                   + ", ".join(f"{p} {r:.2f} mm" for p, r in res.items())
                   + (f".<br>Gemeten afstand sensor-nozzle: X {off[0]:+.1f} mm, Y {off[1]:+.1f} mm." if off else "."))
        if issues:
            self.result_label.setText(
                f"<span style='color:{WARN_COLOR}'><b>Kalibratie gelukt, maar controleer:</b> "
                + " ".join(i.msg for i in issues) + f"</span><br>{details}")
        else:
            self.result_label.setText(
                f"<span style='color:{OK_COLOR}; font-size: 15px;'><b>Kalibratie geslaagd! De printer weet "
                f"nu waar de sensorkop t.o.v. de objecten is.</b></span><br>{details}")

    def closeEvent(self, e):
        if self.done_points and self.done_points != set(calibration.POINTS):
            if QMessageBox.question(self, "Kalibratie", "De kalibratie is nog niet af. Toch sluiten?") \
                    != QMessageBox.Yes:
                e.ignore()
                return
        super().closeEvent(e)
