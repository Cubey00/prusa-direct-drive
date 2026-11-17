# Integrated Focus-Field / MTF workflow

Implementation baseline: `development-bep-groep` at
`645454b5728e67f504c244e8e021b8144450d375`. No features were imported from `main`.
The existing PySide6 printing/plates, three-fiducial calibration, CAD/path programming,
force-touch execution and macOS camera entry points remain in place. The Tk calibrator
continues to launch as a separate process.

This experiment fixes lens focus and varies **target working distance** by moving the bed.
It measures apparent regional best focus and system SFR. It does not isolate intrinsic
Petzval curvature or establish an ISO 12233 conformance claim.

## Start without hardware

```sh
python -m pip install -r requirements.txt
python -m pathprogrammer
```

Select **Focus-Field MTF**. Simulation is checked by default. **Check preview & chart**
renders a synthetic chart, checks all four markers, locates local edges and displays
the captured image. **Start scan** saves the scan and generates the report in a worker
thread. The remaining application stays responsive, including STOP.

The same analysis is available without Qt or a camera SDK:

```sh
python -m focusfield simulate --quick
python -m focusfield simulate --recipe experiments/focusfield_20mm.toml
python -m focusfield analyze logs/runs/focusfield/<run_id>
python -m focusfield analyze logs/runs/focusfield/<partial_run_id> --allow-partial
```

`--quick` uses one frame at each of 21 positions and one sweep. The versioned default
recipe uses 19.50–20.50 mm at 0.05 mm intervals, three frames per position and two
same-direction sweeps. Set `reverse_alternate = true` to compare sweep directions.
Simulation has a known radial best-focus surface with added apparent tilt and noise;
its output is explicitly labelled synthetic.

## Generate or use the supplied optical chart

The supplied SVG and physical-coordinate layout are preserved in `focusfield/charts/`.
The supplied generator is integrated as `focusfield/generate_chart.py`, with the original
command name retained as a root-level wrapper:

```sh
python generate_focus_chart.py --output charts/my_chart.svg --field-width 60 --field-height 45
# Equivalent package command; --help lists grid, square, marker, page and slant options:
python -m focusfield chart --help
python -m focusfield chart --output charts/my_chart.svg --rows 5 --cols 7 --angle 5
```

Each command writes the SVG and `<stem>_layout.json` together. The UI generator exposes
visible field dimensions and selects its new JSON for the next run. For other generator
options use the CLI, then **Select chart layout**. The default is 31 slanted squares
(5 × 7 grid with empty corners) and four unique `DICT_4X4_50` markers, IDs 0/1/2/3.
These are optical markers, separate from tactile plate cones F1–F3 and slicing manifests.

Print at 100%, measure the 10 mm scale bar and target flatness, and measure field of view
at the actual working distance. The provisional 60 × 45 mm field is not a camera
calibration. Marker visibility, local edge size, printing quality and distortion must be
checked across the complete scan. A plain homography does not correct wide-angle distortion.

## Configure a physical fixture

Physical scans are disabled in the shipped settings. Edit **the existing**
`machine_settings.toml` `[focusfield]` section, or use `PRINTERPROJECT_SETTINGS` for a
separate measured machine profile. Older settings without this section remain valid and
leave optical motion disabled. Do not copy the simulator's 99–101 mm Z interval into a
real profile.

| Setting | Meaning |
| --- | --- |
| `enabled`, `measured` | Both must be true before physical scanning |
| `fixture_id`, `calibration_id` | Actual fixture and independently measured optical calibration identifiers |
| `camera_serial`, `firmware_id` | Camera identity and verified mounted custom-firmware build |
| `safe_z_min_mm`, `safe_z_max_mm` | Measured collision-free interval, inside `printer.z_max` |
| `reference_z_mm`, `reference_wd_mm` | Reported printer Z and independent distance from defined front-lens reference to target |
| `minimum_wd_mm` | Minimum permitted lens/target working distance |
| `wd_uncertainty_mm` | Positive measured WD calibration uncertainty; included in clearance check |
| `position_tolerance_mm` | Command/readback acceptance tolerance; does not establish physical accuracy |
| `z_speed_mm_s` | Bounded scan speed (default 1 mm/s) |

