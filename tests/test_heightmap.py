import numpy as np
import trimesh

from pathprogrammer import heightmap
from pathprogrammer.model_io import Orientation, oriented_mesh


def mesh():
    base = trimesh.creation.box(extents=(20, 10, 2))
    block = trimesh.creation.box(extents=(5, 4, 3))
    block.apply_translation((5, 0, 2.5))        # right of centre, sticks 3 mm above the base top
    return oriented_mesh(trimesh.util.concatenate([base, block]), Orientation())


def test_heights_and_extent():
    xs, ys, H = heightmap.compute(mesh(), 0.2)
    assert abs(xs[-1] - 20) < 0.2 and abs(ys[-1] - 10) < 0.2
    at = lambda x, y: H[np.argmin(abs(ys - y)), np.argmin(abs(xs - x))]
    assert abs(at(2, 5) - 2) < 1e-6              # base: 2 mm above the bed
    assert abs(at(15, 5) - 5) < 1e-6             # block: 5 mm
    # face y runs toward the front, block is centred: spans y 3..7
    assert abs(at(15, 1) - 2) < 1e-6


def test_csv_roundtrip_and_png(tmp_path):
    xs, ys, H = heightmap.compute(mesh(), 0.5)
    heightmap.save_csv(tmp_path / "h.csv", xs, ys, H)
    x2, y2, H2 = heightmap.load_csv(tmp_path / "h.csv")
    assert np.allclose(xs, x2, atol=1e-3) and np.allclose(H, H2, atol=1e-3, equal_nan=True)
    heightmap.save_png(tmp_path / "h.png", xs, ys, H, [(5, 5)])
    assert (tmp_path / "h.png").stat().st_size > 0
