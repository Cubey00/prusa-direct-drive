"""Opt-in bounded setup operations. Preview and Start Scan never invoke these."""
from dataclasses import asdict, dataclass, replace
import math
import time

import numpy as np

from .acquisition import FORMATS
from .calibration import XYCalibration, validate_reference
from .chart import load_chart, regional_metrics
from .controller import RunResult, ScanController
from .data import RunWriter, atomic_json, storage_preflight
from .focus_fit import fit_peak
from .hardware import ZStage
from .quality import required_regions


@dataclass(frozen=True)
class CenteringLimits:
    max_step_mm: float = 0.25
    max_total_mm: float = 2.0
    max_iterations: int = 8
    tolerance_px: float = 2.0
    speed_mm_s: float = 1.0

    def validate(self):
        if (not np.isfinite([self.max_step_mm, self.max_total_mm, self.tolerance_px, self.speed_mm_s]).all()
                or not 0 < self.max_step_mm <= 0.5 or not self.max_step_mm <= self.max_total_mm <= 5
                or type(self.max_iterations) is not int or not 1 <= self.max_iterations <= 20
                or not 0 < self.tolerance_px <= 10 or not 0 < self.speed_mm_s <= 2):
            raise ValueError("Invalid centering step, travel, iteration, tolerance or speed limits")
        return self


@dataclass(frozen=True)
class ScoutRecipe:
    z_start_mm: float = 99.5
    z_end_mm: float = 100.5
    step_mm: float = 0.1
    fine_step_mm: float = 0.025
    frames_per_position: int = 1
    max_positions: int = 101
    allow_widen: bool = True
    max_narrow_span_mm: float = 0.8
    min_metric_span: float = 0.02
    max_fit_rmse: float = 0.04

    def validate(self, fixture):
        if (not np.isfinite([self.z_start_mm, self.z_end_mm, self.step_mm, self.fine_step_mm,
                             self.max_narrow_span_mm, self.min_metric_span, self.max_fit_rmse]).all()
                or not fixture.safe_z_min_mm <= self.z_start_mm < self.z_end_mm <= fixture.safe_z_max_mm
                or not 0.01 <= self.step_mm <= 0.5 or not 0.001 <= self.fine_step_mm < self.step_mm
                or type(self.frames_per_position) is not int or not 1 <= self.frames_per_position <= 3
                or type(self.max_positions) is not int or not 5 <= self.max_positions <= 201
                or type(self.allow_widen) is not bool or self.max_narrow_span_mm <= 0
                or self.min_metric_span <= 0 or self.max_fit_rmse <= 0):
            raise ValueError("Scout interval, steps and limits must fit the measured safe envelope")
        # Validate the worst case before opening devices, including optional widening.
        lo, hi = (fixture.safe_z_min_mm, fixture.safe_z_max_mm) if self.allow_widen else (self.z_start_mm, self.z_end_mm)
        if fixture.wd_for_z(lo) - fixture.wd_uncertainty_mm < fixture.minimum_wd_mm - 1e-9:
            raise ValueError("Scout working distance including uncertainty violates minimum clearance")
        if any(abs(v-round(v, 3)) > 1e-8 for v in (lo, hi, self.z_start_mm, self.z_end_mm, self.step_mm, self.fine_step_mm)):
            raise ValueError("Scout bounds and steps exceed the driver's 0.001 mm command precision")
        if len(set(self.positions(lo, hi)) | set(self.positions(self.z_start_mm, self.z_end_mm))) > self.max_positions:
            raise ValueError("Scout would exceed its bounded position budget; reduce the interval or increase coarse step")
        if len(self.positions(self.z_start_mm, self.z_end_mm)) < 5:
            raise ValueError("Scout needs at least five coarse positions")
        return self

    def positions(self, lo, hi):
        count = int(math.floor((hi-lo)/self.step_mm + 1e-8))
        return sorted({round(lo+i*self.step_mm, 3) for i in range(count+1)} | {round(hi, 3)})


