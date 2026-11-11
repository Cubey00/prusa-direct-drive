"""Run settings box (approach/travel speed, max Z travel, contact threshold), shown next to the model controls."""
from PySide6.QtCore import Signal
from PySide6.QtWidgets import QGridLayout, QGroupBox, QLabel

from .. import config
from .left_panel import _spin


class RunSettingsBox(QGroupBox):
    changed = Signal()

    def __init__(self, parent=None):
        super().__init__("Run settings", parent)
        sl = QGridLayout(self)
        self.approach = _spin(*config.APPROACH_SPEED_RANGE)
        self.approach.setSuffix(" mm/s")
        self.travel = _spin(*config.TRAVEL_SPEED_RANGE)
        self.travel.setSuffix(" mm/s")
        self.max_travel = _spin(*config.MAX_TRAVEL_RANGE)
        self.contact = _spin(0.5, 100)
        self.contact.setSuffix(" g")
        for i, (text, w) in enumerate((("Approach speed (until contact)", self.approach),
                                       ("XY travel speed", self.travel),
                                       ("Max Z travel to find contact", self.max_travel),
                                       ("Contact threshold", self.contact))):
            sl.addWidget(QLabel(text), i, 0)
            sl.addWidget(w, i, 1)
            w.valueChanged.connect(lambda: self.changed.emit())

    def settings_values(self):
        return dict(approach_speed=self.approach.value(), travel_speed=self.travel.value(),
                    max_travel=self.max_travel.value(), contact_g=self.contact.value())

    def set_settings(self, s):
        self.approach.setValue(s.approach_speed)
        self.travel.setValue(s.travel_speed)
        self.max_travel.setValue(s.max_travel)
        self.contact.setValue(s.contact_g)
