"""Offline reanalysis of preserved images; CSV, field plots and static HTML."""
import csv
import hashlib
import html
import json
from pathlib import Path

import numpy as np
import tifffile

from . import __version__
from .acquisition import Capture, FrameGuard
from .chart import edge_rois, load_chart, register_capture
from .config import Recipe
from .data import atomic_json
from .focus_fit import apparent_plane, fit_peak
from .sfr import slanted_edge
from .quality import assess_field, required_regions, usable_edges


def _csv(path, rows, fields):
    temp = path.with_suffix(".csv.tmp")
    with temp.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    temp.replace(path)


def validate_run(path, manifest, recipe, *, allow_partial=False, acquisition_complete=False):
    frames = manifest["frames"]
    if manifest.get("schema_version") != 1:
        raise ValueError("Unsupported run schema")
    if manifest.get("dry_run"):
        raise ValueError("A motion-only dry run has no images to analyze")
    if (not allow_partial and (len(frames) != manifest["expected_frames"]
            or (manifest["state"] != "COMPLETED" and not acquisition_complete))):
        raise ValueError("Incomplete run; explicitly choose partial reanalysis to inspect it")
    seen, images, times = set(), set(), []
    guard = FrameGuard()
    f = manifest["wd_calibration"]
    from machine_settings import FocusFixture
    fixture = FocusFixture(**f)
    expected = {(rep, round(z, 6)): 0 for rep, _, z in recipe.schedule(fixture)}
    for frame in frames:
        # Metadata-only ordering check; samples are validated when read below.
        timestamp = frame["host_monotonic_ns"]
        sequence = frame.get("capture_sequence")
        if guard.last_time is not None and (timestamp < guard.last_time or
                (timestamp == guard.last_time and sequence is None)):
            raise ValueError("Frame timestamps moved backwards or lack ordered sequence")
        if sequence is not None and guard.last_sequence is not None and sequence <= guard.last_sequence:
            raise ValueError("Duplicate or reordered host capture sequence")
        device_id = frame.get("device_frame_id")
        if device_id is not None and guard.last_device_id is not None and device_id <= guard.last_device_id:
            raise ValueError("Duplicate or reordered device frame ID")
        if device_id is None and guard.last_device_id is not None:
            raise ValueError("Device frame ID disappeared")
        guard.last_time, guard.last_sequence, guard.last_device_id = timestamp, sequence, device_id
        if frame["image_id"] in seen:
            raise ValueError("Duplicate image ID")
        seen.add(frame["image_id"])
        times.append(frame["host_monotonic_ns"])
        image = (path / frame["image"]).resolve()
        if image in images:
            raise ValueError("Duplicate frame image path")
        images.add(image)
        if not image.is_relative_to(path.resolve() / "images"):
            raise ValueError("Frame path outside run images directory")
        if not image.is_file() or hashlib.sha256(image.read_bytes()).hexdigest() != frame["sha256"]:
            raise ValueError(f"Missing or changed image: {frame['image']}")
        if (frame["wd_calibration_id"] != f["calibration_id"]
                or frame["pixel_format"] != recipe.pixel_format
                or frame["camera_serial"] != f["camera_serial"]):
            raise ValueError("Frame calibration, camera or pixel format changed within run")
        if not np.isfinite([frame["printer_z_reported_mm"], frame["printer_z_command_mm"],
                            frame["nominal_working_distance_mm"]]).all():
            raise ValueError("Nonfinite frame distance")
        if abs(frame["nominal_working_distance_mm"] - fixture.wd_for_z(frame["printer_z_reported_mm"])) > 1e-6:
            raise ValueError("Frame working distance disagrees with WD calibration")
        if abs(frame["printer_z_command_mm"] - frame["printer_z_reported_mm"]) > fixture.position_tolerance_mm:
            raise ValueError("Frame position outside tolerance")
        key = (frame["repetition"], round(frame["printer_z_command_mm"], 6))
        if key not in expected:
            raise ValueError("Frame not in recipe schedule")
        expected[key] += 1
    if not allow_partial and any(n != recipe.frames_per_position for n in expected.values()):
        raise ValueError("Incorrect frame count at a scheduled position")


