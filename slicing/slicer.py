"""Run prusa-slicer-console on a plate 3MF with a fixed profile, no rearranging, no supports."""
import re
import shutil
import subprocess
import sys
from pathlib import Path

from . import config as cfg


class SlicerError(Exception):
    pass


def find_slicer():
    if cfg.SLICER_EXE:
        return Path(cfg.SLICER_EXE)
    for p in cfg.SLICER_SEARCH:
        if p.exists():
            return p
    found = shutil.which("prusa-slicer-console") or shutil.which("prusa-slicer")
    if found:
        return Path(found)
    raise SlicerError("PrusaSlicer not found. Install it or set SLICER_EXE in slicing/config.py.")


def viewer_command(gcode):
    """Command that opens a (b)gcode file in PrusaSlicer's 3D G-code viewer."""
    exe = find_slicer()
    if sys.platform == "darwin":
        return [str(exe), "--gcodeviewer", str(gcode)]
    folder = exe.parent
    viewer = folder / "prusa-gcodeviewer.exe"
    if viewer.exists():
        return [str(viewer), str(gcode)]
    gui = folder / "prusa-slicer.exe"
    if gui.exists():
        return [str(gui), "--gcodeviewer", str(gcode)]
    found = shutil.which("prusa-gcodeviewer")
    if found:
        return [found, str(gcode)]
    raise SlicerError("PrusaSlicer's G-code viewer was not found next to the slicer.")


def open_in_viewer(gcode):
    """Start the viewer without waiting for it."""
    gcode = Path(gcode)
    if not gcode.exists():
        raise SlicerError(f"{gcode.name} does not exist; slice the plate first.")
    subprocess.Popen(viewer_command(gcode))


def slicer_version(exe):
    """(major, minor, patch) from the first line of --help, e.g. 'PrusaSlicer-2.6.1+win64-...'."""
    out = subprocess.run([str(exe), "--help"], capture_output=True, text=True, timeout=60).stdout
    m = re.search(r"PrusaSlicer-(\d+)\.(\d+)\.(\d+)", out)
    if not m:
        raise SlicerError(f"Could not read the PrusaSlicer version from {exe}")
    return tuple(int(v) for v in m.groups())


def read_profile(path):
    """key -> value of a PrusaSlicer config .ini."""
    path = Path(path)
    if not path.exists():
        raise SlicerError(
            f"No slicer profile at {path}. In PrusaSlicer select the Core One printer, the print "
            f"settings and the filament, then File > Export > Export Config to that path.")
    values = {}
    for line in path.read_text(errors="ignore").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            values[k.strip()] = v.strip()
    return values


def _bed_extent(bed_shape):
    """'0x0,250x0,250x220,0x220' -> (250.0, 220.0)."""
    pts = [tuple(float(v) for v in p.split("x")) for p in bed_shape.split(",") if "x" in p]
    xs, ys = zip(*pts)
    return max(xs) - min(xs), max(ys) - min(ys)


def check_compatible(version, profile):
    """Refuse a profile for another printer or bed, and a slicer too old for the Core One."""
    model = profile.get("printer_model", "")
    if model and model.upper() != cfg.PRINTER_MODEL:
        raise SlicerError(f"The profile is for printer model {model} "
                          f"({profile.get('printer_settings_id', '?')}), not {cfg.PRINTER_MODEL}. "
                          f"Select the Core One (not the Core One L) in PrusaSlicer and export again.")
    if profile.get("bed_shape"):
        extent = _bed_extent(profile["bed_shape"])
        if any(abs(a - b) > 0.5 for a, b in zip(extent, cfg.BED_SIZE)):
            raise SlicerError(f"The profile's bed is {extent[0]:g} x {extent[1]:g} mm, expected "
                              f"{cfg.BED_SIZE[0]:g} x {cfg.BED_SIZE[1]:g} mm.")
    if model.upper().startswith("COREONE") and version[:2] < cfg.MIN_SLICER_VERSION:
        need = ".".join(map(str, cfg.MIN_SLICER_VERSION))
        raise SlicerError(f"PrusaSlicer {'.'.join(map(str, version))} does not know the Core One; "
                          f"install {need} or newer.")


def slice_plate(model_path, exe=None, profile_path=None):
    """Slice one plate; returns (gcode path, slicer log). .bgcode if the profile enables binary G-code."""
    exe = Path(exe) if exe else find_slicer()
    profile_path = Path(profile_path) if profile_path else cfg.SLICER_PROFILE
    profile = read_profile(profile_path)
    check_compatible(slicer_version(exe), profile)
    model_path = Path(model_path)
    ext = ".bgcode" if profile.get("binary_gcode") == "1" else ".gcode"
    out = model_path.with_suffix(ext)
    out.unlink(missing_ok=True)         # a stale file must not pass as this run's output
    cmd = [str(exe), "--load", str(profile_path), *cfg.SLICER_OVERRIDES, "--dont-arrange",
           "--export-gcode", str(model_path), "--output", str(out)]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=cfg.SLICER_TIMEOUT)
    log = proc.stdout + proc.stderr
    if proc.returncode != 0 or not out.exists():
        tail = "\n".join(log.strip().splitlines()[-10:])
        raise SlicerError(f"Slicing {model_path.name} failed (exit {proc.returncode}):\n{tail}")
    return out, log
