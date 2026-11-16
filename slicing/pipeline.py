"""Whole run: dataset folder -> plate 3MFs + manifests -> sliced G-code -> summary.json.
Used by `python -m slicing` and by the path programmer's import buttons."""
import json
from pathlib import Path

from . import config as cfg
from .layout import make_grid
from .plate import build_plates, write_manifest
from .filaments import Bundle, FilamentError, printer_suffix, write_filament_ini
from .slicer import SlicerError, check_compatible, find_slicer, read_profile, slice_plate, slicer_version

_OUTPUT_PATTERNS = ("filament.ini", "plate_*.3mf", "plate_*.json", "plate_*.gcode", "plate_*.bgcode",
                    "plate_*_slicer.log", "summary.json")


def clean_output(out_dir):
    """Remove the files of an earlier run, so a smaller dataset leaves no stale plates behind."""
    out_dir = Path(out_dir)
    for pattern in _OUTPUT_PATTERNS:
        for f in out_dir.glob(pattern):
            f.unlink()


def prepare_filament(filament, profile, out_dir):
    """filament.ini for preset `filament` (a name from PrusaSlicer's bundle) next to the plates,
    or None to keep the profile's own filament. Raises SlicerError if it can't be used."""
    if not filament:
        return None
    try:
        bundle = Bundle()
        suffix = printer_suffix(profile)
        if not filament.endswith(suffix):
            raise FilamentError(f"Filament {filament!r} is not for this printer and nozzle ({suffix})")
        return write_filament_ini(bundle, filament, Path(out_dir) / "filament.ini")
    except FilamentError as e:
        raise SlicerError(str(e)) from None


def run(dataset, out_dir, pitch=cfg.CELL_PITCH, profile=cfg.SLICER_PROFILE, do_slice=True,
        on_status=print, filament=None):
    """Returns the summary dict (also written to summary.json) plus 'manifests' (not written).
    filament: a PrusaSlicer filament preset name (e.g. 'Generic PETG @COREONE HF0.4'); None keeps
    the one in the profile. Slicing problems don't raise: they end up in 'slice_error' (none
    sliced) or 'slice_failed'."""
    out_dir = Path(out_dir)
    grid = make_grid(pitch=pitch)
    on_status(f"Raster {grid.cols} x {grid.rows} = {grid.capacity} parts per plate, "
              f"pitch {grid.pitch:g} mm, parts up to {grid.cell_free:g} mm")
    out_dir.mkdir(parents=True, exist_ok=True)
    clean_output(out_dir)
    manifests, rejected = build_plates(dataset, out_dir, grid)
    n_parts = sum(len(m["parts"]) for m in manifests)
    on_status(f"{n_parts} parts on {len(manifests)} plate(s), {len(rejected)} rejected")
    for r in rejected:
        on_status(f"  rejected {r['file']}: {'; '.join(r['reasons'])}")

    failed, slice_error, used_filament = [], None, None
    if do_slice and manifests:
        try:
            exe = find_slicer()
            version = slicer_version(exe)
            profile_values = read_profile(profile)
            check_compatible(version, profile_values)
            filament_ini = prepare_filament(filament, profile_values, out_dir)
            used_filament = filament or profile_values.get("filament_settings_id", "").strip('"')
            on_status(f"Filament: {used_filament}")
        except SlicerError as e:
            slice_error = str(e)
            on_status(f"Not sliced: {e}")
        else:
            for m in manifests:
                model = out_dir / m["model"]
                on_status(f"Slicing {model.name} ({len(m['parts'])} parts) ...")
                try:
                    gcode, log = slice_plate(model, exe, profile, filament_ini)
                except SlicerError as e:
                    on_status(f"  {e}")
                    failed.append(m["model"])
                    continue
                (out_dir / f"{model.stem}_slicer.log").write_text(log)
                m.update(gcode=gcode.name, slicer="PrusaSlicer " + ".".join(map(str, version)),
                         profile=str(profile), filament=used_filament)
                write_manifest(m, out_dir)
                on_status(f"  -> {gcode.name}")
                lines = log.splitlines()
                for i, line in enumerate(lines):     # e.g. "Low bed adhesion" with the part names
                    if line.startswith("Print warning"):
                        on_status("  " + "\n  ".join(l for l in lines[i:i + 6] if l.strip()))

    summary = {"dataset": str(dataset), "out": str(out_dir), "plates": [m["model"] for m in manifests],
               "parts": n_parts, "rejected": rejected, "sliced": do_slice and slice_error is None,
               "slice_error": slice_error, "slice_failed": failed,
               "filament": None if slice_error or not do_slice else used_filament}
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2))
    return {**summary, "manifests": manifests}
