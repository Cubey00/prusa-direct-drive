# Integrated Focus-Field / MTF workflow

Implementation baseline: `development-bep-groep` at
`086dc5f8f7a39e26a480f8c361eebc72a5c566b4`. The original patch was authored
against `645454b`; PR #2 preserves the three subsequent development commits.
No features were imported from `main`. The review corrections are described in
[the implementation review](focusfield-review.md).
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

Select **Focus-Field MTF**. The page opens in **Measurement** with **Use simulation**
enabled. Turn off simulation when you're ready to use the real camera and printer.

1. Set the working distance, sweep half-span and step. The app fits whole steps on both
   sides of the working distance, within the requested span. For independent endpoints,
   use **Advanced scan settings → Use manual scan bounds**. The page warns if the
   selected grid misses the working distance.
2. For real hardware, generate both targets for the visible field or select the
   layout that matches your existing print. Simulation can use the bundled layout.
3. Use **Check chart** to capture a frame and check that the chart field and all four
   markers fit. The same step shows the loaded lens calibration or suggests the
   checkerboard test and provides instructions.
4. Review the range, positions, frame count and disk space. **Run scan** starts a
   simulated scan. **Review scan…** on real hardware asks for approval after the
   physical fixture and safety checks. A completed analysis is available under
   **Results**. **STOP** stays available while the worker is active. On real hardware the
   **Physical fixture and safety checks** are always shown; tick them before every run.

Use **Other run options** to select **Quick check**, run a motion-only dry run,
or load a scan recipe. Centering and scouting are under **Optional centering and scouting**.

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

Each command writes the artwork and `<stem>_layout.json` together. The UI generator exposes
visible field dimensions and selects its new JSON for the next run. **Generate both charts…**
exports the MTF target and lens checkerboard side by side, with a Siemens star below them.
Choose **A4 PDF** or **SVG** when saving. The PDF switches to landscape if needed and
preserves actual size. For sheets too large for a single A4 page, save an SVG and use larger paper.
The equivalent CLI command adds `--with-checkerboard`; use a `.pdf` output to get A4.
For other generator
options use the CLI, then **Use existing layout…**. The default is 31 slanted squares
(5 × 7 grid with empty corners) and four unique `DICT_4X4_50` markers, IDs 0/1/2/3.
These are optical markers, separate from tactile plate cones F1–F3 and slicing manifests.

The visible width and height follow from the lens and the working distance. With a
loaded `lens.json` they come from its measured intrinsics; otherwise from the nominal focal
length and pixel size (equidistant fisheye estimate, defaults: the 3.2 mm Lensation
B10M3218S123C on the IMX412 in `machine_settings.toml` `[focusfield]
lens_focal_mm` / `pixel_size_um`, editable in the panel). **Enter the visible field by hand**
overrides both. Print at 100%, measure the 10 mm scale bar and target flatness, and measure
field of view at the actual working distance. The nominal field is not a camera calibration. Marker visibility, local edge size, printing quality and distortion must be
checked across the complete scan. For a distorted lens, use a measured lens profile.

### Calibrate and measure on the combined sheet

1. Print at **Actual Size** (100%), verify the 10 mm scale bar, and tape the sheet flat to the bed.
   Center the Siemens star at the planned working distance. Adjust the lens until its
   blurred center is smallest in the camera image, then lock the focus.
   Complete the physical fixture and dry-run checks, including a
   verified XY clearance envelope across the entire sheet and selected Z range.
2. In **Prepare the target sheet**, load the measured optical XY mapping. Use
   the printer controls and camera preview to center the checkerboard once, then select
   **Record checkerboard XY**. For automatic transfer after calibration, also center
   the MTF panel and select **Record MTF chart XY**.
3. Choose **Calibrate lens…** or **Calibrate lens and review scan…** and approve the printer motion. The app
   samples multiple XY positions and uses the near, middle and far depths of the
   selected sweep, with the lens focus fixed. It fits a fisheye model using the
   printed square spacing and measured changes in Z.
4. When the held-out views pass the 3-pixel limit, the app saves and loads
   `lens.json` in a new lens folder under the Focus-Field runs folder. If you recorded
   the MTF position, it moves there and checks the chart with the correction applied.
   A passing check opens the usual scan review for approval. Otherwise, center the MTF
   panel and select **Check chart** when ready. STOP cancels the active operation.

