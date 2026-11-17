import csv
from dataclasses import replace
import json
from pathlib import Path

import numpy as np
import pytest

from focusfield.chart import load_chart
from focusfield.config import Recipe, simulation_fixture
from focusfield.controller import ScanController
from focusfield.hardware import SimCamera, SimStage
from focusfield.reports import analyze_run, validate_run
from machine_settings import SETTINGS


@pytest.fixture(scope="module")
def optical_run(tmp_path_factory):
    recipe = Recipe(step_mm=0.1, repetitions=1, frames_per_position=1, settle_s=0)
    fixture = simulation_fixture()
    stage = SimStage(fixture)
    camera = SimCamera(stage, recipe, load_chart(recipe.chart_path))
    controller = ScanController(recipe, SETTINGS, fixture, stage, camera, tmp_path_factory.mktemp("optical"))
    result = controller.run()
    assert result.state == "COMPLETED", result.message
    return Path(result.path), recipe


def test_known_simulated_focus_surface_and_reports(optical_run):
    path, recipe = optical_run
    summary = json.loads((path / "analysis.json").read_text())
    assert summary["valid_frames"] == 11
    assert summary["valid_focus_fits"] >= 115
    assert summary["measurement_validated"] is False
    fits = list(csv.DictReader((path / "focus_results.csv").open()))
    errors = []
    for fit in fits:
        if fit["valid"] != "True":
            continue
        nx = (float(fit["x_mm"]) - 50)/30
        ny = (float(fit["y_mm"]) - 40)/22.5
        expected = 20 + 0.15*(nx*nx+ny*ny) + 0.06*nx - 0.04*ny
        errors.append(abs(float(fit["best_wd_mm"]) - expected))
    assert np.percentile(errors, 95) < 0.035
    for name in ("frames.csv", "sfr_results.csv", "focus_results.csv", "chart_detection.csv",
                 "focus_distance_map.png", "mtf_20mm_map.png", "best_mtf_map.png",
                 "apparent_tilt_residual_map.png", "through_focus_curves.png", "report.html"):
        assert (path / name).stat().st_size > 100
    report = (path / "report.html").read_text()
    assert "SIMULATED" in report and "not intrinsic Petzval curvature" in report


def test_manifest_validation_rejects_missing_count_changed_wd_and_duplicate_ids(optical_run):
    path, recipe = optical_run
    for kind in ("count", "wd", "ids", "path", "hash", "timestamp"):
        manifest = json.loads((path / "manifest.json").read_text())
        if kind == "count":
            manifest["frames"].pop()
        elif kind == "wd":
            manifest["frames"][0]["nominal_working_distance_mm"] += 0.1
        elif kind == "ids":
            manifest["frames"][1]["image_id"] = manifest["frames"][0]["image_id"]
        elif kind == "path":
            manifest["frames"][0]["image"] = "../../anything.tiff"
        elif kind == "hash":
            manifest["frames"][0]["sha256"] = "wrong"
        else:
            manifest["frames"][1]["host_monotonic_ns"] = manifest["frames"][0]["host_monotonic_ns"] - 1
        with pytest.raises(ValueError):
            validate_run(path, manifest, recipe)


def test_partial_run_requires_explicit_offline_option(optical_run):
    path, recipe = optical_run
    manifest = json.loads((path / "manifest.json").read_text())
    manifest["frames"].pop()
    manifest["state"] = "ABORTED"
    with pytest.raises(ValueError, match="Incomplete"):
        validate_run(path, manifest, recipe)
    validate_run(path, manifest, recipe, allow_partial=True)


def test_offline_reanalysis_has_no_hardware_dependency(optical_run, monkeypatch):
    import sys
    monkeypatch.setitem(sys.modules, "gantry_controls", None)
    monkeypatch.setitem(sys.modules, "ids_camera", None)
    summary = analyze_run(optical_run[0])
    assert summary["valid_focus_fits"] >= 115


def test_completed_acquisition_with_zero_usable_fits_is_not_a_valid_measurement(optical_run, monkeypatch):
    path, _ = optical_run
    monkeypatch.setattr('focusfield.reports.fit_peak', lambda *args: dict(valid=False, reason='peak_not_bracketed'))
    summary = analyze_run(path)
    assert summary['acquisition_complete'] and summary['analysis_complete']
    assert not summary['qa_passed'] and not summary['measurement_valid']
    assert summary['valid_focus_fits'] == 0
    assert any('center' in r for r in summary['qa_rejection_reasons'])
    assert any('peripheral' in r for r in summary['qa_rejection_reasons'])
    manifest = json.loads((path / 'manifest.json').read_text())
    assert not manifest['measurement_valid'] and manifest['analysis_complete']
    assert (path / 'images/frame_000001.tiff').is_file()
    assert 'Measurement valid: False' in (path / 'report.html').read_text()
