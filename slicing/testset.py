"""python -m slicing.testset [folder]  -> 30 simple test parts as .3mf (default: testset/)."""
import sys
from pathlib import Path

from . import config as cfg
from .shapes import simple_parts


def write_testset(folder):
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    paths = []
    for i, (name, mesh) in enumerate(simple_parts(), start=1):
        path = folder / f"{i:02d}_{name}.3mf"
        mesh.export(path)
        paths.append(path)
    return paths


if __name__ == "__main__":
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else cfg.PROJECT_DIR / "testset"
    print(f"Wrote {len(write_testset(out))} parts to {out}")
