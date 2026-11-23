"""Optical adapters do not home, touch, enable UDP metrics, or retreat on STOP."""
import threading
import time
import cv2
import numpy as np
from scipy.ndimage import gaussian_filter

from .acquisition import Capture, capture_metadata


class ZStage:
    simulated = False

    def __init__(self, fixture, printer_z_max, xy_calibration=None, printer_travel=None, *, xy_clearance=None):
        self.fixture = fixture
        self.printer_z_max = printer_z_max
        self.opened = False
        self.cancelled = threading.Event()
        self.xy_calibration = xy_calibration
        self.printer_travel = printer_travel
        self.xy_clearance = xy_clearance

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
        self.fixture.validate_motion_safety()
        gc.connect()
        self.opened = True
        if self.cancelled.is_set():
            raise InterruptedError("STOP requested")
        self.check(self.position())

    def position(self):
        import gantry_controls as gc
        return gc.read_position(cancel=self.cancelled)[2]

    def position_xyz(self):
        import gantry_controls as gc
        return gc.read_position(cancel=self.cancelled)

    def check_xy(self, x, y, z):
        c = self.xy_clearance
        if c is None and self.xy_calibration is not None:
            c = self.xy_calibration.validate(self.fixture).clearance()
        if c is None or self.printer_travel is None:
            raise ValueError("XY motion requires verified mechanical clearance and printer travel limits")
        c.validate()
        self.check(z)
        r = c.housing_radius_mm
        if (not np.isfinite([x, y, z]).all()
                or not c.safe_x_mm[0]+r <= x <= c.safe_x_mm[1]-r
                or not c.safe_y_mm[0]+r <= y <= c.safe_y_mm[1]-r
                or not self.printer_travel[0][0] <= x <= self.printer_travel[0][1]
                or not self.printer_travel[1][0] <= y <= self.printer_travel[1][1]
                or not c.safe_z_mm[0] <= z <= c.safe_z_mm[1]
                or z-c.housing_below_nozzle_mm-c.target_height_mm < c.clearance_margin_mm):
            raise ValueError("XY move outside housing envelope, printer travel or verified safe-Z/target clearance")

    def move_xy_absolute(self, x, y, *, speed_mm_s=1.0):
        import gantry_controls as gc
        if self.cancelled.is_set():
            raise InterruptedError("STOP requested")
        if not self.opened:
            raise RuntimeError("Optical stage must pass preflight before movement")
        if not 0 < speed_mm_s <= 2:
            raise ValueError("XY setup speed must be in 0..2 mm/s")
        current = self.position_xyz()
        self.check_xy(*current)
        self.check_xy(x, y, current[2])
        gc.move_absolute(x=x, y=y, XYfeed=speed_mm_s*60, cancel=self.cancelled)

    def move_z_absolute(self, z):
        import gantry_controls as gc
        if not self.opened:
            raise RuntimeError("Optical stage must pass preflight before movement")
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
        self.x = self.y = 150.0

    def position_xyz(self):
        return self.x, self.y, self.z

    def move_xy_absolute(self, x, y, *, speed_mm_s=1.0):
        if self.stopped:
            raise InterruptedError("STOP requested")
        self.calls.append(("move_xy", x, y))
        self.x, self.y = x, y

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
    scale = 16

    def __init__(self, stage, recipe, chart):
        self.stage, self.recipe, self.chart = stage, recipe, chart
        self.frame_id = 0
        self.rng = np.random.default_rng(7)
        self.offset_px = np.zeros(2)
        self.xy_jacobian = np.array([[-16, 3], [2, 16]])

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
        delta = self.offset_px + self.xy_jacobian @ (np.array([self.stage.x, self.stage.y]) - 150)
        if np.any(delta):
            image = cv2.warpAffine(image, np.float32([[1, 0, delta[0]], [0, 1, delta[1]]]),
                                   (image.shape[1], image.shape[0]), borderValue=0.82)
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
