import subprocess
import sys

import pytest

from device_session import DeviceBusy, DeviceLease


def test_ownership_excludes_threads_and_processes_and_releases_on_error():
    with pytest.raises(RuntimeError, match="simulated error"):
        with DeviceLease("printer"):
            with pytest.raises(DeviceBusy):
                DeviceLease("printer").acquire()
            result = subprocess.run([sys.executable, "-c", "from device_session import DeviceLease; DeviceLease('printer').acquire()"],
                                    text=True, capture_output=True)
            assert result.returncode != 0 and "owned by another" in result.stderr
            raise RuntimeError("simulated error")
    with DeviceLease("printer"):
        pass


def test_camera_and_printer_ownership_are_independent():
    with DeviceLease("camera"), DeviceLease("printer"):
        with pytest.raises(DeviceBusy):
            DeviceLease("camera").acquire()
