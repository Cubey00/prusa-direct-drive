"""Model files and outer dimensions (this widget), plus the calibration box (`cal_box`), which the
main window places on its own: calibration points and the visual calibrator link."""
from PySide6.QtCore import QTimer, Signal
from PySide6.QtWidgets import (QAbstractSpinBox, QCheckBox, QDoubleSpinBox, QFrame, QGridLayout, QGroupBox, QHBoxLayout,
                               QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget)

from ..calibration import LABELS, POINTS, CalPoints
from .style import ACCENT, ACCENT_DARK, action_button


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
        lay.setContentsMargins(4, 0, 4, 4)

        # --- file ---
        self.file_box = file_box = QGroupBox("Files")
        fl = QGridLayout(file_box)
        self.open_btn = QPushButton("Open model...")
        self.save_folder_btn = QPushButton("Programs folder...")
        self.save_folder_btn.setToolTip("Default folder for saving and loading program (.json) files")
        self.runs_folder_btn = QPushButton("Runs folder...")
        self.runs_folder_btn.setToolTip("Folder in which each run gets its own folder (log, images, heightmap)")
        self.run_name = QLineEdit()
        self.run_name.setPlaceholderText("run_YYYYMMDD_HHMMSS")
        self.run_name.setToolTip("Name of this run's folder inside the runs folder")
        self.file_label = QLabel("[no model]")
        self.file_label.setWordWrap(True)
        self.folder_label = QLabel("")
        self.folder_label.setWordWrap(True)
        self.folder_label.setStyleSheet("color: gray;")
        self.runs_folder_label = QLabel("")
        self.runs_folder_label.setWordWrap(True)
        self.runs_folder_label.setStyleSheet("color: gray;")
        fl.addWidget(self.open_btn, 0, 0, 1, 2)
        fl.addWidget(self.file_label, 1, 0, 1, 2)
        fl.addWidget(self.save_folder_btn, 2, 0)
        fl.addWidget(self.runs_folder_btn, 2, 1)
        fl.addWidget(self.folder_label, 3, 0, 1, 2)
        fl.addWidget(self.runs_folder_label, 4, 0, 1, 2)
        fl.addWidget(QLabel("Run name"), 5, 0)
        fl.addWidget(self.run_name, 5, 1)
        lay.addWidget(file_box)

        # --- dimensions ---
        self.dim_box = dim_box = QGroupBox("Landscape dimensions")
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
        self.cal_box = cal_box = QGroupBox("Kalibratie")
        cv = QVBoxLayout(cal_box)
        self.calib_btn = QPushButton("Start kalibratie")
        self.calib_btn.setToolTip("Stap voor stap de sensor op kegel 1, 2 en 3 zetten; start ook de calibrator")
        action_button(self.calib_btn, ACCENT, ACCENT_DARK)
        cv.addWidget(self.calib_btn)
        self.link_label = QLabel("Calibrator not running")
        self.link_label.setWordWrap(True)
        self.link_label.setStyleSheet("color: gray; font-weight: normal;")
        cv.addWidget(self.link_label)
        self.diag_label = QLabel("")
        self.diag_label.setWordWrap(True)
        cv.addWidget(self.diag_label)
        # The fiducial coordinates themselves (face from the plate manifest, machine from the
        # calibration) are kept here but not shown: the wizard fills them.
        self.points_box = QWidget()
        cl = QGridLayout(self.points_box)
        self.face_fields, self.machine_fields = {}, {}
        for r, p in enumerate(POINTS):
            cl.addWidget(QLabel(LABELS[p]), r, 0)
            self.face_fields[p] = self._xy_row(cl, r)
        for r, p in enumerate(POINTS, start=len(POINTS)):
            cl.addWidget(QLabel(LABELS[p]), r, 0)
            self.machine_fields[p] = self._xy_row(cl, r)
        self.points_box.setVisible(False)
        cv.addWidget(self.points_box)

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
        self.diag_label.setStyleSheet(f"color: {color}; font-weight: normal;")
        self.diag_label.setText(text)
