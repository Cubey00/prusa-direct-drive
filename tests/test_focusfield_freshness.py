from types import SimpleNamespace

import numpy as np
import pytest

from focusfield.acquisition import CameraAdapter, Capture, FrameGuard, capture_metadata
from focusfield.config import Recipe
from focusfield.data import storage_preflight


def capture(frame_id=1):
    return Capture(np.full((32, 32), 100, np.uint8), capture_metadata('Mono8', 'CAM', 10000, 1,
                   device_frame_id=frame_id))


def test_coarse_host_clock_accepts_ordered_fresh_frames(monkeypatch):
    monkeypatch.setattr('focusfield.acquisition.time.monotonic_ns', lambda: 1000)
    guard = FrameGuard()
    guard.check(capture(10), after=1000)
    guard.check(capture(11), after=1000)
    with pytest.raises(ValueError, match='Stale'):
        guard.check(capture(12), after=1001)


@pytest.mark.parametrize('second', [10, 9, 0])
def test_duplicate_reordered_and_reset_device_ids_are_rejected(second):
    guard = FrameGuard()
    guard.check(capture(10))
    with pytest.raises(ValueError, match='device frame ID'):
        guard.check(capture(second))


def test_gaps_are_recorded_without_inventing_frames():
    guard = FrameGuard()
    guard.check(capture(10))
    sample = capture(13)
    guard.check(sample)
    assert sample.metadata['device_frame_gap'] == 2


def test_reused_capture_sequence_is_rejected_even_with_new_device_id():
    a, b = capture(10), capture(11)
    b.metadata['capture_sequence'] = a.metadata['capture_sequence']
    guard = FrameGuard()
    guard.check(a)
    with pytest.raises(ValueError, match='capture sequence'):
        guard.check(b)


def adapter(ids, restart=lambda: None):
    samples = iter(ids)
    camera = CameraAdapter(Recipe(pixel_format='Mono8', discard_frames=2))
    camera.camera = SimpleNamespace(capture_raw=lambda: capture(next(samples)), restart_acquisition=restart)
    return camera


def test_queued_old_frame_after_restart_fails_before_acceptance():
    camera = adapter([10, 10, 11, 12])
    camera.capture()
    with pytest.raises(ValueError, match='queued'):
        camera.fresh()
    assert camera.freshness is None


def test_restart_discards_are_checked_and_audited():
    camera = adapter([10, 11, 12, 15])
    controller_guard = FrameGuard()
    controller_guard.check(camera.capture())
    camera.fresh()
    sample = camera.capture()
    controller_guard.check(sample)
    assert sample.metadata['device_frame_id'] == 15
    assert sample.metadata['device_frame_gap'] == 2
    assert sample.metadata['freshness']['device_frame_fence'] == 12
    assert not sample.metadata['freshness']['exposure_timing_proven']


def test_restart_failure_is_not_treated_as_fresh():
    def failed():
        raise RuntimeError('restart failed')
    camera = adapter([10, 11], failed)
    camera.capture()
    with pytest.raises(RuntimeError, match='restart failed'):
        camera.fresh()
    assert camera.freshness is None


def test_capture_cancel_prevents_read_and_cleanup_race():
    camera = adapter([])
    camera.cancel.set()
    with pytest.raises(InterruptedError):
        camera.capture()


def test_storage_estimate_includes_native_pixels_tiff_overhead_and_reserve(tmp_path, monkeypatch):
    monkeypatch.setattr('focusfield.data.shutil.disk_usage', lambda _: SimpleNamespace(free=10**12))
    estimate = storage_preflight(tmp_path, 126, 4056*3040*2)
    assert estimate['required_bytes'] > 126*4056*3040*2
    monkeypatch.setattr('focusfield.data.shutil.disk_usage', lambda _: SimpleNamespace(free=1))
    with pytest.raises(OSError, match='Free space or choose another log folder'):
        storage_preflight(tmp_path / 'new', 126, 4056*3040*2)


def test_camera_evidence_retains_partial_frames_after_restart_failure(tmp_path, monkeypatch):
    from focusfield.validation import camera_check
    import json
    closed = []
    def failed():
        raise ValueError('counter reset after restart')
    camera = SimpleNamespace(open=lambda: None, capture=lambda: capture(10), fresh=failed,
        close=lambda: closed.append(True), camera=SimpleNamespace(diagnostics=lambda: {'hardware_validated': False}))
    monkeypatch.setattr('focusfield.validation.CameraAdapter', lambda recipe: camera)
    with pytest.raises(ValueError, match='counter reset'):
        camera_check(tmp_path / 'evidence', 'Mono8')
    evidence = json.loads((tmp_path / 'evidence/camera_evidence.json').read_text())
    assert len(evidence['captures']) == 1 and closed == [True]
    assert not evidence['hardware_validated'] and not evidence['printer_motion']
    assert (tmp_path / 'evidence/raw_000.tiff').is_file()
