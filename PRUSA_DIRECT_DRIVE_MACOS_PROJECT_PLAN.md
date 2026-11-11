# Prusa Direct Drive: macOS Enablement and Camera Integration Plan

**Project plan / Codex handoff**  
**Prepared:** 11 November 2026  
**Status:** Proposed plan; not an implementation or hardware certification  
**Target platforms:** macOS on Intel (`x86_64`) and Apple Silicon (`arm64`)  
**Main application:** [Cubey00/prusa-direct-drive](https://github.com/Cubey00/prusa-direct-drive)  
**Camera dependency:** [stefberends/scientific-camera-capture](https://github.com/stefberends/scientific-camera-capture)

> **Project objective:** Make the existing Prusa Direct Drive materials-research application usable on modern Macs, including printer movement, loadcell probing, model-based path programming, full-resolution continuous image capture, individual scientific snapshots, and associated experiment records. Reuse Scientific Camera Capture as a separately maintained package; do not rebuild its camera driver inside the printer project.

---

## 1. Executive summary

Prusa Direct Drive has two largely independent parts: (1) the printer/strain-gauge/path-programming application and (2) image acquisition. The Python control code uses largely portable technologies (PySerial, UDP, Tkinter, PySide6), but macOS hardware operation has not been verified. Its current camera code directly imports the proprietary IDS peak Python SDK, contains an absolute Windows output path, and is only partially wired into the GUI.

The separate **Scientific Camera Capture** project already supplies the intended replacement foundation: a dependency-light Python API, simulator, raw SCC recording, concurrent stream snapshots, event markers, reader/repair tools, standard pixel converters, CLI, tests, and a lazy Aravis backend. Its [initial software MVP was merged in PR #1](https://github.com/stefberends/scientific-camera-capture/pull/1) on 11 November 2026. This **does not establish** working IDS USB3 Vision acquisition on either Mac architecture. The camera project's own [implementation status](https://github.com/stefberends/scientific-camera-capture/blob/main/docs/implementation-status.md) and [compatibility checklist](https://github.com/stefberends/scientific-camera-capture/blob/main/docs/device-compatibility.md) explicitly separate software tests from hardware validation.

The recommended strategy has **two parallel validation tracks**, followed by integration:

1. **Camera track:** make `scientific-camera-capture` reliably operate with the physical IDS U3-38L1XLS-C and, later, a second GenICam camera on macOS.
2. **Printer track:** verify Prusa Core One USB serial, UDP telemetry, motion, loadcell calibration, and custom-firmware stop behavior on macOS independently of camera acquisition.
3. **Integration track:** add an optional adapter in Prusa Direct Drive and build one coordinated workflow for imaging and touch measurements.

A project can be considered *software integrated* without being *hardware validated*. Those statuses must remain separate in GitHub, release notes, and documentation.

## 2. Goals and non-goals

### Goals

- Run the **existing** Tkinter manual-control application and PySide6 `pathprogrammer` on Intel and Apple Silicon Macs.
- Preserve existing printer functionality and safety behavior rather than replacing the gantry subsystem.
- Use `scientific-camera-capture` as an **independently versioned dependency**, installed into the application's Python environment, not copied as source into Prusa Direct Drive.
- Discover and configure supported cameras by capabilities, initially the IDS U3-38L1XLS-C, later other USB3 Vision / GigE Vision GenICam devices.
- Offer **continuous full-resolution raw recording** and **individual snapshots**, including snapshots selected from an active recording.
- Preserve sensor data and metadata; do not require RGB conversion to record or save a scientific frame.
- Associate imaging events with the program, point, gantry position, and loadcell trace, with honest timestamp provenance and uncertainty.
- Keep camera use optional: printer control and simulation must still work with no camera connected or native Aravis installation.
- Provide reproducible setup, meaningful diagnostics, automated tests, and a physical acceptance checklist.

### Non-goals for initial macOS release

- Rewriting all of IDS peak or distributing proprietary IDS binaries.
- Guaranteeing 18.2 fps on every computer/cable/SSD before testing.
- Promising scientifically precise exposure-to-force synchronization using host-receipt timestamps alone.
- Implementing an unrelated new GUI, new motion planner, or substantial firmware redesign.
- Supporting every UVC webcam or every vendor-specific industrial-camera transport out of the box.
- Refactoring safety-critical gantry operations merely to make the camera adapter convenient.
- Removing the existing IDS peak code until replacement imaging has been validated and rollback is available.

## 3. Current state of the two repositories

### 3.1 Scientific Camera Capture — available foundation

**Repository:** <https://github.com/stefberends/scientific-camera-capture>

- Package version `0.1.0`; initial PR merged into `main` as a **software MVP**.
- Public API: `CameraManager`, camera configuration/capabilities, `start_stream()`, stream snapshots, events, raw recording, `RecordingReader`, export and repair utilities.
- CLI `scicam`: simulator discovery/inspection, recording, snapshots, diagnosis and camera testing/benchmarks.
- Simulator and automated tests; the project reports **56 passing tests, one hardware test skipped** at its published local checkpoint. Its GitHub CI checks software behavior on macOS and Linux but do not validate camera transport.
- Lazy `Aravis 0.8` backend, with native installation guidance for macOS.
- SCC raw recording with checksums and recovery mechanisms; camera data is captured separately from disk writing.
- Standard pixel formats such as `Mono8`, `Mono16`, `BayerRG8`, `BayerRG10p`, and `BayerRG12p` have software converters and export paths. **IDS proprietary grouped Bayer 10/12-bit conversion remains disabled pending independently verified fixtures.** Raw vendor-format data can still be preserved.
- Simulator reports at full reference-camera geometry are encouraging but **do not prove USB throughput, real sensor frame integrity, or macOS device support**.

**Key references:** [README](https://github.com/stefberends/scientific-camera-capture/blob/main/README.md), [macOS setup](https://github.com/stefberends/scientific-camera-capture/blob/main/docs/macos-setup.md), [architecture/API](https://github.com/stefberends/scientific-camera-capture/blob/main/docs/architecture.md), [integration](https://github.com/stefberends/scientific-camera-capture/blob/main/docs/integration.md), [performance](https://github.com/stefberends/scientific-camera-capture/blob/main/docs/performance.md).

### 3.2 Prusa Direct Drive — available foundation

**Repository:** <https://github.com/Cubey00/prusa-direct-drive>

| Area | Existing implementation | Mac-specific uncertainty / integration work |
| --- | --- | --- |
| Printer transport | `gantry_controls.py`: PySerial, G-code, position, homing, stop | Detect real `/dev/cu.*` device, check permissions/handshake, confirm firmware responses |
| Force telemetry | `strain_gauge.py`: UDP metrics, taring, smoothing, calibration scale | Verify LAN route, printer metrics, packet arrival/timing on macOS |
| Touch algorithm | `touch.py`: contact search, force/depth stop, motion traces | Physical safety and response with custom firmware must be verified independently |
| Manual UI | `GUI.py`: Tkinter jogging, position, gauge, calibration | Tkinter installation/desktop behavior; camera preview currently commented out |
| Path programmer | `pathprogrammer/`: PySide6 3MF display, calibration, programmed touches, simulation/logging | End-to-end Mac smoke tests; camera not yet integrated |
| Imaging | `ids_camera.py` and `imaging.py`: IDS peak SDK, TIFF saving | Replace direct import path with optional camera adapter; remove hardcoded Windows path |
| Build/install | `requirements.txt`; no dedicated release/installer workflow | Reproducible macOS environment, optional camera dependencies, CI and docs |

**Known safety precondition:** Prusa Direct Drive documentation says it depends on a **modified Core One firmware** for immediate `M410` motion stop. That firmware is not currently published in the repository. A Mac port does not remove this dependency; no physical touch experiment should be treated as accepted until the exact firmware and emergency stop/retract path have been verified. See [README](https://github.com/Cubey00/prusa-direct-drive/blob/main/README.md), [gantry_controls.py](https://github.com/Cubey00/prusa-direct-drive/blob/main/gantry_controls.py), and [touch.py](https://github.com/Cubey00/prusa-direct-drive/blob/main/touch.py).

## 4. Target end-to-end behavior

At the end of the project a user should be able to:

1. Install Prusa Direct Drive on a supported Mac and start the path programmer **without any camera installed**.
2. Connect a Prusa Core One through USB and verify serial commands, homing and real positions.
3. Receive and graph loadcell data; test low-risk probing with the verified custom firmware and safety controls.
4. Connect a supported GenICam camera and select it by a stable discovered ID.
5. Inspect actual camera capabilities, configure supported resolution, pixel format, exposure, gain and trigger features.
6. Save a single full-resolution scientific image with metadata; use raw export if colour conversion is unsupported.
7. Start and stop continuous SCC raw recording while the path programmer is running; see recording statistics and any overload/failure.
8. Add imaging actions to measurement points or program settings (e.g., before touching, contact event, after touching) without stopping the raw stream.
9. Finish with one experiment record that links the original path program, touch data, camera recording directory, frames/snapshots, and timestamps/events.
10. Review the results on macOS without requiring a connected camera. Reopen raw recordings and export supported formats later.

**Data contract:** raw frames remain in SCC recording storage. Prusa run logs store references (session/recording IDs, frame/acquisition IDs, event IDs, timestamps and paths) rather than duplicating megapixel image bytes inside JSON.

## 5. High-level architecture and repository boundaries

```text
macOS (Intel or Apple Silicon)
  |
  +-- Prusa Direct Drive (application / experiment owner)
  |     +-- PySide6 Path Programmer (primary integrated UI)
  |     +-- Tkinter gantry calibrator (existing subprocess)
  |     +-- Printer transport: PySerial / G-code
  |     +-- Loadcell telemetry: UDP / force probing
  |     +-- Experiment coordinator and run log
  |     +-- CameraAdapter (NEW, optional)
  |                     |
  |                     v
  +-- scientific-camera-capture (independent installed package)
        +-- CameraManager / capability discovery
        +-- Simulated and Aravis backends
        +-- Stream / snapshots / events
        +-- SCC raw writer / reader / export
                          |
                          v
              Aravis + PyGObject + USB3 Vision camera
```

### Responsibility split

| Repository | Owns | Does **not** own |
| --- | --- | --- |
| `scientific-camera-capture` | Device discovery, transport, frames, recording, snapshot requests, raw formats, camera errors and diagnostics | Printer motion, physical probe safety, specimen or experiment semantics, GUI business logic |
| `prusa-direct-drive` | Camera/printer configuration, workflow orchestration, GUI controls, touch/image event mapping, experiment metadata, platform UX | Vendor USB3 Vision implementation, raw SCC format internals, Bayer packing decoder |

**Integration principle:** build a thin `CameraAdapter` and `ExperimentImagingCoordinator` in Prusa Direct Drive. Depend on a tagged or otherwise pinned tested camera-package revision. Keep native imports lazy so that Mac users can operate printer-only or simulator-only functionality without Aravis installed.

**Process ownership:** the current visual calibrator launches `GUI.py` in a separate process and controls the printer serial/UDP ports. Keep the camera owned by **one** process, preferably the path programmer during runs. Do not open the same camera separately in both GUIs. Avoid passing large frames through IPC; pass only lightweight events/commands or frame IDs if cross-process interaction is later needed.

## 6. Workstreams and milestones

The milestones below are **deliverable gates**, not calendar promises. Camera and printer validation can proceed in parallel. Integration must not be declared hardware-complete until both have passed.

### Milestone M0 — Confirm baseline and freeze interfaces

**Goal:** Establish reproducible reference points before any integration edits.

**Work:**
- Record the current head commit and version of both repositories; establish a tested camera-package revision for integration.
- Write a short Mac support matrix: Intel/arm64, Python version, macOS version, USB topology, camera firmware, printer firmware, storage device.
- Set up a basic development environment for both repos; run their simulator/unit tests independently.
- Decide the supported initial workflow: PySide6 path programmer as the primary integrated UI; legacy Tkinter camera integration can follow later.
- Agree on error and session state behavior for no camera, camera disconnected, storage full, and user STOP.

**Deliverables:** baseline test report, requirements matrix, GitHub issues grouped under milestones.  
**Exit gate:** both codebases can be installed/tested independently; no assumptions about native hardware are presented as verified.

### Milestone M1A — Native Mac camera validation (camera repository)

**Goal:** Prove the reference camera is actually accessible through Aravis on a Mac.

**Work:**
- On each architecture, install matching Python, Homebrew Aravis and PyGObject; use [documented diagnostics](https://github.com/stefberends/scientific-camera-capture/blob/main/docs/macos-setup.md).
- Use `scicam doctor`, `discover`, `inspect`, and `camera-test`; save device identity, GenICam feature inventory, XML and native binding signatures.
- Confirm the camera's **actual advertised** full resolution, pixel formats, exposure/gain ranges, frame rate, trigger modes and USB transport properties.
- Capture original raw frames and verify payload length/stride; check frame IDs and timestamp provenance.
- Compare USB sync/async transfer modes only after simple acquisition works; measure actual performance, not just configured fps.
- Perform a sustained 30-minute raw recording with concurrent snapshots, checksums, error accounting, unplug/replug and recovery testing.
- Obtain independently validated sample frames/reference decoding before enabling `BayerRG10g40IDS`/`BayerRG12g24IDS` conversion. Do not block raw capture on this step.
- Repeat core tests on another GenICam camera to validate multi-camera design; this can occur after the initial Mac integration demo.

**Deliverables:** hardware compatibility report per Mac architecture and camera, native backend bug fixes in camera repo, reproducible test instructions, representative permitted fixtures.  
**Exit gate:** at least one Mac/reference camera combination can stream and record full-resolution native frames with explicit integrity/performance evidence. **Full Intel + Apple Silicon support** is claimed only after both are tested.  
**Blocker rule:** if Aravis cannot detect the camera, debug transport independently; do not modify Prusa Direct Drive to work around a driver failure.

### Milestone M1B — Native Mac printer validation (Prusa repository)

**Goal:** Establish the printer-only Mac baseline, without imaging enabled.

**Work:**
- Identify the Core One's `/dev/cu.*` serial device; confirm PySerial vendor-based discovery and document manual override.
- Confirm position readback, homing, small jogs, absolute moves, queued jog behavior and clean disconnect.
- Verify UDP metrics configuration and loadcell traffic on the same host/network; test tare, calibration scale and gauge display.
- Validate the custom firmware's immediate `M410` behavior and the stop/resync/retreat code with safe test conditions before probing a specimen.
- Test `python GUI.py`, `python -m pathprogrammer`, 3MF loading, spatial calibration and simulated runs on both Mac architectures.
- Record any Tkinter, Qt, filesystem, case-sensitivity and permission issues as separate bugs rather than introducing Mac-specific branches throughout the codebase.

**Deliverables:** printer Mac smoke-test checklist, validated command/firmware assumptions, issues for fixes.  
**Exit gate:** printer-only manual operation and simulation run on macOS, with physical movement and force probing individually safety-validated before combined experiments.

### Milestone M2 — Portable application packaging and optional dependencies

**Goal:** Make the printer application installable and usable without camera SDKs.

**Work:**
- Add modern Python package metadata and a supported installation path to Prusa Direct Drive; keep existing launch commands working.
- Introduce optional camera dependencies rather than importing `ids_peak` at application startup.
- Replace hardcoded Windows paths in `imaging.py` and camera benchmarks with `pathlib` and user-selected data directories.
- Document required Python/Tk versions, PySide6, PySerial, USB expectations, native Aravis/PyGObject setup and troubleshooting.
- Add macOS CI for non-hardware tests; print runner architecture. Do not mistake a `macos-latest` runner for proof that Intel and arm64 have both been covered.
- Add launch smoke tests where GUI/headless behavior permits; preserve Windows printer functionality if it remains supported.

**Deliverables:** reproducible Mac install guide, optional camera extra or separately pinned dependency, CI baseline.  
**Exit gate:** printer simulation and manual GUI can start with no IDS peak, no Aravis, and no camera hardware.

### Milestone M3 — Camera adapter in Prusa Direct Drive

**Goal:** Connect the camera package without mixing responsibilities.

**Work:**
- Introduce a consumer-side `CameraAdapter` for device list, open/close, capabilities/configure, preview, single snapshot, record start/stop, stats and event markers.
- Make camera ID and experiment output folder explicit configurable settings; avoid implicit 'first camera' assumptions.
- Implement lifecycle and error-state transitions: disconnected, idle, streaming, recording, failed and closing.
- Use the simulator backend for tests, and select the Aravis backend only when installed/configured.
- Define ownership/thread boundaries: camera operations in workers, UI updates on Qt/Tk UI threads, no synchronous disk work in the UI callback path.
- Preserve an optional legacy `ids_camera.py` path only as a transitional compatibility layer; do not double-own one physical camera.

**Deliverables:** adapter and simulator tests in Prusa Direct Drive; working headless snapshot and recording examples.  
**Exit gate:** application code can record and snapshot with the simulator and cleanly recover from intentional camera/storage failures, without changing printer motion logic.

### Milestone M4 — Manual imaging experience

**Goal:** Let users operate camera acquisition independently from an experiment.

**Work:**
- Prefer the path programmer as the initial camera-control surface; add discovery, model/ID, configuration, raw capture, recording start/stop, status, disk-space and loss/queue indicators.
- Offer a live **best-effort** preview separate from recording, including an informative state when a pixel format has no decoder.
- Support saving a standalone scientific snapshot with raw payload/metadata even if RGB/TIFF export is not yet available.
- Implement safe close/stop/restart behavior and explicit errors on disconnect or writer overload.
- Optionally bring a small camera panel to the Tkinter manual GUI once the primary experience is stable.

**Deliverables:** manual UI demonstration using simulator, then physical camera.  
**Exit gate:** user can acquire a snapshot and a raw video sequence on macOS with understandable status messages; preview cannot compromise recording throughput.

### Milestone M5 — Imaging in programmed touch experiments

**Goal:** Tie camera data to model-based measurement runs.

**Work:**
- Define experiment-level imaging settings, e.g., `off`, `snapshot_before_point`, `record_during_run`, `mark_contact`, `snapshot_after_touch`.
- Extend the executor with structured events for: run start/end, point position reached, touch begin, contact detected, force/depth target reached, return and abort. Preserve existing `TouchResult` traces and status semantics.
- Put a single `ExperimentImagingCoordinator` between executor events and the camera adapter. It must not directly call printer G-code.
- Store `time.monotonic_ns()` event timestamps from the same Mac host, program/point IDs, position and force when available. Preserve uncertainty and differentiate frame receipt from exposure time.
- Associate snapshot/frame references to run logs rather than duplicating frames. Keep SCC raw recording usable as a standalone dataset.
- Define fail policy: camera failure may abort an imaging-required experiment, but must not trigger unsafe or contradictory printer movement. User STOP always prioritizes the printer's verified stop path.
- Test offline replay: results remain readable after the camera is unplugged or the original Mac is unavailable.

**Deliverables:** integrated simulation of a 3MF touch sequence with linked frame IDs/events; verified physical workflow later.  
**Exit gate:** automated run produces recoverable and auditable camera+force records, with clear failure handling and no undocumented timing claims.

### Milestone M6 — System validation, usability and release

**Goal:** Establish a documented Mac release candidate.

**Work:**
- Run an acceptance matrix on Intel and Apple Silicon: printer-only, camera-only, full integration, no-camera fallback, failure handling and restart.
- Validate sustained recording with the real camera; use raw bit-exact checks plus device telemetry where available. Host-side counts alone cannot prove that no sensor exposure was lost.
- Test realistic simultaneous loadcell UDP traffic, printer USB serial and camera USB3 streaming; note shared-port/hub and SSD bottlenecks.
- Test a complete programmed experiment, stop during touch, stop during recording, disconnects, interrupted recording repair, and disk capacity thresholds.
- Produce a sample dataset and documented analysis/replay procedure. Label any remaining unsupported colour formats explicitly.
- Pin a tested camera-package release in Prusa Direct Drive; only then deprecate the original SDK coupling.

**Deliverables:** release notes, support matrix, known limitations, setup guide, rollback instructions and test evidence.  
**Exit gate:** all selected user workflows work on supported physical configurations. A broader 'works on all Macs/cameras' claim is out of scope without representative tests.

## 7. Integration contract and workflow decisions

### Two forms of snapshot

1. **Snapshot from an active stream:** select an existing camera frame (e.g., next or nearest host-receipt time). It does *not* initiate another exposure. Recording should continue uninterrupted.
2. **Independent triggered capture:** when the camera is idle and advertises software trigger support, request a new image with `capture_single()`. This is not interchangeable with bookmarking a frame during continuous free-run. Hardware-triggered synchronisation may be investigated later.

The Prusa UI must label these modes accurately. If a simultaneous recording is underway, a UI button labelled 'Capture snapshot' should take a stream snapshot rather than attempting an unsupported independent trigger.

### Scientific data and timestamp policy

- Treat camera frame **host-receipt** timestamps as such; they are not exposure-start timestamps.
- Use one host monotonic-clock domain for camera and printer-event association where possible. Keep original camera timestamps separately.
- Force data comes via UDP with latency; do not claim exact force at exposure without measured synchronization.
- If no matching frame lies within the requested time window, record an explicit **unmatched** event.
- Preserve original raw pixels. Display colour/preview is a convenience; unsupported IDS grouped pixel formats should still record intact, with export disabled until validated.
- Include program/model ID, calibration, printer firmware, camera model/firmware, camera package version, Mac architecture, acquisition settings and data integrity reports in session metadata where available.

### Storage layout (conceptual)

```text
experiments/
  run_<id>/
    experiment.json           # Cross-references and platform/device metadata
    prusa_run.json            # Existing touch-point summary + force traces
    camera.scc/               # SCC recording, original raw data and events
    exports/                  # Optional TIFF, images and derived analysis
```

Actual filenames should follow the current packages' contracts. The integration layer should create a stable manifest referring to the camera library's own storage; do not fork the SCC format.

### Failure and safety policy

| Failure | Required behavior |
| --- | --- |
| Camera absent | Printer-only functions and simulation still launch and work |
| Native Aravis/PyGObject absent | Clear dependency/diagnostic message; no startup crash for printer-only use |
| Unsupported pixel format | Raw acquisition remains possible; unsupported conversion is explicitly disabled |
| Camera unplugged during run | Camera session fails visibly; printer workflow follows documented experiment fail policy |
| Recording queue overload / storage error | Explicit recording failure and preserved recoverable prefix; never claim a complete recording |
| Missing/late UDP force data | Existing force safety logic applies; do not infer a usable force trace |
| User presses STOP | Verified printer stop path takes precedence; camera cleanup is coordinated after safe motion response |
| App crashes during recording | Preserve SCC partial data; use library repair tooling with audit records |
| Calibration/model mismatch | Warn or block according to existing path programmer preflight logic |

## 8. Quality and acceptance matrix

| Test area | Simulator / CI evidence | Real-device requirement |
| --- | --- | --- |
| Camera API, SCC writer, snapshots, repair | Required on each PR | One complete recovery test on real Mac storage |
| Mac app import and GUI smoke test | Required on macOS CI | Native Intel and arm64 app launch |
| USB serial / UDP metrics | Mock tests | Physical Prusa Core One on Mac |
| Homing, probing and emergency stop | Logic tests are necessary but insufficient | Controlled machine-specific validation with custom firmware |
| Full-resolution IDS raw capture | Synthetic buffer/throughput tests | Physical 4056 × 3040 frames, true format/stride and fps measurements |
| 30-minute recording with snapshots | Stress/failure injection | Physical camera + SSD + error counters + checksum validation |
| Colour conversion | Independent standard-format fixtures | Vendor grouped IDS golden fixtures; otherwise RAW-only |
| Point-program + camera association | Simulator integration tests | Physical one-point then multi-point research workflow |
| Multi-camera compatibility | Mock capability discovery | Second physical GenICam camera |
| Rollback | Older adapter path tested if retained | Recovery to known working camera-free printer workflow |

### Minimum viable Mac integration (first usable release)

- Prusa Direct Drive launches with its current GUI/programmer functionality on a Mac.
- Printer serial, UDP metrics, homing and controlled probing are hardware validated (firmware requirement documented).
- Camera can be discovered, configured, and can acquire/safely save raw full-resolution images on at least one validated Mac reference system.
- A user can record a sequence and save snapshots from it without silent drops.
- A programmed touch experiment can attach snapshot/event references to its run log.
- Camera not present remains a supported condition.
- Installation, diagnostics, test status and unsupported features are documented.

**Full platform release gate:** repeat relevant physical checks on *both* Intel and Apple Silicon. Do not equate passing Python CI with validated native USB hardware support.

## 9. Proposed GitHub issue / PR backlog

Create focused issues and PRs; each issue should name its repository, an owner, observable exit criteria and the evidence to attach. Suggested ordering:

| Priority | Repository | Issue / PR topic | Depends on |
| --- | --- | --- | --- |
| P0 | Camera | Validate Aravis native setup and discover IDS on Intel Mac | M0 |
| P0 | Camera | Validate physical IDS raw frames, format/stride and fps | Native device discovered |
| P0 | Prusa | macOS printer serial, UDP and manual UI smoke test | M0 |
| P0 | Prusa | Document and validate modified firmware + stop/retreat safety | Physical printer available |
| P1 | Prusa | Introduce optional camera package dependency and remove Windows-only paths | M0 |
| P1 | Prusa | Add camera adapter + simulator-backed tests | Camera API selected |
| P1 | Prusa | Add camera control and snapshot/recording UI to path programmer | Adapter |
| P1 | Prusa | Add touch events and camera run associations | Adapter + executor event model |
| P1 | Camera | IDS vendor packing goldens and optional decoder | Independently sourced fixtures |
| P1 | Camera | Sustained full-resolution recording with concurrent snapshots on physical Mac | Transport validated |
| P2 | Both | Apple Silicon and Intel physical acceptance reports | Functionally complete |
| P2 | Camera | Verify second GenICam camera and record compatibility | First camera validated |
| P2 | Prusa | Packaging, documentation and first Mac release candidate | End-to-end validated |
| P3 | Prusa | Optional camera controls in Tkinter calibrator | Path programmer experience stable |
| P3 | Both | Advanced exposure/force synchronization via hardware trigger | Measured timing requirement |

Do not mix camera transport changes and printer orchestration changes into one PR. For a broken Aravis method or pixel decoder, make the fix and tests in the camera repository, release a package revision, then update Prusa's dependency pin.

## 10. Risks and decision gates

| Risk | Impact | Decision / mitigation |
| --- | --- | --- |
| IDS model cannot be streamed with current Aravis GI/native build | Blocks camera MVP on Mac | Prove with `scicam` CLI in isolation; evaluate Aravis patch or alternate transport bridge rather than rewriting app |
| IDS-specific packed Bayer format lacks validated decoder | Blocks colour export, not raw recording | Keep raw capture; obtain independent fixtures before enabling converter |
| Intel and arm64 native libraries are mixed | Import/runtime failures | Verify `platform.machine()`, Homebrew prefix, Python ABI and GI typelib consistently |
| Camera and printer share saturated USB hardware | Missing frames, blocked serial behavior | Validate dedicated high-bandwidth port/controller where possible; measure actual bus topology |
| SSD cannot sustain raw acquisition | Recording overload | Surface error, monitor queue/disk, use faster storage or lower rate; never hide loss |
| Modified `M410` firmware unavailable or unverified | Physical probe safety blocker | Keep physical touch acceptance blocked until known firmware and stop path tested |
| Cross-process camera ownership ambiguity | Exclusive-open failures or hanging shutdown | Keep one camera owner; do not open camera from calibrator subprocess during run |
| UI thread blocked by camera/IO | Frozen STOP and poor usability | Worker threads for capture/IO; main thread only for signals/rendering |
| Timestamp association mistaken for exposure synchronization | Invalid scientific claims | Keep timing source and uncertainty fields; plan hardware triggering separately |
| Breakage from upstream camera package changes | Regression | Pin tested version/commit, integration tests, explicit compatibility reviews |

### Fallbacks if native Aravis is blocked

Keep printer-only/macOS testing and simulator integration moving. Options to investigate, in order: reproduce and fix native Aravis/GI setup; isolate a version-specific Aravis bug; use upstream USB3 Vision tools to differentiate camera transport from Python binding problems; evaluate a scoped open-source transport patch or alternative camera driver. An external Linux acquisition host is a possible research workaround, but **not** equivalent to native macOS support and should be labelled as a separate architecture.

## 11. Codex implementation guidance

Use this plan as the **integration roadmap**, not as authorization to rewrite every module in a single PR. Codex should:

1. Inspect both repositories and the current camera-library docs/code **at task start**, because this plan is a point-in-time snapshot.
2. Use new branches and small PRs. Keep `scientific-camera-capture` independently usable. Avoid copying its source into Prusa Direct Drive.
3. Begin with reproducible smoke tests and the simulator; treat native hardware validation as a distinct, human-observed acceptance gate.
4. Separate changes into camera backend fixes (camera repo), Mac portability and app integration (Prusa repo), and independent documentation/tests.
5. Preserve existing movement and safety behavior; flag any proposed change to stop/retract behavior for explicit hardware verification.
6. Keep native camera dependencies lazy/optional; avoid modifying system permissions or installing Homebrew packages automatically.
7. For every PR report: **what changed**, **where**, **why**, **which tests passed**, **which hardware checks were performed**, and **what remains unverified**.
8. Prefer capability-driven camera settings, raw-first storage, and traceable event IDs to vendor-specific UI assumptions.
9. Preserve rollback paths until the new imaging integration demonstrates both functionality and reliability.
10. Do not declare the macOS project finished until the relevant M6 release gates are satisfied.

### Recommended first Codex assignment

> **Task: M0 + M2 foundation in Prusa Direct Drive.** Inspect current project entry points, dependencies, camera imports, path handling and tests. Create a narrowly scoped branch that makes the printer app installable and testable on macOS **without importing IDS peak or requiring Aravis at startup**. Add a simulator-only camera integration seam and relevant tests, but do not change gantry safety behavior or claim hardware support. Document the installation and current limitations. Open a PR with the actual test results and proposed next integration steps. The separate camera repository must remain untouched for this task unless a demonstrable upstream API issue is found.

Once this is merged, proceed to `CameraAdapter` integration while physical USB3 Vision and printer validation run as parallel tracks.

---

## 12. Reference material

**Scientific Camera Capture:**
- [Repository](https://github.com/stefberends/scientific-camera-capture)
- [Merged initial MVP PR #1](https://github.com/stefberends/scientific-camera-capture/pull/1)
- [Implementation status](https://github.com/stefberends/scientific-camera-capture/blob/main/docs/implementation-status.md)
- [Architecture and API](https://github.com/stefberends/scientific-camera-capture/blob/main/docs/architecture.md)
- [Integration guide](https://github.com/stefberends/scientific-camera-capture/blob/main/docs/integration.md)
- [macOS setup](https://github.com/stefberends/scientific-camera-capture/blob/main/docs/macos-setup.md)
- [Device compatibility checklist](https://github.com/stefberends/scientific-camera-capture/blob/main/docs/device-compatibility.md)
- [Performance evidence](https://github.com/stefberends/scientific-camera-capture/blob/main/docs/performance.md)
- [Pixel format policy](https://github.com/stefberends/scientific-camera-capture/blob/main/docs/pixel-formats.md)
- [SCC recording format](https://github.com/stefberends/scientific-camera-capture/blob/main/docs/recording-format.md)

**Prusa Direct Drive:**
- [Repository](https://github.com/Cubey00/prusa-direct-drive)
- [Printer motion/serial](https://github.com/Cubey00/prusa-direct-drive/blob/main/gantry_controls.py)
- [Loadcell telemetry](https://github.com/Cubey00/prusa-direct-drive/blob/main/strain_gauge.py)
- [Touch control](https://github.com/Cubey00/prusa-direct-drive/blob/main/touch.py)
- [Manual Tkinter GUI](https://github.com/Cubey00/prusa-direct-drive/blob/main/GUI.py)
- [Legacy camera wrapper](https://github.com/Cubey00/prusa-direct-drive/blob/main/ids_camera.py)
- [Legacy imaging module](https://github.com/Cubey00/prusa-direct-drive/blob/main/imaging.py)
- [Path programmer executor](https://github.com/Cubey00/prusa-direct-drive/blob/main/pathprogrammer/executor.py)
- [Path programmer UI](https://github.com/Cubey00/prusa-direct-drive/blob/main/pathprogrammer/ui/main_window.py)

**Evidence limitation:** This plan was prepared by reading repository files and GitHub metadata. It does not represent actual hands-on testing of the camera, printer, Mac, or modified firmware. Update the plan after hardware evidence is collected.
