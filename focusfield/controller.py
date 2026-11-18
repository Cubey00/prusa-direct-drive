"""Qt-independent acquisition state machine with incremental persistence."""
from dataclasses import asdict, dataclass, field
import threading
import time

from .chart import load_chart
from .data import RunWriter, storage_preflight
from .acquisition import FrameGuard


@dataclass(frozen=True)
class OperatorChecks:
    fixture_and_clearance: bool = False
    increasing_z_lowers_bed: bool = False
    firmware_and_stop_verified: bool = False
    lens_locked_and_chart_checked: bool = False
    collision_free_dry_run: bool = False
    manual_printer_idle: bool = False

    def validate(self, dry_run=False):
        required = (self.fixture_and_clearance, self.increasing_z_lowers_bed,
                    self.firmware_and_stop_verified)
        if not all(required):
            raise ValueError("Confirm measured fixture/clearance, Z direction and verified firmware/STOP")
        if not dry_run and not (self.lens_locked_and_chart_checked and self.collision_free_dry_run):
            raise ValueError("Confirm lens/chart and a completed collision-free dry run before image operations")


@dataclass
class RunResult:
    state: str
    path: str
    message: str = ""
    acquisition_complete: bool = False
    analysis_complete: bool = False
    measurement_valid: bool = False
    details: dict = field(default_factory=dict)


