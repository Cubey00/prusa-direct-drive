"""python -m slicing <dataset folder> <output folder> [--pitch 25] [--profile x.ini] [--no-slice]

Places every .3mf of the dataset on a fixed raster (plus three fiducial cones), writes one plate
3MF + manifest JSON per bed, slices each plate and writes summary.json with the rejected parts."""
import argparse
import json
import sys
from pathlib import Path

from . import config as cfg
from .layout import make_grid
from .plate import build_plates, write_manifest
from .slicer import SlicerError, find_slicer, slice_plate, slicer_version


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m slicing", description=__doc__.splitlines()[2])
    ap.add_argument("dataset", type=Path)
    ap.add_argument("out", type=Path)
    ap.add_argument("--pitch", type=float, default=cfg.CELL_PITCH, help="mm between cell centres")
    ap.add_argument("--profile", type=Path, default=cfg.SLICER_PROFILE, help="PrusaSlicer config .ini")
    ap.add_argument("--no-slice", action="store_true", help="only write the plate 3MFs and manifests")
    args = ap.parse_args(argv)

    grid = make_grid(pitch=args.pitch)
    print(f"Raster {grid.cols} x {grid.rows} = {grid.capacity} parts per plate, "
          f"pitch {grid.pitch:g} mm, parts up to {grid.cell_free:g} mm")
    manifests, rejected = build_plates(args.dataset, args.out, grid)
    n_parts = sum(len(m["parts"]) for m in manifests)
    print(f"{n_parts} parts on {len(manifests)} plate(s), {len(rejected)} rejected")
    for r in rejected:
        print(f"  rejected {r['file']}: {'; '.join(r['reasons'])}")

    failed = []
    if not args.no_slice and manifests:
        try:
            exe = find_slicer()
            version = ".".join(map(str, slicer_version(exe)))
        except SlicerError as e:
            print(e)
            return 1
        for m in manifests:
            model = args.out / m["model"]
            print(f"Slicing {model.name} ({len(m['parts'])} parts) ...", flush=True)
            try:
                gcode, log = slice_plate(model, exe, args.profile)
            except SlicerError as e:
                print(f"  {e}")
                failed.append(m["model"])
                continue
            (args.out / f"{model.stem}_slicer.log").write_text(log)
            m.update(gcode=gcode.name, slicer=f"PrusaSlicer {version}", profile=str(args.profile))
            write_manifest(m, args.out)
            print(f"  -> {gcode.name}")
            lines = log.splitlines()
            for i, line in enumerate(lines):     # e.g. "Low bed adhesion" with the part names
                if line.startswith("Print warning"):
                    print("  " + "\n  ".join(l for l in lines[i:i + 6] if l.strip()))

    summary = {"dataset": str(args.dataset), "plates": [m["model"] for m in manifests],
               "parts": n_parts, "rejected": rejected, "slice_failed": failed}
    (args.out / "summary.json").write_text(json.dumps(summary, indent=2))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