### Calibrate the lens from saved checkerboard images

Use a flat, rigid checkerboard with known square spacing. Lock the lens and use the
same focus, camera, resolution, crop, orientation and binning as the MTF capture.
Save full-size RGB or monochrome images from that setup. For a 20 mm experiment, take
the calibration views close to that working distance. Vary the board's tilt and position
so the inner corners sample the center and all sides of the usable field. Keep the
entire checkerboard visible in each shot. Collect at least eight fit images and two
separate validation images, including views near the edge of the field.

This example uses a board with seven by five **inner corners** and 5 mm squares.

```sh
python -m focusfield calibrate-lens --camera-serial CAMERA_SERIAL --corners 7 5 --square-mm 5 \
  --images calibration/*.tif --validation validation/*.tif --output lens.json
```

The command reads saved images and reports the fit RMS and each validation view's
RMS. It uses the standard OpenCV pinhole model with radial and tangential distortion.
The command requires each validation view to fit within 3 native sensor pixels RMS.
**Lens calibration help…** in **Check the chart** explains automatic calibration and gives the manual command for the current
platform, including PowerShell on Windows. Use **Load lens calibration…** in the
same step, then run **Check chart** on the flat MTF target. The app checks the
camera serial and native image size.
If using a recipe file, put `lens_calibration = "lens.json"` in its top-level settings,
with the path relative to the recipe. The run snapshots the profile for offline analysis.

An existing OpenCV pinhole calibration can be imported as JSON with `camera_serial`,
`image_size_px` as `[width, height]`, a 3×3 `camera_matrix`, and a flat `dist_coeffs`
array in OpenCV order `[k1, k2, p1, p2, k3]`. The last coefficient is optional.

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
| `motion_safety_verified` | Independently verified full fixture/housing/nozzle clearance and mechanical behavior |
| `camera_pipeline_verified` | Actual camera format, dimensions, CFA, ROI/binning, linearity and processing verified |
| `verified_pixel_format` | Optional verified native capture format used automatically by the physical Measurement preset |
| `frame_timing_verified` | Supervised verification that restart/discard accepts only post-settle exposures |
| `wd_reference_record` | Immutable JSON record of two independently measured WD/Z positions, with date, method, lens plane and operator |
| `sfr_reference_verified`, `sfr_validation_id` | Separate investigator attestation of same-raw-image reference comparison; needed for measurement validity, not merely acquisition |

The mapping is `WD = reference_wd_mm + reported_Z - reference_z_mm`: increasing Z lowers
the Core One bed. Verify this sign on the actual fixture. The tactile `[camera] mm_per_px`
and unmeasured sensor offsets are not used as optical calibration.

All three physical verification flags ship **false**. A supervised **motion-only dry run**
requires an enabled/measured fixture, verified mechanical clearance, measured WD reference,
safe travel/minimum-WD bounds, firmware/STOP and Z-direction checks, idle-printer checks and
explicit operator approval. It neither creates nor opens a camera, and does not require
camera pipeline/timing or lens/chart acquisition checks. A successful motion-only run
still acquires no optical data and never marks a measurement valid.

**Image-acquiring motion operations (scans, centering and scouting)** additionally require camera pipeline and
frame timing verification, lens/chart checks and a completed collision-free dry run.
The motion adapter enforces mechanical prerequisites; the controller enforces the
operation-specific acquisition prerequisites. Older enabled profiles remain blocked
until the applicable checks and the measured WD record are supplied.
Setting flags is an operator attestation, not something inferred from a successful software test.
The software does not perform those physical verifications or authorize unattended use.

### Record the physical WD reference (no automatic motion)

Configure the independently measured fixture bounds and identities first, with motion disabled.
Use **Record measured WD reference… (no motion)** to enter reported printer Z and separately
measured WD at two supervised positions. Specify the lens reference plane, the chart's
patterned surface/thickness/orientation, measurement method, uncertainty and operator.
The second pair must verify positive unit-slope WD/Z behavior within twice the stated
uncertainty. A marker size or focus peak is not an independent distance measurement.

