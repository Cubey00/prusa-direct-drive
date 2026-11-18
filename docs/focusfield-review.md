# PR #2 implementation review and corrections

Reviewed against `development-bep-groep` at `086dc5f8f7a39e26a480f8c361eebc72a5c566b4`
and PR head `139eab0a4622b3fb98453fc02ee70717df01b22f`. Both remote refs were fetched and
confirmed unchanged before editing. Work remains on the existing `feat/focusfield-mtf`
branch in [PR #2](https://github.com/Cubey00/prusa-direct-drive/pull/2); no merge or
physical printer movement was performed. The attached review was used as requirements
and evidence to investigate, rather than assuming its hardware-dependent claims were verified.

## Follow-up review: motion-only preflight and the nominal measurement objective

The second review at `05ca5cc` confirmed two workflow defects and a scout robustness issue:

| Finding | Correction / status |
| --- | --- |
| Camera verification blocked a motion-only dry run | Mechanical fixture/reference/clearance validation is separate from camera pipeline/timing validation. Motion-only controllers do not create, open, cancel or close a camera. Measured bounds/minimum WD, firmware/STOP, Z sign, printer state and explicit operator approval remain mandatory. Image/setup operations still require all acquisition checks. Defaults remain disabled. |
| Scout replaced the requested 20 mm plane with a midpoint | Fine proposals preserve `nominal_wd_mm`. Their grid is anchored to that requested command when it is inside the fine interval. An excluded nominal plane is retained with `separate_nominal_plane_required=true`, a persistent UI warning and no extra scheduled movement. Reports only use actual requested nominal-plane commands; missing acquisition or unusable nominal edges prevents measurement validity, even if focus fits succeed. |
| Defocused initial edges could prevent scouting | Initial chart identity/full visibility/reprojection remain mandatory. Unusable slanted edges can be retained/skipped while searching the approved bounded Z interval. Every reported regional peak still requires five strictly usable positions and two samples on each side; lost markers, clipping, border errors and unsafe poses remain fatal. One budgeted extension within the already verified envelope can also handle insufficient usable samples. No artificial metrics or best-focus distances are inserted. |
| Real-lens XY/distortion calibration remains external | **Unresolved hardware/calibration work.** There is no in-app Jacobian/intrinsic/distortion measurement procedure. The operator must supply a measured local mapping and validate the actual wide-angle lens. The registration threshold remains blocking and original SFR pixels remain unwarped. Passing simulation/CI is not that physical validation. |

The follow-up changes are localized to `machine_settings.py`, `focusfield/config.py`,
`focusfield/controller.py`, `focusfield/hardware.py`, `focusfield/chart.py`,
`focusfield/setup.py`, `focusfield/reports.py`, `pathprogrammer/ui/focusfield_panel.py`,
their existing tests and these documents. Analysis semantics advance to version 0.2.1.
No dependency, default physical enablement, automatic calibration motion or PR merge is added.

## Confirmed regressions and corrections

| Issue | Evidence / correction | Expected behavior |
| --- | --- | --- |
| Windows fresh frames falsely rejected | Original [Windows CI](https://github.com/Cubey00/prusa-direct-drive/actions/runs/44075563124) had 6 optical failures with `Stale frame received after motion`. A deterministic constant-clock test reproduces the strict-equality problem. | Equal host ticks are accepted with ordered capture sequences; backward times, old receive fences, duplicated/reordered device IDs and resets still fail. No artificial timestamp increments. |
| Native high-depth IDS view was wrong | Inspected vendor `ids-peak-ipl` 1.18.0 Python bindings: `get_numpy_2D()` is an 8-bit view, while `get_numpy_2D_16()` is the explicit 16-bit view. | Native unpacked Mono10/12/16 and BayerRG10/12 use the 16-bit API, copy samples, and validate dtype/depth/shape/range. Known values including full scale are tested. No silent cast or packed-format guessing. |
| Freshness relied too much on host receipt | Host receipt is not sensor exposure start; restart/discard was not auditable and IDs were not checked. | Adapter and controller ordering checks, restart command completion, discard-fence provenance and native clock domains are recorded. A separate physical timing attestation is required before image operations. Counter resets fail conservatively. |
| Preview STOP did not cancel preview | Preview had no cancellation event. | STOP and window-close cancel preview, wait for an active camera call to return, and close/release the camera in its worker. Disconnect and close/cancel paths are tested. |
| A completed scan could have no usable optical field | Lifecycle completion had no separate QA/validity status. | Acquisition completion, analysis completion, data QA and measurement validity are separate; zero/invalid center/peripheral fits produce explicit rejection reasons while preserving originals. |
| Geometry tolerance increased with resolution | A strong synthetic barrel-distortion fixture produced about 9.6 px marker RMS but passed the former 1%-of-frame tolerance. | Default maximum is 3 original sensor pixels, configurable/snapshotted; distortion/missing-marker failures are distinct from bad/low-contrast/clipped edges. No measurement pixels are warped. |
| Optical STOP could wait forever on busy acknowledgements | Existing command waits intentionally tolerate indefinite busy replies. | Only callers supplying a cancellation token interrupt those waits, including M114 readback; STOP can obtain the serial lock and resync/fail cleanly. Default tactile/printing behavior remains intact. |
| Optional metadata could change CSV columns | Per-frame field lists could differ when metadata appeared/disappeared. | One evolving CSV schema is used; an added column rewrites existing rows with consistent headers. The atomic manifest remains authoritative after a crash. |
| Unsupported backend recipe could silently show RGB8 | The combo box lacked some other backend formats. | Loading an unsupported backend recipe fails explicitly before changing the editor. |
| Inaccurate baseline and missing disk estimate | Documentation cited the patch author's earlier baseline and omitted full-resolution storage. | Actual development SHA and platform provenance are documented; declared and actual image sizes are checked before motion, with TIFF/report/reserve allowances and actionable insufficient-space errors. |

## Hardware-dependent unknowns — remain unverified

- No actual U3-38L1XLS-C or printer was used. Actual GenICam format availability,
  payload/ROI, CFA parity, binning/decimation, black level, gamma/sharpening, gain and
  auto-exposure behavior require camera-host inspection and known-image tests.
- Native counter behavior, exposure timing after restart, sensor timestamp meaning and
  clock synchronization require a supervised timing experiment. New IDs/host times
  alone are not exposure-start evidence. Counter resets intentionally stop acquisition;
  the SCC simulated backend resets its counter at stream restart and the camera-only
  evidence utility conservatively rejects that case, retaining partial evidence.
  Do not mark physical frame timing verified based on that simulator.
- Full housing/nozzle/mount clearance, the safe XY rectangle and travel Z, target height,
  mechanical Z sign, backlash/repeatability, STOP mechanics, thermal drift and illumination
  must be verified on the fixture. A numerical command/readback is not metrology.
- A real measured WD record and optical XY mapping must be supplied by the operator.
  Neither is inferred from tactile camera scale, marker size or sharpness.

`enabled`, `measured` and every new physical verification flag still ship **false**.
Saving a WD record explicitly disables physical motion. Image-acquiring motion/setup
operations require their own operator approval in addition to measured calibration and
collision-free dry-run checks. A motion-only preflight requires the mechanical checks
and approval but no camera validation. This implementation did not enable unattended scanning.

## Scientific validation gaps

- Same-real-raw-image SFRMAT5/Imatest outputs were not supplied. A comparison utility now
  verifies source SHA-256, ROI, orientation and sampling units, and records MTF50 and
  fixed-frequency errors against an external curve using investigator-specified
  tolerances. It does not automatically certify the result.
- Automated independent closed-form Gaussian references include mono and Bayer-green
  sampling. Scene fixtures include perspective tilt, strong barrel distortion, lost
  corners, clipping, missing field coverage and coarse-focus recovery/failure cases.
  These establish deterministic software behavior; they do not establish real-camera
  reference agreement or ISO 12233 conformance.
- Only one Bayer green lattice is analyzed, at pitch 2 sensor pixels. The other green
  lattice, color/spectral response, chart edge quality/flatness and wide-angle distortion
  remain measurement limitations. Printing artifacts can dominate high-frequency results.
- Apparent best-focus variation is not isolated Petzval curvature; apparent tilt is
  reported separately. Statistical fit uncertainty, absolute WD calibration uncertainty
  and repeatability are separate, incomplete parts of an uncertainty budget.
- Default QA gates concern data usability. No lens MTF acceptance specification has
  been invented. Synthetic/provisional RGB8/unverified reference results remain invalid
  as quantitative physical measurements, even when acquisition and analysis complete.

## New feature work

1. **Image-space registration diagnostics:** configured field center, individual marker
   corners/centers, reprojection RMS, full-field visibility and center error. Preview
   itself never repositions the printer.
2. **Measured WD setup:** a dialog records two independently measured WD/Z pairs, sign
   and unit-slope consistency, lens plane, patterned-surface/thickness/orientation notes,
   method, operator, UTC date, uncertainty and calibration/fixture/camera IDs. Records
   are immutable; saving never enables motion.
3. **Opt-in XY centering:** local calibrated 2D Jacobian supports reversed/nonorthogonal
   axes. Limited steps/travel/speed/iterations, housing envelope and printer bounds,
   safe travel Z, re-detection/readback, mapping-residual and non-improvement checks,
   printer-state/lease exclusion and asynchronous STOP. No automatic homing, Z clearance
   move or recovery move. Initial/final errors/XYZ and calibration/approval are logged.
4. **Opt-in Z scout:** separate bounded scout recipe; center plus four peripheral metrics
   and raw frames. Unbracketed peaks or insufficient usable samples permit one extension
   within prevalidated bounds and the total position budget. Flat/noisy curves,
   clipped/lost-marker data or peaks outside the full envelope
   fail. A fine-scan proposal spans bracketed regional peaks; broad spreads are flagged.
   Loading it does not start motion. Fine acquisition requires separate approval and
   records setup history/proposal. Estimated best-focus WD never establishes 20 mm.
5. **Camera-only evidence and external SFR comparison:** explicit CLI tools, with no
   printer access. Inventory/evidence remain unverified until investigator review.

## Exact changed file paths and rationale

Paths below are relative to the repository root, `Cubey00/prusa-direct-drive`.

| Files | Change and rationale |
| --- | --- |
| `focusfield/acquisition.py` | Strict native sample/CFA/status contract; coarse-clock-safe ordering; native sequence validation; restart/discard evidence; cancellable camera adapter. |
| `ids_camera.py` | Correct high-depth SDK view, strict readback, inventory/native metadata and command-complete restart; legacy owned RGB8 preview API preserved. |
| `macos_camera.py` | SCC native/frame metadata, configuration inventory, geometry validation and incomplete-frame rejection; legacy owned RGB8 contract preserved. |
| `focusfield/camera_info.py` | Read-only GenICam inventory with unknown controls explicitly retained; reject unsupported sampling geometry. |
| `focusfield/controller.py` | Storage/physical preflight, ordering guard, serialized STOP dispatch, independent completion/validity flags and setup provenance. |
| `focusfield/data.py` | Storage estimate/preflight, explicit initial status and consistent evolving frame CSV columns; atomic manifest and lossless images preserved. |
| `focusfield/reports.py` | Coarse-clock-safe offline validation, configurable original-pixel geometry/edge QA, center/peripheral coverage and explicit invalid-result reasons in JSON/HTML/manifests. |
| `focusfield/quality.py` | Versioned data-usability thresholds and geometry-selected required regions, with both edge orientations required. |
| `focusfield/sfr.py` | Apply snapshotted numerical usability gates without changing the original-pixel SFR path. |
| `focusfield/chart.py` | Detailed center/marker registration, sensor-pixel reprojection limit, ROI border check and robust scout/centering metrics. |
| `focusfield/calibration.py` | Immutable physical WD audit records and local measured XY mapping validation/correction; no hardware connection/motion. |
| `focusfield/setup.py` | Bounded, cancellable setup controller, centering limits, separate scout recipe, safe peak proposals and simulation mapping. |
| `focusfield/hardware.py` | Guarded optical XYZ/XY interface; clearance/bounds and cancellation; simulator XY response. Existing Z-only callers retain compatibility. |
| `gantry_controls.py` | Optional cancellation for acknowledgement/readback waits; default printer/tactile calls retain previous behavior. |
| `focusfield/config.py` | Backward-compatible quality/storage snapshot defaults and stricter physical verification gate. |
| `machine_settings.py`, `machine_settings.toml` | Disabled-by-default verification fields and WD record path; load older profiles; escape saved calibration text/path strings correctly. |
| `pathprogrammer/ui/focusfield_panel.py` | Separate preview/WD/centering/scout controls, explicit physical approvals, cancellable preview, proposal loading/provenance and status distinction; refuse silent format downgrade. |
| `pathprogrammer/ui/optical_setup_dialogs.py` | Hardware-free WD audit and bounded scout forms. |
| `pathprogrammer/ui/main_window.py` | Global STOP reaches optical preview/setup as well as acquisition; existing physical run stop remains available. |
| `focusfield/validation.py`, `focusfield/__main__.py` | Camera-only evidence and exact-ROI reference-comparison CLI commands; no dependency additions. |
| `focusfield/__init__.py` | Analysis version 0.2.1 for explicit nominal-plane acquisition semantics; existing versioned snapshots remain readable. |
| `tests/test_macos_startup.py` | Separate test-only correction of the inherited asynchronous Tk startup race; production startup behavior unchanged. |
| `tests/conftest.py` | Explicit synthetic calibration audit fixture for hardware stubs; no real calibration claim. |
| `tests/test_focusfield_freshness.py` | Equal/coarse ticks, stale/duplicate/reordered/reset/gapped frames, queued old frames, restart failure/discards, cancellation, storage and partial camera evidence. |
| `tests/test_focusfield_setup.py` | Center geometry/sign/nonorthogonality, uncalibrated/unsafe/failed motion, STOP/lost markers/clipping, WD audit, bounded scout success/widening/noisy/flat/outside/broad peaks and proposal-only behavior. |
| `tests/test_focusfield_validation.py` | Inventory, unsupported formats/geometry, strict dtype/CFA, QA coverage/thresholds, distortion/perspective/corners and same-image analytic reference comparison. |
| `tests/test_focusfield_camera.py` | All advertised backend decoder paths: known native values/depths, correct SDK view and SCC standard packed payloads. |
| `tests/test_focusfield_controller.py`, `tests/test_focusfield_config.py` | Coarse-clock integration/offline ordering, storage/physical preflight, concurrent STOP and backward-compatible defaults. |
| `tests/test_focusfield_reports.py` | Zero-fit full acquisitions are explicitly invalid and preserve frames; validate decreasing timestamps separately from valid equal ticks. |
| `tests/test_focusfield_ui.py` | New diagnostics, no preview motion, setup/format gates, physical-preview cancellation/disconnect/window-close and lease cleanup. |
| `tests/test_serial_link.py` | Busy optical motion/readback cancellation permits STOP/resync failure and prevents subsequent dispatch. |
| `docs/focusfield.md`, `docs/focusfield-review.md` | Accurate baseline, workflows, schema/limits, safety/physical gates, timestamp semantics, QA/storage/reference procedure and review/validation gaps. |

## Validation and platform provenance

Original PR run: [44075563124](https://github.com/Cubey00/prusa-direct-drive/actions/runs/44075563124).
Base run with inherited macOS failure: [44069836224](https://github.com/Cubey00/prusa-direct-drive/actions/runs/44069836224).

The inherited macOS test was reproduced independently on unmodified `086dc5f` during the
initial upload. Commit `3095c41` changes only that test, waiting for its mocked asynchronous
gauge startup in the real Tk event loop; the isolated corrected test passed locally.

Local macOS/Python 3.12 uses `requirements-macos.txt`, simulated camera and offscreen Qt:

```sh
QT_QPA_PLATFORM=offscreen PRUSA_CAMERA_BACKEND=simulated python -m pytest -q
```

Localhost access is needed for existing IPC/PrusaLink tests; the sandbox-only run is not
a valid platform baseline. Local full-suite and focused verification results are recorded
with the PR update. Revision `05ca5cc` passed **312 tests with 12 skips** locally,
including the corrected inherited startup test and STOP/provenance/format cases.
The follow-up adds camera-free physical dry-run gates, nominal-plane sampling/reporting,
fractional-reference grid anchoring and registered-but-defocused scout regressions.
Its local full suite passed **338 tests with 12 skips**; the focused follow-up suite
passed **103 tests**. Python compilation and diff whitespace checks also passed.
A subsequent command-precision edge case explicitly rejects an unrepresentable nominal
plane instead of rounding it into a false acquisition claim; the updated scout/setup
suite, including that additional regression, passed **31 tests**.
Windows and Ubuntu are verified by the repository's existing GitHub Actions matrix,
not by claiming a Windows/Linux runtime on this Mac. Current platform results are visible
in [PR #2 checks](https://github.com/Cubey00/prusa-direct-drive/pull/2/checks).

Final-diff whitespace checking and Python compilation are required before pushing. The
full regression suite includes existing slicing, PrusaLink, calibration, path programming,
serial/force/tactile workflows and the new optical fixtures. Physical-camera calibration,
supervised printer motion and recognized-tool agreement on actual raw captures remain
unresolved hardware/scientific work, intentionally blocked from automatic enablement.
