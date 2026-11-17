from dataclasses import replace
import json
import time
import numpy as np
import pytest

from focusfield.acquisition import Capture, capture_metadata
from focusfield.config import Recipe, simulation_fixture
from focusfield.controller import OperatorChecks, ScanController
from focusfield.hardware import SimStage, ZStage
from machine_settings import SETTINGS


class FakeCamera:
    simulated = True

    def __init__(self):
        self.closed = self.fresh_count = self.captures = 0
        self.fail_at = None

    def open(self):
        pass

    def fresh(self):
        self.fresh_count += 1

    def capture(self):
        self.captures += 1
        if self.captures == self.fail_at:
            raise RuntimeError("Camera disconnected")
        m = capture_metadata("Mono12", "SIMULATED", 10000, 1, device_frame_id=self.captures)
        return Capture(np.full((32, 32), 2000, dtype=np.uint16), m)

    def close(self):
        self.closed += 1


@pytest.fixture
def rig(tmp_path, verified_optical_fixture):
    f = verified_optical_fixture
    r = Recipe(wd_start_mm=19.8, wd_end_mm=20.2, step_mm=0.1, frames_per_position=2,
               repetitions=1, settle_s=0, pixel_format="Mono12")
    stage, camera = SimStage(f), FakeCamera()
    return ScanController(r, SETTINGS, f, stage, camera, tmp_path)


def test_schedule_readback_metadata_and_lossless_frames(rig):
    result = rig.run(analyze=False)
    assert result.state == "COMPLETED"
    manifest = json.loads((rig.writer.path / "manifest.json").read_text())
    assert len(manifest["frames"]) == 10
    frame = manifest["frames"][0]
    assert frame["printer_z_command_mm"] == pytest.approx(99.8)
    assert frame["nominal_working_distance_mm"] == pytest.approx(19.8)
    assert frame["bit_depth"] == 12 and frame["wd_calibration_id"] == "SIMULATED"
    assert rig.camera.closed == 1 and rig.camera.fresh_count == 5
    assert rig.stage.calls[-1] == ("close",)
    import tifffile
    array = tifffile.imread(rig.writer.path / frame["image"])
    assert array.dtype == np.uint16 and array.max() == 2000


def test_coarse_equal_timestamp_scan_preserves_order_for_offline_analysis(rig, monkeypatch):
    from focusfield.reports import validate_run
    monkeypatch.setattr('focusfield.acquisition.time.monotonic_ns', lambda: 1000)
    result = rig.run(analyze=False)
    assert result.state == 'COMPLETED', result.message
    assert {f['host_monotonic_ns'] for f in rig.writer.manifest['frames']} == {1000}
    validate_run(rig.writer.path, rig.writer.manifest, rig.recipe)