def suggest_scout(fixture, nominal_wd_mm):
    """Draft a short scout around the requested WD within the measured fixture limits."""
    safe_lo = max(fixture.safe_z_min_mm,
                  fixture.z_for_wd(fixture.minimum_wd_mm + fixture.wd_uncertainty_mm))
    safe_hi = fixture.safe_z_max_mm
    anchor = min(max(fixture.z_for_wd(nominal_wd_mm), safe_lo), safe_hi)
    lo = math.ceil(max(safe_lo, anchor - 0.5) * 1000 - 1e-8) / 1000
    hi = math.floor(min(safe_hi, anchor + 0.5) * 1000 + 1e-8) / 1000
    step = min(0.1, math.floor((hi - lo) * 250 + 1e-8) / 1000)
    if step < 0.01:
        raise ValueError("The available scout interval is too short for five positions")
    fine_step = min(0.025, math.floor(step * 500 + 1e-8) / 1000)
    return ScoutRecipe(z_start_mm=lo, z_end_mm=hi, step_mm=step,
                       fine_step_mm=fine_step, allow_widen=False).validate(fixture)


def propose_fine_scan(samples, scout, fixture, recipe):
    """Propose only interpolated, bracketed intervals; never extrapolate optical distance."""
    regions = required_regions(load_chart(recipe.chart_path))
    if not samples or len(set(regions)) != 5:
        raise ValueError("Scout requires center and four peripheral regions")
    fits = {}
    for region in regions:
        z = sorted({s['z_mm'] for s in samples if region in s['metrics']})
        if len(z) < 5:
            raise ValueError(f"Insufficient usable scout samples for {region}; need five positions with strict edge quality")
        values = [float(np.median([s['metrics'][region] for s in samples
                                  if s['z_mm'] == p and region in s['metrics']])) for p in z]
        if np.ptp(values) < scout.min_metric_span:
            raise ValueError(f"Flat scout curve for {region}; check lens, illumination and chart manually")
        fit = fit_peak(z, values)
        if not fit['valid']:
            raise ValueError(f"Unbracketed scout peak for {region}: {fit['reason']}; no fine scan proposed")
        i = int(np.argmax(values))
        # A noisy local bump must not become an invented focus estimate.
        left, right = np.diff(values[:i+1]), np.diff(values[i:])
        if (fit['fit_rmse'] > scout.max_fit_rmse or fit['fit_uncertainty_mm'] > scout.step_mm/2
                or np.count_nonzero(left < -scout.min_metric_span) > 1
                or np.count_nonzero(right > scout.min_metric_span) > 1):
            raise ValueError(f"Noisy or multiple scout peaks for {region}; inspect saved curves")
        fits[region] = dict(best_focus_z_mm=fit['best_wd_mm'],
                           best_focus_wd_estimate_mm=fixture.wd_for_z(fit['best_wd_mm']),
                           fit_uncertainty_mm=fit['fit_uncertainty_mm'])
    peaks = [f['best_focus_z_mm'] for f in fits.values()]
    step = scout.fine_step_mm
    margin = max(2*step, scout.step_mm)
    # Anchor the grid to the requested nominal command, so it is actually
    # sampled whenever the focus interval includes it. Never move that plane.
    anchor = fixture.z_for_wd(recipe.nominal_wd_mm)
    lo = anchor + math.floor((min(peaks)-margin-anchor)/step+1e-8)*step
    hi = anchor + math.ceil((max(peaks)+margin-anchor)/step-1e-8)*step
    sampled_lo, sampled_hi = min(s['z_mm'] for s in samples), max(s['z_mm'] for s in samples)
    lo = max(lo, anchor + math.ceil((max(fixture.safe_z_min_mm, sampled_lo)-anchor)/step-1e-8)*step)
    hi = min(hi, anchor + math.floor((min(fixture.safe_z_max_mm, sampled_hi)-anchor)/step+1e-8)*step)
    if min(peaks)-lo < 2*step-1e-8 or hi-max(peaks) < 2*step-1e-8:
        raise ValueError("Safe/sampled bounds cannot provide two fine positions on each side of every peak")
    if lo <= anchor <= hi and abs(anchor-round(anchor, 3)) > 1e-8:
        raise ValueError("Requested nominal WD is not representable at the driver's 0.001 mm Z command precision; "
                         "verify the measured reference and nominal command instead of rounding the measurement plane")
    lo, hi = round(lo, 3), round(hi, 3)
    separate_nominal = not lo-1e-8 <= anchor <= hi+1e-8
    fine = replace(recipe, wd_start_mm=round(fixture.wd_for_z(lo), 6),
                   wd_end_mm=round(fixture.wd_for_z(hi), 6), step_mm=step,
                   separate_nominal_plane_required=separate_nominal)
    fine.validate_fixture(fixture, fixture.safe_z_max_mm, simulation=True)
    broad = hi-lo > scout.max_narrow_span_mm
    return dict(z_start_mm=lo, z_end_mm=hi, regional_estimates=fits,
                absolute_wd_uncertainty_mm=fixture.wd_uncertainty_mm,
                broad_scan_required=broad, suggested_recipe=fine.to_dict(),
                requested_nominal_wd_mm=recipe.nominal_wd_mm,
                separate_nominal_plane_required=separate_nominal,
                nominal_plane_message=(f"Separate measurement at requested {recipe.nominal_wd_mm:g} mm WD is required; "
                                       "it is outside this focus sweep. No additional motion is scheduled."
                                       if separate_nominal else
                                       f"Requested {recipe.nominal_wd_mm:g} mm nominal plane is included on the fine-scan grid"),
                reasoning=("No single narrow interval covers all regions; bounded broader scan proposed" if broad else
                           "Bracketed center/peripheral peaks with at least two fine samples on either side"),
                approved=False, absolute_wd_source="independent fixture reference, never marker size or sharpness")


