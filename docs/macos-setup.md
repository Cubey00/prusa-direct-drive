# macOS compatibility

This branch keeps the application functionality from `main`. Printer transport,
force probing, STOP/retreat, both GUIs, path-program JSON, execution and logging are
unchanged. It adds a macOS implementation of the existing `IDS_Camera` interface
using the separately maintained Scientific Camera Capture package. Windows and
Linux keep their existing IDS peak implementation.

## Install and launch

Use Python 3.12 with Tk, and a matching interpreter/native-library architecture:

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-macos.txt
python -m pip check
python -m pathprogrammer
# Existing manual printer UI, when the printer is available:
python GUI.py
```

The launch commands and UI are the same as `main`. The path programmer's existing
**Simulate (dry run, no printer)** option permits hardware-free execution. Normal
manual-GUI startup attempts to configure printer metrics, as it does on `main`.
No new camera panel, experiment modes, package installer or storage layout is added.

The macOS requirements pin Scientific Camera Capture 0.1.0 to commit
`38e691be39bf13bd3578f8a1afcbf6ead090fe96`, including its NumPy/TIFF export extra.
For an actual camera, follow its [native macOS setup guide](https://github.com/stefberends/scientific-camera-capture/blob/38e691be39bf13bd3578f8a1afcbf6ead090fe96/docs/macos-setup.md)
to install compatible Aravis/PyGObject libraries. These native libraries are not
installed automatically. Do not mix arm64 and Intel Python/native dependencies.

## Existing camera API

Existing code continues to use:

```python
from ids_camera import IDS_Camera

camera = IDS_Camera(camera_index=0)
try:
    camera.set_exposure(50e3)  # microseconds, as on main
    camera.set_gain(2)
    camera.flush()
    rgb = camera.snap()       # independent uint8 array, height × width × 3
finally:
    camera.close()
```

On macOS, Aravis is the default SCC backend. `camera_index` retains main's indexed
selection behavior. Set `PRUSA_CAMERA_ID` to an exact discovered SCC ID to choose a
specific camera instead. No simulator fallback occurs unless explicitly selected:

```sh
export PRUSA_CAMERA_BACKEND=simulated
python -c 'from ids_camera import IDS_Camera; c=IDS_Camera(); print(c.snap().shape); c.close()'
```

`snap()` waits for the next complete frame; `flush()` therefore needs no separate
stale-buffer drain. Exposure/gain changes pause and restart acquisition through SCC.
Verified Mono8/Mono16 and Bayer RG8/RG10p/RG12p formats produce the existing RGB8
array contract, using SCC's decoder and RGGB interpolation. Higher bit depths are
shifted to 8 bits; monochrome is repeated into RGB channels. This is linear image
conversion, not a claim of matching the proprietary SDK's colour calibration.
If the default native pixel format lacks a verified decoder, an advertised standard
format is selected. If none is available, capture fails explicitly rather than
guessing vendor packing. IDS grouped Bayer formats need independent fixtures before
their conversion can be enabled in the camera package.

On macOS only, `imaging.py` uses a working-directory `photos/` default, and the legacy
camera benchmark uses `fototest/`. `PRUSA_PHOTO_DIR` overrides those defaults; the
existing `imaging.set_photo_folder()` also works. Windows/Linux retain their original
paths and acquisition behavior. The imaging module and benchmark still open the
camera when imported, matching main; neither is imported by the current GUIs.

## Validation boundary

```sh
QT_QPA_PLATFORM=offscreen python -m pytest -q
```

The added tests cover macOS routing, the existing camera methods, RGB frame ownership,
supported pixel layouts, TIFF saving, benchmark capture and startup of the unchanged
GUIs. Mocked vendor tests verify that Windows/Linux continue to use IDS peak. The
original tests retain their existing behavior, including skips when a private
`model.3mf` is absent. macOS CI checks Intel and Apple Silicon; hosted software tests
are not physical device acceptance.

No printer or IDS camera was available for validation. Actual native camera discovery,
full-resolution colour/transport behavior, printer USB/UDP operation and the exact
modified Core One firmware's `M410` stop behavior remain physically unverified.
The firmware requirement from `main` is unchanged. No hardware-support certification
is implied by simulator or CI results.
