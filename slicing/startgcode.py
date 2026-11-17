"""A faster start of each print: fewer bed probe points and no wait for the bed frame to warm up.

Prusa's start G-code (slicing/profiles/coreone.ini) runs `G29 P1`, which probes every probe-grid
point in the first layer's area enlarged by one grid step (~30 mm) all round: 7 x 6 = 42 points on
our plates. And `G29 G` waits until the whole bed frame is within 1 degC of the bed target, which
takes minutes from a cold bed.

Instead each spot in machine_settings.toml [printing] bed_probe_points is probed on its own
(`G29 P1 X Y W H` with a small box around one grid point; `C` keeps the points probed before),
and the start G-code's own `G29 P3.2` / `G29 P3.13` fill in the rest of the mesh from them.
The result is written as an extra .ini that PrusaSlicer loads after the profile.
"""
from pathlib import Path

# Probe grid of the team firmware (Cubey00/prusa-direct-drive-firmware,
# include/marlin/Configuration_COREONE.h / _adv.h, ubl_G29.cpp probe_major_points):
# a 21 x 21 mesh from MESH_MIN to MESH_MAX; G29 P1 probes only every 3rd point, skipping the border.
MESH_MIN = (2.0, 43.0)        # MESH_MIN_X, MESH_MIN_Y (Y: travel starts at 42 because of the spacer)
MESH_MAX = (198.0, 217.0)     # MESH_MAX_X (travel ends at X 201), MESH_MAX_Y
GRID_POINTS = 21
GRID_BORDER = 1
GRID_MAJOR_STEP = 3
BOX = 2.0                     # mm around a grid point: catches that point only

PROBE_LINE = "G29 P1 ; invalidate mbl & probe print area"
ABSORB_LINE = "G29 G ; absorb heat"
INI_NAME = "start_gcode.ini"


def probe_grid():
    """X and Y positions (bed mm) of the points G29 P1 can probe."""
    def axis(i):
        step = (MESH_MAX[i] - MESH_MIN[i]) / (GRID_POINTS - 1)
        return [MESH_MIN[i] + k * step for k in range(GRID_BORDER, GRID_POINTS - GRID_BORDER, GRID_MAJOR_STEP)]
    return axis(0), axis(1)


def snap(points):
    """Nearest probe-grid point for each (x, y), without duplicates, in the given order."""
    xs, ys = probe_grid()
    out = []
    for x, y in points:
        p = (min(xs, key=lambda v: abs(v - x)), min(ys, key=lambda v: abs(v - y)))
        if p not in out:
            out.append(p)
    return out


def probe_commands(points):
    """G-code lines probing only these spots: the first one clears the old mesh, the rest add to it."""
    lines = []
    for i, (x, y) in enumerate(snap(points)):
        keep = "" if i == 0 else " C"
        lines.append(f"G29 P1 X{x - BOX:.1f} Y{y - BOX:.1f} W{2 * BOX:g} H{2 * BOX:g}{keep} "
                     f"; probe near X{x:.1f} Y{y:.1f}")
    return lines


def adapt(start_gcode, probe_points, wait_for_bed_frame):
    """The profile's start_gcode value (as in the .ini: lines joined by a literal '\\n') with the
    probing and the bed-frame wait replaced. Returns (new value, notes about what changed)."""
    lines = start_gcode.split("\\n")
    notes = []
    if probe_points:
        if PROBE_LINE in lines:
            i = lines.index(PROBE_LINE)
            cmds = probe_commands(probe_points)
            lines[i:i + 1] = cmds
            notes.append(f"bed probing: {len(cmds)} points")
        else:
            notes.append(f"bed probing unchanged: '{PROBE_LINE}' not found in the profile")
    if not wait_for_bed_frame:
        if ABSORB_LINE in lines:
            lines[lines.index(ABSORB_LINE)] = "; no wait for the bed frame to warm up (machine_settings.toml)"
            notes.append("no wait for the bed frame")
        else:
            notes.append(f"bed frame wait unchanged: '{ABSORB_LINE}' not found in the profile")
    return "\\n".join(lines), notes


def write_start_ini(profile_values, out_dir, printing):
    """INI_NAME in out_dir with the adapted start_gcode, or None if nothing changes.
    printing: machine_settings Printing. Returns (path or None, notes)."""
    start = profile_values.get("start_gcode")
    if start is None:
        return None, ["start G-code unchanged: the profile has none"]
    new, notes = adapt(start, printing.bed_probe_points, printing.wait_for_bed_frame)
    if new == start:
        return None, notes
    path = Path(out_dir) / INI_NAME
    path.write_text(f"# start G-code from the profile, adapted by slicing/startgcode.py\nstart_gcode = {new}\n",
                    encoding="utf-8")
    return path, notes
