"""Plate layout and slicer settings. The physical values (bed, usable area, raster, fiducials)
come from machine_settings.toml: change them there. Slicer-only settings stay below.

Bed coordinates are PrusaSlicer's: origin at the front-left corner of the print area, +X to the
right, +Y to the back, mm. The manifest stores everything in these coordinates; the three
fiducial cones on each plate tie them to the gantry later.
"""
from pathlib import Path

from machine_settings import SETTINGS

PROJECT_DIR = Path(__file__).resolve().parent.parent

# ---------------- bed (machine_settings.toml [printer], [work_area]) ----------------
BED_SIZE = SETTINGS.printer.bed_size
# The stops keep the head out of part of the bed: ((x min, x max), (y min, y max)) in bed mm.
USABLE_AREA = SETTINGS.work_area.usable
MAX_HEIGHT = SETTINGS.raster.max_part_height

# ---------------- raster ([raster]) ----------------
MARGIN_X = SETTINGS.raster.margin_x
MARGIN_Y = SETTINGS.raster.margin_y
CELL_PITCH = SETTINGS.raster.cell_pitch
MIN_GAP = SETTINGS.raster.min_gap

# ---------------- fiducials ([fiducials]) ----------------
# Three cones in an L in the side strips (front-left, front-right, back-left of the usable area),
# so the plate can't be read rotated.
FIDUCIAL_INSET = SETTINGS.fiducials.inset
FIDUCIAL_BASE_D = SETTINGS.fiducials.base_diameter
FIDUCIAL_HEIGHT = SETTINGS.fiducials.height

# ---------------- checks ----------------
OVERHANG_ANGLE = 45.0          # degrees from vertical; steeper downward faces need support
OVERHANG_MAX_AREA = 0.5        # mm^2 of such faces (above the bed) that is still tolerated

# ---------------- slicer ----------------
SLICER_EXE = None              # None = search the PrusaSlicer install and PATH
SLICER_SEARCH = [
    Path(r"C:\Program Files\Prusa3D\PrusaSlicer\prusa-slicer-console.exe"),
    Path("/Applications/PrusaSlicer.app/Contents/MacOS/PrusaSlicer"),
    Path.home() / "Applications/PrusaSlicer.app/Contents/MacOS/PrusaSlicer",
]
# Export once from the PrusaSlicer GUI (File > Export > Export Config) with the Core One printer,
# the print settings and the filament selected. Every plate is sliced with exactly this file.
SLICER_PROFILE = PROJECT_DIR / "slicing" / "profiles" / "coreone.ini"
SLICER_OVERRIDES = ["--support-material=0", "--support-material-auto=0"]   # never supports
PRINTER_MODEL = SETTINGS.printer.model   # printer_model the profile must have (COREONEL is the bigger L)
MIN_SLICER_VERSION = (2, 9)    # first PrusaSlicer that knows the Core One
SLICER_TIMEOUT = 600           # s per plate
