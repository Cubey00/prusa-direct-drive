from functools import partial

import pytest
import trimesh
from PySide6.QtCore import QPointF, QSettings

from pathprogrammer.ui import plates_panel
from pathprogrammer.ui.plates_panel import PlatesPanel
from slicing import pipeline


@pytest.fixture
def panel(qtbot, tmp_path, monkeypatch):
    monkeypatch.setattr(plates_panel, "TESTSET_DIR", tmp_path / "testset")
    monkeypatch.setattr(plates_panel, "PLATES_DIR", tmp_path / "plates")
    monkeypatch.setattr(pipeline, "find_slicer", lambda: tmp_path / "PrusaSlicer")
    monkeypatch.setattr(pipeline, "slicer_version", lambda exe: (2, 9, 3))
    # no profile, so the run stops at "not sliced" instead of slicing for real
    monkeypatch.setattr(plates_panel.pipeline, "run",
                        partial(pipeline.run, profile=tmp_path / "missing.ini"))
    p = PlatesPanel(QSettings(str(tmp_path / "s.ini"), QSettings.IniFormat))
    qtbot.addWidget(p)
    p.resize(450, 600)
    p.show()
    return p


def test_import_testset_builds_one_plate_and_tries_to_slice(panel, qtbot, tmp_path):
    assert panel.import_testset()
    qtbot.waitUntil(lambda: panel.result is not None, timeout=30000)
    assert len(list((tmp_path / "testset").glob("*.3mf"))) == 30
    assert (tmp_path / "plates" / "testset" / "plate_01.3mf").exists()
    assert panel.plate_list.count() == 1
    assert len(panel.preview.manifest["parts"]) == 30
    assert "Not sliced" in panel.status.text() and "Export Config" in panel.status.text()
    assert panel.dataset_btn.isEnabled() and panel.open_btn.isEnabled()


def test_preview_tooltip_finds_parts(panel, qtbot):
    panel.import_testset()
    qtbot.waitUntil(lambda: panel.result is not None, timeout=30000)
    pv = panel.preview
    part = pv.manifest["parts"][0]
    assert pv.item_at(pv._to_px(part["x"], part["y"])).startswith(part["id"])
    f = pv.manifest["fiducials"][0]
    assert pv.item_at(pv._to_px(f["x"], f["y"])).startswith("F1")
    assert pv.item_at(QPointF(1, 1)) is None


def test_import_empty_folder_reports_failure(panel, qtbot, tmp_path, monkeypatch):
    shown = []
    monkeypatch.setattr(plates_panel.QMessageBox, "warning", lambda *a: shown.append(a[2]))
    (tmp_path / "empty").mkdir()
    panel.import_dataset(tmp_path / "empty")
    qtbot.waitUntil(lambda: bool(shown), timeout=10000)
    assert "No .3mf or .stl files" in shown[0]
    assert panel.testset_btn.isEnabled() and not panel.is_busy()


def test_pipeline_rerun_removes_stale_plates(tmp_path):
    src = tmp_path / "set"
    src.mkdir()
    for i in range(45):
        trimesh.creation.box((3, 3, 2)).export(src / f"{i:03d}.3mf")
    out = tmp_path / "out"
    first = pipeline.run(src, out, do_slice=False, on_status=lambda m: None)
    assert len(first["plates"]) == 2
    for f in list(src.glob("*.3mf"))[10:]:
        f.unlink()
    second = pipeline.run(src, out, do_slice=False, on_status=lambda m: None)
    assert second["plates"] == ["plate_01.3mf"]
    assert not (out / "plate_02.3mf").exists() and not (out / "plate_02.json").exists()


def test_show_in_3d_needs_gcode_and_opens_viewer(panel, qtbot, tmp_path, monkeypatch):
    panel.import_testset()
    qtbot.waitUntil(lambda: panel.result is not None, timeout=30000)
    assert not panel.view3d_btn.isEnabled()          # not sliced (no profile in this test)
    opened = []
    monkeypatch.setattr(plates_panel, "open_in_viewer", opened.append)
    gcode = tmp_path / "plates" / "testset" / "plate_01.bgcode"
    gcode.write_bytes(b"")
    panel.result["manifests"][0]["gcode"] = gcode.name
    panel._show_plate(0)
    assert panel.view3d_btn.isEnabled()
    panel.view3d_btn.click()
    assert opened == [gcode]


def test_viewer_command_uses_prusa_gcodeviewer(tmp_path):
    from slicing.slicer import SlicerError, viewer_command
    try:
        cmd = viewer_command(tmp_path / "x.bgcode")
    except SlicerError:
        pytest.skip("PrusaSlicer not installed")
    assert cmd[-1].endswith("x.bgcode") and "prusa" in cmd[0].lower()


def test_open_plate_fills_fiducials_and_calibrates(qtbot, tmp_path):
    from pathprogrammer.ui.main_window import MainWindow
    from slicing.testset import write_testset
    write_testset(tmp_path / "set")
    pipeline.run(tmp_path / "set", tmp_path / "out", do_slice=False, on_status=lambda m: None)
    w = MainWindow(QSettings(str(tmp_path / "s.ini"), QSettings.IniFormat))
    qtbot.addWidget(w)
    w.plates.openPlateRequested.emit(str(tmp_path / "out" / "plate_01.3mf"))
    cal = w.left.cal_points()
    assert all(cal.face[p] is not None for p in ("F1", "F2", "F3"))
    assert w.transform is None                       # machine points still missing
    manifest = __import__("json").loads((tmp_path / "out" / "plate_01.json").read_text())
    shift = complex(3.0, -2.0)                       # plate sits 3 mm right, 2 mm to the front
    for f in manifest["fiducials"]:
        w.left.set_machine_point(f["name"], f["x"] + shift.real, f["y"] + shift.imag)
    assert w.transform is not None
    part = manifest["parts"][0]
    lo, hi = w.model.mesh.bounds
    fx, fy = part["x"] - lo[0], hi[1] - part["y"]    # the part's centre on the face
    assert w.transform.face_to_machine(fx, fy) == pytest.approx((part["x"] + 3, part["y"] - 2), abs=1e-6)
    w.server.close()