Save a new JSON filename; records cannot be overwritten by this procedure. The selected
machine profile receives the reference, calibration ID and absolute record path, and
`enabled` is explicitly set **false**. Other mechanical/camera/timing checks remain separate.
Changing WD calibration requires a new preview and review of physical bounds before enabling.
The record is copied into every physical scan/setup manifest for reproducibility.

### Verify working distance with the camera

**Check chart** estimates pose from the 16 outer corners of markers 0–3. The
camera profile must match the camera serial and native frame dimensions. The
pose fit uses the measured pinhole or fisheye model and checks both planar
solutions, positive depth, tilt and corner reprojection in native sensor
pixels. BayerRG uses the existing G1 lattice with a two-sensor-pixel pitch and
a one-pixel horizontal origin offset. The marker homography still locates the
MTF regions. SFR samples still come directly from the captured image.

The fitted plane in camera coordinates has a unit normal `n` and contains
`t`, the chart origin in camera coordinates. The optical-axis intersection
from the camera projection centre is `dot(n, t) / n.z`. The perpendicular
distance is `abs(dot(n, t))`, and tilt is `acos(n.z)`. The signed optical
reference offset is the Z coordinate where the physical front-lens reference
plane intersects the optical axis, with positive Z toward the chart.
**Optical WD = axial distance − offset.** A camera profile alone supplies only
the projection-centre distances. Reported printer WD comes from the existing
measured fixture reference and a read-only position query. Both WD values need
the same reference plane and target surface definition for a comparison.

#### Measure and validate the optical reference

1. Use the IDS U3-38L1XLS-C and Lensagon B10M3218S123C in their final fixture.
   Lock focus and calibrate the lens at the intended close distances, with
   held-out calibration views covering the field. Use the native frame size
   and the intended acquisition format.
2. Measure the printed outer edges of all four markers and their positions
   with a calibrated instrument. Check the chart's flatness and verify its
   printed scale. Use a copy of the layout with measured `top_left_mm` and
   `size_mm`, using the same coordinate system as the MTF regions. If the
   print has appreciable skew, unequal axis scales or non-square markers,
   replace the print. The layout represents each marker as an axis-aligned square.
3. Identify the physical plane and patterned surface used by the existing WD
   reference. With a stationary, near-normal chart, independently measure
   several gaps from that plane to the patterned surface at the optical-axis
   intercept using suitable calibrated metrology. Use Check chart with the
   lens profile to obtain the corresponding camera-centre axial distances.
   The difference between each axial distance and independent gap estimates
   the offset. Record its sign, uncertainty, method, instrument, date and
   operator. Nominal focal length, viewing angle and mechanical dimensions do
   not determine this offset.
4. Freeze the lens, layout, detection mode and offset. Capture repeated,
   stationary images at separately verified gaps near 18, 19, 20, 21 and
   22 mm, including a case where printer WD reads 20 mm and independent
   metrology shows 18 mm. Cover the intended tilt and lateral offsets and
   include low-contrast and defocused frames. Use independently measured
   optical-axis gaps at the tilted positions. Report signed error (bias),
   absolute error, repeatability, detection/failure rates, and the total
   optical uncertainty including the offset and independent metrology. Retain
   the validation data separately from the images used to determine the offset.
5. Compare the two values of `optical_refine_corners` on the same frames,
   checking rejection rate, reprojection and distance error against ground
   truth. Subpixel mode uses the successful detection image (including the
   existing lens-rectified fallback) and maps the corners back to native
   coordinates. Choose the mode supported by the measured results. The
   default preserves the original corner detector. A low pixel residual on
   its own cannot establish metric accuracy. Uniform print-scale error and
   some errors in intrinsics can yield a good reprojection fit at the wrong
   distance.

#### Load the measured record

Get fingerprints for the exact lens and marker geometry from the repo.

```sh
python - <<'PY'
from focusfield.chart import load_chart
from focusfield.lens import LensCalibration
from focusfield.optical_distance import lens_fingerprint, marker_geometry_fingerprint

print(lens_fingerprint(LensCalibration.load("lens.json")))
print(marker_geometry_fingerprint(load_chart("measured_layout.json")))
PY
```

Save an `optical_reference.json` with the following fields. The numbers
below illustrate the schema. Replace them with measured values and both
fingerprints with the generated strings.