class SetupController(ScanController):
    def __init__(self, *args, operation, operator_confirmed=False, xy_calibration=None,
                 centering_limits=CenteringLimits(), scout_recipe=ScoutRecipe(), **kwargs):
        super().__init__(*args, **kwargs)
        if operation not in {"center", "scout"}:
            raise ValueError("Unknown optical setup operation")
        self.operation, self.operator_confirmed = operation, operator_confirmed
        self.xy_calibration, self.centering_limits, self.scout_recipe = xy_calibration, centering_limits, scout_recipe
        self.settled_at_ns = 0

    def _capture(self, chart, *, optical_center_px=None, allow_defocus=False):
        self._check_abort()
        before = self.stage.position_xyz()
        self.camera.fresh()
        self._check_abort()
        capture = self.camera.capture()
        self._capture_check(capture, self.settled_at_ns)
        if not self.writer.manifest['frames']:
            self.writer.manifest['storage_preflight_actual'] = storage_preflight(
                self.writer.path, self.frame_budget, capture.pixels.nbytes)
        xyz = self.stage.position_xyz()
        if np.max(np.abs(np.array(before)-xyz)) > self.fixture.position_tolerance_mm:
            raise RuntimeError("Position drifted during optical setup capture")
        storage_preflight(self.writer.path, 1, capture.pixels.nbytes)
        if not self.fixture.safe_z_min_mm <= xyz[2] <= self.fixture.safe_z_max_mm:
            raise ValueError("Setup position outside fixture bounds")
        # Persist before interpretation, including frames that fail marker/edge quality.
        record = self.writer.add(capture, printer_xyz_mm=list(xyz), printer_z_reported_mm=xyz[2],
                                 nominal_working_distance_mm=self.fixture.wd_for_z(xyz[2]))
        self.on_event("frame", record, capture.pixels)
        registration, metrics, flags = regional_metrics(capture, chart, self.recipe,
            optical_center_px=optical_center_px, require_usable_edges=not allow_defocus)
        if allow_defocus and any('Clipped' in reason or 'image border' in reason
                                 for reasons in flags.values() for reason in reasons):
            raise ValueError("Scout exposure is clipped or crosses an image border; correct setup before searching")
        registration.pop('homography')
        sample = dict(image_id=record['image_id'], z_mm=xyz[2], xyz_mm=list(xyz),
                      registration=registration, metrics=metrics, edge_quality_flags=flags)
        missing = set(required_regions(chart)) - metrics.keys()
        sample.update(quality_usable=not missing,
                      unusable_regions={region: flags[region] for region in sorted(missing)})
        self.writer.manifest['setup_samples'].append(sample)
        self.writer.update(self.state)
        return sample, capture

    def _settle(self):
        if self.cancel.wait(0 if self.simulated else self.recipe.settle_s):
            self._check_abort()
        self.settled_at_ns = time.monotonic_ns()

    def _center(self, chart):
        c, limits = self.xy_calibration, self.centering_limits.validate()
        c.validate(self.fixture)
        guard = ZStage(self.fixture, self.settings.printer.z_max, c, self.settings.printer.travel)
        guard.check_xy(*self.stage.position_xyz())
        sample, capture = self._capture(chart, optical_center_px=c.optical_center_px)
        if tuple(c.image_size_px) != (capture.pixels.shape[1], capture.pixels.shape[0]):
            raise ValueError("Camera image dimensions differ from XY calibration")
        initial = sample['registration']['center_error_px']
        total = 0.0
        for iteration in range(limits.max_iterations):
            self._check_abort()
            error = np.asarray(sample['registration']['center_error_px'])
            if np.linalg.norm(error) <= limits.tolerance_px:
                return dict(initial_error_px=initial, final_error_px=error.tolist(),
                            final_xyz_mm=list(self.stage.position_xyz()), total_xy_travel_mm=total,
                            iterations=iteration, centered=True)
            delta = c.correction(error, limits.max_step_mm)
            current = np.array(self.stage.position_xyz())
            target = np.round(current[:2]+delta, 3)
            distance = float(np.linalg.norm(target-current[:2]))
            if distance == 0 or total+distance > limits.max_total_mm:
                raise ValueError("Centering travel budget reached; finish alignment manually")
            guard.check_xy(*current)
            guard.check_xy(*target, current[2])
            self._printer_guard()
            self._check_abort()
            self.stage.move_xy_absolute(*target, speed_mm_s=limits.speed_mm_s)
            total += distance
            self._check_abort()
            reported = np.array(self.stage.position_xyz())
            if np.max(np.abs(reported-np.r_[target, current[2]])) > self.fixture.position_tolerance_mm:
                raise RuntimeError("XY or Z position disagrees with commanded centering position")
            self._settle()
            updated, _ = self._capture(chart, optical_center_px=c.optical_center_px)
            self.on_event('progress', iteration+1, limits.max_iterations)
            actual = np.array(updated['registration']['center_error_px'])
            predicted = error + np.array(c.jacobian_px_per_mm) @ (target-current[:2])
            if np.linalg.norm(actual-predicted) > c.max_residual_px+c.uncertainty_px:
                raise ValueError("Observed XY response differs from calibrated local mapping; stop and recalibrate")
            if np.linalg.norm(actual) >= np.linalg.norm(error):
                raise ValueError("Centering did not improve image-space error; no further move")
            sample = updated
        if np.linalg.norm(sample['registration']['center_error_px']) <= limits.tolerance_px:
            return dict(initial_error_px=initial, final_error_px=sample['registration']['center_error_px'],
                        final_xyz_mm=list(self.stage.position_xyz()), total_xy_travel_mm=total,
                        iterations=limits.max_iterations, centered=True)
        raise ValueError("Centering iteration limit reached; inspect saved positions and mapping")

    def _scout(self, chart):
        s = self.scout_recipe
        samples = []
        # Registration must be trustworthy before motion; defocused edges can be
        # skipped while searching only the already verified mechanical envelope.
        self._capture(chart, allow_defocus=True)
        def acquire(positions):
            for z in positions:
                if any(abs(p['z_mm']-z) < 1e-6 for p in samples):
                    continue
                if len({p['z_mm'] for p in samples}) >= s.max_positions:
                    raise ValueError("Scout position budget exhausted")
                self._check_abort()
                self._printer_guard()
                self.stage.move_z_absolute(z)
                self._check_abort()
                if abs(self.stage.position()-z) > self.fixture.position_tolerance_mm:
                    raise RuntimeError("Scout position readback mismatch")
                self._settle()
                for _ in range(s.frames_per_position):
                    sample, _ = self._capture(chart, allow_defocus=True)
                    samples.append(sample)
                self.on_event('progress', len({p['z_mm'] for p in samples}), s.max_positions)
        acquire(s.positions(s.z_start_mm, s.z_end_mm))
        try:
            return propose_fine_scan(samples, s, self.fixture, self.recipe)
        except ValueError as exc:
            # Defocus/insufficient usable edges or unbracketed peaks allow one
            # bounded extension. Registration/clipping and flat/noisy curves stop.
            if not s.allow_widen or not any(reason in str(exc) for reason in
                    ("Unbracketed", "Insufficient usable scout samples")):
                raise
            self.writer.manifest['widen_reason'] = str(exc)
            acquire(s.positions(self.fixture.safe_z_min_mm, self.fixture.safe_z_max_mm))
            return propose_fine_scan(samples, s, self.fixture, self.recipe)

    def run(self):
        if self.state != 'IDLE':
            raise RuntimeError("A setup controller runs only once")
        outcome, message = "FAILED", ""
        try:
            self._state("PREFLIGHT")
            self._check_abort()
            if self.dry_run:
                raise ValueError("Centering and scouting need images; use a separate supervised motion-only preflight")
            if not self.simulated:
                if self.operator_confirmed is not True:
                    raise ValueError("Explicit operator approval of this bounded setup motion is required")
                self.fixture.validate_physical()
                self.checks.validate()
            if self.operation == 'center':
                if self.xy_calibration is None:
                    raise ValueError("No measured optical XY mapping selected")
                self.xy_calibration.validate(self.fixture)
                self.centering_limits.validate()
                budget = self.centering_limits.max_iterations+1
            else:
                self.scout_recipe.validate(self.fixture)
                budget = self.scout_recipe.max_positions*self.scout_recipe.frames_per_position+1
            self.frame_budget = budget
            self._printer_guard()
            bits, channels = FORMATS[self.recipe.pixel_format]
            storage_preflight(self.output_root, budget, self.recipe.expected_width_px*self.recipe.expected_height_px
                              * channels*(1 if bits == 8 else 2))
            chart = load_chart(self.recipe.chart_path)
            self.writer = RunWriter(self.output_root, self.recipe, self.fixture, self.settings, chart,
                                    simulated=self.simulated, operator_checks=asdict(self.checks))
            self.writer.manifest.update(operation=self.operation, expected_frames=None, setup_samples=[],
                setup_config=asdict(self.centering_limits if self.operation == 'center' else self.scout_recipe),
                xy_calibration=asdict(self.xy_calibration) if self.xy_calibration else None,
                operator_confirmed=self.operator_confirmed,
                wd_reference=validate_reference(self.fixture) if not self.simulated else None)
            self.stage.open()
            if not self.fixture.safe_z_min_mm <= self.stage.position() <= self.fixture.safe_z_max_mm:
                raise ValueError("Initial Z outside verified fixture; no setup motion allowed")
            self.camera.open()
            self._check_abort()
            self._state("CENTERING" if self.operation == 'center' else "SCOUTING")
            details = self._center(chart) if self.operation == 'center' else self._scout(chart)
            self._check_abort()
            self.writer.manifest.update(setup_result=details, acquisition_complete=True,
                measurement_rejection_reasons=["Setup operation only; not an optical measurement"])
            atomic_json(self.writer.path / 'setup_result.json', details)
            outcome = "COMPLETED"
            message = "Chart centered" if self.operation == 'center' else "Fine scan proposed; explicit approval required before starting"
        except InterruptedError as exc:
            outcome, message = "ABORTED", str(exc)
        except Exception as exc:
            outcome, message = "FAILED", f"{type(exc).__name__}: {exc}"
        finally:
            if outcome != 'COMPLETED':
                self.stop()
            if self._stop_thread:
                self._stop_thread.join()
            for device in (self.camera, self.stage):
                try:
                    device.close()
                except Exception as exc:
                    outcome, message = 'FAILED', message + f"; close failed: {exc}"
            if self._stop_error:
                outcome, message = 'FAILED', message + f"; STOP failed: {self._stop_error}"
            self._state(outcome, message)
        return RunResult(outcome, str(self.writer.path) if self.writer else "", message)


def simulation_xy_calibration(fixture):
    return XYCalibration("SIMULATED-XY", fixture.fixture_id, fixture.camera_serial, True, True,
        ((-16, 3), (2, 16)), (800, 640), (1600, 1280), 80, 0.2, 0.2, 3,
        (0, 250), (0, 250), (99, 101), 5, 10, 0, 2,
        "Synthetic local non-orthogonal mapping", "SIMULATED", "2026-11-17T00:00:00+00:00")
