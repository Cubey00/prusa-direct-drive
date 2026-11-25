"""Measure: drive the sensor to the parts of the plate. First step: hover just above point 1."""
from PySide6.QtWidgets import (QDoubleSpinBox, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QPushButton,
                               QSpinBox, QVBoxLayout)

from ..measure import HOVER_TEXT
from .style import action_button

INDIGO, INDIGO_DARK = "#4f46e5", "#4338ca"
GREEN, GREEN_DARK = "#16a34a", "#15803d"


class MeasureBox(QGroupBox):
    def __init__(self, parent=None):
        super().__init__("Measure", parent)
        # --- the whole procedure in one click, with its settings next to it ---
        self.proc_btn = QPushButton("Do whole measuring procedure")
        self.proc_btn.setToolTip("Home X and Y (uses the saved calibration), go just above each part, touch the "
                                 "object (indentation 0, photo), then press the set number of steps deeper with "
                                 "a photo at every step. Stops early at the max force. All photos go into one new "
                                 "folder named after the date and start time.")
        action_button(self.proc_btn, GREEN, GREEN_DARK)
        self.max_force = QDoubleSpinBox()
        self.max_force.setRange(10.0, 400.0)
        self.max_force.setDecimals(0)
        self.max_force.setSingleStep(10.0)
        self.max_force.setSuffix(" g")
        self.max_force.setValue(300.0)
        self.max_force.setToolTip("Maximum load cell force: a step stops here and the procedure ends")
        self.step_mm = QDoubleSpinBox()
        self.step_mm.setRange(0.01, 1.0)
        self.step_mm.setDecimals(2)
        self.step_mm.setSingleStep(0.05)
        self.step_mm.setSuffix(" mm")
        self.step_mm.setValue(0.1)
        self.step_mm.setToolTip("How far the sensor presses deeper per step")
        self.n_steps = QSpinBox()
        self.n_steps.setRange(1, 200)
        self.n_steps.setValue(10)
        self.n_steps.setToolTip("Number of steps after the first touch (one photo per step)")
        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.addRow("Max force", self.max_force)
        form.addRow("Down per step", self.step_mm)
        form.addRow("Steps", self.n_steps)
        proc_row = QHBoxLayout()
        proc_row.addWidget(self.proc_btn, 1)
        proc_row.addLayout(form)
        self.hover_btn = QPushButton(f"Hover {HOVER_TEXT} above point 1")
        self.hover_btn.setToolTip("The bed lowers to a safe height, the head drives to the first part of the "
                                  f"plate and the sensor stops {HOVER_TEXT} above its top")
        action_button(self.hover_btn, INDIGO, INDIGO_DARK)
        self.next_btn = QPushButton("Go to next point")
        self.next_btn.setToolTip("The bed first lowers to a safe height (the sensor goes up, clear of all "
                                 "parts), the head drives to the next part and the sensor stops "
                                 f"{HOVER_TEXT} above its top")
        action_button(self.next_btn, INDIGO, INDIGO_DARK)
        self.next_btn.setEnabled(False)
        self.touch_btn = QPushButton("Move very slowly until the sensor touches the object")
        self.touch_btn.setToolTip("The bed rises very slowly (0.1 mm/s) and stops as soon as the load cell "
                                  "feels contact; the sensor then just touches the object. STOP stops it.")
        action_button(self.touch_btn, INDIGO, INDIGO_DARK)
        self.step_btn = QPushButton("Go 0.1 mm down")
        self.step_btn.setToolTip("The sensor presses 0.1 mm deeper into the object (the bed rises 0.1 mm) "
                                 "and stops as soon as the force exceeds 300 g. Only after the first touch.")
        action_button(self.step_btn, INDIGO, INDIGO_DARK)
        self.depth_label = QLabel("")
        self.depth_label.setStyleSheet("font-weight: bold;")
        self.status = QLabel("Open a plate and calibrate; then the sensor can go to the parts.")
        self.status.setWordWrap(True)
        self.status.setStyleSheet("color: #64748b; font-weight: normal;")
        lay = QVBoxLayout(self)
        lay.addLayout(proc_row)
        lay.addWidget(self.hover_btn)
        lay.addWidget(self.next_btn)
        lay.addWidget(self.touch_btn)
        lay.addWidget(self.step_btn)
        lay.addWidget(self.depth_label)
        lay.addWidget(self.status)

    def set_status(self, text, level="info"):
        color = {"info": "#64748b", "ok": "#16a34a", "busy": "#2563eb", "error": "#c00000"}[level]
        self.status.setStyleSheet(f"color: {color}; font-weight: normal;")
        self.status.setText(text)
