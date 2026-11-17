"""Nozzle -> sensor offset: shown above the calibration button, changed by hand only on purpose.

The calibration measures the offset and saves it; typing values in is the exception. So the
dialog asks twice: a check box, and the word AANPASSEN typed out, before Save works.
"""
from PySide6.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout, QLabel,
                               QLineEdit, QMessageBox, QVBoxLayout)

import machine_settings

CONFIRM_WORD = "AANPASSEN"


def offset_text(sensor):
    """Short description of [sensor] offset for the main window."""
    x, y, z = sensor.offset
    how = "gemeten bij de kalibratie" if sensor.measured else "geschat, nog niet gemeten"
    return (f"Sensorkop t.o.v. nozzle: <b>X {x:+.2f} &nbsp;Y {y:+.2f} &nbsp;Z {z:+.2f} mm</b>"
            f"<br><span style='color:#64748b'>({how})</span>")


class SensorOffsetDialog(QDialog):
    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Sensorafstand handmatig aanpassen")
        self.saved = None
        warn = QLabel(
            "<b style='color:#b91c1c'>Liever niet.</b> De kalibratie meet deze afstand op de kegels en "
            "slaat hem op; dat is nauwkeuriger dan een getal dat je hier intypt. Pas hem alleen aan als je "
            "zeker weet dat de waarde niet klopt (bijvoorbeeld na het verplaatsen van de sensor) en "
            "kalibreer daarna opnieuw.")
        warn.setWordWrap(True)
        self.sure = QCheckBox("Ik weet wat ik doe en wil de ingestelde waarden overschrijven")
        self.fields = []
        form = QFormLayout()
        for name, v in zip("XYZ", settings.sensor.offset):
            s = QDoubleSpinBox()
            s.setRange(-200.0, 200.0)
            s.setDecimals(2)
            s.setSuffix(" mm")
            s.setValue(v)
            s.setEnabled(False)
            form.addRow(f"offset {name}", s)
            self.fields.append(s)
        self.word = QLineEdit()
        self.word.setPlaceholderText(f"typ {CONFIRM_WORD}")
        self.word.setEnabled(False)
        form.addRow("Bevestig", self.word)
        self.buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        self.buttons.button(QDialogButtonBox.Save).setEnabled(False)
        self.buttons.accepted.connect(self.save)
        self.buttons.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addWidget(warn)
        lay.addWidget(self.sure)
        lay.addLayout(form)
        lay.addWidget(self.buttons)
        self.sure.toggled.connect(self._update)
        self.word.textChanged.connect(self._update)

    def _update(self, *_):
        on = self.sure.isChecked()
        for s in self.fields:
            s.setEnabled(on)
        self.word.setEnabled(on)
        self.buttons.button(QDialogButtonBox.Save).setEnabled(
            on and self.word.text().strip().upper() == CONFIRM_WORD)

    def save(self):
        x, y, z = (round(s.value(), 2) for s in self.fields)
        try:
            self.saved = machine_settings.save_values(
                "sensor", {"offset_x": x, "offset_y": y, "offset_z": z, "measured": False})
        except machine_settings.SettingsError as e:
            QMessageBox.warning(self, "Sensorafstand", f"Niet opgeslagen: {e}")
            return
        self.accept()
