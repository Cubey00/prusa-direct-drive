"""The existing camera API uses SCC on macOS and retains IDS peak elsewhere."""
import importlib.util
from pathlib import Path
import runpy
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pytest


ROOT = Path(__file__).resolve().parents[1]
needs_camera_package = pytest.mark.skipif(
    sys.platform != "darwin" or importlib.util.find_spec("scientific_camera") is None,
    reason="SCC camera compatibility runs on macOS with requirements-macos.txt")


def test_macos_route_does_not_import_vendor_sdk(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setitem(sys.modules, "ids_peak", None)
    monkeypatch.setitem(sys.modules, "ids_peak_ipl", None)
    from macos_camera import IDS_Camera
    namespace = runpy.run_path(str(ROOT / "ids_camera.py"))
    assert namespace["IDS_Camera"] is IDS_Camera


def test_missing_mac_camera_package_has_install_guidance(monkeypatch):
    from macos_camera import IDS_Camera
    monkeypatch.setitem(sys.modules, "scientific_camera", None)
    with pytest.raises(RuntimeError, match="requirements-macos.txt"):
        IDS_Camera(backend="simulated")


@pytest.mark.parametrize("platform", ["win32", "linux"])
def test_other_platforms_retain_vendor_camera_contract(platform, monkeypatch):
    peak = Mock()
    nodes, stream, info, device = Mock(), Mock(), Mock(), Mock()
    nodes.FindNode.return_value.Value.return_value = 16
    stream.NumBuffersAnnouncedMinRequired.return_value = 2
    stream.AnnouncedBuffers.return_value = []
    device.RemoteDevice.return_value.NodeMaps.return_value = [nodes]
    device.DataStreams.return_value = [SimpleNamespace(OpenDataStream=lambda: stream)]
    info.IsOpenable.return_value = True
    info.OpenDevice.return_value = device
    devices = Mock()
    devices.empty.return_value = False
    devices.size.return_value = 1
    devices.__getitem__ = Mock(return_value=info)
    peak.DeviceManager.Instance.return_value.Devices.return_value = devices
    expected = np.arange(18, dtype=np.uint8).reshape(2, 3, 3)
    conversion = Mock()
    conversion.BufferToImage.return_value.ConvertTo.return_value.get_numpy_3D.return_value = expected
    monkeypatch.setitem(sys.modules, "ids_peak", SimpleNamespace(
        ids_peak=peak, ids_peak_ipl_extension=conversion))
    monkeypatch.setitem(sys.modules, "ids_peak_ipl", SimpleNamespace(ids_peak_ipl=Mock()))
    with monkeypatch.context() as routing:
        routing.setattr(sys, "platform", platform)
        namespace = runpy.run_path(str(ROOT / "ids_camera.py"))
    camera = namespace["IDS_Camera"]()
    try:
        peak.Library.Initialize.assert_called_once_with()
        actual = camera.snap()
        np.testing.assert_array_equal(actual, expected)
        assert not np.shares_memory(actual, expected)
        camera.set_exposure(50e3)
        nodes.FindNode.assert_called_with("ExposureTime")
        nodes.FindNode.return_value.SetValue.assert_called_with(50e3)
        camera.set_gain(2.0)
        nodes.FindNode.assert_called_with("Gain")
        nodes.FindNode.return_value.SetValue.assert_called_with(2.0)
        stream.WaitForFinishedBuffer.side_effect = TimeoutError
        camera.flush()
    finally:
        camera.close()
    peak.Library.Close.assert_called_once_with()
    camera.close()
    peak.Library.Close.assert_called_once_with()
    with pytest.raises(RuntimeError, match="closed"):
        camera.snap()


@needs_camera_package
def test_macos_camera_public_methods_and_owned_rgb_frames(monkeypatch):
    from ids_camera import IDS_Camera
    monkeypatch.setenv("PRUSA_CAMERA_BACKEND", "simulated")
    camera = IDS_Camera()
    try:
        camera.set_exposure(50e3)
        camera.set_gain(2)
        assert camera._camera.configuration.exposure_us == 50e3
        assert camera._camera.configuration.gain == 2
        camera.flush()
        first = camera.snap()
        original = first.copy()
        assert first.shape == (48, 64, 3) and first.dtype == np.uint8
        assert first.flags.owndata
        second = camera.snap()
        np.testing.assert_array_equal(first, original)
        assert not np.shares_memory(first, second)
        with pytest.raises(Exception):
            camera.set_exposure(0)
        assert camera.snap().shape == first.shape
    finally:
        camera.close()
    camera.close()
    for method in (camera.snap, camera.flush, lambda: camera.set_gain(1),
                   lambda: camera.set_exposure(1000)):
        with pytest.raises(RuntimeError, match="closed"):
            method()


@needs_camera_package
@pytest.mark.parametrize("pixel_format", ["Mono8", "Mono16", "BayerRG8", "BayerRG10p", "BayerRG12p"])
def test_verified_formats_keep_legacy_rgb8_array_shape(pixel_format):
    from macos_camera import IDS_Camera
    camera = IDS_Camera(backend="simulated")
    try:
        camera._configure(width=8, height=4, pixel_format=pixel_format)
        image = camera.snap()
        assert image.shape == (4, 8, 3) and image.dtype == np.uint8
        assert image.flags.owndata
    finally:
        camera.close()


@needs_camera_package
def test_bayer_rgb_conversion_uses_camera_library(monkeypatch):
    from macos_camera import IDS_Camera
    from scientific_camera import Frame, FrameMetadata
    mosaic = np.zeros((4, 4), dtype=np.uint8)
    mosaic[::2, ::2] = 255
    frame = Frame(FrameMetadata(camera_id="test", acquisition_index=0, device_frame_id=0,
                                status="complete", host_received_monotonic_ns=1,
                                width=4, height=4, stride=4, payload_bytes=16,
                                pixel_format="BayerRG8"), mosaic.tobytes())
    camera = IDS_Camera(backend="simulated")
    try:
        monkeypatch.setattr(camera._stream, "next_frame", lambda **kwargs: frame)
        result = camera.snap()
        assert np.all(result[..., 0] == 255)
        assert not np.any(result[..., 1:])
    finally:
        camera.close()


@needs_camera_package
def test_explicit_selection_and_bad_indices(monkeypatch):
    from macos_camera import IDS_Camera
    monkeypatch.setenv("PRUSA_CAMERA_BACKEND", "simulated")
    for index in (-1, 1):
        with pytest.raises(RuntimeError, match="does not exist"):
            IDS_Camera(index)
    monkeypatch.setenv("PRUSA_CAMERA_ID", "simulated")
    camera = IDS_Camera()
    camera.close()
    with pytest.raises(ValueError, match="aravis or simulated"):
        IDS_Camera(backend="unknown")


@needs_camera_package
@pytest.mark.parametrize("formats, expected", [(("BayerRG8",), "BayerRG8"),
                                              (("BayerRG10g40IDS",), None)])
def test_unverified_native_format_is_not_guessed(formats, expected, monkeypatch):
    import scientific_camera
    from macos_camera import IDS_Camera
    session = Mock()
    session.configuration = SimpleNamespace(pixel_format="BayerRG10g40IDS")
    session.capabilities.return_value.pixel_formats = formats
    manager = Mock()
    manager.discover.return_value = [SimpleNamespace(id="native")]
    manager.diagnostics = {}
    manager.open.return_value = session
    monkeypatch.setattr(scientific_camera, "CameraManager", lambda **kwargs: manager)
    if expected is None:
        with pytest.raises(RuntimeError, match="No verified RGB converter"):
            IDS_Camera(backend="simulated")
        session.close.assert_called_once_with()
        session.start_stream.assert_not_called()
    else:
        camera = IDS_Camera(backend="simulated")
        try:
            config, = session.configure.call_args.args
            assert config.pixel_format == expected
        finally:
            camera.close()


@needs_camera_package
def test_missing_native_backend_does_not_fall_back_to_simulator(monkeypatch):
    from macos_camera import IDS_Camera
    monkeypatch.setitem(sys.modules, "gi", None)
    with pytest.raises(RuntimeError, match="backend unavailable"):
        IDS_Camera(backend="aravis")


@needs_camera_package
def test_camera_ownership_is_released_after_close():
    from macos_camera import IDS_Camera
    camera = IDS_Camera(backend="simulated")
    try:
        with pytest.raises(Exception, match="simulated"):
            IDS_Camera(backend="simulated")
    finally:
        camera.close()
    reopened = IDS_Camera(backend="simulated")
    reopened.close()


@needs_camera_package
def test_imaging_and_benchmark_use_portable_mac_output(monkeypatch, tmp_path):
    import time
    import tifffile
    monkeypatch.setenv("PRUSA_CAMERA_BACKEND", "simulated")
    monkeypatch.setenv("PRUSA_PHOTO_DIR", str(tmp_path / "photos"))
    monkeypatch.setenv("MPLCONFIGDIR", str(tmp_path / "matplotlib"))
    imaging = runpy.run_path(str(ROOT / "imaging.py"))
    try:
        assert imaging["PHOTO_FOLDER"] == tmp_path / "photos"
        assert imaging["single_image"]().shape == (48, 64, 3)
        imaging["image_and_annotate"]("macos")
        deadline = time.monotonic() + 5
        while not list((tmp_path / "photos").glob("*.tiff")) and time.monotonic() < deadline:
            time.sleep(0.01)
        path, = (tmp_path / "photos").glob("*.tiff")
        # Wait until the asynchronous writer has finished, not merely created the file.
        while True:
            try:
                image = tifffile.imread(path)
                break
            except (ValueError, OSError):
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.01)
        assert image.shape == (48, 64, 3) and image.dtype == np.uint8
    finally:
        imaging["camera"].close()
        imaging["save_queue"].put(None)
    benchmark = runpy.run_path(str(ROOT / "camera speedtester.py"))
    try:
        assert benchmark["PHOTO_FOLDER"] == tmp_path / "photos"
        image, timings = benchmark["snap_image_timed"]()
        assert image.shape == (48, 64, 3) and timings["snap_image_ms"] >= 0
    finally:
        benchmark["camera"].close()
        benchmark["save_queue"].put(None)
