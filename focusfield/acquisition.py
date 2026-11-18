"""Owned frames, explicit pixel metadata and a backend-neutral capture adapter."""
from dataclasses import dataclass
from datetime import datetime, timezone
import itertools
import threading
import time
import numpy as np

FORMATS = {"RGB8": (8, 3), "Mono8": (8, 1), "Mono10": (10, 1), "Mono12": (12, 1),
           "Mono16": (16, 1), "BayerRG8": (8, 1), "BayerRG10": (10, 1),
           "BayerRG12": (12, 1), "BayerRG10p": (10, 1), "BayerRG12p": (12, 1)}
_capture_sequence = itertools.count(1)


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
        if not a.size or a.dtype not in (np.dtype("uint8"), np.dtype("uint16")) or a.ndim not in (2, 3):
            raise ValueError("Lossless captures require uint8/uint16 mono/Bayer/RGB samples")
        if m["bit_depth"] not in (8, 10, 12, 16) or a.max() > 2**m["bit_depth"] - 1:
            raise ValueError("Samples disagree with declared bit depth")
        if m["pixel_format"] not in FORMATS or FORMATS[m["pixel_format"]][0] != m["bit_depth"]:
            raise ValueError("Unsupported or inconsistent native pixel format / bit depth")
        if m["pixel_format"] == "RGB8":
            if a.ndim != 3 or a.shape[2] != 3 or a.dtype != np.uint8:
                raise ValueError("RGB8 capture must have three uint8 channels")
        elif a.ndim != 2:
            raise ValueError("Raw Mono/Bayer capture must be two-dimensional")
        if a.dtype != (np.dtype("uint8") if m["bit_depth"] == 8 else np.dtype("uint16")):
            raise ValueError("Native sample dtype disagrees with bit depth")
        if not np.isfinite([m["exposure_us"], m["gain"]]).all() or m["exposure_us"] <= 0:
            raise ValueError("Invalid exposure/gain readback")
        m.setdefault("width_px", int(a.shape[1]))
        m.setdefault("height_px", int(a.shape[0]))
        m.setdefault("cfa_layout", "RGGB" if m["pixel_format"].startswith("BayerRG") else "none")
        if m["width_px"] != a.shape[1] or m["height_px"] != a.shape[0]:
            raise ValueError("Image dimensions disagree with capture metadata")
        expected_cfa = "RGGB" if m["pixel_format"].startswith("BayerRG") else "none"
        if m["cfa_layout"] != expected_cfa:
            raise ValueError("Unsupported CFA layout or ROI parity; expected " + expected_cfa)
        if m.get('acquisition_status', 'complete') != 'complete':
            raise ValueError("Capture acquisition status is not complete")
        return self


def capture_metadata(pixel_format, serial, exposure_us, gain, **extra):
    if pixel_format not in FORMATS:
        raise ValueError(f"Unsupported format {pixel_format}")
    return dict(pixel_format=pixel_format, bit_depth=FORMATS[pixel_format][0], camera_serial=str(serial),
                exposure_us=float(exposure_us), gain=float(gain),
                timestamp=datetime.now(timezone.utc).isoformat(), host_monotonic_ns=time.monotonic_ns(),
                capture_sequence=next(_capture_sequence),
                timestamp_source="host_metadata_created; not sensor exposure time; monotonic clock may repeat",
                processing="legacy RGB8 conversion (provisional)" if pixel_format == "RGB8" else
                           "native raw samples; camera processing requires physical verification",
                measurement_validated=False, **extra)


class FrameGuard:
    """Nondecreasing host clock plus strictly increasing IDs, never a fabricated time.

    Gaps are allowed and counted. A device ID reset/reorder requires a new run;
    stream restart alone never authorizes accepting a duplicate frame.
    """
    def __init__(self):
        self.last_time = self.last_sequence = self.last_device_id = None
        self.identity = None

    def check(self, capture, after=0):
        capture.validate()
        m = capture.metadata
        timestamp = m["host_monotonic_ns"]
        if type(timestamp) is not int or timestamp < 0:
            raise ValueError("Invalid host monotonic timestamp")
        if timestamp < after or (self.last_time is not None and timestamp < self.last_time):
            raise ValueError("Stale frame received after motion or host timestamp moved backwards")
        sequence = m.get("capture_sequence")
        if sequence is not None and (type(sequence) is not int or
                (self.last_sequence is not None and sequence <= self.last_sequence)):
            raise ValueError("Duplicate or reordered host capture sequence")
        if timestamp == self.last_time and sequence is None:
            raise ValueError("Equal host timestamps require an ordered capture sequence")
        identity = (m["camera_serial"], m["pixel_format"], m.get("width_px"), m.get("height_px"))
        if self.identity is not None and identity != self.identity:
            raise ValueError("Camera identity, format or dimensions changed")
        frame_id = m.get("device_frame_id")
        if frame_id is not None:
            if type(frame_id) is not int or frame_id < 0:
                raise ValueError("Invalid device frame ID")
            if self.last_device_id is not None:
                if frame_id <= self.last_device_id:
                    raise ValueError("Duplicate, queued or reordered device frame ID (or counter reset)")
                # The adapter observes discarded frames too; a second controller
                # guard must not relabel intentional discards as dropped frames.
                m.setdefault("device_frame_gap", frame_id - self.last_device_id - 1)
            self.last_device_id = frame_id
        elif self.last_device_id is not None:
            raise ValueError("Device frame ID disappeared")
        self.last_time, self.last_sequence, self.identity = timestamp, sequence, identity


def check_cancel(cancel):
    if cancel is not None and cancel.is_set():
        raise InterruptedError("STOP requested")


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
        self.cancel = threading.Event()
        self.guard = FrameGuard()
        self.freshness = None

    def open(self):
        check_cancel(self.cancel)
        from ids_camera import IDS_Camera
        kwargs = {} if self.recipe.pixel_format == "RGB8" else {"pixel_format": self.recipe.pixel_format}
        self.camera = IDS_Camera(**kwargs)
        self.camera.set_exposure(self.recipe.exposure_us)
        self.camera.set_gain(self.recipe.gain)
        check_cancel(self.cancel)

    def fresh(self):
        # Restart the stream to discard queued and in-flight exposures after settling.
        check_cancel(self.cancel)
        self.freshness = None
        self.camera.restart_acquisition()
        for _ in range(self.recipe.discard_frames):
            self.capture()
        self.freshness = dict(restart_completed=True, discarded_frames=self.recipe.discard_frames,
                              device_frame_fence=self.guard.last_device_id,
                              exposure_timing_proven=False,
                              rule="restart/discard and increasing device IDs; physical timing requires separate verification")

    def capture(self):
        check_cancel(self.cancel)
        if self.recipe.pixel_format == "RGB8":
            pixels = self.camera.snap()
            metadata = self.camera.capture_info("RGB8")
            capture = Capture(pixels, metadata).validate()
        else:
            capture = self.camera.capture_raw().validate()
        check_cancel(self.cancel)
        self.guard.check(capture)
        capture.metadata["freshness"] = self.freshness
        return capture

    def capture_preview_rgb(self):
        return self.camera.snap()

    def close(self):
        if self.camera is not None:
            self.camera.close()
            self.camera = None
