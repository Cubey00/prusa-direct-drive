"""Look of the path programmer: one stylesheet, plus the colours of the main action buttons."""

ACCENT = "#2563eb"          # blue: calibration
ACCENT_DARK = "#1d4ed8"
GO = "#16a34a"              # green: print
GO_DARK = "#15803d"
STOP = "#dc2626"
STOP_DARK = "#b91c1c"


def action_button(button, color, dark):
    """A big coloured button for one of the main actions."""
    button.setMinimumHeight(38)
    button.setStyleSheet(
        f"QPushButton {{ background: {color}; color: white; font-weight: 600; font-size: 14px;"
        f" border: none; border-radius: 6px; padding: 8px 14px; }}"
        f"QPushButton:hover {{ background: {dark}; }}"
        f"QPushButton:pressed {{ background: {dark}; padding-top: 9px; }}"
        "QPushButton:disabled { background: #cbd5e1; color: #f8fafc; }")


APP_STYLESHEET = """
QMainWindow, QWidget#page { background: #eef1f5; }
QGroupBox {
    background: #ffffff; border: 1px solid #dde2ea; border-radius: 8px;
    margin-top: 14px; padding: 10px 8px 8px 8px; font-weight: 600; color: #1f2937;
}
QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 4px; color: #334155; }
QFrame#collapsible { background: #ffffff; border: 1px solid #dde2ea; border-radius: 8px; }
QToolButton#collapsibleHeader {
    border: none; background: transparent; font-weight: 600; color: #334155;
    padding: 6px 8px; text-align: left;
}
QToolButton#collapsibleHeader:hover { color: #2563eb; }
QPushButton {
    background: #ffffff; border: 1px solid #cbd5e1; border-radius: 6px; padding: 5px 10px; color: #1f2937;
}
QPushButton:hover { background: #f1f5f9; border-color: #94a3b8; }
QPushButton:pressed { background: #e2e8f0; }
QPushButton:disabled { color: #a0a7b4; background: #f8fafc; border-color: #e2e8f0; }
QLineEdit, QComboBox, QDoubleSpinBox, QSpinBox, QPlainTextEdit, QListWidget, QTableView {
    background: #ffffff; border: 1px solid #cbd5e1; border-radius: 5px; padding: 3px;
    selection-background-color: #cfe0ff; selection-color: #111827;
}
QProgressBar {
    border: 1px solid #cbd5e1; border-radius: 5px; background: #f1f5f9; text-align: center; height: 16px;
}
QProgressBar::chunk { background: #2563eb; border-radius: 4px; }
QSplitter::handle { background: #dde2ea; }
QScrollArea { border: none; background: transparent; }
QCheckBox::indicator { width: 13px; height: 13px; border: 1px solid #7a7a7a; border-radius: 2px; background: white; }
QCheckBox::indicator:checked { background: #2563eb; border-color: #1d4ed8; }
"""
