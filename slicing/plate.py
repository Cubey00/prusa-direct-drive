"""Load a dataset, place the parts on the raster and write one plate 3MF + manifest per bed."""
import json
from dataclasses import dataclass
from pathlib import Path

import trimesh

from pathprogrammer.model_io import load_model
from . import config as cfg
from .layout import check_part, fiducials, make_grid, on_bed, split_plates
from .shapes import fiducial


@dataclass
class Part:
    id: str              # file stem, unique in the dataset
    source: Path
    sha256: str
    mesh: trimesh.Trimesh    # centred on x = y = 0, lowest point at z = 0
    size: tuple
    warnings: list


def load_dataset(folder, grid):
    """All .3mf files in `folder` (sorted by name) -> (accepted parts, rejected [{file, reasons}])."""
    files = sorted(Path(folder).glob("*.3mf"))
    if not files:
        raise ValueError(f"No .3mf files in {folder}")
    parts, rejected = [], []
    for f in files:
        try:
            model = load_model(f)
        except Exception as e:
            rejected.append({"file": f.name, "reasons": [f"could not load: {e}"]})
            continue
        mesh = on_bed(model.mesh)
        chk = check_part(mesh, grid)
        warnings = chk.warnings + ([model.units_note] if model.units_note else [])
        if chk.ok:
            parts.append(Part(f.stem, f, model.sha256, mesh, chk.size, warnings))
        else:
            rejected.append({"file": f.name, "reasons": chk.reasons})
    return parts, rejected


def _translate(mesh, x, y):
    m = mesh.copy()
    m.apply_translation([x, y, 0])
    return m


def build_plate(parts, grid, number, out_dir):
    """Write plate_NN.3mf and plate_NN.json; returns the manifest dict."""
    out_dir = Path(out_dir)
    name = f"plate_{number:02d}"
    scene = trimesh.Scene()
    placed = []
    for i, p in enumerate(parts):
        col, row = grid.cell(i)
        x, y = grid.centre(col, row)
        scene.add_geometry(_translate(p.mesh, x, y), geom_name=p.id, node_name=p.id)
        placed.append({"id": p.id, "source": str(p.source), "sha256": p.sha256,
                       "cell": [col, row], "x": round(x, 3), "y": round(y, 3),
                       "size": [round(v, 3) for v in p.size], "warnings": p.warnings})
    fids = fiducials()
    cone = fiducial()
    for f in fids:
        scene.add_geometry(_translate(cone, f["x"], f["y"]), geom_name=f["name"], node_name=f["name"])

    model_path = out_dir / f"{name}.3mf"
    scene.export(model_path)
    manifest = {
        "plate": number,
        "model": model_path.name,
        "gcode": None,
        "coordinates": "bed mm: origin front-left of the print area, +X right, +Y back, "
                       "z = 0 on the bed; x/y are the centre of a part's bounding box",
        "bed_size": list(cfg.BED_SIZE),
        "grid": {"cols": grid.cols, "rows": grid.rows, "pitch": grid.pitch,
                 "x0": grid.x0, "y0": grid.y0, "cell_order": "row by row from the front-left"},
        "fiducials": fids,
        "fiducial_shape": {"type": "cone", "base_d": cfg.FIDUCIAL_BASE_D,
                           "height": cfg.FIDUCIAL_HEIGHT},
        "parts": placed,
    }
    write_manifest(manifest, out_dir)
    return manifest


def write_manifest(manifest, out_dir):
    path = Path(out_dir) / f"plate_{manifest['plate']:02d}.json"
    path.write_text(json.dumps(manifest, indent=2))
    return path


def build_plates(folder, out_dir, grid=None):
    """Dataset folder -> plate files in out_dir. Returns (manifests, rejected)."""
    grid = grid or make_grid()
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    parts, rejected = load_dataset(folder, grid)
    manifests = [build_plate(chunk, grid, n, out_dir)
                 for n, chunk in enumerate(split_plates(parts, grid.capacity), start=1)]
    return manifests, rejected
