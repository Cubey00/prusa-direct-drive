"""Plate layout and slicer settings. Edit here, like the settings block in gantry_controls.

Bed coordinates are PrusaSlicer's: origin at the front-left corner of the print area, +X to the
right, +Y to the back, mm. The manifest stores everything in these coordinates; the three
fiducial cones on each plate tie them to the gantry later.
"""
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent

# ---------------- bed ----------------
BED_SIZE = (250.0, 220.0)      # Core One print area (X, Y)
# The stops on the rods keep the head out of the front and back 60 mm of the bed (seen from the
# door), so only this part of it is used: ((x min, x max), (y min, y max)) in bed mm.
USABLE_AREA = ((0.0, 250.0), (60.0, 160.0))
MAX_HEIGHT = 50.0              # mm, refuse taller parts (the sensor has to reach over them later)

# ---------------- raster ----------------
# Free strips inside the usable area: left and right wide enough for the fiducials, front and
# back only a small edge so the short Y range still holds several rows.
MARGIN_X = 20.0
MARGIN_Y = 5.0
CELL_PITCH = 20.0              # mm between neighbouring cell centres (10 x 4 = 40 cells)
MIN_GAP = 5.0                  # mm free between a part and the edge of the next cell

# ---------------- fiducials ----------------
# Three cones in an L in the side strips (front-left, front-right, back-left of the usable area),
# so the plate can't be read rotated.
FIDUCIAL_INSET = MARGIN_X / 2  # mm from the usable area's edges to the cone axis
FIDUCIAL_BASE_D = 8.0          # mm
FIDUCIAL_HEIGHT = 5.0          # mm

# ---------------- checks ----------------
OVERHANG_ANGLE = 45.0          # degrees from vertical; steeper downward faces need support
OVERHANG_MAX_AREA = 0.5        # mm^2 of such faces (above the bed) that is still tolerated

# ---------------- slicer ----------------
SLICER_EXE = None              # None = search the PrusaSlicer install and PATH
SLICER_SEARCH = [
    Path(r"C:\Program Files\Prusa3D\PrusaSlicer\prusa-slicer-console.exe"),
]
# Export once from the PrusaSlicer GUI (File > Export > Export Config) with the Core One printer,
# the print settings and the filament selected. Every plate is sliced with exactly this file.
SLICER_PROFILE = PROJECT_DIR / "slicing" / "profiles" / "coreone.ini"
SLICER_OVERRIDES = ["--support-material=0", "--support-material-auto=0"]   # never supports
MIN_SLICER_VERSION = (2, 9)    # first PrusaSlicer that knows the Core One
SLICER_TIMEOUT = 600           # s per plate
