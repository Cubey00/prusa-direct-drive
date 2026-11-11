"""macOS implementation of the existing IDS_Camera API via Scientific Camera Capture.

The printer application and its Windows/Linux IDS peak implementation are unchanged.
Native transport and verified pixel decoding belong to the separate camera package.
"""
import os
import threading

import numpy as np


class IDS_Camera:
    def __init__(self, camera_index=0, *, camera_id=None, backend=None):
        self._lock = threading.RLock()
        self._camera = self._stream = None
        self._closed = False
        try:
            from scientific_camera import CameraManager, CameraConfiguration
            from scientific_camera.pixels.registry import lookup
        except ModuleNotFoundError as exc:
            if exc.name != "scientific_camera":
                raise
            raise RuntimeError(
                "macOS camera support requires Scientific Camera Capture. "
                "Install with: python -m pip install -r requirements-macos.txt"
            ) from exc
        backend = backend or os.environ.get("PRUSA_CAMERA_BACKEND", "aravis")
        if backend == "aravis":
            from scientific_camera.backends.aravis import AravisBackend
            driver = AravisBackend()
        elif backend == "simulated":
            from scientific_camera.backends.simulated import SimulatedBackend
            driver = SimulatedBackend()
        else:
            raise ValueError("PRUSA_CAMERA_BACKEND must be aravis or simulated")
        manager = CameraManager(backends=[driver])
        devices = manager.discover(backends=[backend])
        if manager.diagnostics:
            raise RuntimeError(f"macOS camera backend unavailable: {manager.diagnostics[backend]}")
        camera_id = camera_id or os.environ.get("PRUSA_CAMERA_ID")
        if camera_id is None:
            if not isinstance(camera_index, int) or camera_index < 0 or camera_index >= len(devices):
                raise RuntimeError(f"Camera {camera_index} does not exist. Found {len(devices)} camera(s).")
            camera_id = devices[camera_index].id
        try:
            self._camera = manager.open(camera_id)
            # The legacy snap contract is RGB8. Never guess vendor-specific packing.
            pixel_format = self._camera.configuration.pixel_format
            if not lookup(pixel_format).decodable:
                formats = self._camera.capabilities().pixel_formats
                supported = next((name for name in ("BayerRG8", "BayerRG12p", "BayerRG10p", "Mono8", "Mono16")
                                  if name in formats), None)
                if supported is None:
                    raise RuntimeError(
                        f"No verified RGB converter for {pixel_format}. "
                        "Select a standard Mono/Bayer RG format supported by Scientific Camera Capture."
                    )
                self._camera.configure(CameraConfiguration(pixel_format=supported))
            self._stream = self._camera.start_stream()
        except Exception:
            self.close()
            raise

    def _ensure_open(self):
        if self._closed:
            raise RuntimeError("Camera has been closed.")

    def flush(self):
        """The next snap already waits for a new complete frame, so no stale queue is used."""
        with self._lock:
            self._ensure_open()

    def snap(self):
        """Return an owned RGB8 array, matching the existing camera consumer contract."""
        from scientific_camera.pixels.demosaic import demosaic_rggb
        from scientific_camera.pixels.registry import lookup
        from scientific_camera.pixels.standard import decode
        with self._lock:
            self._ensure_open()
            frame = self._stream.next_frame(timeout=5)
            samples = decode(frame)
            spec = lookup(frame.metadata.pixel_format)
            rgb = demosaic_rggb(samples) if spec.cfa else np.repeat(samples[..., None], 3, axis=2)
            if spec.bit_depth > 8:
                rgb = np.right_shift(rgb, spec.bit_depth - 8)
            return np.array(rgb, dtype=np.uint8, copy=True, order="C")

    def _configure(self, **values):
        from scientific_camera import CameraConfiguration
        with self._lock:
            self._ensure_open()
            self._stream.stop()
            self._stream = None
            try:
                self._camera.configure(CameraConfiguration(**values))
            finally:
                self._stream = self._camera.start_stream()

    def set_exposure(self, exposure_us):
        self._configure(exposure_us=exposure_us)

    def set_gain(self, gain):
        self._configure(gain=gain)

    def close(self):
        with self._lock:
            if self._closed:
                return
            try:
                if self._camera is not None:
                    self._camera.close()
            finally:
                self._closed = True

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass
