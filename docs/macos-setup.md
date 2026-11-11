# macOS compatibility

This branch keeps the application functionality from `development-bep-groep`,
including three-point fiducial calibration, plate slicing and PrusaLink printing,
with focused fixes to the standalone printer diagnostic and drift logger. The
macOS integration preserves development's printer motion, force probing,
STOP/retreat, both GUIs and path-program execution. It adds a
macOS implementation of the existing `IDS_Camera` interface
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

The launch commands and UI are the same as `development-bep-groep`. The path
programmer's existing
**Simulate (dry run, no printer)** option permits hardware-free execution. Normal
manual-GUI startup attempts to configure printer metrics, as it does on development.
No new camera panel, experiment modes, package installer or storage layout is added.

For **Print plates**, install PrusaSlicer 2.9 or newer in `/Applications` or
`~/Applications`. The app finds `PrusaSlicer.app/Contents/MacOS/PrusaSlicer` there
and uses the same executable with `--gcodeviewer` for **Show in 3D**. A custom
installation can be selected with `SLICER_EXE` in `slicing/config.py`. Confirm that
`slicing/profiles/coreone.ini` matches the actual printer, filament and print
settings before slicing. PrusaLink printing uses the existing **Printer settings...**
dialog; install the complete macOS requirements, which include development's
`requests` dependency. Actual slicing/viewer launch and network printing still
require validation with the installed PrusaSlicer and printer.

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

## Real printer on macOS

Camera support and simulator tests do not establish printer compatibility. The
README states that parts of this application require custom Core One firmware
that has not been published. Obtain and identify the exact modified firmware
before planning hardware acceptance; stock firmware compatibility is not established.

### USB serial selection and ownership

The existing transport uses pyserial at 115200 baud, opens on first use, waits two
seconds for the handshake, and selects exactly one USB device with Prusa VID
`0x2C99`. On macOS, inspect the actual `/dev/cu.*` callout device names:

```sh
python -m serial.tools.list_ports -v
python printer_command_test.py --help
python printer_command_test.py --port /dev/cu.usbmodemEXAMPLE
```

Replace the example with an enumerated device. The diagnostic sends only `M333`
to report metrics configuration and then closes the connection; it does not home
or move. Without `--port`, it uses the unchanged VID detection. A missing VID
(including adapters that omit it) or multiple matching devices requires an explicit
selection; the transport does not guess. The diagnostic's `--port` overrides
`gantry_controls.PORT_OVERRIDE` for that process. For the existing GUIs, set
`PORT_OVERRIDE` in `gantry_controls.py` to the verified `/dev/cu.*` path if automatic
selection cannot identify one device. This is not an environment-variable setting.

Give the serial device one owner at a time. Close slicer serial monitors, terminals,
other GUI instances and diagnostics before opening the next tool. Use one UDP
metrics receiver at a time as well; socket reuse does not provide exclusive
ownership or reliable delivery to multiple application instances.

### UDP metrics, tare and calibration

`gantry_controls.enable_metrics()` sends `M334 <Mac-IP> 8514`, then `M331 loadcell`,
`M331 pos_x`, `M331 pos_y` and `M331 pos_z`. A changed metrics destination can require
confirmation on the printer screen; the `M334` command allows up to 300 seconds
of serial silence for this. The receiver binds UDP port **8514** on all IPv4
interfaces. Permit incoming traffic for the selected Python interpreter in the
macOS firewall and check that the printer can route to the Mac's chosen address.
Private LAN access, Wi-Fi client isolation, VPN routing and multiple Ethernet/Wi-Fi
interfaces can affect this independently of a working USB connection.

The IP helper prefers a private non-loopback, non-link-local address, prioritizing
`192.168.*`, and otherwise uses the default-route interface. That heuristic cannot
prove printer reachability or select the right network among several private
interfaces. Override it with the Mac's printer-reachable LAN IPv4 address before
starting the chosen tool, for example:

```sh
export PRINTER_METRICS_IP=192.168.1.42
python gauge_drift_reader.py --help
python gauge_drift_reader.py --duration 30 --startup-timeout 15 --output ./drift_log.json
```

Use the real LAN address instead of the example. The drift logger configures
metrics and records without motion; its default duration is 1200 seconds. Its
startup wait for enough load-cell samples is bounded by `--startup-timeout`, and
`--output` chooses the log file: absolute paths are accepted, and relative paths
are resolved beside `gauge_drift_reader.py`, retaining the existing log location.
Failed metrics setup or startup data collection aborts and cleans up the listener
and serial connection. Importing the diagnostic
or drift module does not open hardware or start logging.

