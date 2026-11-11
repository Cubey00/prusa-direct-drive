"""Consumer seam tests against the separately installed, optional camera library."""
import json

import pytest

from pathprogrammer.camera import main, open_simulated_camera


def test_requires_explicit_simulator_id():
    with pytest.raises(ValueError, match="only camera_id='simulated'"):
        open_simulated_camera("aravis:any-device")


def test_raw_recording_and_snapshot_roundtrip(tmp_path):
    sc = pytest.importorskip("scientific_camera")
    output = tmp_path / "run.scc"
    with open_simulated_camera("simulated") as camera:
        assert camera.info.id == "simulated"
        with camera.start_stream() as stream:
            with stream.start_recording(output):
                shot = stream.snapshot(save=True)
                assert shot.acquisition_mode == "stream_selection"
                assert shot.reference
    reader = sc.RecordingReader(output)  # checksums enabled by default
    frames = {frame.metadata.acquisition_index: frame for frame in reader.iter_frames()}
    assert frames[shot.frame_id].payload == shot.frame.payload
    assert reader.manifest["state"] == "complete"
    # Session ownership is released so a subsequent acquisition can open the same ID.
    with open_simulated_camera("simulated") as reopened:
        assert reopened.capture_single().frame is not None


def test_existing_output_is_preserved_and_camera_released(tmp_path):
    pytest.importorskip("scientific_camera")
    output = tmp_path / "existing.scc"
    output.mkdir()
    marker = output / "keep.txt"
    marker.write_text("original data")
    with pytest.raises(FileExistsError):
        main(["--camera-id", "simulated", "--output", str(output)])
    assert marker.read_text() == "original data"
    with open_simulated_camera("simulated") as camera:
        assert camera.capture_single().frame is not None


def test_smoke_command_verifies_raw_data(tmp_path, capsys):
    pytest.importorskip("scientific_camera")
    main(["--camera-id", "simulated", "--output", str(tmp_path / "smoke.scc")])
    report = json.loads(capsys.readouterr().out)
    assert report["verified_frames"] > 0
    assert report["snapshot_reference"]
    assert report["timing_provenance"] == "host_receipt; exposure timing unknown"