```json
{
  "offset_mm": 1.25,
  "uncertainty_mm": 0.08,
  "lens_reference_plane": "Exact description from the measured printer WD reference",
  "provenance": "Offset record: method, instrument, date, operator and data location",
  "lens_sha256": "<lens fingerprint>",
  "marker_geometry_sha256": "<marker geometry fingerprint>",
  "pixel_format": "BayerRG8",
  "refine_corners": false,
  "validation": {
    "checks_mm": [[18.00, 19.29], [20.00, 21.24], [22.00, 23.21]],
    "uncertainty_mm": 0.20,
    "max_tilt_deg": 5.0,
    "max_axis_offset_mm": 4.0,
    "max_reprojection_rms_px": 2.0,
    "provenance": "Independent validation record and uncertainty budget"
  }
}
```

Each `checks_mm` pair is `[independent physical WD, fitted camera-centre axial
distance]`. Use at least two distinct gaps spanning the measured operating
range. The range, tilt, lateral distance of the axis intercept from the marker
field centre, and reprojection limit gate absolute WD. The total validation
uncertainty must cover the offset uncertainty and the residual of every check.
Without matching and applicable validation, Check chart reports the
projection-centre distance when pose is available.

Add these top-level options to a copy of your scan recipe before any TOML
table headers. Paths are relative to the recipe. Load it through
**Other run options → Load recipe…**, select the same
capture format as the record, and run **Check chart**.

```toml
chart_path = "measured_layout.json"
lens_calibration = "lens.json"
optical_reference = "optical_reference.json"
pixel_format = "BayerRG8"
optical_refine_corners = false
measure_optical_distance = true
# Optional minimum warning threshold in mm, chosen from physical validation.
# optical_discrepancy_threshold_mm = 0.5
```

The warning threshold is the greater of the configured value and the sum of
the validated optical and printer WD uncertainties. Verify that an intentional
2 mm mismatch reliably exceeds this threshold before relying on the warning.
`measure_optical_distance` opts scans into per-frame verification. Check chart
always attempts optical verification when a lens profile is loaded. A scan
warning flags a potentially invalid measurement in the manifest and report.
The feature adds diagnostics only. Motion and focus-curve distances still use
the measured printer reference.

The automated tests use synthetic images and corner coordinates. Absolute
accuracy, reliable detection of a 2 mm difference, and the best refinement
mode for this camera and lens still require physical validation.

### Optional physical XY centering

**Check chart** reports the projected chart-field center,
the four marker corners/centers, reprojection RMS and center error in original sensor pixels.
If marker detection fails, the preview lists decoded and missing IDs. Frame each complete
black marker border with its light margin. If all four IDs decode but reprojection exceeds
the displayed limit, check that the printed chart matches the loaded layout and lies flat.
Curved rows or columns on a flat chart indicate distortion that a homography cannot model.
Without an optical XY profile, error is relative to the image midpoint and is diagnostic only.
Centering can be selected when the preview shows an alignment error and a valid XY mapping is loaded.

An optical XY profile is a separate JSON measurement, loaded with **Load measured optical
XY mapping…**. It must map small **printer XY changes to image-center changes**, including
axis reversals/nonorthogonal axes, at the actual working Z and image configuration. The
correction solves `J * delta_machine = -error_image`. Never copy tactile `mm_per_px`.
Calibrate from supervised small known displacements; fit/validate the local mapping and
its residual/uncertainty over a limited image radius. A wide-angle/global image scale is
insufficient. No mapping-calibration movement is performed by this software.

Measure the XY Jacobian separately from the lens profile. Both measurements should use
the actual lens and imaging configuration. The XY mapping still uses chart-center errors
in native sensor coordinates.

Example profile structure (illustrative numbers; **not a physical calibration**):

```json
{
  "calibration_id": "replace-with-measured-id",
  "fixture_id": "replace-with-fixture-id", "camera_serial": "replace-with-serial",
  "measured": false, "clearance_verified": false,
  "jacobian_px_per_mm": [[-16, 3], [2, 16]],
  "optical_center_px": [2028, 1520], "image_size_px": [4056, 3040],
  "valid_radius_px": 40, "uncertainty_px": 0.5,
  "residual_px": 0.5, "max_residual_px": 2,
  "safe_x_mm": [0, 0], "safe_y_mm": [0, 0], "safe_z_mm": [0, 0],
  "housing_radius_mm": 0, "housing_below_nozzle_mm": 0,
  "target_height_mm": 0, "clearance_margin_mm": 0,
  "method": "", "operator": "", "measured_at": ""
}
```

