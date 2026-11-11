# macOS foundation setup

This branch starts milestones M0 and M2 of the [project plan](../PRUSA_DIRECT_DRIVE_MACOS_PROJECT_PLAN.md).
Use the existing PySide6 path programmer as the primary application. Camera capture is
optional, and the new integration seam currently supports only the camera simulator.
Printer motion, probing, STOP, and retreat behavior are unchanged.

## Environment and installation

Use Python 3.12 with Tk 8.6 and matching architecture dependencies. Record the actual
environment before comparing results:

```sh
sw_vers
uname -m
python3 --version
python3 -c 'import platform, tkinter; print(platform.machine(), "Tk", tkinter.TkVersion)'
```

From a local checkout, install into a virtual environment:

```sh
git switch feat/macos-support
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m pip check
```

The base requirements install the application and its test tools. They do not install
Scientific Camera Capture, IDS peak, PyGObject, or native Aravis. The base requirements
are not a lockfile; record installed versions with `python -m pip freeze` when reporting
a validation result. This is a source-checkout installation; standalone application
packaging is still pending.

## Camera-free startup and tests

The Tkinter smoke check creates the manual-control window and closes it automatically:

```sh
python GUI.py --smoke-test
```

This mode does not start metrics, connect to a printer, or send STOP during cleanup.
It is a launch check, not an interactive simulator for the manual controls. Normal
`python GUI.py` still starts metrics using the printer connection.

Start the path programmer with:

```sh
python -m pathprogrammer
```

The path programmer can open without a printer or camera. Before executing a dry run,
select **Simulate (dry run, no printer)**. Loading a 3MF model, defining calibration,
and adding touch points still use the existing preflight checks. Opening the visual
calibrator launches the normal printer-connected Tkinter application.

Run automated checks from the repository root:

```sh
QT_QPA_PLATFORM=offscreen python -m pytest -q
```

The calibrator IPC tests need permission to bind a localhost socket. Tests that require
the untracked `model.3mf` fixture are skipped when it is absent. Qt's offscreen mode
does not provide a display for Tkinter; run its launch check in a logged-in macOS
desktop session.

## Optional camera simulator

Install the separately maintained camera package only when needed:

```sh
python -m pip install -r requirements-camera.txt
python -m pathprogrammer.camera --camera-id simulated --output /tmp/prusa-camera-smoke.scc
```

Choose an output directory that does not already exist. The command records raw frames,
saves a stream snapshot and an event marker, then reads the recording back with checksum
verification. Its JSON output includes the verified frame count and snapshot reference.
It requires no camera, IDS peak, GI, or Aravis. The optional requirement pins camera
package version 0.1.0 at commit `38e691be39bf13bd3578f8a1afcbf6ead090fe96` so API changes
can be reviewed explicitly.

Application code can use `pathprogrammer.camera.open_simulated_camera("simulated")`
as a context manager. The returned camera-library session owns acquisition and cleanup.
Camera IDs are explicit; this seam does not select the first discovered device or probe
native backends. Call synchronous acquisition and disk operations from workers when
adding GUI integration. Native camera selection, UI controls, and touch-event associations
are not implemented here.

## Legacy imaging and troubleshooting

Importing `imaging.py` no longer opens an IDS camera, starts its TIFF saver, or creates
a Windows-specific output folder. Explicit legacy capture still requires the vendor IDS
peak SDK, and saving TIFFs additionally requires `tifffile`. Install/configure those only
for a separately validated legacy-camera setup. `set_photo_folder(path)` selects an output
directory; the default is `photos` relative to the working directory. The simulator path
uses the camera library's SCC raw format instead.

If `import tkinter` fails, use a Python installation that supplies a matching Tk build,
then recreate the virtual environment. If Qt or Tk cannot open a window, check that the
command is running in a desktop session. Keep Python, Qt, Homebrew libraries, and any
future GI bindings on the same architecture; do not mix an Intel interpreter under
Rosetta with arm64 native libraries. These steps do not install Homebrew packages or
change system permissions.

For future physical-camera setup, follow the camera repository's
[macOS guide](https://github.com/stefberends/scientific-camera-capture/blob/38e691be39bf13bd3578f8a1afcbf6ead090fe96/docs/macos-setup.md).
Aravis/PyGObject installation and real-device validation are separate from this simulator
foundation.

## Validation boundaries and next milestones

See the [dated validation report](macos-validation.md) for actual local test results,
dependency versions, and environment-specific test constraints.

| Area | Current scope | Evidence still required |
| --- | --- | --- |
| Intel macOS | Local development host: macOS 15.8.1, Python 3.12.3 | Validation results recorded separately; physical workflows pending |
| Apple Silicon | Target platform | Native desktop launch and physical acceptance |
| Camera simulator | Raw recording/snapshot seam | Full adapter lifecycle and failure handling in M3 |
| USB serial and UDP loadcell metrics | Existing discovery and transport retained | Actual `/dev/cu.*` device, USB topology, LAN route, metrics timing |
| Printer motion and probing | Existing behavior retained | Exact printer firmware and controlled STOP/retreat tests |
| Physical camera | No hardware backend wired into the app | Camera identity/firmware, raw format/stride, throughput, storage and recovery |

The printer requires modified Core One firmware for immediate `M410` stopping; that
firmware is not included in the repository. Passing software tests does not validate
physical probing or establish compatibility with a particular Mac, printer, or camera.
Record USB topology, printer/camera firmware, and storage hardware in physical acceptance
reports. No physical device checks are claimed by this foundation.

Next, M3 adds the consumer-side camera adapter and recovery tests; M4 adds camera controls;
M5 associates imaging with experiment events. Native printer/camera validation proceeds
separately, and M6 remains the release gate for validated Intel and Apple Silicon support.