def analyze_run(path, *, allow_partial=False, should_abort=lambda: False, acquisition_complete=False,
                on_progress=lambda *args: None):
    path = Path(path)
    manifest = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
    recipe = Recipe(**json.loads((path / "config.json").read_text(encoding="utf-8")))
    chart = load_chart(path / "chart_layout.json")
    validate_run(path, manifest, recipe, allow_partial=allow_partial, acquisition_complete=acquisition_complete)
    results, detection = [], []
    for index, frame in enumerate(manifest["frames"]):
        if should_abort():
            raise InterruptedError("Analysis stopped")
        pixels = tifffile.imread(path / frame["image"])
        capture = Capture(pixels, frame).validate()
        base = dict(image_id=frame["image_id"], repetition=frame["repetition"],
                    wd_mm=frame["nominal_working_distance_mm"],
                    commanded_wd_mm=(manifest['wd_calibration']['reference_wd_mm']
                        + frame['printer_z_command_mm'] - manifest['wd_calibration']['reference_z_mm']))
        try:
            registration, gray, pitch = register_capture(capture, chart, recipe)
            h, error = registration['homography'], registration['reprojection_rms_px']
            detection.append(dict(**base, valid=True, reason="", marker_error_px=error))
        except ValueError as exc:
            detection.append(dict(**base, valid=False, reason=str(exc)))
            continue
        offset_x = 1 if registration["lens_corrected"] and pitch == 2 else 0
        for square in chart["slanted_squares"]:
            region = f"r{square['row']}_c{square['col']}"
            info = dict(**base, region=region, x_mm=square["center_mm"][0], y_mm=square["center_mm"][1])
            try:
                rois = edge_rois(gray, h, square, project_chart=registration["project_chart"])
            except ValueError as exc:
                for edge in ("top", "bottom", "left", "right"):
                    results.append(dict(**info, edge=edge, valid=False, reason=str(exc)))
                continue
            for label, horizontal, roi, px, py in rois:
                row = dict(**info, edge=label, x_px=px*pitch+offset_x, y_px=py*pitch)
                try:
                    sfr = slanted_edge(roi, recipe.fixed_frequency_cpp, horizontal=horizontal, sensor_pitch=pitch,
                                       quality=recipe.quality)
                    row.update(valid=True, reason="", mtf50_cpp=sfr.mtf50_cpp, fixed_mtf=sfr.fixed_mtf,
                               angle_deg=sfr.angle_deg, contrast=sfr.contrast)
                except ValueError as exc:
                    row.update(valid=False, reason=str(exc))
                results.append(row)
        on_progress(index+1, len(manifest["frames"]))
    fits = []
    for square in chart["slanted_squares"]:
        region = f"r{square['row']}_c{square['col']}"
        for edge in ("top", "bottom", "left", "right"):
            for repetition in range(recipe.repetitions):
                rows = [r for r in results if r["region"] == region and r["edge"] == edge
                        and r["repetition"] == repetition and r["valid"]]
                fit = fit_peak([r["wd_mm"] for r in rows], [r["fixed_mtf"] for r in rows])
                fit.update(region=region, edge=edge, repetition=repetition,
                           scan_direction="reverse" if recipe.reverse_alternate and repetition % 2 else "forward",
                           x_mm=square["center_mm"][0], y_mm=square["center_mm"][1],
                           nominal_fixed_mtf=None, nominal_mtf50_cpp=None)
                # Nominal sharpness must come from that requested command, not
                # an interpolated value or the midpoint/peak of a focus sweep.
                nominal_rows = [r for r in rows if abs(r['commanded_wd_mm']-recipe.nominal_wd_mm) < 1e-6]
                for metric, key in (("fixed_mtf", "nominal_fixed_mtf"), ("mtf50_cpp", "nominal_mtf50_cpp")):
                    values = [r[metric] for r in nominal_rows if r.get(metric) is not None]
                    if values:
                        fit[key] = float(np.mean(values))
                fits.append(fit)
    for fit in fits:
        peers = [f["best_wd_mm"] for f in fits if f["valid"] and f["region"] == fit["region"]
                 and f["edge"] == fit["edge"]]
        fit["repeatability_std_mm"] = float(np.std(peers, ddof=1)) if len(peers) > 1 else None
        fit["wd_calibration_uncertainty_mm"] = manifest["wd_calibration"]["wd_uncertainty_mm"]
    for orientation, edges in (("horizontal", {"top", "bottom"}), ("vertical", {"left", "right"})):
        valid = [f for f in fits if f["valid"] and f["edge"] in edges]
        plane = apparent_plane([[f["x_mm"], f["y_mm"], f["best_wd_mm"]] for f in valid])
        if plane:
            coeff, residual = plane
            for fit, delta in zip(valid, residual):
                fit.update(orientation=orientation, apparent_plane_x=float(coeff[0]),
                           apparent_plane_y=float(coeff[1]), apparent_plane_intercept=float(coeff[2]),
                           apparent_plane_residual_mm=float(delta))
    _csv(path / "chart_detection.csv", detection,
         ["image_id", "repetition", "wd_mm", "commanded_wd_mm", "valid", "reason", "marker_error_px"])
    _csv(path / "sfr_results.csv", results,
         ["image_id", "repetition", "wd_mm", "commanded_wd_mm", "region", "edge", "x_mm", "y_mm", "x_px", "y_px",
          "valid", "reason", "mtf50_cpp", "fixed_mtf", "angle_deg", "contrast"])
    fields = ["region", "edge", "repetition", "scan_direction", "x_mm", "y_mm", "valid", "reason", "best_wd_mm",
              "fit_uncertainty_mm", "fit_rmse", "best_metric", "nominal_fixed_mtf", "nominal_mtf50_cpp",
              "repeatability_std_mm", "wd_calibration_uncertainty_mm", "orientation", "apparent_plane_x",
              "apparent_plane_y", "apparent_plane_intercept", "apparent_plane_residual_mm"]
    _csv(path / "focus_results.csv", fits, fields)
    valid_count = sum(f["valid"] for f in fits)
    qa_reasons = assess_field(chart, fits, detection, recipe)
    required = required_regions(chart)
    f = manifest['wd_calibration']
    nominal_z = f['reference_z_mm'] + recipe.nominal_wd_mm - f['reference_wd_mm']
    nominal_frames = [frame for frame in manifest['frames'] if abs(frame['printer_z_command_mm']-nominal_z) < 1e-6]
    nominal_measured = all(len([frame for frame in nominal_frames if frame['repetition'] == rep])
                           >= recipe.frames_per_position for rep in range(recipe.repetitions))
    nominal_usable = True
    if not nominal_measured:
        qa_reasons.append(f"Separate measurement at requested {recipe.nominal_wd_mm:g} mm WD is required; "
                          "no complete acquisition at that commanded plane")
        nominal_usable = False
    else:
        for rep in range(recipe.repetitions):
            regions = {region for region in required if usable_edges({row['edge'] for row in results
                       if row['region'] == region and row['repetition'] == rep and row['valid']
                       and abs(row['commanded_wd_mm']-recipe.nominal_wd_mm) < 1e-6}, recipe.quality)}
            if required[0] not in regions or len(regions & set(required[1:])) < recipe.quality.min_peripheral_regions:
                nominal_usable = False
                qa_reasons.append(f"Sweep {rep}: requested nominal-plane center/peripheral edges are unusable")
    good_frames = 0
    for frame in manifest["frames"]:
        regions = {r for r in required if usable_edges({row['edge'] for row in results
                   if row["image_id"] == frame["image_id"] and row["region"] == r and row["valid"]}, recipe.quality)}
        if required[0] in regions and len(regions & set(required[1:])) >= recipe.quality.min_peripheral_regions:
            good_frames += 1
    if good_frames / max(1, len(manifest["frames"])) < recipe.quality.min_valid_frame_fraction:
        qa_reasons.append("Insufficient frames with usable center/peripheral edges (blur, clipping or edge-fit failure)")
    reasons = list(qa_reasons)
    if allow_partial:
        reasons.append("Partial acquisition")
    if manifest["simulated"]:
        reasons.append("Synthetic data; physical measurement not validated")
    if recipe.pixel_format == "RGB8":
        reasons.append("RGB8 processing is provisional for quantitative MTF")
    f = manifest["wd_calibration"]
    if not (f.get("sfr_reference_verified") and f.get("sfr_validation_id")):
        reasons.append("Independent same-image SFR reference validation has not been verified")
    if not all(f.get(k) for k in ("motion_safety_verified", "camera_pipeline_verified", "frame_timing_verified", "wd_reference_record")):
        reasons.append("Physical fixture, WD reference, camera pipeline or exposure timing not verified")
    summary = dict(analysis_version=__version__, acquisition_state=manifest["state"],
                   partial=allow_partial, simulated=manifest["simulated"], measurement_validated=False,
                   valid_focus_fits=valid_count, total_focus_fits=len(fits),
                   valid_frames=sum(d["valid"] for d in detection), total_frames=len(detection),
                   fixed_frequency_cpp=recipe.fixed_frequency_cpp)
    summary.update(acquisition_complete=not allow_partial, analysis_complete=True,
                   requested_nominal_wd_mm=recipe.nominal_wd_mm, nominal_plane_measured=nominal_measured,
                   nominal_plane_qa_passed=nominal_usable, separate_nominal_plane_required=not nominal_measured,
                   qa_passed=not qa_reasons, qa_rejection_reasons=qa_reasons,
                   measurement_valid=not reasons, measurement_validated=not reasons,
                   measurement_rejection_reasons=reasons, usable_field_frames=good_frames)
    if should_abort():
        raise InterruptedError("Analysis stopped")
    _plots(path, fits, results, recipe.nominal_wd_mm)
    atomic_json(path / "analysis.json", summary)
    _html(path, summary, recipe, fits)
    # Keep online and offline status consistent without touching acquired originals.
    manifest.update({k: summary[k] for k in ("acquisition_complete", "analysis_complete", "qa_passed", "qa_rejection_reasons", "measurement_valid", "measurement_validated",
                                           "requested_nominal_wd_mm", "nominal_plane_measured", "nominal_plane_qa_passed", "separate_nominal_plane_required",
                                           "measurement_rejection_reasons")})
    atomic_json(path / "manifest.json", manifest)
    return summary


