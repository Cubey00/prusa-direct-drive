"""python -m slicing <dataset folder> <output folder> [--pitch 20] [--profile x.ini] [--no-slice]

Places every .3mf and .stl of the dataset on a fixed raster (plus three fiducial cones), writes one plate
3MF + manifest JSON per bed, slices each plate and writes summary.json with the rejected parts."""
import argparse
import sys
from pathlib import Path

from . import config as cfg
from .pipeline import run


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m slicing", description=__doc__.splitlines()[2])
    ap.add_argument("dataset", type=Path)
    ap.add_argument("out", type=Path)
    ap.add_argument("--pitch", type=float, default=cfg.CELL_PITCH, help="mm between cell centres")
    ap.add_argument("--profile", type=Path, default=cfg.SLICER_PROFILE, help="PrusaSlicer config .ini")
    ap.add_argument("--no-slice", action="store_true", help="only write the plate 3MFs and manifests")
    args = ap.parse_args(argv)
    s = run(args.dataset, args.out, args.pitch, args.profile, not args.no_slice,
            on_status=lambda msg: print(msg, flush=True))
    return 1 if s["slice_error"] or s["slice_failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
