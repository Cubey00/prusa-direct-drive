# printerproject

Python control of a Prusa Core One (as a gantry) with an IDS camera and a strain gauge read via printer metrics, for automated imaging and force-touch probing.

## Code
- `GUI.py` – Tkinter app tying everything together (jogging, photos, gauge calibration, touch probing).
- `gantry_controls.py` – USB serial link to the printer: connect, G-code send, home, relative moves, e-stop, `MotionController`.
- `strain_gauge.py` – `StrainGauge` reads force values from the printer metrics (`enable_metrics`), fed by the serial reader (`start()` registers `feed`).
- Metrics travel over the USB serial link, not the network: the modified firmware (`C:\Prusa-Firmware-Buddy`) sends them after `M334 usb`, one `metric:<name> <fields> <timestamp_us>` line per point, mixed with the G-code replies (a line lock in `USBSerial` keeps lines whole; stock firmware only does UDP). In `gantry_controls` one reader thread owns all serial reads: `metric:` lines go to `add_metric_listener` callbacks, everything else to the reply queue that `_send`/`_resync_serial` read.
- `touch.py` – `touch()`: force/depth touch, returns a `TouchResult`; forces in grams. Gauge data arrives late and contact is stiff, so continuous moves overshoot (≈ stiffness × speed × delay): fast approach to contact (M410), back off, slow re-search (`SEARCH_SPEED`), then a continuous press at the touch speed (M410 at the target force or depth). `expect_z` (previous contact, passed by `PrinterHardware`) lets the slow search skip the fast hit. Simulated in `tests/test_touch.py`. Each `TouchResult` carries a `trace` (t, phase, metrics Z, |force|) that lands in the run log. STOP in the path programmer sets a cancel event (touch halts with M410) and `PrinterHardware.close()` then returns to the Z at run start.
- The printer sends a G1's `ok` only once the move is **done**, not when it is queued. To watch the load cell during a move, send it with `gantry_controls._send_nowait()` and drop the pending `ok` with `_resync_serial()` (`halt_move` does this). Z1.53 is where the moves to the travel limit end (floor).
- **M410 WORKS.** The printer firmware is modified so M410 is an immediate stop. Use continuous moves stopped with M410; do not chunk moves (tried before: makes the printer jittery) and don't blame overshoot on M410.
- `pathprogrammer/` – PySide6 app (`python -m pathprogrammer`): load a `.3mf`, rotate to a face, two-point face→machine calibration, ordered touch-point programs (JSON), execute with a simulator or the printer. Pure-logic modules (`calibration`, `program`, `model_io`, `face_render`, `executor`, `ipc`, `hardware`) have no Qt dependency; UI is in `ui/`. Launches `GUI.py --link <port>` as the visual calibrator (sends head positions back over `multiprocessing.connection`); only one process may own the serial port, so execution requires the calibrator closed. Limits/conventions in `pathprogrammer/config.py` (`FACE_Y_TO_MACHINE_SIGN`, `MACHINE_XY_BOUNDS` placeholder). Each run gets its own folder `<runs folder>/<run name>/` (chosen in the Files box, default `runs/`; gitignore it if large) with `run_log.json`, `images.json`, `heightmap.csv/png` and `images/point_<pt>_<k>.tiff`. `heightmap.py` z-buffers the visible face of the oriented mesh (face x/y = print XY, height above the model bottom = bed 0). Points have `image_step` (g or mm): `touch(image_step=, capture=)` presses in segments, each stopped with M410, and exposes (`hardware.snap`, `config.CAMERA_EXPOSURE_US`) with the bed standing still; `images.json` lists force and gantry XY in print (face) coordinates per image (`imagesave.ImageSaver` writes TIFFs in a thread).
- `tests/` – pytest (+ pytest-qt) for the path programmer; run `python -m pytest` from the root.
- `ids_camera.py` – `IDS_Camera` wrapper around the IDS peak SDK.
- `imaging.py` – snap/save TIFF images (threaded saver); `set_photo_folder`.
- `gauge_drift_reader.py` – logs and plots strain gauge drift over time.
- `camera speedtester.py` – benchmarks the snap + TIFF save pipeline.
- `printer_command_test.py`, `test.py` – quick serial/G-code connection tests.

## Data / other
- `calibration.json` – hand-edited load cell `scale` (grams = scale × tared reading), read by `strain_gauge.py`; fit it with the GUI calibration points.
- `logs/` – drift logs (JSON) and plots, calibration plots.
- `fototest/` – sample TIFFs from a camera test.
- `MMConfig_demo.cfg`, `*.lnk` – Micro-Manager config and shortcuts (Micro-Manager, IDS peak Cockpit).
- `USB drive backup/` – unrelated backup: printer firmware (`.bbf`), sliced prints (`.bgcode`), knife/part CAD (`.dxf`), logos, coursework PDFs, test G-code.