def _plots(path, fits, rows, nominal):
    # FigureCanvasAgg avoids a GUI backend and pyplot's process-global figure registry.
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    maps = [("best_wd_mm", "focus_distance_map.png", "Apparent best-focus WD (mm)"),
            ("nominal_fixed_mtf", "mtf_20mm_map.png", f"MTF at fixed nominal {nominal:g} mm plane"),
            ("best_metric", "best_mtf_map.png", "Best fitted regional fixed-frequency MTF"),
            ("apparent_plane_residual_mm", "apparent_tilt_residual_map.png", "Residual after apparent tilt plane (mm)")]
    for metric, filename, title in maps:
        fig = Figure(figsize=(11, 4.5), layout="constrained")
        FigureCanvasAgg(fig)
        for index, (name, edges) in enumerate((("horizontal edges", {"top", "bottom"}),
                                               ("vertical edges", {"left", "right"}))):
            ax = fig.add_subplot(1, 2, index+1)
            use = [f for f in fits if f["edge"] in edges and f.get(metric) is not None
                   and (metric == "nominal_fixed_mtf" or f["valid"])]
            if use:
                # Average repeats and opposite sides for maps; CSV retains every individual fit.
                points = sorted({(f["x_mm"], f["y_mm"]) for f in use})
                values = [np.mean([f[metric] for f in use if (f["x_mm"], f["y_mm"]) == p]) for p in points]
                scatter = ax.scatter(*np.array(points).T, c=values, s=130, marker="s")
                fig.colorbar(scatter, ax=ax)
            else:
                ax.text(0.5, 0.5, "No valid data", transform=ax.transAxes, ha="center")
            ax.set(title=name, xlabel="Chart X (mm)", ylabel="Chart Y (mm)")
            ax.invert_yaxis()
        fig.suptitle(title + " — provisional system measurement")
        fig.savefig(path / filename, dpi=140)
    fig = Figure(figsize=(10, 5), layout="constrained")
    FigureCanvasAgg(fig)
    ax = fig.add_subplot()
    for key in sorted({(r["region"], r["edge"], r["repetition"]) for r in rows if r["valid"]}):
        use = [r for r in rows if r["valid"] and (r["region"], r["edge"], r["repetition"]) == key]
        xs = sorted({r["wd_mm"] for r in use})
        ys = [np.mean([r["fixed_mtf"] for r in use if r["wd_mm"] == x]) for x in xs]
        ax.plot(xs, ys, alpha=0.25, linewidth=0.8)
    ax.axvline(nominal, color="black", linestyle="--", label="fixed nominal plane")
    ax.set(xlabel="Nominal working distance (mm)", ylabel="Fixed-frequency MTF", title="Regional through-focus curves")
    ax.legend()
    fig.savefig(path / "through_focus_curves.png", dpi=140)


