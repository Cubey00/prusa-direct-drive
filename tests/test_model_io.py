import numpy as np
import pytest

from pathprogrammer import config
from pathprogrammer.face_render import ShadedTopRenderer
from pathprogrammer.model_io import Orientation, face_size, load_model, oriented_mesh

MODEL = config.PROJECT_DIR / "model.3mf"
needs_model = pytest.mark.skipif(not MODEL.exists(), reason="model.3mf not present")


@needs_model
def test_load_and_size():
    m = load_model(MODEL)
    assert len(m.sha256) == 64 and m.units_note == ""
    w, h = face_size(oriented_mesh(m.mesh, Orientation()))
    assert 1 < w < 500 and 1 < h < 500


def test_rotations_cycle():
    o = Orientation()
    for _ in range(4):
        o.spin()
    assert o == Orientation()
    o.tilt_up(); o.tilt_down(); o.turn_left(); o.turn_right()
    assert o == Orientation()
    o.tilt_up()
    assert Orientation.from_list(o.to_list()) == o
    assert round(np.linalg.det(o.matrix)) == 1


@needs_model
def test_tilt_shows_other_face():
    m = load_model(MODEL)
    top = face_size(oriented_mesh(m.mesh, Orientation()))
    o = Orientation()
    o.tilt_up()
    side = oriented_mesh(m.mesh, o)
    assert side.bounds[0] == pytest.approx([0, 0, 0], abs=1e-9)
    assert face_size(side) != pytest.approx(top)


@needs_model
def test_render_size():
    mesh = oriented_mesh(load_model(MODEL).mesh, Orientation())
    img = ShadedTopRenderer().render(mesh, px_per_mm=4)
    w, h = face_size(mesh)
    assert img.rgba.shape == (round(h * 4), round(w * 4), 4)
    assert img.rgba[..., 3].any()
