"""Fixed raster on the bed, fiducial positions, part checks and the split into plates.
Pure logic (numpy/trimesh only), no files and no slicer."""
import math
from dataclasses import dataclass, field

import numpy as np

from . import config as cfg


@dataclass(frozen=True)
class Grid:
    cols: int
    rows: int
    pitch: float
    x0: float          # centre of cell (0, 0) = front-left cell
    y0: float

    @property
    def capacity(self):
        return self.cols * self.rows

    @property
    def cell_free(self):
        """Largest part footprint (mm, square) a cell takes."""
        return self.pitch - cfg.MIN_GAP

    def cell(self, index):
        """(col, row) of the index-th cell: row by row from the front-left, left to right."""
        return index % self.cols, index // self.cols

    def centre(self, col, row):
        return self.x0 + col * self.pitch, self.y0 + row * self.pitch


def make_grid(area=cfg.USABLE_AREA, margin=(cfg.MARGIN_X, cfg.MARGIN_Y), pitch=cfg.CELL_PITCH):
    """As many cells as fit inside the margins of the usable area, the raster centred in it.
    A cell's whole width (pitch - MIN_GAP) stays inside the margins."""
    (ax0, ax1), (ay0, ay1) = area
    free = pitch - cfg.MIN_GAP
    cols = int((ax1 - ax0 - 2 * margin[0] - free) // pitch) + 1
    rows = int((ay1 - ay0 - 2 * margin[1] - free) // pitch) + 1
    if free <= 0 or cols < 1 or rows < 1:
        raise ValueError(f"No cell of {pitch:g} mm fits inside the usable area and margins")
    x0 = (ax0 + ax1 - (cols - 1) * pitch) / 2
    y0 = (ay0 + ay1 - (rows - 1) * pitch) / 2
    return Grid(cols, rows, pitch, x0, y0)


def fiducials(area=cfg.USABLE_AREA, inset=cfg.FIDUCIAL_INSET, height=cfg.FIDUCIAL_HEIGHT):
    """Tip positions of the three cones: F1 front-left, F2 front-right, F3 back-left."""
    (x0, x1), (y0, y1) = area
    return [
        {"name": "F1", "x": x0 + inset, "y": y0 + inset, "z": height},
        {"name": "F2", "x": x1 - inset, "y": y0 + inset, "z": height},
        {"name": "F3", "x": x0 + inset, "y": y1 - inset, "z": height},
    ]


# ---------------- part checks ----------------

def on_bed(mesh):
    """Copy with the bounding box centred on x = y = 0 and the lowest point at z = 0."""
    m = mesh.copy()
    lo, hi = m.bounds
    m.apply_translation([-(lo[0] + hi[0]) / 2, -(lo[1] + hi[1]) / 2, -lo[2]])
    return m


def overhang_area(mesh, angle=cfg.OVERHANG_ANGLE, bed_tol=0.01):
    """mm^2 of downward faces steeper than `angle` from vertical, not lying on the bed."""
    limit = -math.sin(math.radians(90 - angle))     # normal z below this needs support
    down = mesh.face_normals[:, 2] < limit
    lifted = mesh.triangles[:, :, 2].min(axis=1) > mesh.bounds[0][2] + bed_tol
    return float(mesh.area_faces[down & lifted].sum())


@dataclass
class Check:
    ok: bool
    size: tuple                 # (x, y, z) extent in mm
    reasons: list = field(default_factory=list)
    warnings: list = field(default_factory=list)


def check_part(mesh, grid):
    """Fits a cell, not too tall, prints without support."""
    size = tuple(float(v) for v in mesh.bounds[1] - mesh.bounds[0])
    res = Check(True, size)
    if max(size[0], size[1]) > grid.cell_free:
        res.reasons.append(f"footprint {size[0]:.1f} x {size[1]:.1f} mm exceeds the "
                           f"{grid.cell_free:g} mm cell")
    if size[2] > cfg.MAX_HEIGHT:
        res.reasons.append(f"height {size[2]:.1f} mm exceeds {cfg.MAX_HEIGHT:g} mm")
    area = overhang_area(mesh)
    if area > cfg.OVERHANG_MAX_AREA:
        res.reasons.append(f"{area:.1f} mm^2 overhang steeper than {cfg.OVERHANG_ANGLE:g} deg "
                           f"(needs support)")
    if not mesh.is_watertight:
        res.warnings.append("mesh is not watertight; the slicer will try to repair it")
    res.ok = not res.reasons
    return res


def split_plates(items, capacity):
    """Consecutive chunks of at most `capacity`."""
    return [items[i:i + capacity] for i in range(0, len(items), capacity)]