All bounds are machine coordinates. The XY rectangle represents an independently checked
clear space, shrunk by the complete housing/nozzle envelope radius. Printer travel limits
are checked independently. At the current Z, require
`Z - housing_below_nozzle_mm - target_height_mm >= clearance_margin_mm`, as well as both
the fixture's Z limits and the XY calibration's safe travel Z interval. A rectangular
envelope must be conservative for **every point of the straight travel segment**, not
only its endpoints. Include mounts, sensor/nozzle housing, obstacles and chart substrate.

After a successful preview, **Center chart…** asks the operator to approve the bounded
physical operation. Defaults: at most 8 iterations, 0.25 mm per step, 2 mm total travel,
1 mm/s and 2 sensor-pixel convergence tolerance. These are setup control limits, not
claims of stage accuracy. The runner re-detects after every settle, verifies XYZ readback
and the observed mapping response, and stops on lost markers, clipped/poor edges,
non-improvement, residual mismatch, budget exhaustion or STOP. It does not raise/lower Z,
home, or make a recovery move. Initial/final errors, XYZ, frames, mapping and operator
approval are saved in the setup manifest. Test the simulator and small supervised steps first.

### Optional bounded coarse scout

**Scout focus interval…** is available after a successful chart check. Its dialog
suggests a short Z interval around the nominal WD within the measured fixture limits.
Review the position count and adjust the bounds, coarse step and fine step in the dialog.
Widening to the full verified interval is optional and its maximum travel and position
count are shown before approval. The scout snapshots a
separate `ScoutRecipe`, not the final acquisition recipe. Default coarse step 0.1 mm,
fine step 0.025 mm, one full-resolution frame/position, maximum 101 positions. The initial
interval must contain at least five positions, and all commands have 0.001 mm precision.

The runner first requires all four chart IDs, full-field visibility and acceptable
reprojection at the current position. Initial slanted edges may be defocused/unusable:
the scout retains those frames/flags and searches only inside the verified mechanical
envelope. At each position, only edges that pass the unchanged strict MTF checks
contribute metrics; missing regional metrics are never filled or treated as focus peaks.
Each region still needs at least five usable positions with two on either side of its peak.

An unbracketed curve or insufficient usable edge samples can trigger one extension to
the already measured fixture's full safe Z envelope. This extension is included in the
operator's approval and total position budget. Flat/noisy usable curves, lost markers,
clipping, image-border failure or excessive reprojection stop the scout. No fine scan
is proposed without strict center/peripheral peak coverage; a boundary/outside peak
never authorizes travel beyond that envelope.
There is no unbounded search or image-derived collision-clearance inference.

Every required regional peak needs two coarse positions on each side. The numerical
defaults reject metric spans below 0.02, local-fit RMSE above 0.04 and fit uncertainty
above half a coarse step; these are configurable scout usability gates, **not lens
pass/fail specifications**. The known simulator's cusp-like coarse peaks have RMSE up to
about 0.031. Validate different optics/noise against independent data before changing gates.

The proposed fine interval surrounds all required peaks with at least two fine samples
on either side, inside both measured and already sampled bounds. Widely separated peaks
produce an explicit **broader scan required** result. Best-focus WD estimate, statistical
fit uncertainty and absolute WD calibration uncertainty remain separate. The proposed
recipe **retains the user's requested nominal WD**, normally 20 mm; scouting never
changes it to the interval midpoint or a focus peak. When the nominal plane lies in
the proposed interval, the fine grid is anchored to that command so it is sampled.
If that command is not representable at the driver's 0.001 mm Z precision with the
measured WD reference, the proposal fails explicitly instead of rounding the nominal
measurement plane. Verify the reference/command; software must not invent calibration.
If the focus interval excludes it, `separate_nominal_plane_required=true` is saved in
the proposal/recipe. The UI displays a persistent warning that a separate measurement
at the requested distance is needed; no extra move outside the proposed bounds occurs.

