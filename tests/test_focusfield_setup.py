from dataclasses import asdict, replace
import json
import sys
from types import SimpleNamespace

import numpy as np
import pytest

from focusfield.calibration import WDReference, save_reference, validate_reference
from focusfield.chart import load_chart, register_chart
from focusfield.config import Recipe, simulation_fixture
from focusfield.controller import OperatorChecks
from focusfield.hardware import SimCamera, SimStage, ZStage
from focusfield.quality import required_regions
from focusfield.setup import ScoutRecipe, SetupController, propose_fine_scan, simulation_xy_calibration
from machine_settings import DEFAULT_PATH, SETTINGS, load


@pytest.fixture
def setup_rig(tmp_path):
    fixture = simulation_fixture()
    recipe = Recipe(step_mm=0.1, frames_per_position=1, repetitions=1, settle_s=0,
                    expected_width_px=1600, expected_height_px=1280, pixel_format='Mono16')
    stage = SimStage(fixture)
    camera = SimCamera(stage, recipe, load_chart(recipe.chart_path))
    camera.offset_px = np.array([12., -8.])
    return SetupController(recipe, SETTINGS, fixture, stage, camera, tmp_path, operation='center',
                           xy_calibration=simulation_xy_calibration(fixture))


def test_scout_default_centers_on_nominal_wd_and_does_not_expand_wide_fixture():
    from focusfield.setup import suggest_scout

    fixture = replace(simulation_fixture(), reference_wd_mm=19.7, reference_z_mm=99.9,
                      safe_z_min_mm=94.8, safe_z_max_mm=105.2)
    scout = suggest_scout(fixture, 20.0)
    assert (scout.z_start_mm, scout.z_end_mm) == (99.7, 100.7)
    assert len(scout.positions(scout.z_start_mm, scout.z_end_mm)) == 11
    assert not scout.allow_widen
    scout.validate(fixture)


def test_registration_center_uses_chart_geometry(setup_rig):
    frame = setup_rig.camera.capture()
    r = register_chart(frame.pixels/65535, load_chart(setup_rig.recipe.chart_path))
    np.testing.assert_allclose(r['chart_center_px'], [812, 632], atol=1)
    np.testing.assert_allclose(r['center_error_px'], [12, -8], atol=1)
    assert set(r['marker_corners_px']) == {'0', '1', '2', '3'}
    assert r['full_field_visible']
    assert not r['optical_center_calibrated']


def test_centering_reversed_nonorthogonal_mapping_converges_and_logs(setup_rig):
    result = setup_rig.run()
    assert result.state == 'COMPLETED', result.message
    manifest = setup_rig.writer.manifest
    final = manifest['setup_result']
    assert final['centered'] and np.linalg.norm(final['final_error_px']) <= 2
    assert 1 <= final['iterations'] <= 8
    assert manifest['xy_calibration']['jacobian_px_per_mm'] == ((-16, 3), (2, 16))
    assert not manifest['measurement_valid']
    assert not any(c[0] == 'move' for c in setup_rig.stage.calls)
    for a, b in zip(manifest['setup_samples'], manifest['setup_samples'][1:]):
        assert np.linalg.norm(np.array(b['xyz_mm'][:2])-a['xyz_mm'][:2]) <= 0.251


@pytest.mark.parametrize('change', ['uncalibrated', 'unsafe_z', 'out_of_bounds', 'missing_mapping'])
def test_centering_blocks_unverified_mapping_and_unsafe_envelope(setup_rig, change):
    c = setup_rig.xy_calibration
    if change == 'uncalibrated':
        setup_rig.xy_calibration = replace(c, measured=False)
    elif change == 'unsafe_z':
        setup_rig.xy_calibration = replace(c, safe_z_mm=(100.2, 101))
    elif change == 'out_of_bounds':
        setup_rig.xy_calibration = replace(c, safe_x_mm=(145, 155), housing_radius_mm=4.99)
    else:
        setup_rig.xy_calibration = None
    result = setup_rig.run()
    assert result.state == 'FAILED', result.message
    assert not any(c[0] in {'move_xy', 'move'} for c in setup_rig.stage.calls)


