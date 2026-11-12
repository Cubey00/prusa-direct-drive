"""Limits and defaults for the path programmer. Values that depend on the physical setup (forces,
machine bounds, calibration tolerances) come from machine_settings.toml: change them there."""
from pathlib import Path

from machine_settings import SETTINGS

PROJECT_DIR = Path(__file__).resolve().parent.parent
GUI_SCRIPT = PROJECT_DIR / "GUI.py"
RUN_LOG_DIR = PROJECT_DIR / "logs" / "runs"

# face +Y points to the front, machine +Y to the back -> mirror. Check once on the hardware.
FACE_Y_TO_MACHINE_SIGN = -1

# ---------------- speeds (mm/s) ----------------
TRAVEL_SPEED = 40.0
TRAVEL_SPEED_RANGE = (1.0, 60.0)       # same as GUI.get_speed
APPROACH_SPEED = 2.0                   # fixed speed until contact
APPROACH_SPEED_RANGE = (0.1, 5.0)
SEARCH_SPEED = 0.1                     # slow contact search after a fast contact / near the expected height
TOUCH_SPEED = 0.5                      # default speed after contact
TOUCH_SPEED_RANGE = (0.05, 2.0)
RETURN_SPEED = 10.0                    # back up to the travel height

# ---------------- touch ----------------
CONTACT_G = SETTINGS.forces.contact_g          # |force| that counts as contact
MAX_FORCE_G = SETTINGS.forces.max_force_g      # abort the touch above this
GUARD_TRIP_G = SETTINGS.forces.guard_trip_g    # force_guard: emergency drop above this
GUARD_RELEASE_G = SETTINGS.forces.guard_release_g
FORCE_RANGE = (1.0, 100.0)             # grams, for "M = ..." points
DEPTH_RANGE = (0.01, 0.1)              # mm past contact, for "d = ..." points
MAX_TRAVEL = 50.0                      # mm below the start Z to look for contact
MAX_TRAVEL_RANGE = (0.5, 200.0)
HOLD_S = 0.1

# ---------------- machine ----------------
MACHINE_XY_BOUNDS = SETTINGS.work_area.machine     # [work_area] machine_x / machine_y

# ---------------- calibration ----------------
CAL_MIN_DISTANCE = SETTINGS.calibration.min_distance   # mm
CAL_SCALE_TOL = SETTINGS.calibration.scale_tol         # warn if |scale - 1| exceeds this
CAL_RESIDUAL_TOL = SETTINGS.calibration.residual_tol   # mm; warn if a point is this far off the fit

# ---------------- rendering ----------------
RENDER_PX_PER_MM = 8.0
RENDER_MAX_PX = 2500
