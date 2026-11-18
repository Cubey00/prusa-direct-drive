import importlib.util
from pathlib import Path
import runpy
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pytest

from focusfield.acquisition import Capture, capture_metadata


def test_capture_contract_rejects_missing_or_mislabelled_samples():
    capture = Capture(np.ones((4, 4), dtype=np.uint16)*4095,
                      capture_metadata("Mono12", "123", 1000, 1))
    assert capture.validate() is capture
    capture.pixels[0, 0] = 4096
    with pytest.raises(ValueError, match="bit depth"):
        capture.validate()
    capture.pixels = np.ones((4, 4, 3), dtype=np.uint16)
    with pytest.raises(ValueError, match="two-dimensional"):
        capture.validate()


@pytest.mark.parametrize("pixel_format,dtype,bits", [("Mono8", np.uint8, 8), ("Mono10", np.uint16, 10),
    ("Mono12", np.uint16, 12), ("Mono16", np.uint16, 16), ("BayerRG12", np.uint16, 12),
    ("BayerRG10", np.uint16, 10), ("BayerRG8", np.uint8, 8)])
def test_ids_native_samples_and_format_selected_before_buffer_allocation(monkeypatch, pixel_format, dtype, bits):
    nodes = {}
    sequence = []
    for key, value in (("PayloadSize", 16), ("PixelFormat", pixel_format), ("DeviceSerialNumber", "CAM123"),
                       ("ExposureTime", 1000), ("Gain", 1), ("Width", 4), ("Height", 2),
                       ("OffsetX", 0), ("OffsetY", 0), ("BinningHorizontal", 1), ("BinningVertical", 1)):
        node = Mock()
        node.Value.return_value = value
        node.CurrentEntry.return_value.SymbolicValue.return_value = value
        nodes[key] = node
    nodes["PixelFormat"].SetCurrentEntry.side_effect = lambda value: sequence.append("format")
    nodemap = Mock()
    nodemap.FindNode.side_effect = lambda name: nodes.setdefault(name, Mock())
    stream, device, info = Mock(), Mock(), Mock()
    stream.NumBuffersAnnouncedMinRequired.return_value = 2
    stream.AnnouncedBuffers.return_value = []
    stream.AllocAndAnnounceBuffer.side_effect = lambda size: sequence.append("buffer") or Mock()
    device.RemoteDevice.return_value.NodeMaps.return_value = [nodemap]
    device.DataStreams.return_value = [SimpleNamespace(OpenDataStream=lambda: stream)]
    info.IsOpenable.return_value = True
    info.OpenDevice.return_value = device
    devices = Mock()
    devices.empty.return_value = False
    devices.size.return_value = 1
    devices.__getitem__ = Mock(return_value=info)
    peak, conversion = Mock(), Mock()
    peak.DeviceManager.Instance.return_value.Devices.return_value = devices
    nodes['PixelFormat'].Entries.return_value = []
    expected = np.array([0, 1, 2, 3, 4, 5, 2**(bits-1), 2**bits-1], dtype=dtype).reshape(2, 4)
    conversion.BufferToImage.return_value.get_numpy_2D.return_value = expected
    conversion.BufferToImage.return_value.get_numpy_2D_16.return_value = expected
    buffer = Mock()
    buffer.IsIncomplete.return_value = False
    buffer.FrameID.return_value = 17
    stream.WaitForFinishedBuffer.return_value = buffer
    monkeypatch.setitem(sys.modules, "ids_peak", SimpleNamespace(ids_peak=peak, ids_peak_ipl_extension=conversion))
    monkeypatch.setitem(sys.modules, "ids_peak_ipl", SimpleNamespace(ids_peak_ipl=Mock()))
    monkeypatch.setattr(sys, "platform", "linux")
    camera_class = runpy.run_path(str(Path(__file__).resolve().parents[1] / "ids_camera.py"))["IDS_Camera"]
    camera = camera_class(pixel_format=pixel_format)
    try:
        assert sequence == ["format", "buffer", "buffer"]
        capture = camera.capture_raw().validate()
        np.testing.assert_array_equal(capture.pixels, expected)
        assert not np.shares_memory(capture.pixels, expected)
        assert capture.metadata["bit_depth"] == bits
        assert capture.metadata["camera_serial"] == "CAM123"
        assert capture.metadata["device_frame_id"] == 17
        conversion.BufferToImage.return_value.ConvertTo.assert_not_called()
        if bits == 8:
            conversion.BufferToImage.return_value.get_numpy_2D_16.assert_not_called()
        else:
            conversion.BufferToImage.return_value.get_numpy_2D.assert_not_called()
        stream.QueueBuffer.assert_called_with(buffer)
        camera.restart_acquisition()
        nodes['AcquisitionStop'].WaitUntilDone.assert_called_once_with()
        nodes['AcquisitionStart'].WaitUntilDone.assert_called_once_with()
        stream.Flush.assert_called_once_with(peak.DataStreamFlushMode_DiscardAll)
        buffer.IsIncomplete.return_value = True
        with pytest.raises(RuntimeError, match="Incomplete"):
            camera.capture_raw()
        stream.QueueBuffer.assert_called_with(buffer)
    finally:
        camera.close()


