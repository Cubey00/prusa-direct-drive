"""Existing app windows can launch on macOS without opening devices."""
import os
from pathlib import Path
import subprocess
import sys
import textwrap

import pytest

ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="macOS startup checks")


def run_isolated(tmp_path, script):
    bootstrap = """
import importlib.abc
from unittest.mock import patch
import sys

class NoCameraSDK(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'ids_peak', 'ids_peak_ipl', 'scientific_camera', 'gi'}:
            raise AssertionError('App startup imported a camera SDK: ' + fullname)

sys.meta_path.insert(0, NoCameraSDK())
"""
    result = subprocess.run([sys.executable, "-c", textwrap.dedent(bootstrap) + textwrap.dedent(script)],
                            cwd=tmp_path, env=dict(os.environ, PYTHONPATH=str(ROOT),
                            QT_QPA_PLATFORM="offscreen"), capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr


def test_existing_path_programmer_window(tmp_path):
    run_isolated(tmp_path, """
        from PySide6.QtCore import QSettings
        from PySide6.QtWidgets import QApplication
        from pathlib import Path
        import trimesh
        import gantry_controls as gc
        import serial
        from strain_gauge import StrainGauge
        from pathprogrammer.calibration import CalPoints
        from pathprogrammer.executor import run_program
        from pathprogrammer.hardware import SimHardware
        from pathprogrammer import program as pm
        from pathprogrammer.ui.main_window import MainWindow
        with patch.object(gc, 'connect') as connect, patch.object(serial, 'Serial') as serial_open, \
             patch.object(StrainGauge, 'start') as gauge_start:
            app = QApplication([])
            window = MainWindow(QSettings('settings.ini', QSettings.IniFormat))
            window.show()
            app.processEvents()
            assert window.isVisible()
            model = Path('box.3mf')
            model.write_bytes(trimesh.creation.box(extents=[60, 20, 10]).export(file_type='3mf'))
            assert window.open_model(model)
            calibration = CalPoints()
            calibration.face = {'F1': (0, 0), 'F2': (60, 0), 'F3': (0, 20)}
            calibration.machine = {'F1': (100, 150), 'F2': (160, 150), 'F3': (100, 130)}
            window.left.set_cal_points(calibration)
            window.add_point_at(10, 5)
            program, errors = window.preflight()
            assert not errors, errors
            pm.save(program, 'program.json')
            window.load_program('program.json', ask=False)
            assert window.points.rowCount() == 1
            summary = run_program(program, window.transform, SimHardware(time_scale=0),
                                  log_dir=Path('logs'))
            assert summary.outcome == 'completed' and summary.done == 1
            assert window.close()
            connect.assert_not_called()
            serial_open.assert_not_called()
            gauge_start.assert_not_called()
    """)


def test_existing_manual_window(tmp_path):
    run_isolated(tmp_path, """
        import tkinter as tk
        import GUI
        with patch.object(GUI, 'enable_metrics') as metrics, \
             patch.object(GUI.StrainGauge, 'start') as gauge_start:
            root = tk.Tk()
            window = GUI.App(root)
            root.update()
            assert root.winfo_exists()
            metrics.assert_called_once_with()
            gauge_start.assert_called_once_with()
            root.destroy()
    """)
