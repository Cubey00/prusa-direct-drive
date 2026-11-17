import sys
from pathlib import Path

from PySide6.QtGui import QColor, QIcon, QPalette
from PySide6.QtWidgets import QApplication

from .ui.main_window import MainWindow
from .ui.style import APP_STYLESHEET


def light_palette():
    p = QPalette()
    for role, color in ((QPalette.Window, "#f5f5f5"), (QPalette.WindowText, "#1a1a1a"),
                        (QPalette.Base, "#ffffff"), (QPalette.AlternateBase, "#f0f0f0"),
                        (QPalette.Text, "#1a1a1a"), (QPalette.Button, "#ffffff"),
                        (QPalette.ButtonText, "#1a1a1a"), (QPalette.Highlight, "#cfe3ff"),
                        (QPalette.HighlightedText, "#1a1a1a"), (QPalette.ToolTipBase, "#ffffff"),
                        (QPalette.ToolTipText, "#1a1a1a"), (QPalette.PlaceholderText, "#888888"),
                        (QPalette.Light, "#ffffff"), (QPalette.Midlight, "#d8d8d8"),
                        (QPalette.Mid, "#a8a8a8"), (QPalette.Dark, "#7a7a7a"), (QPalette.Shadow, "#505050")):
        p.setColor(role, QColor(color))
    for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
        p.setColor(QPalette.Disabled, role, QColor("#a0a0a0"))
    return p


ICON = Path(__file__).resolve().parent.parent / "assets" / "icon.png"


def main():
    if sys.platform == "win32":          # own taskbar button with our icon, not python's
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("TUDelft.TouchPathProgrammer")
        except Exception:
            pass
    app = QApplication(sys.argv)
    app.setApplicationDisplayName("Touch path programmer")
    if ICON.exists():
        app.setWindowIcon(QIcon(str(ICON)))
    app.setStyle("Fusion")             # follows the palette, unlike the native style in Windows dark mode
    app.setPalette(light_palette())
    app.setStyleSheet(APP_STYLESHEET)
    win = MainWindow()
    if len(sys.argv) > 1 and sys.argv[1].lower().endswith(".3mf"):
        win.open_model(sys.argv[1])
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
