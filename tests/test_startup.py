"""Startup checks run in fresh processes, without native camera packages or hardware."""
import os
from pathlib import Path
import subprocess
import sys
import textwrap

import pytest


PROJECT_DIR = Path(__file__).resolve().parents[1]
BOOTSTRAP = """
import importlib.abc
from pathlib import Path
import sys
from unittest.mock import patch

blocked = {"ids_camera", "ids_peak", "ids_peak_ipl", "ids_peak_afl", "gi",
           "scientific_camera", "tifffile"}
attempted = []

class NoCameraImports(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split(".")[0] in blocked:
            attempted.append(fullname)
            raise ModuleNotFoundError(f"Forbidden camera import: {fullname}", name=fullname)

sys.meta_path.insert(0, NoCameraImports())

import serial
import gantry_controls as gc
from strain_gauge import StrainGauge
"""


def run_isolated(tmp_path, script):
    env = dict(os.environ, PYTHONPATH=str(PROJECT_DIR), QT_QPA_PLATFORM="offscreen",
               MPLCONFIGDIR=str(tmp_path / "matplotlib"))
    result = subprocess.run([sys.executable, "-c", BOOTSTRAP + textwrap.dedent(script)],
                            cwd=tmp_path, env=env, capture_output=True, text=True, timeout=45)
    assert result.returncode == 0, result.stdout + result.stderr


def test_camera_free_imports_and_path_programmer_window(tmp_path):
    run_isolated(tmp_path, """
        with patch.object(serial, "Serial") as serial_open, \
             patch.object(gc, "connect") as connect, \
             patch.object(StrainGauge, "start") as gauge_start:
            import GUI
            import imaging
            import pathprogrammer.__main__
            from pathprogrammer.camera import open_simulated_camera
            from pathprogrammer.ui.main_window import MainWindow
            from PySide6.QtCore import QSettings
            from PySide6.QtWidgets import QApplication

            assert imaging.camera is None
            assert imaging._saver_thread is None
            assert not Path("photos").exists()
            assert not attempted

            app = QApplication([])
            window = MainWindow(QSettings(str(Path("settings.ini").resolve()),
                                          QSettings.IniFormat))
            window.show()
            app.processEvents()
            assert window.isVisible()
            assert window.close()
            app.processEvents()
            serial_open.assert_not_called()
            connect.assert_not_called()
            gauge_start.assert_not_called()
            assert not attempted

            try:
                open_simulated_camera("simulated")
            except RuntimeError as exc:
                assert "requirements-camera.txt" in str(exc)
            else:
                raise AssertionError("Missing optional camera package must be diagnosed")
            try:
                imaging.single_image()
            except RuntimeError as exc:
                assert "IDS peak SDK" in str(exc)
            else:
                raise AssertionError("Missing legacy SDK must be diagnosed")
            assert imaging.camera is None
            assert not Path("photos").exists()
    """)


@pytest.mark.skipif(sys.platform != "darwin", reason="native Tk smoke check requires macOS")
def test_manual_gui_smoke_mode_does_not_connect_or_stop_printer(tmp_path):
    run_isolated(tmp_path, """
        with patch.object(serial, "Serial") as serial_open, \
             patch.object(gc, "connect") as connect, \
             patch.object(StrainGauge, "start") as gauge_start:
            import GUI
            with patch.object(GUI, "enable_metrics") as metrics, \
                 patch.object(GUI, "motionstop") as stop:
                GUI.main(["--smoke-test"])
                metrics.assert_not_called()
                stop.assert_not_called()
            serial_open.assert_not_called()
            connect.assert_not_called()
            gauge_start.assert_not_called()
            assert not attempted
    """)
