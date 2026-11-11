"""Left panel: model file, outer dimensions, calibration points and the visual calibrator link."""
from PySide6.QtCore import QTimer, Signal
from PySide6.QtWidgets import (QAbstractSpinBox, QCheckBox, QDoubleSpinBox, QFrame, QGridLayout, QGroupBox, QHBoxLayout,
                               QLabel, QPushButton, QVBoxLayout, QWidget)

from ..calibration import POINTS, CalPoints

NAMES = {"back_left": "back left", "front_right": "front right"}


class SelectAllSpinBox(QDoubleSpinBox):
    """Selects its text on focus, so one click and typing replaces the value (or the "--" placeholder)."""

    def focusInEvent(self, e):
        super().focusInEvent(e)
        QTimer.singleShot(0, self.selectAll)      # after the click has placed the cursor


def _spin(lo=-1e4, hi=1e4):
    s = SelectAllSpinBox()
    s.setRange(lo, hi)
    s.setDecimals(2)
    s.setSuffix(" mm")
    s.setKeyboardTracking(False)
    s.setButtonSymbols(QAbstractSpinBox.NoButtons)
    return s


class LeftPanel(QWidget):
    calibrationChanged = Signal()
    faceSizeChanged = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)

        # --- file ---
        file_box = QGroupBox("Files")
        fl = QGridLayout(file_box)
        self.open_btn = QPushButton("Open model...")
        self.save_folder_btn = QPushButton("Programs folder...")
        self.save_folder_btn.setToolTip("Default folder for saving and loading program (.json) files")
        self.log_folder_btn = QPushButton("Run logs folder...")
        self.log_folder_btn.setToolTip("Folder where run logs are written when a program is executed")
        self.file_label = QLabel("[no model]")
        self.file_label.setWordWrap(True)
        self.folder_label = QLabel("")
        self.folder_label.setWordWrap(True)
        self.folder_label.setStyleSheet("color: gray;")
        self.log_folder_label = QLabel("")
        self.log_folder_label.setWordWrap(True)
        self.log_folder_label.setStyleSheet("color: gray;")
        fl.addWidget(self.open_btn, 0, 0, 1, 2)
        fl.addWidget(self.file_label, 1, 0, 1, 2)
        fl.addWidget(self.save_folder_btn, 2, 0)
        fl.addWidget(self.log_folder_btn, 2, 1)
        fl.addWidget(self.folder_label, 3, 0, 1, 2)
        fl.addWidget(self.log_folder_label, 4, 0, 1, 2)
        lay.addWidget(file_box)

        # --- dimensions ---
        dim_box = QGroupBox("Landscape dimensions")
        dl = QGridLayout(dim_box)
        dl.addWidget(QLabel("Outer dimensions"), 0, 0)
        self.dim_x, self.dim_y = _spin(0.01), _spin(0.01)
        dl.addWidget(QLabel("X"), 0, 1)
        dl.addWidget(self.dim_x, 0, 2)
        dl.addWidget(QLabel("Y"), 0, 3)
        dl.addWidget(self.dim_y, 0, 4)
        self.dim_override = QCheckBox("Override")
        dl.addWidget(self.dim_override, 1, 0)
        self.units_label = QLabel("")
        self.units_label.setStyleSheet("color: #b36b00;")
        dl.addWidget(self.units_label, 1, 1, 1, 4)
        self.dim_override.toggled.connect(self._override_toggled)
        self._override_toggled(False)
        self.dim_x.valueChanged.connect(lambda: self.faceSizeChanged.emit())
        self.dim_y.valueChanged.connect(lambda: self.faceSizeChanged.emit())
        lay.addWidget(dim_box)

        # --- calibration ---
        cal_box = QGroupBox("Calibration")
        cl = QGridLayout(cal_box)
        cl.addWidget(QLabel("Location of points (from <b>back left corner</b> of the face):"), 0, 0, 1, 5)
        self.face_fields, self.machine_fields = {}, {}
        r = 1
        for p in POINTS:
            cl.addWidget(QLabel(f"{NAMES[p]} point"), r, 0)
            self.face_fields[p] = self._xy_row(cl, r)
            r += 1
        self.calib_btn = QPushButton("Open visual calibrator")
        self.calib_btn.setStyleSheet("font-size: 15px; padding: 8px;")
        cl.addWidget(self.calib_btn, r, 0, 1, 5)
        r += 1
        self.link_label = QLabel("Calibrator not running")
        self.link_label.setStyleSheet("color: gray;")
        cl.addWidget(self.link_label, r, 0, 1, 5)
        r += 1
        cl.addWidget(QLabel("Absolute locations (machine):"), r, 0, 1, 5)
        r += 1
        for p in POINTS:
            cl.addWidget(QLabel(f"{NAMES[p]} point"), r, 0)
            self.machine_fields[p] = self._xy_row(cl, r)
            r += 1
        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        cl.addWidget(line, r, 0, 1, 5)
        r += 1
        self.diag_label = QLabel("")
        self.diag_label.setWordWrap(True)
        cl.addWidget(self.diag_label, r, 0, 1, 5)
        lay.addWidget(cal_box)

        lay.addStretch()

    def _xy_row(self, grid, r):
        x, y = _spin(), _spin()
        x.setSpecialValueText("--")
        y.setSpecialValueText("--")
        x.setMinimum(-10000.01)
        y.setMinimum(-10000.01)            # minimum shows as "--" = not set
        x.setValue(x.minimum())
        y.setValue(y.minimum())
        grid.addWidget(QLabel("X"), r, 1)
        grid.addWidget(x, r, 2)
        grid.addWidget(QLabel("Y"), r, 3)
        grid.addWidget(y, r, 4)
        for s in (x, y):
            s.valueChanged.connect(lambda: self.calibrationChanged.emit())
        return x, y

    def _override_toggled(self, on):
        self.dim_x.setReadOnly(not on)
        self.dim_y.setReadOnly(not on)

    # --- values ---
    @staticmethod
    def _get(pair):
        x, y = pair
        if x.value() == x.minimum() or y.value() == y.minimum():
            return None
        return x.value(), y.value()

    @staticmethod
    def _set(pair, xy):
        x, y = pair
        for s, v in zip((x, y), xy if xy is not None else (None, None)):
            s.blockSignals(True)
            s.setValue(s.minimum() if v is None else v)
            s.blockSignals(False)

    def cal_points(self):
        c = CalPoints()
        for p in POINTS:
            c.face[p] = self._get(self.face_fields[p])
            c.machine[p] = self._get(self.machine_fields[p])
        return c

    def set_cal_points(self, cal):
        for p in POINTS:
            self._set(self.face_fields[p], cal.face[p] if cal else None)
            self._set(self.machine_fields[p], cal.machine[p] if cal else None)
        self.calibrationChanged.emit()

    def set_machine_point(self, name, x, y):
        self._set(self.machine_fields[name], (x, y))
        self.calibrationChanged.emit()

    def set_face_size(self, w, h):
        if self.dim_override.isChecked():
            return
        for s, v in ((self.dim_x, w), (self.dim_y, h)):
            s.blockSignals(True)
            s.setValue(v)
            s.blockSignals(False)

    def face_size(self):
        return self.dim_x.value(), self.dim_y.value()

    def set_diagnostics(self, text, level):
        color = {"ok": "#1b7a1b", "warning": "#b36b00", "error": "#c00000"}.get(level, "gray")
        self.diag_label.setStyleSheet(f"color: {color};")
        self.diag_label.setText(text)