The mapping is `WD = reference_wd_mm + reported_Z - reference_z_mm`: increasing Z lowers
the Core One bed. Verify this sign on the actual fixture. The tactile `[camera] mm_per_px`
and unmeasured sensor offsets are not used as optical calibration.

Before a physical scan, independently verify mechanical clearance, full travel, stage
repeatability, firmware, STOP, fixed lens focus, chart scale and exposure. The UI requires
explicit setup checks. First run **Motion only** under supervision, then confirm the
completed collision-free dry run before acquiring images. The initial Z must already be
inside the validated interval; the software will not move into it from an unknown position.

The adapter sends absolute Z only, waits for the driver's M400 and checks position before
and after capture. It never homes (`G28`), probes, starts force metrics, or touches the chart.
No optical control imports `imaging.py` or its device/saver side effects.

## Ownership, printer state and STOP

The main window excludes physical optical operations from calibrator/touch/print actions.
OS-backed `DeviceLease` locks also protect the printer and camera across application
processes, including the Tk helper. Serial ownership lasts from `connect()` to `disconnect()`;
camera ownership lasts until `close()`. PrusaLink control/upload holds the same printer
lease for its request. OS locks are released on process exit. The locks are cooperative
and cannot prevent an unrelated external application or another computer controlling devices.

PrusaLink is checked before opening the serial connection and before each position. Only
known idle states pass. A missing/unavailable PrusaLink connection needs the explicit
manual-idle checkbox; a known active/paused/busy state cannot be overridden by it.
Do not start printer operations from another client during scanning. An idle API response
does not prove mechanical stationarity or replace motion completion/readback checks.

STOP latches cancellation, invokes the existing immediate `motionstop()` in a separate
thread, and closes devices after STOP finishes. Cancellation is also checked atomically
with serial command dispatch so a pending optical move cannot start after STOP. A partial
manifest is retained. There is **no automatic retreat or recovery movement**. On faults
the same stop/close path runs. If closing the app during optical work, wait for the run to
end and close again; the running QThread is not destroyed while hardware is active.

## Camera contract and support matrix

Existing `snap()` callers still receive owned RGB8 preview arrays. Optical acquisition
has a separate `capture_raw()` contract: native owned samples plus format, bit depth,
camera identity, exposure/gain, timestamp, frame counter and processing provenance.

| Backend | Native raw path | Scope |
| --- | --- | --- |
| Windows/Linux IDS peak | Unpacked Mono8/10/12/16, BayerRG8/10/12 | Format selected before payload buffers are allocated; no IPL RGB conversion in raw capture. Requires installed IDS peak SDK and actual format support. |
| macOS SCC/Aravis | Mono8/16, BayerRG8, standard BayerRG10p/12p | Uses the pinned SCC decoder; vendor grouped formats such as BayerRG12g24IDS are rejected. Camera must advertise the requested format. |
| Simulator | Recipe formats, generated samples | Control/analysis validation only; no physical measurement claim. |

Transport streams restart after each settle interval to discard queued and in-flight
frames, then configured frames are discarded before recording. Host receive timestamps
are monotonic and explicitly **not sensor exposure-start timestamps**. Frame freshness,
full sensor area, binning, linearity, gamma/sharpening, timing, actual pixel format and
exposure stability still require verification on real equipment. Unsupported formats,
incomplete frames, identity changes and exposure/gain mismatches fail the run.

All current reports, including raw ones, are marked **provisional / measurement validation
required**. Raw transport and decoder tests are not physical calibration or a reference
instrument comparison. RGB8 is intended for control prototypes; it is not quantitative
raw MTF. Real hardware acquisition and unattended operation have not been validated here.

## Analysis and scientific limits

Analysis verifies input schema, schedule, frame counts, image checksums, increasing timestamps,
camera/format consistency and the reported-Z/WD relationship. Incomplete runs need an
explicit partial-data option. The run's snapshot of the physical chart is used, so reanalysis
does not depend on an original absolute chart pathname remaining available.

