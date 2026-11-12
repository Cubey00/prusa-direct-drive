"""Execute button end to end in Simulate mode, with a generated model (no model.3mf needed)."""
import trimesh
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QMessageBox

from pathprogrammer.calibration import CalPoints
from pathprogrammer.ui.main_window import MainWindow


def test_execute_simulated_run_creates_run_folder(qtbot, tmp_path, monkeypatch):
    model = tmp_path / "box.3mf"
    trimesh.creation.box(extents=(40, 30, 5)).export(model)
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Yes))
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: None))
    w = MainWindow(QSettings(str(tmp_path / "s.ini"), QSettings.IniFormat))
    qtbot.addWidget(w)
    w.settings.setValue("runs_folder", str(tmp_path / "runs"))
    assert w.open_model(model)
    c = CalPoints()
    c.face = {"back_left": (0, 0), "front_right": (40, 30)}
    c.machine = {"back_left": (100, 150), "front_right": (140, 120)}
    w.left.set_cal_points(c)
    w.left.run_name.setText("my run")
    w.add_point_at(10, 10)
    w.run.simulate.setChecked(True)
    w.execute()
    assert w.thread is not None, "Execute did not start a run"
    qtbot.waitUntil(lambda: w.thread is None, timeout=30000)
    run = tmp_path / "runs" / "my run"
    assert w.run.status.text().startswith("completed")
    for f in ("run_log.json", "images.json", "heightmap.csv", "heightmap.png", "images/point_01_01.tiff"):
        assert (run / f).exists(), f
