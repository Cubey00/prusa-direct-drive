"""Run prusa-slicer-console on a plate 3MF with a fixed profile, no rearranging, no supports."""
import re
import shutil
import subprocess
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


def check_compatible(version, profile):
    model = profile.get("printer_model", "")
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
