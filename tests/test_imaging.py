import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import imaging


def test_legacy_camera_opens_on_first_capture_only(monkeypatch):
    camera = Mock()
    camera.snap.return_value = [[1, 2]]
    factory = Mock(return_value=camera)
    monkeypatch.setitem(sys.modules, "ids_camera", SimpleNamespace(IDS_Camera=factory))
    monkeypatch.setattr(imaging, "camera", None)
    assert imaging.single_image().tolist() == [[1, 2]]
    imaging.single_image()
    factory.assert_called_once_with()
    camera.set_exposure.assert_called_once_with(50e3)
    assert camera.flush.call_count == 2


def test_failed_legacy_setup_closes_camera_and_allows_retry(monkeypatch):
    camera = Mock()
    camera.set_exposure.side_effect = RuntimeError("setup failed")
    monkeypatch.setitem(sys.modules, "ids_camera", SimpleNamespace(IDS_Camera=lambda: camera))
    monkeypatch.setattr(imaging, "camera", None)
    with pytest.raises(RuntimeError, match="setup failed"):
        imaging.single_image()
    camera.close.assert_called_once_with()
    assert imaging.camera is None
    camera.set_exposure.side_effect = None
    assert imaging.get_camera() is camera
