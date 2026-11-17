"""Owned frames, explicit pixel metadata and a backend-neutral capture adapter."""
from dataclasses import dataclass
from datetime import datetime, timezone
import re
import sys
import time
import numpy as np


@dataclass
class Capture:
    pixels: np.ndarray
    metadata: dict

    def validate(self):
        m, a = self.metadata, self.pixels
        for key in ("pixel_format", "bit_depth", "camera_serial", "exposure_us", "gain",
                    "timestamp", "host_monotonic_ns", "processing"):
            if key not in m:
                raise ValueError(f"Capture metadata missing {key}")
        if a.dtype not in (np.dtype("uint8"), np.dtype("uint16")) or a.ndim not in (2, 3):
            raise ValueError("Lossless captures require uint8/uint16 mono/Bayer/RGB samples")
        if m["bit_depth"] not in (8, 10, 12, 16) or a.max() > 2**m["bit_depth"] - 1:
            raise ValueError("Samples disagree with declared bit depth")
        if m["pixel_format"] == "RGB8":
            if a.ndim != 3 or a.shape[2] != 3 or a.dtype != np.uint8:
                raise ValueError("RGB8 capture must have three uint8 channels")
        elif a.ndim != 2:
            raise ValueError("Raw Mono/Bayer capture must be two-dimensional")
        if not np.isfinite([m["exposure_us"], m["gain"]]).all() or m["exposure_us"] <= 0:
            raise ValueError("Invalid exposure/gain readback")
        m.setdefault("width_px", int(a.shape[1]))
        m.setdefault("height_px", int(a.shape[0]))
        m.setdefault("cfa_layout", "RGGB" if m["pixel_format"].startswith("BayerRG") else "none")
        if m["width_px"] != a.shape[1] or m["height_px"] != a.shape[0]:
            raise ValueError("Image dimensions disagree with capture metadata")
        return self


def capture_metadata(pixel_format, serial, exposure_us, gain, **extra):
    bits = re.search(r"(8|10|12|16)", pixel_format)
    if bits is None:
        raise ValueError(f"Unsupported format {pixel_format}")
    return dict(pixel_format=pixel_format, bit_depth=int(bits.group()), camera_serial=str(serial),
                exposure_us=float(exposure_us), gain=float(gain),
                timestamp=datetime.now(timezone.utc).isoformat(), host_monotonic_ns=time.monotonic_ns(),
                timestamp_source="host_receive; not sensor exposure time",
                processing="legacy RGB8 conversion (provisional)" if pixel_format == "RGB8" else
                           "native raw samples; camera processing requires physical verification",
                measurement_validated=False, **extra)


def analysis_plane(pixels, metadata):
    """Return normalized plane + sensor-pixel pitch. Never treat a CFA as mono.

    BayerRG: one green lattice G1 at rows 0,2,... and columns 1,3,... . It has
    2x sensor pitch in both axes. No interpolation/demosaic; Nyquist = 0.25 cpp.
    """
    a = np.asarray(pixels)
    fmt = metadata["pixel_format"]
    if fmt.startswith("BayerRG"):
        plane, pitch = a[0::2, 1::2], 2.0
    elif fmt == "RGB8":
        plane, pitch = a[..., 1], 1.0
    elif fmt.startswith("Mono"):
        plane, pitch = a, 1.0
    else:
        raise ValueError(f"Unsupported CFA/format {fmt}")
    return plane.astype(float) / (2**metadata["bit_depth"] - 1), pitch


class CameraAdapter:
    simulated = False

    def __init__(self, recipe):
        self.recipe = recipe
        self.camera = None

    def open(self):
        from ids_camera import IDS_Camera
        kwargs = {} if self.recipe.pixel_format == "RGB8" else {"pixel_format": self.recipe.pixel_format}
        self.camera = IDS_Camera(**kwargs)
        self.camera.set_exposure(self.recipe.exposure_us)
        self.camera.set_gain(self.recipe.gain)

    def fresh(self):
        # Restart the stream to discard queued and in-flight exposures after settling.
        self.camera.restart_acquisition()
        for _ in range(self.recipe.discard_frames):
            self.capture()

    def capture(self):
        if self.recipe.pixel_format == "RGB8":
            pixels = self.camera.snap()
            metadata = self.camera.capture_info("RGB8")
            return Capture(pixels, metadata).validate()
        return self.camera.capture_raw().validate()

    def capture_preview_rgb(self):
        return self.camera.snap()

    def close(self):
        if self.camera is not None:
            self.camera.close()
            self.camera = None
