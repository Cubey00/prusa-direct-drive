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

    def set_exposure(self, us):
        self.exposure = us

    def set_gain(self, g):
        self.gain = g

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
        assert states == [(False, "No camera: no camera connected")]     # said once, not every retry
        plugged.set()
        _wait(lambda: len(frames) >= 2)
        assert states[-1] == (True, "Camera connected") and feed.connected
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
    files = list((tmp_path / "fotos").glob("photo_*.tif"))
    assert len(files) == 1 and tifffile.imread(files[0]).shape == (300, 400, 3)
    assert "Photo 1 saved" in panel.saved_label.text()


def test_no_camera_no_photo(qtbot, tmp_path):
    panel = PhotoPanel(QSettings(str(tmp_path / "s.ini"), QSettings.IniFormat),
                       open_camera=lambda: (_ for _ in ()).throw(RuntimeError("No camera found")))
    qtbot.addWidget(panel)
    try:
        qtbot.waitUntil(lambda: "No camera" in panel.state_label.text(), timeout=5000)
        assert not panel.photo_btn.isEnabled()
        panel.take_photo()
        assert "No camera connected" in panel.saved_label.text()
    finally:
        panel.shutdown()


def _wait(cond, timeout=5.0):
    import time
    end = time.monotonic() + timeout
    while not cond():
        assert time.monotonic() < end, "timed out"
        time.sleep(0.01)


def test_exposure_and_gain_reach_the_camera_also_after_a_reconnect(qtbot, tmp_path):
    cams = []
    panel = PhotoPanel(QSettings(str(tmp_path / "s.ini"), QSettings.IniFormat),
                       open_camera=lambda: cams.append(FakeCamera(fail_after=50)) or cams[-1])
    qtbot.addWidget(panel)
    try:
        qtbot.waitUntil(lambda: bool(cams) and getattr(cams[0], "exposure", None) == 100_000, timeout=5000)
        panel.exposure.setValue(20.0)
        panel.gain.setValue(2.5)
        qtbot.waitUntil(lambda: (cams[-1].exposure, getattr(cams[-1], "gain", None)) == (20_000, 2.5),
                        timeout=5000)
        qtbot.waitUntil(lambda: len(cams) >= 2, timeout=10000)      # 50 frames, then "unplugged"
        qtbot.waitUntil(lambda: getattr(cams[-1], "exposure", None) == 20_000, timeout=5000)
    finally:
        panel.shutdown()
    assert QSettings(str(tmp_path / "s.ini"), QSettings.IniFormat).value("camera/exposure_ms", type=float) == 20.0


def test_errors_are_explained():
    assert "SDK" in camera_feed.explain(Exception("GENICAM_GENTL64_PATH environment variable not found!"))
    assert "pip install" in camera_feed.explain(ModuleNotFoundError("No module named 'ids_peak'"))
    assert camera_feed.explain(RuntimeError("No camera found.")) == "no camera connected"


def test_measuring_photo_gets_object_depth_and_force(qtbot, tmp_path):
    settings = QSettings(str(tmp_path / "s.ini"), QSettings.IniFormat)
    settings.setValue("photo_folder", str(tmp_path / "fotos"))
    panel = PhotoPanel(settings, open_camera=FakeCamera)
    qtbot.addWidget(panel)
    panel.tag_provider = lambda: {"object": "mini square", "depth_mm": 0.2, "force_g": 123.45}
    try:
        qtbot.waitUntil(panel.photo_btn.isEnabled, timeout=5000)
        panel.photo_btn.click()
        qtbot.waitUntil(lambda: panel.count == 1, timeout=5000)
    finally:
        panel.shutdown()
    files = list((tmp_path / "fotos").glob("photo_mini-square_d0.200mm_F123.5g_*.tif"))
    assert len(files) == 1
    rows = (tmp_path / "fotos" / photo_panel.LOG_NAME).read_text(encoding="utf-8").splitlines()
    assert rows[0] == "file;time;object;indentation_mm;force_g"
    assert rows[1].startswith(files[0].name) and rows[1].endswith(";mini square;0.200;123.5")
    assert "indentation 0.200 mm" in panel.saved_label.text()