def test_wrong_xy_sign_stops_after_one_supervised_step(setup_rig):
    setup_rig.xy_calibration = replace(setup_rig.xy_calibration, jacobian_px_per_mm=((16, -3), (-2, -16)))
    result = setup_rig.run()
    assert result.state == 'FAILED' and ('mapping' in result.message or 'improve' in result.message)
    assert len([c for c in setup_rig.stage.calls if c[0] == 'move_xy']) == 1


@pytest.mark.parametrize('fault', ['stop', 'markers_lost', 'clipping'])
def test_centering_stop_or_lost_quality_prevents_subsequent_move(setup_rig, fault):
    original = setup_rig.camera.capture
    if fault == 'stop':
        setup_rig.on_event = lambda kind, *args: setup_rig.stop() if kind == 'frame' else None
    else:
        count = 0
        def bad_capture():
            nonlocal count
            c = original()
            count += 1
            if count >= (2 if fault == 'markers_lost' else 1):
                if fault == 'markers_lost':
                    c.pixels[:] = 0
                else:
                    # Keep marker identity; clip all measurement edges.
                    c.pixels[:] = np.where(c.pixels > 32768, 65535, 0)
            return c
        setup_rig.camera.capture = bad_capture
    result = setup_rig.run()
    assert result.state == ('ABORTED' if fault == 'stop' else 'FAILED')
    moves = [c for c in setup_rig.stage.calls if c[0] == 'move_xy']
    assert len(moves) == (1 if fault == 'markers_lost' else 0)
    assert setup_rig.stage.calls[-1] == ('close',)


def test_physical_setup_requires_fresh_operator_approval(setup_rig, verified_optical_fixture):
    setup_rig.fixture = verified_optical_fixture
    setup_rig.simulated = setup_rig.stage.simulated = setup_rig.camera.simulated = False
    setup_rig.checks = OperatorChecks(True, True, True, True, True)
    setup_rig.printer_state = lambda: 'IDLE'
    result = setup_rig.run()
    assert result.state == 'FAILED' and 'operator approval' in result.message
    assert ('open',) not in setup_rig.stage.calls


def test_setup_never_interprets_motion_only_as_permission_for_image_based_moves(setup_rig):
    setup_rig.dry_run = True
    result = setup_rig.run()
    assert result.state == 'FAILED' and 'need images' in result.message
    assert not any(c[0] in {'open', 'move_xy', 'move'} for c in setup_rig.stage.calls)


def scout_samples(peaks, noise=False):
    regions = required_regions(load_chart(Recipe().chart_path))
    samples = []
    for i, z in enumerate(np.linspace(99, 101, 21)):
        metrics = {r: float(np.exp(-5*(z-p)**2)) for r, p in zip(regions, peaks)}
        if noise:
            metrics = {r: float((i % 3)/2) for r in regions}
        samples.append(dict(z_mm=float(z), metrics=metrics))
    return samples


def test_scout_proposes_bracketed_safe_fine_range_and_separate_uncertainties():
    proposal = propose_fine_scan(scout_samples([100, 100.1, 100.15, 100.2, 100.05]),
                                 ScoutRecipe(), simulation_fixture(), Recipe())
    assert 99 <= proposal['z_start_mm'] < 100 < 100.2 < proposal['z_end_mm'] <= 101
    assert not proposal['approved'] and not proposal['broad_scan_required']
    assert proposal['absolute_wd_uncertainty_mm'] == 0.01
    assert all('fit_uncertainty_mm' in r for r in proposal['regional_estimates'].values())
    Recipe(**proposal['suggested_recipe']).validate_fixture(simulation_fixture(), 273, simulation=True)
    fine = Recipe(**proposal['suggested_recipe'])
    assert fine.nominal_wd_mm == 20 and 20 in fine.distances()
    assert not proposal['separate_nominal_plane_required']


def test_scout_keeps_20mm_objective_when_focus_peaks_are_at_20_3mm():
    proposal = propose_fine_scan(scout_samples([100.3]*5), ScoutRecipe(), simulation_fixture(), Recipe())
    fine = Recipe(**proposal['suggested_recipe'])
    assert fine.wd_start_mm > 20 and fine.nominal_wd_mm == 20
    assert proposal['requested_nominal_wd_mm'] == 20
    assert proposal['separate_nominal_plane_required'] and fine.separate_nominal_plane_required
    assert 'Separate measurement at requested 20 mm' in proposal['nominal_plane_message']
    assert 20 not in fine.distances()  # no implicit return to a different physical plane
    fine.validate_fixture(simulation_fixture(), 273, simulation=True)