The scout range appears as a draft beside the current range. **Review scout range**
reviews that draft with the current capture settings and requires a separate physical
approval. The manual or centered range stays available. Setup history and the selected
proposal are linked into the acquisition manifest.

Before a physical scan, independently verify mechanical clearance, full travel, stage
repeatability, firmware, STOP, fixed lens focus, chart scale and exposure. The UI requires
explicit setup checks. Select **Motion-only dry run** and **Run dry run** under supervision,
then confirm the completed collision-free dry run before acquiring images. The initial Z
must already be inside the validated interval.

Unchecking **Simulation** selects **Measurement**. Use a native format verified
for the camera, either from `verified_pixel_format` or from the measurement recipe when
the fixture does not specify one. **Quick check** uses RGB8, one frame per position
and one sweep. Measurement retains the loaded recipe's capture counts and camera
settings. Alternate sweep direction is available with two or more sweeps. The chart
check and scan use the selected preset's format, exposure and gain.

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

`host_monotonic_ns` is the metadata-creation time in the host monotonic clock domain;
`timestamp` is host UTC. Host clocks can return equal adjacent ticks, particularly in
fast Windows mocks. Equal timestamps are permitted only with ordered unique
`capture_sequence` values; backward times and samples older than the post-settle fence fail.
Accepted native `device_frame_id` values must increase, including discarded samples.
Duplicates, reordered/queued frames and counter resets fail; gaps are recorded rather
than inventing exposures. A counter reset requires a fresh run. The host sequence is
process-local and **not** a device exposure counter.

Restart/discard completion and the last discarded device ID are saved with every
accepted adapter frame. Restart failure is fatal. Device timestamps/units and their
unsynchronized clock domain are saved where available; the driver does not assert that
they denote exposure start. Host arrival, a new ID or restart alone does not prove
exposure timing. Real motion requires the separate `frame_timing_verified` attestation
from a supervised timing experiment. Preview cancellation waits for the current camera
call (frame timeout 5 s), then the worker closes the camera; STOP/window-close never
close it concurrently with an active read.

### Camera-only evidence capture

On the supervised camera host, explicitly run (this opens a camera but never a printer):

```sh
python -m focusfield camera-check --output camera_evidence --pixel-format BayerRG8
```