def _html(path, summary, recipe, fits):
    status = "SIMULATED" if summary["simulated"] else "PROVISIONAL — hardware/processing validation required"
    rows = "".join("<tr>" + "".join(f"<td>{html.escape(str(f.get(k, '')))}</td>" for k in
                   ("region", "edge", "repetition", "valid", "best_wd_mm", "fit_uncertainty_mm", "reason")) + "</tr>" for f in fits)
    plots = "".join(f'<figure><img width="950" src="{f}"><figcaption>{label}</figcaption></figure>' for f, label in
                    (("focus_distance_map.png", "Apparent best focus distance; not intrinsic Petzval curvature"),
                     ("mtf_20mm_map.png", f"Fixed nominal {recipe.nominal_wd_mm:g} mm plane"),
                     ("best_mtf_map.png", "Best attainable fitted regional metric"),
                     ("apparent_tilt_residual_map.png", "Residual after separately fitted apparent plane"),
                     ("through_focus_curves.png", "Through-focus curves; orientations and repeats preserved in CSV")))
    report = f'''<!doctype html><html lang="en"><meta charset="utf-8"><title>Focus-Field results</title>
<style>body{{font:16px system-ui;max-width:1100px;margin:40px auto}}td,th{{padding:6px;border:1px solid #ccc}}
table{{border-collapse:collapse}}img{{max-width:100%}}.notice{{background:#fff1c2;padding:16px}}</style>
<h1>Focus-Field / MTF</h1><p class="notice">{status}. Partial analysis: {summary['partial']}.</p>
<p>Acquisition complete: {summary['acquisition_complete']}. Analysis complete: {summary['analysis_complete']}.
Data QA passed: {summary['qa_passed']}. Measurement valid: {summary['measurement_valid']}.</p>
<p>Requested nominal WD: {recipe.nominal_wd_mm:g} mm. Nominal-plane command acquired: {summary['nominal_plane_measured']}.
Separate nominal-plane measurement required: {summary['separate_nominal_plane_required']}.
Absolute physical WD retains the fixture calibration uncertainty.</p>
<ul>{''.join('<li>' + html.escape(r) + '</li>' for r in summary['measurement_rejection_reasons'])}</ul>
<p>Valid focus fits: {summary['valid_focus_fits']} / {summary['total_focus_fits']}.
Valid chart frames: {summary['valid_frames']} / {summary['total_frames']}.</p>
<p>Frequency: {recipe.fixed_frequency_cpp:g} cycles per original sensor pixel. RGB8 uses the green channel;
BayerRG uses one green sublattice with pitch 2 sensor pixels (Nyquist 0.25 cycles/sensor pixel).
No demosaicing or geometric resampling in SFR. Gaussian-reference tested algorithm; ISO conformance is not established.</p>
<p>Best-focus variation includes chart/bed tilt, target flatness, alignment, lens and sensor effects.
Fit uncertainty is statistical only; WD calibration uncertainty and repeatability are separate CSV columns.
Command precision and reported position do not establish physical stage accuracy.</p>
<p><a href="frames.csv">Frames</a> · <a href="chart_detection.csv">Chart detections</a> ·
<a href="sfr_results.csv">Per-edge SFR</a> · <a href="focus_results.csv">Focus fits</a> ·
<a href="manifest.json">Manifest</a></p>{plots}<table><tr>
<th>Region</th><th>Edge</th><th>Repeat</th><th>Valid</th><th>Best WD (mm)</th><th>Fit uncertainty (mm)</th><th>Reason</th>
</tr>{rows}</table></html>'''
    (path / "report.html").write_text(report, encoding="utf-8")