All four unique chart markers seed local searches in every image. Each square is detected
again locally; SFR uses original image pixels with no geometric warp or interpolated
rotation. Missing markers, poor localization, severe marker reprojection error, too-small
ROIs, low contrast, clipping, inappropriate slant, poor phase coverage and nonstraight
edges produce explicit validity flags.

RGB8 uses green-channel samples. BayerRG uses one green sublattice (even rows, odd columns),
with sampling pitch **two original sensor pixels in both axes**. Results are converted
to cycles per original sensor pixel, with green-lattice Nyquist 0.25 cycles/sensor pixel.
The recipe frequency is below that limit. This discards the second green lattice and does
not infer unavailable full-resolution monochrome samples.

The SFR prototype fits derivative-centroid edge positions, projects samples on the edge
normal, bins at 4×, differentiates, applies a Tukey window and central-derivative response
correction. Gaussian-reference fixtures cover two orientations and multiple blur widths;
these tests do not establish ISO certification or validate every lens/target/noise case.

MTF50 and fixed-frequency MTF are saved per edge and frame. Focus fits use fixed-frequency
MTF after averaging frames at a position, fitting each sweep and edge separately. They
require at least five positions, with two positions on either side of the peak. Boundary,
flat, nonconcave and unstable fits are invalid. The local quadratic's covariance provides
statistical fit uncertainty; calibration uncertainty and inter-sweep repeatability are
separate values, not a complete uncertainty budget. CSV retains sweep direction, so forward
and reverse results can be compared.

Maps separate horizontal/vertical image-edge groups. Best WD, fixed nominal-plane MTF,
best fitted MTF and apparent tilt-plane residual are separate outputs. Nominal-plane
values are interpolated only within observed data, never substituted with peak sharpness.
The fitted plane is labelled **apparent tilt**; subtracting it does not isolate intrinsic
lens curvature. Optical distortion, flatness, chart quality, camera/bed alignment and
sensor processing remain contributors to measured system performance.

## Files and offline review

Each unique folder under `logs/runs/focusfield/` contains:

- Immutable recipe and chart snapshots: `config.json`, `chart_layout.json`.
- Atomically updated `manifest.json`: source SHA/dirty state, typed settings, fixture/WD
  calibration, operator checks, expected frames, run state and per-frame metadata.
- Lossless `images/frame_000001.tiff`, indexed by ID rather than rounded distance.
- Incremental `frames.csv`, including commanded/reported Z, nominal WD, exposure, format,
  bit depth, repetition/direction, timestamp, calibration ID, processing and SHA-256 checksum.
- `chart_detection.csv`, `sfr_results.csv`, `focus_results.csv` and `analysis.json`.
- Best-focus, nominal-plane MTF, best-MTF and apparent-plane-residual PNG maps, through-focus
  curves and a local `report.html` linking to originals and CSV.

The manifest is authoritative if a crash happens between its atomic update and CSV append.
No hardware connection is opened during reanalysis. **Analyze saved run** can select a
historical folder; **Open report** opens its local HTML. Failed and aborted runs retain
their originals; reanalysis never rewrites acquired TIFFs.

## Validation

Baseline suite at the pinned development commit: **133 passed, 27 skipped** in the Linux
cloud environment. Integrated suite: **213 passed, 27 skipped**, including the optional
SCC simulated-backend tests. New tests cover recipe/bounds/clearance, absolute motion and no homing,
command cancellation, printer-state conflicts, STOP/disconnect/partial runs, lossless frames,
chart generation and SVG markers, Gaussian SFR, green-lattice units, peak/plane fitting,
known simulated focus recovery, reports, cross-process ownership, UI integration, mocked IDS
raw capture and SCC simulated native formats. Run:

```sh
QT_QPA_PLATFORM=offscreen python -m pytest
```

Existing macOS-only tests need macOS and `requirements-macos.txt`; the optional new SCC
decoder tests run on any platform when that package is installed. No printer, physical
camera, stage metrology or reference optical instrument was available for this implementation.
The optional migration of remaining Tk manual controls is left for separate work; preserving
that workflow avoids changing existing force/calibration behavior as part of this feature.
