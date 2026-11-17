"""Foto control: live feed from a camera that comes and goes, photos saved as TIFF in a folder."""
import threading

import numpy as np
import pytest
import tifffile
from PySide6.QtCore import QSettings

from pathprogrammer import camera_feed
from pathprogrammer.camera_feed import CameraFeed, preview_of
from pathprogrammer.ui import photo_panel
from pathprogrammer.ui.photo_panel import PhotoPanel


class FakeCamera:
    closed = 0

    def __init__(self, fail_after=None):
        self.n, self.fail_after = 0, fail_after

    def snap(self):
        self.n += 1
        if self.fail_after is not None and self.n > self.fail_after:
            raise RuntimeError("cable pulled")
        return np.full((300, 400, 3), self.n % 255, dtype=np.uint8)

    def close(self):
        FakeCamera.closed += 1


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr(camera_feed, "RETRY_S", 0.05)
    monkeypatch.setattr(camera_feed, "FRAME_INTERVAL_S", 0.01)
    monkeypatch.delenv(camera_feed.DISABLE_ENV, raising=False)


def test_preview_is_small_8bit():
    a = preview_of(np.zeros((3040, 4056, 3), np.uint16) + 1000, width=500)
    assert a.dtype == np.uint8 and a.shape[1] <= 500 and a.max() == 255


def test_feed_waits_for_a_camera_then_streams():
    attempts, states, frames = [], [], []
    plugged = threading.Event()

    def open_camera():
        attempts.append(1)
        if not plugged.is_set():
            raise RuntimeError("No camera found")
        return FakeCamera()

    feed = CameraFeed(frames.append, lambda c, m: states.append((c, m)), lambda *a: None, open_camera)
    feed.start()
    try:
        _wait(lambda: len(attempts) >= 2)
        assert states == [(False, "Geen camera: No camera found")]     # said once, not every retry
        plugged.set()
        _wait(lambda: len(frames) >= 2)
        assert states[-1] == (True, "Camera verbonden") and feed.connected
    finally:
        feed.release()
    assert not feed.is_running() and states[-1][0] is False


def test_unplugged_camera_is_reopened():
    cams = []
    feed = CameraFeed(lambda f: None, lambda c, m: None, lambda *a: None,
                      lambda: cams.append(FakeCamera(fail_after=2)) or cams[-1])
    feed.start()
    try:
        _wait(lambda: len(cams) >= 2)
    finally:
        feed.release()


def test_photo_button_saves_a_tiff(qtbot, tmp_path):
    settings = QSettings(str(tmp_path / "s.ini"), QSettings.IniFormat)
    settings.setValue("photo_folder", str(tmp_path / "fotos"))
    panel = PhotoPanel(settings, open_camera=FakeCamera)
    qtbot.addWidget(panel)
    try:
        qtbot.waitUntil(panel.photo_btn.isEnabled, timeout=5000)
        qtbot.waitUntil(lambda: panel.view.pixmap() is not None and not panel.view.pixmap().isNull(),
                        timeout=5000)
        panel.photo_btn.click()
        qtbot.waitUntil(lambda: panel.count == 1, timeout=5000)
    finally:
        panel.shutdown()                       # waits until the TIFF is written
    files = list((tmp_path / "fotos").glob("foto_*.tif"))
    assert len(files) == 1 and tifffile.imread(files[0]).shape == (300, 400, 3)
    assert "Foto 1 opgeslagen" in panel.saved_label.text()


def test_no_camera_no_photo(qtbot, tmp_path):
    panel = PhotoPanel(QSettings(str(tmp_path / "s.ini"), QSettings.IniFormat),
                       open_camera=lambda: (_ for _ in ()).throw(RuntimeError("No camera found")))
    qtbot.addWidget(panel)
    try:
        qtbot.waitUntil(lambda: "Geen camera" in panel.state_label.text(), timeout=5000)
        assert not panel.photo_btn.isEnabled()
        panel.take_photo()
        assert "Geen camera aangesloten" in panel.saved_label.text()
    finally:
        panel.shutdown()


def _wait(cond, timeout=5.0):
    import time
    end = time.monotonic() + timeout
    while not cond():
        assert time.monotonic() < end, "timed out"
        time.sleep(0.01)
