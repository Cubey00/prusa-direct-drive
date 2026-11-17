"""Qt-independent acquisition state machine with incremental persistence."""
from dataclasses import asdict, dataclass
import threading
import time

from .chart import load_chart
from .data import RunWriter


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
                    self.firmware_and_stop_verified, self.lens_locked_and_chart_checked)
        if not all(required) or (not dry_run and not self.collision_free_dry_run):
            raise ValueError("Confirm fixture, Z direction, firmware/STOP, chart/lens and collision-free dry run")


@dataclass
class RunResult:
    state: str
    path: str
    message: str = ""


class ScanController:
    def __init__(self, recipe, settings, fixture, stage, camera, output_root, *,
                 printer_state=lambda: None, checks=OperatorChecks(), dry_run=False,
                 on_event=lambda *args: None):
        self.recipe, self.settings, self.fixture = recipe, settings, fixture
        self.stage, self.camera, self.output_root = stage, camera, output_root
        self.printer_state, self.checks, self.dry_run = printer_state, checks, dry_run
        self.on_event = on_event
        self.cancel = threading.Event()
        self.state = "IDLE"
        self.writer = None
        self._stop_thread = None
        self._stop_error = ""
        self.simulated = bool(stage.simulated and camera.simulated)
        if stage.simulated != camera.simulated:
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
        if self._stop_thread is None:
            def stop_device():
                try:
                    self.stage.stop()
                except Exception as exc:
                    self._stop_error = str(exc)
            self._stop_thread = threading.Thread(target=stop_device, daemon=True)
            self._stop_thread.start()

    def _capture_check(self, capture, after=0):
        capture.validate()
        m = capture.metadata
        if not self.simulated and m.get("simulated"):
            raise ValueError("A simulated camera cannot be used with a physical stage")
        if m["host_monotonic_ns"] <= after:
            raise ValueError("Stale frame received after motion")
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
            self.recipe.validate_fixture(self.fixture, self.settings.printer.z_max, simulation=self.simulated)
            if not self.simulated:
                self.checks.validate(self.dry_run)
            self._printer_guard()
            chart = load_chart(self.recipe.chart_path)
            self.writer = RunWriter(self.output_root, self.recipe, self.fixture, self.settings, chart,
                                    simulated=self.simulated, dry_run=self.dry_run,
                                    operator_checks=asdict(self.checks))
            self._check_abort()
            self.stage.open()
            current = self.stage.position()
            if not self.fixture.safe_z_min_mm <= current <= self.fixture.safe_z_max_mm:
                raise ValueError("Initial Z outside fixture bounds; move manually to the validated interval")
            if not self.dry_run:
                self.camera.open()
                self._capture_check(self.camera.capture())
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
            if analyze and not self.dry_run:
                self._state("ANALYZING")
                from .reports import analyze_run
                summary = analyze_run(self.writer.path, should_abort=self.cancel.is_set, acquisition_complete=True,
                                      on_progress=lambda done, total: self.on_event("progress", done, total))
                message = (f"Saved {len(self.writer.manifest['frames'])} frames; "
                           f"{summary['valid_focus_fits']} / {summary['total_focus_fits']} valid focus fits")
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
            for device in (self.camera, self.stage):
                try:
                    device.close()
                except Exception as exc:
                    outcome = "FAILED"
                    message += f"; close failed: {exc}"
            if self._stop_error:
                outcome = "FAILED"
                message += f"; STOP failed: {self._stop_error}"
            self._state(outcome, message)
        return RunResult(outcome, str(self.writer.path) if self.writer else "", message)