def test_insufficient_storage_blocks_motion(rig, monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr('focusfield.data.shutil.disk_usage', lambda _: SimpleNamespace(free=0))
    result = rig.run(analyze=False)
    assert result.state == 'FAILED' and 'Insufficient storage' in result.message
    assert not any(c[0] in {'open', 'move'} for c in rig.stage.calls)


@pytest.mark.parametrize('flag', ['motion_safety_verified', 'camera_pipeline_verified', 'frame_timing_verified'])
def test_physical_motion_needs_all_verified_calibrations(rig, flag):
    rig.stage.simulated = rig.camera.simulated = rig.simulated = False
    rig.fixture = replace(rig.fixture, **{flag: False})
    rig.checks = OperatorChecks(True, True, True, True, True)
    rig.printer_state = lambda: 'IDLE'
    result = rig.run(analyze=False)
    assert result.state == 'FAILED'
    assert ('open',) not in rig.stage.calls


def test_camera_disconnect_preserves_partial_data_and_stops_without_retreat(rig):
    rig.camera.fail_at = 4
    result = rig.run(analyze=False)
    assert result.state == "FAILED" and "disconnected" in result.message
    manifest = json.loads((rig.writer.path / "manifest.json").read_text())
    assert len(manifest["frames"]) == 2
    assert rig.stage.calls[-2:] == [("stop",), ("close",)]


def test_stop_before_open_and_during_scan(rig):
    rig.stop()
    assert rig.run(analyze=False).state == "ABORTED"
    assert ("open",) not in rig.stage.calls


def test_concurrent_stop_requests_dispatch_one_stop_and_prevent_motion(rig):
    import threading
    workers = [threading.Thread(target=rig.stop) for _ in range(16)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join()
    assert rig.run(analyze=False).state == 'ABORTED'
    assert rig.stage.calls.count(('stop',)) == 1
    assert ('open',) not in rig.stage.calls


def test_stop_in_frame_callback_prevents_next_move(rig):
    rig.on_event = lambda kind, *args: rig.stop() if kind == "frame" else None
    assert rig.run(analyze=False).state == "ABORTED"
    assert len([c for c in rig.stage.calls if c[0] == "move"]) == 1
    assert rig.camera.closed == 1


def test_position_mismatch_faults_and_closes(rig):
    rig.stage.position = lambda: 100
    assert rig.run(analyze=False).state == "FAILED"
    assert rig.stage.calls[-2:] == [("stop",), ("close",)]


def test_stale_frame_faults(rig):
    original = rig.camera.capture
    def stale():
        capture = original()
        if rig.camera.captures > 1:
            capture.metadata["host_monotonic_ns"] = 1
        return capture
    rig.camera.capture = stale
    result = rig.run(analyze=False)
    assert result.state == "FAILED" and "Stale" in result.message


@pytest.mark.parametrize("state,manual,success", [("PRINTING", True, False), ("PAUSED", True, False),
    ("BUSY", False, False), (None, False, False), (None, True, True), ("IDLE", False, True)])
def test_printer_state_guard_cannot_be_bypassed_for_active_print(rig, state, manual, success):
    rig.stage.simulated = rig.camera.simulated = rig.simulated = False
    rig.printer_state = lambda: state
    rig.checks = OperatorChecks(True, True, True, True, True, manual)
    result = rig.run(analyze=False)
    assert (result.state == "COMPLETED") == success
    if not success:
        assert ("open",) not in rig.stage.calls


def test_print_started_between_positions_aborts(rig):
    rig.stage.simulated = rig.camera.simulated = rig.simulated = False
    rig.checks = OperatorChecks(True, True, True, True, True)
    states = iter(["IDLE", "IDLE", "PRINTING"])
    rig.printer_state = lambda: next(states)
    result = rig.run(analyze=False)
    assert result.state == "FAILED"
    assert len([c for c in rig.stage.calls if c[0] == "move"]) == 1


def test_motion_only_dry_run_has_no_images(rig):
    rig.dry_run = True
    assert rig.run().state == "COMPLETED"
    assert rig.camera.captures == 0 and not rig.writer.manifest["frames"]


def test_stop_during_analysis_marks_run_aborted(rig, monkeypatch):
    def analyze(*args, **kwargs):
        rig.stop()
        return {"valid_focus_fits": 1, "total_focus_fits": 1}
    monkeypatch.setattr("focusfield.reports.analyze_run", analyze)
    assert rig.run().state == "ABORTED"


def test_simulated_camera_backend_with_physical_stage_is_rejected_before_motion(rig):
    rig.stage.simulated = rig.camera.simulated = rig.simulated = False
    rig.checks = OperatorChecks(True, True, True, True, True)
    rig.printer_state = lambda: "IDLE"
    original = rig.camera.capture
    def capture():
        value = original()
        value.metadata["simulated"] = True
        return value
    rig.camera.capture = capture
    result = rig.run(analyze=False)
    assert result.state == "FAILED" and "simulated camera" in result.message
    assert not any(c[0] == "move" for c in rig.stage.calls)


def test_real_stage_uses_only_absolute_z_no_homing_or_metrics(monkeypatch, verified_optical_fixture):
    from types import SimpleNamespace
    import sys
    calls = []
    fake = SimpleNamespace(connect=lambda: calls.append("connect"), read_position=lambda **kwargs: (5, 8, 100),
        move_absolute=lambda **kwargs: calls.append(kwargs), disconnect=lambda: calls.append("disconnect"),
        motionstop=lambda: calls.append("stop"))
    monkeypatch.setitem(sys.modules, "gantry_controls", fake)
    stage = ZStage(verified_optical_fixture, 273)
    stage.open()
    stage.move_z_absolute(100.05)
    stage.stop()
    with pytest.raises(InterruptedError):
        stage.move_z_absolute(100.1)
    stage.close()
    assert calls == ["connect", {"z": 100.05, "Zfeed": 60, "cancel": stage.cancelled}, "stop", "disconnect"]
