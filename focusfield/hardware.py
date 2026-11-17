"""Optical adapters do not home, touch, enable UDP metrics, or retreat on STOP."""
import threading
import time
import cv2
import numpy as np
from scipy.ndimage import gaussian_filter

from .acquisition import Capture, capture_metadata


class ZStage:
    simulated = False

    def __init__(self, fixture, printer_z_max):
        self.fixture = fixture
        self.printer_z_max = printer_z_max
        self.opened = False
        self.cancelled = threading.Event()

    def check(self, z):
        f = self.fixture
        if not (np.isfinite(z) and 0 <= f.safe_z_min_mm <= z <= f.safe_z_max_mm <= self.printer_z_max):
            raise ValueError("Z outside measured optical fixture bounds")
        if f.wd_for_z(z) - f.wd_uncertainty_mm < f.minimum_wd_mm:
            raise ValueError("Minimum optical clearance violated")

    def open(self):
        import gantry_controls as gc
        if self.cancelled.is_set():
            raise InterruptedError("STOP requested")
        gc.connect()
        self.opened = True
        self.check(self.position())

    def position(self):
        import gantry_controls as gc
        return gc.read_position()[2]

    def move_z_absolute(self, z):
        import gantry_controls as gc
        self.check(z)
        if self.cancelled.is_set():
            raise InterruptedError("STOP requested")
        gc.move_absolute(z=z, Zfeed=self.fixture.z_speed_mm_s * 60, cancel=self.cancelled)

    def stop(self):
        import gantry_controls as gc
        self.cancelled.set()
        if self.opened:
            gc.motionstop()

    def close(self):
        import gantry_controls as gc
        if self.opened:
            gc.disconnect()
            self.opened = False


class SimStage:
    simulated = True

    def __init__(self, fixture):
        self.fixture, self.z = fixture, fixture.reference_z_mm
        self.calls = []
        self.stopped = False

    def open(self):
        self.calls.append(("open",))

    def position(self):
        return self.z

    def move_z_absolute(self, z):
        if self.stopped:
            raise InterruptedError("STOP requested")
        if not self.fixture.safe_z_min_mm <= z <= self.fixture.safe_z_max_mm:
            raise ValueError("Simulated Z outside bounds")
        self.calls.append(("move", z))
        self.z = z

    def stop(self):
        self.stopped = True
        self.calls.append(("stop",))

    def close(self):
        self.calls.append(("close",))


class SimCamera:
    """Rendered chart with known radial best distance plus apparent tilt, seeded noise."""
    simulated = True

    def __init__(self, stage, recipe, chart):
        self.stage, self.recipe, self.chart = stage, recipe, chart
        self.frame_id = 0
        self.scale = 16
        self.rng = np.random.default_rng(7)

    def open(self):
        pass

    def fresh(self):
        pass

    def capture(self):
        scale, chart = self.scale, self.chart
        image = np.full((int(chart["page_height"]*scale), int(chart["page_width"]*scale)), 0.82)
        dictionary = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
        for f in chart["fiducials"]:
            x, y = (int(v*scale) for v in f["top_left_mm"])
            size = round(f["size_mm"]*scale)
            marker = cv2.aruco.generateImageMarker(dictionary, f["id"], size)
            image[y:y+size, x:x+size] = marker / 255 * 0.7 + 0.12
        wd = self.stage.fixture.wd_for_z(self.stage.z)
        field = chart["visible_field"]
        for square in chart["slanted_squares"]:
            x, y = square["center_mm"]
            nx = (x - field["left"] - field["width"]/2) / (field["width"]/2)
            ny = (y - field["top"] - field["height"]/2) / (field["height"]/2)
            best = 20 + 0.15*(nx*nx+ny*ny) + 0.06*nx - 0.04*ny
            sigma = 0.7 + 5*abs(wd-best)
            side = square["side_mm"]*scale
            radius = int(side * 0.95)
            # Supersample artwork, then integrate into sensor pixels.
            patch = np.full((2*radius*4, 2*radius*4), 0.82, dtype=np.float32)
            box = cv2.boxPoints(((radius*4, radius*4), (side*4, side*4), square["rotation_deg"]))
            cv2.fillConvexPoly(patch, np.int32(box), 0.16)
            patch = cv2.resize(patch, (2*radius, 2*radius), interpolation=cv2.INTER_AREA)
            patch = gaussian_filter(patch, sigma)
            px, py = int(x*scale)-radius, int(y*scale)-radius
            image[py:py+2*radius, px:px+2*radius] = patch
        image += self.rng.normal(0, 0.0005, image.shape)
        fmt = self.recipe.pixel_format
        bits = 8 if fmt.endswith("8") else (12 if "12" in fmt else 10 if "10" in fmt else 16)
        pixels = np.rint(np.clip(image, 0, 1) * (2**bits-1)).astype(np.uint8 if bits == 8 else np.uint16)
        if fmt == "RGB8":
            pixels = np.repeat(pixels[..., None], 3, axis=2)
        self.frame_id += 1
        metadata = capture_metadata(fmt, "SIMULATED", self.recipe.exposure_us, self.recipe.gain,
                                    device_frame_id=self.frame_id, backend="synthetic", simulated=True)
        metadata["processing"] = "synthetic chart with known focus surface; not physical measurements"
        return Capture(pixels, metadata)

    def close(self):
        pass
