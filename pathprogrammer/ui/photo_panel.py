"""Foto control: the live camera image, always on, and a button that saves a photo in a folder."""
from pathlib import Path

from PySide6.QtCore import QSettings, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QImage, QPixmap
from PySide6.QtWidgets import (QFileDialog, QGroupBox, QHBoxLayout, QLabel, QPushButton, QSizePolicy,
                               QVBoxLayout)

from .. import config
from ..camera_feed import CameraFeed, default_camera
from .style import ACCENT, ACCENT_DARK, action_button

PHOTOS_DIR = config.PROJECT_DIR / "photos"


def to_qimage(a):
    """QImage (own copy) of an 8-bit H x W or H x W x 3 array."""
    h, w = a.shape[:2]
    if a.ndim == 2:
        return QImage(a.data, w, h, w, QImage.Format_Grayscale8).copy()
    return QImage(a.data, w, h, 3 * w, QImage.Format_RGB888).copy()


class LiveView(QLabel):
    """Shows the latest frame scaled into the widget, keeping its aspect ratio."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAlignment(Qt.AlignCenter)
        self.setMinimumSize(240, 160)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setStyleSheet("background: #0f172a; color: #94a3b8; border-radius: 6px;")
        self._image = None
        self.setText("Geen camerabeeld")

    def set_image(self, image):
        self._image = image
        self._rescale()

    def clear_image(self, text):
        self._image = None
        self.setPixmap(QPixmap())
        self.setText(text)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._rescale()

    def _rescale(self):
        if self._image is not None:
            self.setPixmap(QPixmap.fromImage(self._image).scaled(self.size(), Qt.KeepAspectRatio,
                                                                  Qt.SmoothTransformation))


class PhotoPanel(QGroupBox):
    frameReady = Signal(object)         # numpy preview, from the feed thread
    stateChanged = Signal(bool, str)
    photoSaved = Signal(object, object)  # path or None, error or None

    def __init__(self, settings=None, open_camera=None, parent=None):
        super().__init__("Foto control", parent)
        self.settings = settings or QSettings("TUDelft", "pathprogrammer")
        self.count = 0
        self.view = LiveView()
        self.state_label = QLabel("Camera zoeken...")
        self.state_label.setStyleSheet("color: #64748b; font-weight: normal;")
        self.photo_btn = QPushButton("Maak foto")
        action_button(self.photo_btn, ACCENT, ACCENT_DARK)
        self.photo_btn.setEnabled(False)
        self.folder_btn = QPushButton("Map...")
        self.folder_btn.setToolTip("Map waarin de foto's komen")
        self.open_btn = QPushButton("Open map")
        self.saved_label = QLabel("")
        self.saved_label.setWordWrap(True)
        self.saved_label.setStyleSheet("color: #64748b; font-weight: normal;")
        self.saved_label.setTextInteractionFlags(Qt.TextSelectableByMouse)

        lay = QVBoxLayout(self)
        lay.addWidget(self.view, 1)
        lay.addWidget(self.state_label)
        row = QHBoxLayout()
        row.addWidget(self.photo_btn, 2)
        row.addWidget(self.folder_btn)
        row.addWidget(self.open_btn)
        lay.addLayout(row)
        lay.addWidget(self.saved_label)

        self.photo_btn.clicked.connect(self.take_photo)
        self.folder_btn.clicked.connect(self.choose_folder)
        self.open_btn.clicked.connect(lambda: self._open_folder())
        self.frameReady.connect(self._on_frame)
        self.stateChanged.connect(self._on_state)
        self.photoSaved.connect(self._on_saved)
        self.feed = CameraFeed(self.frameReady.emit, self.stateChanged.emit, self.photoSaved.emit,
                               open_camera or (lambda: default_camera(config.CAMERA_EXPOSURE_US)))
        self._show_folder()
        if CameraFeed.disabled():
            self.state_label.setText("Camera uitgeschakeld (PRINTERPROJECT_NO_CAMERA)")
        self.feed.start()

    # ---------------- folder ----------------
    def folder(self):
        return Path(self.settings.value("photo_folder", str(PHOTOS_DIR)))

    def choose_folder(self):
        path = QFileDialog.getExistingDirectory(self, "Map voor foto's", str(self.folder()))
        if path:
            self.settings.setValue("photo_folder", path)
            self._show_folder()

    def _show_folder(self):
        self.saved_label.setText(f"Foto's gaan naar: {self.folder()}")

    def _open_folder(self):
        self.folder().mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.folder())))

    # ---------------- camera ----------------
    def take_photo(self):
        if not self.feed.take_photo(self.folder()):
            self.saved_label.setText("Geen camera aangesloten: er is geen foto gemaakt.")

    def release(self):
        """Close the camera so a run can use it."""
        self.feed.release()
        self.view.clear_image("Camera in gebruik door de run")

    def resume(self):
        self.feed.start()

    def shutdown(self):
        self.feed.release()

    def _on_frame(self, a):
        self.view.set_image(to_qimage(a))

    def _on_state(self, connected, message):
        self.photo_btn.setEnabled(connected)
        color = "#16a34a" if connected else "#b45309"
        self.state_label.setStyleSheet(f"color: {color}; font-weight: normal;")
        self.state_label.setText(("● " if connected else "○ ") + message
                                 + ("" if connected or not self.feed.is_running() else
                                    " (zoekt automatisch verder)"))
        if not connected and self.feed.is_running():
            self.view.clear_image("Geen camerabeeld")

    def _on_saved(self, path, error):
        if error:
            self.saved_label.setText(f"Foto niet opgeslagen: {error}")
            return
        self.count += 1
        self.saved_label.setText(f"Foto {self.count} opgeslagen: {path}")