def test_scout_grid_samples_requested_nominal_with_fractional_wd_reference():
    f = replace(simulation_fixture(), reference_wd_mm=20.003)
    proposal = propose_fine_scan(scout_samples([100, 100.1, 100.15, 100.2, 100.05]), ScoutRecipe(), f, Recipe())
    fine = Recipe(**proposal['suggested_recipe'])
    assert fine.nominal_wd_mm == 20
    assert 20 in fine.distances()
    assert not fine.separate_nominal_plane_required
    fine.validate_fixture(f, 273, simulation=True)


def test_scout_never_rounds_an_unrepresentable_nominal_plane_into_a_false_claim():
    f = replace(simulation_fixture(), reference_wd_mm=20.0032)
    with pytest.raises(ValueError, match='nominal WD is not representable'):
        propose_fine_scan(scout_samples([100, 100.1, 100.15, 100.2, 100.05]), ScoutRecipe(), f, Recipe())


def test_scout_keeps_a_custom_user_nominal_not_only_20mm():
    recipe = Recipe(nominal_wd_mm=20.1)
    proposal = propose_fine_scan(scout_samples([100.3]*5), ScoutRecipe(), simulation_fixture(), recipe)
    assert proposal['suggested_recipe']['nominal_wd_mm'] == 20.1
    assert proposal['requested_nominal_wd_mm'] == 20.1


@pytest.mark.parametrize('case', ['flat', 'monotonic', 'outside', 'noise', 'missing'])
def test_scout_does_not_invent_unusable_peaks(case):
    samples = scout_samples([100]*5, noise=case == 'noise')
    for s in samples:
        if case == 'flat':
            s['metrics'] = {r: 0.5 for r in s['metrics']}
        elif case == 'monotonic':
            s['metrics'] = {r: s['z_mm']-98 for r in s['metrics']}
        elif case == 'outside':
            s['metrics'] = {r: float(np.exp(-(s['z_mm']-102)**2)) for r in s['metrics']}
        elif case == 'missing':
            s['metrics'] = {'only_center': 0.5}
    with pytest.raises(ValueError):
        propose_fine_scan(samples, ScoutRecipe(), simulation_fixture(), Recipe())


def test_widely_separated_peaks_propose_bounded_broader_scan():
    proposal = propose_fine_scan(scout_samples([99.4, 99.6, 100.6, 100.4, 100]),
                                 ScoutRecipe(), simulation_fixture(), Recipe())
    assert proposal['broad_scan_required']
    assert 'broader' in proposal['reasoning']
    assert 99 <= proposal['z_start_mm'] < proposal['z_end_mm'] <= 101


def test_scout_integration_retains_frames_and_never_starts_fine_scan(setup_rig):
    setup_rig.operation = 'scout'
    result = setup_rig.run()
    assert result.state == 'COMPLETED', result.message
    proposal = setup_rig.writer.manifest['setup_result']
    assert not proposal['approved']
    moves = [c for c in setup_rig.stage.calls if c[0] == 'move']
    assert len(moves) == 11
    assert all(99 <= m[1] <= 101 for m in moves)
    assert len(setup_rig.writer.manifest['frames']) == 12


def test_scout_searches_from_registered_but_defocused_initial_image(setup_rig):
    setup_rig.operation = 'scout'
    setup_rig.stage.z = 99
    camera = setup_rig.camera
    original = camera.capture
    def defocused():
        frame = original()
        if setup_rig.stage.z < 99.7:
            # Keep all four real tags; remove only unresolved slanted squares.
            for square in camera.chart['slanted_squares']:
                x, y = np.array(square['center_mm'])*camera.scale + camera.offset_px
                radius = int(square['side_mm']*camera.scale*0.95)
                frame.pixels[int(y)-radius:int(y)+radius, int(x)-radius:int(x)+radius] = 50000
        return frame
    camera.capture = defocused
    result = setup_rig.run()
    assert result.state == 'COMPLETED', result.message
    samples = setup_rig.writer.manifest['setup_samples']
    assert not samples[0]['quality_usable'] and samples[0]['unusable_regions']
    assert any(s['quality_usable'] for s in samples[1:])
    assert setup_rig.writer.manifest['setup_result']['regional_estimates']
    assert all(99 <= c[1] <= 101 for c in setup_rig.stage.calls if c[0] == 'move')


