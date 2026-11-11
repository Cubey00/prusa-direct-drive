import json

import numpy as np
import pytest
import trimesh

from slicing import config as cfg
from slicing.layout import check_part, fiducials, make_grid, on_bed, overhang_area, split_plates
from slicing.plate import build_plates
from slicing.shapes import fiducial, half_sphere, simple_parts
from slicing.slicer import SlicerError, check_compatible, read_profile
from slicing.testset import write_testset


AREA = ((0.0, 250.0), (60.0, 160.0))      # front and back 60 mm blocked by the stops


def test_grid_fits_inside_margins_of_usable_area_and_is_centred():
    g = make_grid(area=AREA, margin=(20, 5), pitch=20)
    assert (g.cols, g.rows, g.capacity) == (10, 4, 40)
    xs = [g.centre(c, 0)[0] for c in range(g.cols)]
    ys = [g.centre(0, r)[1] for r in range(g.rows)]
    assert xs[0] - g.cell_free / 2 >= 20 and xs[-1] + g.cell_free / 2 <= 230
    assert ys[0] - g.cell_free / 2 >= 65 and ys[-1] + g.cell_free / 2 <= 155
    assert xs[0] + xs[-1] == pytest.approx(250) and ys[0] + ys[-1] == pytest.approx(220)


def test_default_grid_stays_out_of_blocked_strips_and_holds_the_testset():
    g = make_grid()
    (ax0, ax1), (ay0, ay1) = cfg.USABLE_AREA
    for i in range(g.capacity):
        x, y = g.centre(*g.cell(i))
        assert ax0 <= x - g.cell_free / 2 and x + g.cell_free / 2 <= ax1
        assert ay0 <= y - g.cell_free / 2 and y + g.cell_free / 2 <= ay1
    assert g.capacity >= 30


def test_grid_too_coarse_raises():
    with pytest.raises(ValueError):
        make_grid(area=((0, 50), (0, 50)), margin=(20, 20), pitch=25)


def test_fiducials_inside_usable_area():
    (ax0, ax1), (ay0, ay1) = cfg.USABLE_AREA
    r = cfg.FIDUCIAL_BASE_D / 2
    for p in fiducials():
        assert ax0 + r <= p["x"] <= ax1 - r and ay0 + r <= p["y"] <= ay1 - r


def test_cell_order_row_by_row_from_front_left():
    g = make_grid()
    assert g.cell(0) == (0, 0)
    assert g.cell(1) == (1, 0)
    assert g.cell(g.cols) == (0, 1)


def test_fiducials_in_margin_l_shape_clear_of_cells():
    g = make_grid()
    f = {p["name"]: p for p in fiducials()}
    assert f["F1"]["y"] == f["F2"]["y"] and f["F1"]["x"] == f["F3"]["x"]
    r = cfg.FIDUCIAL_BASE_D / 2
    for p in f.values():
        for i in range(g.capacity):
            cx, cy = g.centre(*g.cell(i))
            half = g.cell_free / 2 + r
            assert abs(p["x"] - cx) > half or abs(p["y"] - cy) > half


def test_fiducial_tip_height():
    assert fiducial().bounds[1][2] == pytest.approx(cfg.FIDUCIAL_HEIGHT)


def test_overhang_dome_up_ok_dome_down_needs_support():
    dome = half_sphere(10)
    assert overhang_area(dome) == pytest.approx(0, abs=1e-6)
    flipped = dome.copy()
    flipped.apply_transform(trimesh.transformations.rotation_matrix(np.pi, [1, 0, 0]))
    assert overhang_area(on_bed(flipped)) > 10


def test_check_rejects_too_wide_and_overhang():
    g = make_grid()
    assert not check_part(on_bed(trimesh.creation.box((30, 5, 2))), g).ok
    t = trimesh.creation.box((2, 2, 10))
    t = trimesh.util.concatenate([t, trimesh.creation.box((10, 10, 2)).apply_translation([0, 0, 6])])
    res = check_part(on_bed(t), g)          # T-shape: the cap overhangs
    assert not res.ok and "support" in res.reasons[0]


def test_all_test_parts_pass_checks():
    g = make_grid()
    parts = simple_parts()
    assert len(parts) == 30 and len({n for n, _ in parts}) == 30
    for name, mesh in parts:
        res = check_part(on_bed(mesh), g)
        assert res.ok, (name, res.reasons)
        assert mesh.is_watertight, name


def test_split_plates():
    assert split_plates(list(range(5)), 2) == [[0, 1], [2, 3], [4]]


def test_build_plates_positions_roundtrip(tmp_path):
    write_testset(tmp_path / "set")
    trimesh.creation.box((40, 40, 2)).export(tmp_path / "set" / "99_te_groot.3mf")
    grid = make_grid()
    manifests, rejected = build_plates(tmp_path / "set", tmp_path / "out", grid)
    assert [r["file"] for r in rejected] == ["99_te_groot.3mf"]
    assert len(manifests) == 1
    m = json.loads((tmp_path / "out" / "plate_01.json").read_text())
    assert len(m["parts"]) == 30 and len(m["fiducials"]) == 3

    scene = trimesh.load(tmp_path / "out" / "plate_01.3mf")
    placed = {}
    for node in scene.graph.nodes_geometry:
        transform, geom = scene.graph[node]
        placed[node] = scene.geometry[geom].copy().apply_transform(transform).bounds
    for p in m["parts"]:
        lo, hi = placed[p["id"]]
        assert (lo[:2] + hi[:2]) / 2 == pytest.approx([p["x"], p["y"]], abs=1e-3)
        assert lo[2] == pytest.approx(0, abs=1e-6)
    for f in m["fiducials"]:
        lo, hi = placed[f["name"]]
        assert hi[2] == pytest.approx(f["z"])


def test_many_parts_fill_several_plates(tmp_path):
    src = tmp_path / "set"
    src.mkdir()
    for i in range(60):
        trimesh.creation.box((3, 3, 2)).export(src / f"{i:03d}.3mf")
    grid = make_grid()
    manifests, _ = build_plates(src, tmp_path / "out", grid)
    assert [len(m["parts"]) for m in manifests] == [grid.capacity, 60 - grid.capacity]


def test_profile_missing_and_old_slicer(tmp_path):
    with pytest.raises(SlicerError, match="Export Config"):
        read_profile(tmp_path / "nope.ini")
    ini = tmp_path / "p.ini"
    ini.write_text("# generated\nprinter_model = COREONE\nbinary_gcode = 1\n")
    profile = read_profile(ini)
    assert profile["binary_gcode"] == "1"
    with pytest.raises(SlicerError, match="Core One"):
        check_compatible((2, 6, 1), profile)
    check_compatible((2, 9, 2), profile)
