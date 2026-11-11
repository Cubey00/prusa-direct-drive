"""Bottom right: execute / stop / load / save, progress and status."""
from PySide6.QtWidgets import (QCheckBox, QGridLayout, QLabel, QProgressBar, QPushButton, QWidget)


class RunControls(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        g = QGridLayout(self)
        self.execute_btn = QPushButton("Execute program")
        self.load_btn = QPushButton("Load program...")
        self.save_btn = QPushButton("Save program as...")
        self.stop_btn = QPushButton("STOP")
        self.stop_btn.setStyleSheet("background: #c00000; color: white; font-weight: bold; "
                                    "font-size: 18px; padding: 10px;")
        self.simulate = QCheckBox("Simulate (dry run, no printer)")
        self.progress = QProgressBar()
        self.progress.setFormat("%v / %m")
        self.status = QLabel("Idle")
        self.status.setWordWrap(True)
        g.addWidget(self.execute_btn, 0, 0)
        g.addWidget(self.load_btn, 0, 1)
        g.addWidget(self.save_btn, 0, 2)
        g.addWidget(self.stop_btn, 1, 0, 1, 3)       # always enabled
        g.addWidget(self.simulate, 2, 0, 1, 3)
        g.addWidget(self.progress, 3, 0, 1, 3)
        g.addWidget(self.status, 4, 0, 1, 3)

    def set_running(self, running):
        for b in (self.execute_btn, self.load_btn, self.save_btn, self.simulate):
            b.setEnabled(not running)
