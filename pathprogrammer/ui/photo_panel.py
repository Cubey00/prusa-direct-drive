"""Photo control: the live camera image, always on, and a button that saves a photo in a folder.

`tag_provider()` (set by the main window) may return a dict with object / depth_mm / force_g for
the photo taken now (Measure): the file name then says so, and a line goes into measurements.csv in
the photo folder (file, time, object, indentation, force)."""
import csv
import re
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QSettings, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QImage, QPixmap
from PySide6.QtWidgets import (QAbstractSpinBox, QDoubleSpinBox, QFileDialog, QGroupBox, QHBoxLayout, QLabel,
                               QPushButton, QSizePolicy, QVBoxLayout)

from .. import config
from ..camera_feed import CameraFeed, default_camera, photo_name
from .style import ACCENT, ACCENT_DARK, action_button

PHOTOS_DIR = config.PROJECT_DIR / "photos"
LOG_NAME = "measurements.csv"
LOG_HEADER = ["file", "time", "object", "indentation_mm", "force_g"]


def tagged_name(tag, when=None):
    """photo_<object>_d0.300mm_F123.4g_<time>.tif"""
    obj = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(tag.get("object") or "object")).strip("-") or "object"
    return (f"photo_{obj}_d{tag['depth_mm']:.3f}mm_F{tag['force_g']:.1f}g_"
            + photo_name(when).removeprefix("photo_"))
EXPOSURE_MS_RANGE = (0.05, 2000.0)
GAIN_RANGE = (1.0, 16.0)


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
        self.setText("No camera image")

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
        super().__init__("Photo control", parent)
        self.settings = settings or QSettings("TUDelft", "pathprogrammer")
        self.count = 0
        self.tag_provider = lambda: None
        self._tags = {}                  # file name -> tag, logged once the photo is saved
        self.view = LiveView()
        self.state_label = QLabel("Looking for the camera...")
        self.state_label.setStyleSheet("color: #64748b; font-weight: normal;")
        self.photo_btn = QPushButton("Take photo")
        action_button(self.photo_btn, ACCENT, ACCENT_DARK)
        self.photo_btn.setEnabled(False)
        self.folder_btn = QPushButton("Folder...")
        self.folder_btn.setToolTip("Folder the photos are saved in")
        self.open_btn = QPushButton("Open folder")
        self.saved_label = QLabel("")
        self.saved_label.setWordWrap(True)
        self.saved_label.setStyleSheet("color: #64748b; font-weight: normal;")
        self.saved_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.exposure = QDoubleSpinBox()
        self.exposure.setRange(*EXPOSURE_MS_RANGE)
        self.exposure.setDecimals(2)
        self.exposure.setSuffix(" ms")
        self.exposure.setToolTip("Exposure time of the camera: how long the sensor collects light per image. "
                                 "Longer = brighter image. The LED lighting is separate from this.")
        self.exposure.setValue(self.settings.value("camera/exposure_ms", config.CAMERA_EXPOSURE_US / 1000,
                                                   type=float))
        self.gain = QDoubleSpinBox()
        self.gain.setRange(*GAIN_RANGE)
        self.gain.setDecimals(1)
        self.gain.setSingleStep(0.5)
        self.gain.setToolTip("Gain: higher is brighter, but with more noise")
        self.gain.setValue(self.settings.value("camera/gain", 1.0, type=float))
        for w in (self.exposure, self.gain):
            w.setKeyboardTracking(False)
            w.setButtonSymbols(QAbstractSpinBox.UpDownArrows)

        lay = QVBoxLayout(self)
        lay.addWidget(self.view, 1)
        lay.addWidget(self.state_label)
        srow = QHBoxLayout()
        for text, w in (("Exposure", self.exposure), ("Gain", self.gain)):
            lbl = QLabel(text)
            lbl.setStyleSheet("font-weight: normal;")
            srow.addWidget(lbl)
            srow.addWidget(w)
        srow.addStretch()
        lay.addLayout(srow)
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
                               open_camera or default_camera)
        self.exposure.valueChanged.connect(self._settings_changed)
        self.gain.valueChanged.connect(self._settings_changed)
        self._settings_changed()
        self._show_folder()
        if CameraFeed.disabled():
            self.state_label.setText("Camera disabled (PRINTERPROJECT_NO_CAMERA)")
        self.feed.start()

    def _settings_changed(self, *_):
        self.settings.setValue("camera/exposure_ms", self.exposure.value())
        self.settings.setValue("camera/gain", self.gain.value())
        self.feed.set_settings(exposure_us=self.exposure.value() * 1000, gain=self.gain.value())

    # ---------------- folder ----------------
    def folder(self):
        return Path(self.settings.value("photo_folder", str(PHOTOS_DIR)))

    def choose_folder(self):
        path = QFileDialog.getExistingDirectory(self, "Folder for photos", str(self.folder()))
        if path:
            self.settings.setValue("photo_folder", path)
            self._show_folder()

    def _show_folder(self):
        self.saved_label.setText(f"Photos go to: {self.folder()}")

    def _open_folder(self):
        self.folder().mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.folder())))

    # ---------------- camera ----------------
    def take_photo(self):
        try:
            tag = self.tag_provider()
        except Exception:
            tag = None
        name = tagged_name(tag) if tag else None
        if not self.feed.take_photo(self.folder(), name):
            self.saved_label.setText("No camera connected: no photo was taken.")
            return
        if tag:
            self._tags[name] = tag

    def take_tagged(self, folder, tag):
        """A photo with object / depth_mm / force_g in its name into `folder` (made if needed), logged in
        that folder's measurements.csv once saved. Returns the file name, or None without a camera."""
        folder = Path(folder)
        folder.mkdir(parents=True, exist_ok=True)
        name = tagged_name(tag)
        if not self.feed.take_photo(folder, name):
            return None
        self._tags[name] = tag
        return name

    def _log(self, path, tag):
        log = Path(path).parent / LOG_NAME
        new = not log.exists()
        with open(log, "a", newline="", encoding="utf-8") as f:
            w = csv.writer(f, delimiter=";")
            if new:
                w.writerow(LOG_HEADER)
            w.writerow([Path(path).name, datetime.now().isoformat(timespec="seconds"), tag.get("object", ""),
                        f"{tag['depth_mm']:.3f}", f"{tag['force_g']:.1f}"])

    def release(self):
        """Close the camera so a run can use it."""
        self.feed.release()
        self.view.clear_image("Camera in use by the run")

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
                                    " (keeps looking automatically)"))
        if not connected and self.feed.is_running():
            self.view.clear_image("No camera image")

    def _on_saved(self, path, error):
        if error:
            self.saved_label.setText(f"Photo not saved: {error}")
            return
        self.count += 1
        tag = self._tags.pop(Path(path).name, None)
        extra = ""
        if tag:
            try:
                self._log(path, tag)
                extra = (f" ({tag.get('object')}, indentation {tag['depth_mm']:.3f} mm, "
                         f"force {tag['force_g']:.1f} g; also in {LOG_NAME})")
            except OSError as e:
                extra = f" ({LOG_NAME} not updated: {e})"
        self.saved_label.setText(f"Photo {self.count} saved: {path}{extra}")
