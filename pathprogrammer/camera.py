"""Optional simulator integration seam; no printer or native camera ownership.

The returned library session owns acquisition/cleanup and retains its public API.
Call synchronous capture and disk operations from a worker when integrating a UI.
Hardware backends and a full CameraAdapter belong to the next milestone.
"""


def open_simulated_camera(camera_id):
    """Open an explicitly selected simulator, without probing native backends."""
    if camera_id != "simulated":
        raise ValueError("This foundation supports only camera_id='simulated'")
    try:
        from scientific_camera import CameraManager
        from scientific_camera.backends.simulated import SimulatedBackend
    except ModuleNotFoundError as exc:
        if exc.name != "scientific_camera":
            raise
        raise RuntimeError(
            "Camera support is optional. Install it with "
            "python -m pip install -r requirements-camera.txt"
        ) from exc
    return CameraManager(backends=[SimulatedBackend()]).open(camera_id)


def main(argv=None):
    """Headless raw recording/snapshot smoke check, using a new output directory."""
    import argparse
    import json
    from pathlib import Path

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--camera-id", required=True, choices=["simulated"])
    parser.add_argument("--output", required=True, type=Path, help="new SCC recording directory")
    args = parser.parse_args(argv)
    with open_simulated_camera(args.camera_id) as camera:
        with camera.start_stream() as stream:
            with stream.start_recording(args.output):
                shot = stream.snapshot(save=True)
                stream.mark_event("smoke_snapshot", {"snapshot_id": shot.snapshot_id})
    from scientific_camera import RecordingReader
    frames = sum(1 for _ in RecordingReader(args.output).iter_frames())
    if frames == 0:
        raise RuntimeError("Simulator recording contains no frames")
    print(json.dumps({"camera_id": args.camera_id, "recording": str(args.output.resolve()),
                      "verified_frames": frames, "snapshot_reference": shot.reference,
                      "timing_provenance": shot.timing_provenance}))


if __name__ == "__main__":
    main()
