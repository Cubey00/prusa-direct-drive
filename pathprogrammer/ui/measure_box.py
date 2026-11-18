"""Meten: drive the sensor to the parts of the plate. First step: hang 1 cm above point 1."""
from PySide6.QtWidgets import QGroupBox, QLabel, QPushButton, QVBoxLayout

from .style import action_button

INDIGO, INDIGO_DARK = "#4f46e5", "#4338ca"


class MeasureBox(QGroupBox):
    def __init__(self, parent=None):
        super().__init__("Meten", parent)
        self.hover_btn = QPushButton("Ga 1 cm boven punt 1 hangen")
        self.hover_btn.setToolTip("Het bed zakt naar een veilige hoogte, de kop rijdt naar het eerste onderdeel "
                                  "van de plaat en de sensor blijft 1 cm boven de bovenkant hangen")
        action_button(self.hover_btn, INDIGO, INDIGO_DARK)
        self.next_btn = QPushButton("Ga naar volgend punt")
        self.next_btn.setToolTip("Het bed zakt eerst naar een veilige hoogte (de sensor gaat omhoog, vrij van "
                                 "alle onderdelen), de kop rijdt naar het volgende onderdeel en de sensor "
                                 "blijft 1 cm boven de bovenkant hangen")
        action_button(self.next_btn, INDIGO, INDIGO_DARK)
        self.next_btn.setEnabled(False)
        self.touch_btn = QPushButton("Beweeg heel langzaam tot de sensor het object raakt")
        self.touch_btn.setToolTip("Het bed gaat heel langzaam omhoog (0,1 mm/s) en stopt zodra de load cell "
                                  "contact voelt; de sensor blijft het object dan net raken. STOP stopt het.")
        action_button(self.touch_btn, INDIGO, INDIGO_DARK)
        self.step_btn = QPushButton("Ga 0,1 mm naar beneden")
        self.step_btn.setToolTip("De sensor drukt 0,1 mm dieper in het object (het bed gaat 0,1 mm omhoog) "
                                 "en stopt zodra de kracht boven 300 g komt. Kan pas na de eerste aanraking.")
        action_button(self.step_btn, INDIGO, INDIGO_DARK)
        self.depth_label = QLabel("")
        self.depth_label.setStyleSheet("font-weight: bold;")
        self.status = QLabel("Open een plaat en kalibreer; daarna kan de sensor naar de onderdelen.")
        self.status.setWordWrap(True)
        self.status.setStyleSheet("color: #64748b; font-weight: normal;")
        lay = QVBoxLayout(self)
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