@pytest.mark.skipif(importlib.util.find_spec("scientific_camera") is None, reason="optional SCC backend not installed")
@pytest.mark.parametrize("pixel_format,bits", [("Mono16", 16), ("BayerRG8", 8), ("BayerRG10p", 10), ("BayerRG12p", 12)])
def test_scc_raw_decoder_keeps_native_bit_depth_and_owned_samples(pixel_format, bits):
    from macos_camera import IDS_Camera
    camera = IDS_Camera(backend="simulated", pixel_format=pixel_format)
    try:
        camera.set_exposure(1000)
        camera.set_gain(1)
        camera.restart_acquisition()
        capture = camera.capture_raw().validate()
        assert capture.metadata["bit_depth"] == bits
        assert capture.metadata["pixel_format"] == pixel_format
        assert capture.pixels.ndim == 2 and capture.pixels.flags.owndata
        assert capture.pixels.dtype == (np.uint8 if bits == 8 else np.uint16)
        assert camera.snap().dtype == np.uint8
    finally:
        camera.close()


@pytest.mark.skipif(importlib.util.find_spec('scientific_camera') is None, reason='optional SCC backend not installed')
@pytest.mark.parametrize('fmt,bits', [('Mono8', 8), ('Mono16', 16), ('BayerRG8', 8),
                                     ('BayerRG10p', 10), ('BayerRG12p', 12)])
def test_scc_standard_formats_decode_known_native_pixel_values(monkeypatch, fmt, bits):
    from scientific_camera import Frame, FrameMetadata
    from macos_camera import IDS_Camera
    expected = np.array([[0, 1, 2**bits-1, 2**(bits-1)], [3, 4, 5, 6]],
                        dtype=np.uint8 if bits == 8 else np.uint16)
    if fmt.endswith('p'):
        # PFNC standard little-endian contiguous packing, not IDS vendor grouping.
        payload = b''.join(sum(int(v) << (bits*i) for i, v in enumerate(row)).to_bytes(4*bits//8, 'little')
                           for row in expected)
    else:
        payload = expected.astype('<u2' if bits == 16 else 'u1').tobytes()
    frame = Frame(FrameMetadata(camera_id='test', acquisition_index=1, device_frame_id=42,
        status='complete', host_received_monotonic_ns=1, width=4, height=2, stride=len(payload)//2,
        payload_bytes=len(payload), pixel_format=fmt), payload)
    camera = IDS_Camera(backend='simulated', pixel_format=fmt)
    try:
        monkeypatch.setattr(camera._stream, 'next_frame', lambda **kwargs: frame)
        capture = camera.capture_raw()
        np.testing.assert_array_equal(capture.pixels, expected)
        assert capture.metadata['width_px'] == 4 and capture.metadata['height_px'] == 2
        assert capture.metadata['cfa_layout'] == ('RGGB' if fmt.startswith('Bayer') else 'none')
        assert capture.metadata['device_frame_id'] == 42
    finally:
        camera.close()