class ScanController:
    def __init__(self, recipe, settings, fixture, stage, camera, output_root, *,
                 printer_state=lambda: None, checks=OperatorChecks(), dry_run=False,
                 on_event=lambda *args: None, setup_provenance=None, operator_confirmed=False):
        self.recipe, self.settings, self.fixture = recipe, settings, fixture
        self.stage, self.camera, self.output_root = stage, camera, output_root
        self.printer_state, self.checks, self.dry_run = printer_state, checks, dry_run
        self.on_event = on_event
        self.operator_confirmed = operator_confirmed
        self.setup_provenance = setup_provenance or {}
        self.cancel = threading.Event()
        self.state = "IDLE"
        self.writer = None
        self._stop_thread = None
        self._stop_lock = threading.Lock()
        self._stop_error = ""
        self.simulated = bool(stage.simulated) if dry_run else bool(stage.simulated and camera.simulated)
        self.frame_guard = FrameGuard()
        if not dry_run and stage.simulated != camera.simulated:
            raise ValueError("Do not mix simulated and physical devices")

    def _state(self, state, message=""):
        self.state = state
        if self.writer:
            self.writer.update(state, message)
        self.on_event("state", state, message)

    def _check_abort(self):
        if self.cancel.is_set():
            raise InterruptedError("STOP requested")

    def _printer_guard(self):
        if self.simulated:
            return
        from prusalink import IDLE_STATES
        try:
            state = self.printer_state()
        except Exception:
            state = None
        if state is None and self.checks.manual_printer_idle:
            return
        if state not in IDLE_STATES:
            raise RuntimeError(f"Printer not confirmed idle ({state or 'PrusaLink unavailable'})")

    def stop(self):
        """Nonblocking UI entry. STOP completion is joined before devices close."""
        self.cancel.set()
        if not self.dry_run and hasattr(self.camera, "cancel"):
            self.camera.cancel.set()
        with self._stop_lock:
            if self._stop_thread is None:
                def stop_device():
                    try:
                        self.stage.stop()
                    except Exception as exc:
                        self._stop_error = str(exc)
                self._stop_thread = threading.Thread(target=stop_device, daemon=True)
                self._stop_thread.start()

    def _capture_check(self, capture, after=0):
        self.frame_guard.check(capture, after)
        m = capture.metadata
        if not self.simulated and m.get("simulated"):
            raise ValueError("A simulated camera cannot be used with a physical stage")
        if not self.simulated and m.get('device_frame_id') is None:
            raise ValueError("Physical acquisition requires native device frame IDs for sequence verification")
        if m["pixel_format"] != self.recipe.pixel_format:
            raise ValueError("Camera pixel format differs from recipe")
        if not self.simulated and str(m["camera_serial"]) != self.fixture.camera_serial:
            raise ValueError("Camera identity differs from measured fixture")
        if abs(m["exposure_us"] - self.recipe.exposure_us) > max(1, self.recipe.exposure_us * 0.01):
            raise ValueError("Camera exposure differs from recipe")
        if abs(m["gain"] - self.recipe.gain) > 0.01:
            raise ValueError("Camera gain differs from recipe")

    def run(self, analyze=True):
        if self.state != "IDLE":
            raise RuntimeError("A controller runs only once")
        outcome, message = "FAILED", ""
        try:
            self._state("PREFLIGHT")
            self._check_abort()
            self.recipe.validate_fixture(self.fixture, self.settings.printer.z_max,
                                         simulation=self.simulated, dry_run=self.dry_run)
            if not self.simulated:
                if self.operator_confirmed is not True:
                    raise ValueError("Explicit operator approval of this physical motion is required")
                self.checks.validate(self.dry_run)
            self._printer_guard()
            chart = load_chart(self.recipe.chart_path)
            from .acquisition import FORMATS
            bits, channels = FORMATS[self.recipe.pixel_format]
            frames = 0 if self.dry_run else len(self.recipe.schedule(self.fixture)) * self.recipe.frames_per_position
            estimate = storage_preflight(self.output_root, frames,
                self.recipe.expected_width_px * self.recipe.expected_height_px * channels * (1 if bits == 8 else 2))
            self.writer = RunWriter(self.output_root, self.recipe, self.fixture, self.settings, chart,
                                    simulated=self.simulated, dry_run=self.dry_run,
                                    operator_checks=asdict(self.checks))
            self.writer.manifest["storage_preflight"] = estimate
            self.writer.manifest['setup_provenance'] = self.setup_provenance
            self.writer.manifest['operator_confirmed'] = self.operator_confirmed
            if not self.simulated:
                from .calibration import validate_reference
                self.writer.manifest["wd_reference"] = validate_reference(self.fixture)
            self._check_abort()
            self.stage.open()
            current = self.stage.position()
            if not self.fixture.safe_z_min_mm <= current <= self.fixture.safe_z_max_mm:
                raise ValueError("Initial Z outside fixture bounds; move manually to the validated interval")
            if not self.dry_run:
                self.camera.open()
                initial = self.camera.capture()
                self._capture_check(initial)
                self.writer.manifest["storage_preflight_actual"] = storage_preflight(self.output_root, frames, initial.pixels.nbytes)
            self._check_abort()
            self._state("READY")
            schedule = self.recipe.schedule(self.fixture)
            self._state("SCANNING")
            for index, (repetition, wd, z) in enumerate(schedule):
                self._check_abort()
                self._printer_guard()
                current = self.stage.position()
                if not self.fixture.safe_z_min_mm <= current <= self.fixture.safe_z_max_mm:
                    raise ValueError("Stage left fixture bounds")
                self.stage.move_z_absolute(z)
                self._check_abort()
                reported = self.stage.position()
                if abs(reported-z) > self.fixture.position_tolerance_mm:
                    raise RuntimeError("Reported Z disagrees with command")
                if self.cancel.wait(0 if self.simulated else self.recipe.settle_s):
                    self._check_abort()
                settled = time.monotonic_ns()
                if not self.dry_run:
                    self.camera.fresh()
                    for _ in range(self.recipe.frames_per_position):
                        self._check_abort()
                        capture = self.camera.capture()
                        self._capture_check(capture, settled)
                        self._check_abort()
                        if abs(self.stage.position()-z) > self.fixture.position_tolerance_mm:
                            raise RuntimeError("Position drifted during capture")
                        record = self.writer.add(capture, printer_z_command_mm=z,
                            printer_z_reported_mm=reported, nominal_working_distance_mm=self.fixture.wd_for_z(reported),
                            wd_calibration_id=self.fixture.calibration_id, repetition=repetition,
                            scan_direction="reverse" if self.recipe.reverse_alternate and repetition % 2 else "forward")
                        self.on_event("frame", record, capture.pixels)
                self.on_event("progress", index+1, len(schedule))
            self._check_abort()
            self.writer.manifest["acquisition_complete"] = True
            if analyze and not self.dry_run:
                self._state("ANALYZING")
                from .reports import analyze_run
                summary = analyze_run(self.writer.path, should_abort=self.cancel.is_set, acquisition_complete=True,
                                      on_progress=lambda done, total: self.on_event("progress", done, total))
                self.writer.manifest.update(analysis_complete=True,
                    qa_passed=summary.get('qa_passed', False), qa_rejection_reasons=summary.get('qa_rejection_reasons', []),
                    measurement_valid=summary.get("measurement_valid", False),
                    measurement_validated=summary.get("measurement_valid", False),
                    measurement_rejection_reasons=summary.get("measurement_rejection_reasons", ["Unvalidated analysis"]))
                for key in ('requested_nominal_wd_mm', 'nominal_plane_measured',
                            'nominal_plane_qa_passed', 'separate_nominal_plane_required'):
                    if key in summary:
                        self.writer.manifest[key] = summary[key]
                message = (f"Acquisition complete: {len(self.writer.manifest['frames'])} frames; "
                           f"{summary['valid_focus_fits']} / {summary['total_focus_fits']} valid focus fits. "
                           f"Measurement valid: {self.writer.manifest['measurement_valid']}")
            elif self.dry_run:
                message = "Motion-only dry run completed; no images acquired"
            else:
                message = f"Saved {len(self.writer.manifest['frames'])} frames"
            self._check_abort()
            outcome = "COMPLETED"
        except InterruptedError as exc:
            outcome, message = "ABORTED", str(exc)
        except Exception as exc:
            outcome, message = "FAILED", f"{type(exc).__name__}: {exc}"
        finally:
            if outcome != "COMPLETED":
                self.stop()
            if self._stop_thread:
                self._stop_thread.join()
            for device in ((self.stage,) if self.dry_run else (self.camera, self.stage)):
                try:
                    device.close()
                except Exception as exc:
                    outcome = "FAILED"
                    message += f"; close failed: {exc}"
            if self._stop_error:
                outcome = "FAILED"
                message += f"; STOP failed: {self._stop_error}"
            if self.writer and outcome != "COMPLETED":
                self.writer.manifest["measurement_valid"] = False
                self.writer.manifest["measurement_validated"] = False
                self.writer.manifest["measurement_rejection_reasons"] = [message or outcome]
            self._state(outcome, message)
        manifest = self.writer.manifest if self.writer else {}
        return RunResult(outcome, str(self.writer.path) if self.writer else "", message,
                         manifest.get("acquisition_complete", False), manifest.get("analysis_complete", False),
                         manifest.get("measurement_valid", False) if outcome == "COMPLETED" else False)
