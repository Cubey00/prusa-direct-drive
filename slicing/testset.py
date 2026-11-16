"""python -m slicing.testset [--mini] [folder]
-> the 30 simple test parts as .3mf (default folder: testset/), or with --mini only two small
   ones (default folder: testset_mini/) for quick first prints."""
import argparse
import re
from pathlib import Path

from . import config as cfg
from .shapes import simple_parts

# A flat square and a dome: two quick, small prints to try out printing, calibrating and touching
MINI_PARTS = ("vierkant_5mm", "halve_bol_5mm")


def write_testset(folder, names=None):
    """Write the test parts (all of them, or only `names`) as NN_<name>.3mf; the numbers stay those
    of the full set. Earlier NN_*.3mf files in the folder that are not in this set are removed."""
    folder = Path(folder)
    parts = [(i, name, mesh) for i, (name, mesh) in enumerate(simple_parts(), start=1)]
    if names is not None:
        unknown = set(names) - {name for _, name, _ in parts}
        if unknown:
            raise ValueError(f"Unknown test parts: {', '.join(sorted(unknown))}")
        parts = [p for p in parts if p[1] in names]
    folder.mkdir(parents=True, exist_ok=True)
    keep = {f"{i:02d}_{name}.3mf" for i, name, _ in parts}
    for old in folder.glob("*.3mf"):
        if re.fullmatch(r"\d\d_.+\.3mf", old.name) and old.name not in keep:
            old.unlink()
    paths = []
    for i, name, mesh in parts:
        path = folder / f"{i:02d}_{name}.3mf"
        mesh.export(path)
        paths.append(path)
    return paths


def write_mini_testset(folder):
    return write_testset(folder, MINI_PARTS)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(prog="python -m slicing.testset")
    ap.add_argument("folder", nargs="?", type=Path)
    ap.add_argument("--mini", action="store_true", help=f"only {', '.join(MINI_PARTS)}")
    args = ap.parse_args()
    out = args.folder or cfg.PROJECT_DIR / ("testset_mini" if args.mini else "testset")
    written = write_mini_testset(out) if args.mini else write_testset(out)
    print(f"Wrote {len(written)} parts to {out}")