This records native TIFFs before/after restart plus sequence checks, dimensions, payload,
depth/CFA, intensity range and format/readback metadata. IDS inventory enumerates available
GenICam formats and reads ROI, binning/decimation, reversal, black level, gamma/sharpening,
gain and auto-exposure controls. Missing controls remain `null`/unknown, never assumed off.
Vendor-grouped packing is listed in inventory but **not decoded**. Unpacked >8-bit IDS
formats use `get_numpy_2D_16()`: the vendor's `get_numpy_2D()` is an 8-bit view, as confirmed
by inspection of the [IDS peak IPL bindings](https://pypi.org/project/ids-peak-ipl/) 1.18.0.
Raw Bayer requires even ROI offsets and no axis reversal; binning/subsampling is rejected.
This is a candidate decoder support matrix, not verification on the U3-38L1XLS-C.
Inspect actual native samples/known targets and document the processing/timing experiments
before setting verification flags. Evidence capture never sets those flags automatically.

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

The default marker reprojection limit is 3 **original sensor pixels**, independent of
image size. With a lens profile, registration fits the chart in corrected coordinates
and projects the errors and square search positions back into the original frame. If
the first marker detection fails, only the marker detector retries on a rectified image.
The chart must still pass marker and geometry checks. The SFR analysis reads the
original samples and its local straight-edge and slant checks still apply.

### Separate completion and measurement validity

`COMPLETED` describes the operation lifecycle, not optical success. Manifests, analysis
JSON and HTML expose `acquisition_complete`, `analysis_complete`, `qa_passed` (analysis),
`measurement_valid` and explicit rejection reasons. A full acquisition with zero valid
fits remains preserved but fails center/peripheral QA and is never a valid measurement.
Aborted/failed operations always have `measurement_valid=false`.

The versioned recipe snapshots a `[quality]` table. Defaults: contrast >=0.1, normalized
edge span >=0.05, clipped samples <=2%, edge angle 2–12 degrees, straight-edge residual
<=0.8 analysis pixels, marker RMS <=3 sensor pixels, usable field frames >=90%, at least
two valid edges, including both image orientations, at the center and at least four
peripheral regions **in every sweep**.
Each required focus peak must be bracketed. These are data-usability criteria; no
minimum acceptable lens MTF is invented. The center/peripheral squares are selected
from actual chart geometry, including charts with intentionally empty corner positions.

Even when data QA passes, synthetic data, provisional RGB8, missing physical verification
or an unverified independent SFR reference keep `measurement_valid=false`. Quantitative
physical validity requires investigator attestation of raw processing, WD/timing and
same-image reference agreement; this is not ISO certification.

### Independent reference comparison

Use [SFRMAT5](https://www.imaging.org/IST/IST/Standards/Digital_Camera_Resolution_Tools.aspx)
or another recognized tool on the **same linear raw pixels**, ROI and orientation. Export
a JSON containing `tool`, `tool_version`, `settings`, `image_sha256`, `pixel_format`,
`bit_depth`, `roi_sensor_xywh`, `horizontal`, `frequencies_cpp`, `mtf`, and
`fixed_frequency_cpp`. Frequencies must be cycles per **original sensor pixel**, including
Bayer pitch 2. Use the entire curve from zero frequency through an MTF50 crossing.

```sh
python -m focusfield compare-reference raw.tiff external_sfr.json --output comparison.json \
  --mtf50-relative-tolerance 0.08 --fixed-mtf-absolute-tolerance 0.035
```

The investigator must choose comparison tolerances; the example matches analytic
Gaussian software fixtures and is **not** an optics acceptance specification. The tool
verifies the image checksum, native sampling lattice and ROI bounds, compares MTF50
and fixed-frequency MTF without resampling, and saves numeric errors/provenance.
It never automatically attests physical validity. Current automated fixtures cover
Gaussian blur, native Bayer sampling, mild perspective tilt, strong barrel-distortion
rejection, missing corners, clipping and asymmetric field coverage. No real raw image
plus SFRMAT5/Imatest result was supplied, so recognized-tool agreement on actual hardware
remains an explicit validation gap.

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
values come only from frames commanded at the requested nominal WD. They are not
interpolated from neighboring distances or replaced with a peak/midpoint value.
Missing nominal-plane acquisition leaves those CSV/map values empty and sets
`nominal_plane_measured=false`, `separate_nominal_plane_required=true` and explicit
measurement QA rejection reasons. The report also requires usable center/peripheral
edges at the nominal plane. A focus-only sweep may finish successfully while the
requested 20 mm measurement objective remains incomplete. Reported actual Z and
absolute WD calibration uncertainty still apply; a 20 mm command does not prove an
exact physical 20.000 mm distance.
The fitted plane is labelled **apparent tilt**; subtracting it does not isolate intrinsic
lens curvature. Optical distortion, flatness, chart quality, camera/bed alignment and
sensor processing remain contributors to measured system performance.

## Files and offline review

Opt-in scans include an `optical_distance` record for each frame in
`manifest.json`, with the printer and optical values, comparison status and
pose. `config.json` snapshots the lens model and reference record used.
Analysis writes `working_distance.csv` with per-frame distances and quality
and links it from the HTML report. Offline analysis can calculate missing
optical records for an opted-in scan from its stored frames and calibration.

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

Before opening devices, storage preflight estimates uncompressed native pixels plus 2%
and 4 KiB TIFF overhead/frame, 64 MiB report allowance and 256 MiB reserve. Default expected
dimensions are 4056×3040, configurable in the recipe; actual capture size is checked again
before motion and space is checked while writing. At 126 full-size 16-bit monochrome
frames, pixels alone occupy 3.107 GB (decimal), before overhead/reports. RGB8 requires
three channels. Insufficient space names the needed/free amount and selected log folder.

## Validation

Original PR CI at `139eab0`: Ubuntu passed; Windows had **210 passed, 6 failed, 31 skipped**
from equal-clock freshness rejection. Both macOS jobs inherited the asynchronous Tk startup
test failure also present on the base branch. That startup test is corrected in a separate
test-only commit by running the Tk event loop until the mocked background gauge starts;
printer/force startup behavior is unchanged. Current results and CI links are recorded in
[the implementation review](focusfield-review.md). New tests cover recipe/bounds/clearance, absolute motion and no homing,
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
