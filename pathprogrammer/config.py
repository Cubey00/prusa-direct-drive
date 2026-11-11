"""Limits and defaults for the path programmer. Edit here, like the settings block in gantry_controls."""
from pathlib import Path

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
CONTACT_G = 5.0                        # |force| that counts as contact
MAX_FORCE_G = 450.0                    # abort the touch above this (force_guard trips at 500)
FORCE_RANGE = (1.0, 100.0)             # grams, for "M = ..." points
DEPTH_RANGE = (0.01, 0.1)              # mm past contact, for "d = ..." points
MAX_TRAVEL = 50.0                      # mm below the start Z to look for contact
MAX_TRAVEL_RANGE = (0.5, 200.0)
HOLD_S = 0.1

# ---------------- machine ----------------
MACHINE_XY_BOUNDS = ((0.0, 250.0), (0.0, 220.0))   # placeholder, confirm against the Core One

# ---------------- calibration ----------------
CAL_MIN_DISTANCE = 10.0                # mm
CAL_SCALE_TOL = 0.02                   # warn if |scale - 1| exceeds this

# ---------------- rendering ----------------
RENDER_PX_PER_MM = 8.0
RENDER_MAX_PX = 2500
