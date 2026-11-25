"""Live camera feed for the Foto control panel. No Qt: callbacks run in the feed's own thread.

The feed keeps trying to open the camera (every RETRY_S) until one is plugged in, shows every
frame through on_frame (a small copy) and saves a full-size TIFF when a photo is asked for.
A camera can be used by one owner at a time: release() before a run opens it itself.
"""
import os
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

import numpy as np

from .imagesave import ImageSaver

RETRY_S = 3.0               # between attempts to open a camera that isn't there (yet)
FRAME_INTERVAL_S = 0.1      # at most ~10 preview frames a second
PREVIEW_WIDTH = 960         # px of the copy sent to on_frame
DISABLE_ENV = "PRINTERPROJECT_NO_CAMERA"   # set (e.g. by the tests): never open a camera


GENTL_VAR = "GENICAM_GENTL64_PATH"         # set by the IDS peak SDK installer: where its drivers are


def refresh_gentl_path():
    """Windows: take GENTL_VAR from the system environment if this process started before the
    IDS peak SDK was installed, so the camera works without restarting the app."""
    if sys.platform != "win32" or os.environ.get(GENTL_VAR):
        return
    try:
        import winreg
        for root, key in ((winreg.HKEY_LOCAL_MACHINE,
                           r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"),
                          (winreg.HKEY_CURRENT_USER, "Environment")):
            try:
                with winreg.OpenKey(root, key) as k:
                    value, _ = winreg.QueryValueEx(k, GENTL_VAR)
            except OSError:
                continue
            if value:
                os.environ[GENTL_VAR] = os.path.expandvars(value)
                return
    except ImportError:
        pass


def default_camera():
    """The IDS camera (on macOS the Scientific Camera Capture backend behind the same API)."""
    refresh_gentl_path()
    from ids_camera import IDS_Camera
    return IDS_Camera()


def explain(error):
    """What to do about an error from opening the camera, in words for the user."""
    text = str(error)
    if isinstance(error, ModuleNotFoundError) and "ids_peak" in text:
        return "IDS Python packages are missing (pip install ids_peak ids_peak_ipl)"
    if GENTL_VAR in text:
        return "IDS peak SDK (camera driver) is not installed on this computer"
    if "No camera" in text or "Found 0" in text or "does not exist" in text:
        return "no camera connected"
    return text


def preview_of(frame, width=PREVIEW_WIDTH):
    """A small 8-bit copy of a frame (H x W or H x W x 3) for the screen."""
    step = max(1, int(np.ceil(frame.shape[1] / width)))
    small = frame[::step, ::step]
    if small.dtype != np.uint8:
        top = float(small.max()) or 1.0
        small = (small.astype(np.float32) * (255.0 / top)).astype(np.uint8)
    return np.ascontiguousarray(small)


def photo_name(when=None):
    when = when or datetime.now()
    return f"photo_{when:%Y%m%d_%H%M%S}_{when.microsecond // 1000:03d}.tif"


class CameraFeed:
    def __init__(self, on_frame, on_state, on_saved, open_camera=default_camera):
        """on_frame(array) small preview; on_state(connected, message); on_saved(path or None, error)."""
        self.on_frame, self.on_state, self.on_saved = on_frame, on_state, on_saved
        self.open_camera = open_camera
        self._stop = threading.Event()
        self._thread = None
        self._photo = []                 # folders of photos asked for, taken from the next frame
        self._lock = threading.Lock()
        self._settings = {}              # exposure_us / gain to apply (again after every reconnect)
        self._settings_changed = False
        self.connected = False

    @staticmethod
    def disabled():
        return bool(os.environ.get(DISABLE_ENV))

    def start(self):
        if self._thread is not None or self.disabled():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def release(self, timeout=10.0):
        """Stop the feed and close the camera (so a run can open it). Waits for the thread."""
        t = self._thread
        if t is None:
            return
        self._stop.set()
        t.join(timeout)
        self._thread = None

    def is_running(self):
        return self._thread is not None

    def take_photo(self, folder, name=None):
        """Save the next frame to folder (as `name`, default photo_name()). False if there is no camera."""
        if not self.connected:
            return False
        with self._lock:
            self._photo.append((Path(folder), name))
        return True

    def set_settings(self, exposure_us=None, gain=None):
        """Exposure time (us) and gain; applied in the feed thread, also to a camera plugged in later."""
        with self._lock:
            if exposure_us is not None:
                self._settings["exposure_us"] = float(exposure_us)
            if gain is not None:
                self._settings["gain"] = float(gain)
            self._settings_changed = True

    def _apply_settings(self, cam):
        with self._lock:
            settings, self._settings_changed = dict(self._settings), False
        problems = []
        for key, setter in (("exposure_us", "set_exposure"), ("gain", "set_gain")):
            if key in settings:
                try:
                    getattr(cam, setter)(settings[key])
                except Exception as e:
                    problems.append(f"{'exposure' if key == 'exposure_us' else 'gain'}: {e}")
        if problems:
            self._set_state(True, "Camera connected; not configured: " + "; ".join(problems))

    def _set_state(self, connected, message):
        self.connected = connected
        self.on_state(connected, message)

    def _run(self):
        cam = None
        saver = ImageSaver()
        last_error = None
        reported = 0                     # saver errors passed on so far
        try:
            while not self._stop.is_set():
                if cam is None:
                    try:
                        cam = self.open_camera()
                    except Exception as e:
                        msg = f"No camera: {explain(e)}"
                        if msg != last_error:
                            self._set_state(False, msg)
                            last_error = msg
                        self._stop.wait(RETRY_S)
                        continue
                    last_error = None
                    self._set_state(True, "Camera connected")
                    self._settings_changed = True
                if self._settings_changed:
                    self._apply_settings(cam)
                try:
                    frame = cam.snap()
                except Exception as e:
                    self._close(cam)
                    cam = None
                    self._set_state(False, f"Camera disconnected: {e}")
                    self._stop.wait(RETRY_S)
                    continue
                with self._lock:
                    folders, self._photo = self._photo, []
                for folder, name in folders:
                    path = folder / (name or photo_name())
                    saver.put(frame, path)
                    self.on_saved(path, None)
                while reported < len(saver.errors):
                    self.on_saved(None, saver.errors[reported])
                    reported += 1
                self.on_frame(preview_of(frame))
                self._stop.wait(FRAME_INTERVAL_S)
        finally:
            self._close(cam)
            saver.close()
            for e in saver.errors[reported:]:
                self.on_saved(None, e)
            self._set_state(False, "Camera released")

    @staticmethod
    def _close(cam):
        if cam is not None:
            try:
                cam.close()
            except Exception:
                pass