`StrainGauge.tare()` needs 300 received samples. The manual GUI retries its initial
tare as samples arrive and only then arms its force guard; the path programmer
fails opening if it cannot tare within five seconds. A touch waits for initial
data and requires 300 newly received samples for its stationary tare. These checks
are not a continuous freshness guarantee: samples and XYZ values have no expiry,
`force_g()` can keep returning an old average after UDP stops, and position labels
can retain old coordinates. Missing initial force returns `None`; the force guard
treats it as zero rather than a trip. Do not infer a healthy live connection from a
last displayed value or rely on force protection during delayed or missing metrics.

Forces use `grams = SCALE * tared_reading`, with `SCALE` read at import from
`calibration.json` beside `strain_gauge.py`. If the file is missing or invalid,
the code prints a warning and uses `1.0`: values are raw units even where a UI
label says `g`. Verify tare, sign and scale against known loads before interpreting
force thresholds. The GUI's **Weight calibration... / Finish calibration** fits
and displays a slope and intercept; it does not save the file or apply the fit.
Persist the verified slope as the JSON `scale` value and restart the process to
load it. The force conversion uses no fitted intercept; tare supplies the offset.

### Firmware, STOP and return behavior

Force probing depends on the modified firmware handling `M410` as an **immediate
abort and planner flush**. Its semantics on the exact printer must be verified by
a competent operator; an acknowledgement alone does not prove that motion stopped.
After a stop, the transport sends a unique `M118 resyncN` marker and consumes old
replies through its echo and following `ok`. Firmware must support that sequence
or the connection can fail to resynchronize.

UDP batching, averaging and mechanical stiffness introduce force overshoot before
the stop takes effect. `touch.py` records an earlier observation of a 5 g contact
threshold ending at 90–225 g at 2 mm/s; this is source context, not validation of
this Mac or printer. The slow search/backoff logic and software limits do not
establish a maximum force or stopping distance.

The existing stop paths differ:

- Manual GUI **STOP** clears queued nudges and calls `motionstop()` asynchronously;
  it does not itself command a retreat.
- A touch cancellation or overload halts the watched move and normally returns to
  its starting Z in `finally`. That return is skipped while the global stop event
  is set; it can still occur after the event clears. STOP is not a promise that no
  further return motion will follow.
- Path-programmer **STOP** sets cancellation and launches `motionstop()`; closing
  that run then attempts a Z retreat to the height captured when hardware opened.
  The retreat waits for the stop event for at most 20 seconds and reports failure.
- The tared force guard ignores force during homing. Above 500 g it invokes an
  emergency stop/drop toward `Z_MAX = 273` at `DROP_FEED = 1200` mm/min, then stops
  when its force check falls below 50 g, Z_MAX is observed, or the global stop
  event is set. Development sends the drop without waiting for the queued move's
  acknowledgement so it can monitor force, then halts and resynchronizes in
  `finally`. This depends on the same serial/UDP chain; it is not an independent
  hardware interlock.

Higher Z lowers the bed. Check the actual fixture clearance, travel height and
retreat direction; neither a stored Z nor the name “safe Z” certifies clearance.
The exact firmware, physical stop/return behavior and macOS printer operation
remain unverified by the software checks described below.

### Staged hardware-validation checklist

This checklist is for a human operator. No hardware tests or motion were performed
for this change. Complete each stage before progressing; the commands above do
not initiate motion.

1. **Read-only USB checks:** enumerate ports, identify the printer and ensure sole
   ownership. Run the `M333` diagnostic, using `--port` if needed. In a separate
   sole-owner session, query `gantry_controls.read_position()` (`M114`) and close
   the connection. Confirm sensible XYZ output without issuing homing or moves.
2. **Stationary UDP checks:** choose the reachable metrics IP, confirm any printer
   screen prompt, and verify incoming load-cell and all three position metrics.
   Check firewall, routes, VPN and interface selection if data is absent. Use the
   short bounded drift capture to check sample arrival and tare without movement.
   Verify known-load calibration and persisted `scale`; investigate stalled or
   delayed data before continuing.
3. **Human firmware and stop acceptance:** identify the modified firmware and have
   the operator establish immediate `M410` stopping, `M118` resynchronization and
   each intended STOP/return/retreat path using a controlled, clear-workspace
   procedure. Establish an independent way to stop the hardware and verify physical
   clearance for returns and emergency bed lowering. Software simulation cannot
   satisfy this stage.
4. **Supervised motion only after acceptance:** the operator may then validate
   small, low-speed jogging with conservative travel, homing with a clear bed, and
   finally low-speed probing with known loads and limits. Recheck force latency,
   overshoot, contact/backoff and return behavior before a full path program. Stop
   acceptance if observed motion or metrics differ from expectations.

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