@pytest.mark.parametrize('fault', ['missing_tags', 'clipping'])
def test_scout_tolerance_never_bypasses_initial_identification_or_clipping(setup_rig, fault):
    setup_rig.operation = 'scout'
    original = setup_rig.camera.capture
    def invalid():
        frame = original()
        if fault == 'missing_tags':
            frame.pixels[:] = 0
        else:
            frame.pixels[:] = np.where(frame.pixels > 32768, 65535, 0)
        return frame
    setup_rig.camera.capture = invalid
    result = setup_rig.run()
    assert result.state == 'FAILED'
    assert not any(c[0] in {'move', 'move_xy'} for c in setup_rig.stage.calls)


def test_scout_widens_once_only_within_verified_bounds(setup_rig, monkeypatch):
    setup_rig.operation = 'scout'
    setup_rig.scout_recipe = ScoutRecipe(z_start_mm=99.5, z_end_mm=100)
    # Pure camera stub: peaks at 100.2 require widening; persist algorithm decisions.
    def sample(chart, **kwargs):
        z = setup_rig.stage.z
        return dict(z_mm=z, metrics={r: float(np.exp(-5*(z-100.2)**2))
                    for r in required_regions(chart)}), None
    monkeypatch.setattr(setup_rig, '_capture', sample)
    result = setup_rig.run()
    assert result.state == 'COMPLETED', result.message
    assert 'Unbracketed' in setup_rig.writer.manifest['widen_reason']
    z = [c[1] for c in setup_rig.stage.calls if c[0] == 'move']
    assert min(z) >= 99 and max(z) <= 101 and len(z) <= 101


def test_scout_stop_mid_move_prevents_any_next_motion(setup_rig):
    setup_rig.operation = 'scout'
    original = setup_rig.stage.move_z_absolute
    def stop(z):
        original(z)
        setup_rig.stop()
    setup_rig.stage.move_z_absolute = stop
    assert setup_rig.run().state == 'ABORTED'
    assert len([c for c in setup_rig.stage.calls if c[0] == 'move']) == 1


def test_wd_reference_sign_identity_audit_and_disabled_save(tmp_path, verified_optical_fixture):
    record = WDReference(**validate_reference(verified_optical_fixture))
    assert verified_optical_fixture.wd_for_z(100.1) == pytest.approx(20.1)
    with pytest.raises(ValueError, match='unit slope'):
        replace(record, check_wd_mm=19.9).validate()
    with pytest.raises(ValueError, match='identity'):
        validate_reference(replace(verified_optical_fixture, camera_serial='OTHER'))
    settings = tmp_path / 'settings.toml'
    settings.write_text(DEFAULT_PATH.read_text(encoding='utf-8'), encoding='utf-8')
    # Configure the independently measured safety envelope before recording WD.
    from machine_settings import save_values
    save_values('focusfield', dict(safe_z_min_mm=99, safe_z_max_mm=101, reference_z_mm=100), path=settings)
    saved = save_reference(replace(record, lens_reference_plane='Plane "A" \\ marker'), tmp_path / 'new.json', settings)
    assert not saved.focusfield.enabled
    assert validate_reference(load(settings).focusfield)['method'] == record.method
    with pytest.raises(ValueError, match='immutable'):
        save_reference(record, tmp_path / 'new.json', settings)


def test_guarded_xy_serial_failure_and_stop_never_dispatch_later_motion(monkeypatch, verified_optical_fixture):
    calls = []
    def move(**kwargs):
        calls.append(kwargs)
        raise RuntimeError('serial failed')
    gc = SimpleNamespace(connect=lambda: None, read_position=lambda **kwargs: (150, 150, 100), move_absolute=move,
                         motionstop=lambda: calls.append('STOP'), disconnect=lambda: None)
    monkeypatch.setitem(sys.modules, 'gantry_controls', gc)
    c = simulation_xy_calibration(verified_optical_fixture)
    stage = ZStage(verified_optical_fixture, 273, c, SETTINGS.printer.travel)
    stage.open()
    with pytest.raises(RuntimeError, match='serial failed'):
        stage.move_xy_absolute(150.1, 150.2)
    stage.stop()
    with pytest.raises(InterruptedError):
        stage.move_xy_absolute(150.2, 150.3)
    stage.close()
    assert len(calls) == 2 and calls[-1] == 'STOP'
