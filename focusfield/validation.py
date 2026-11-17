"""Supervised camera evidence capture and same-image external SFR comparison.

These utilities never control the printer or automatically attest calibration.
"""
import hashlib
import json
from pathlib import Path

import numpy as np
import tifffile

from .acquisition import CameraAdapter, FrameGuard, analysis_plane
from .config import Recipe
from .data import atomic_json, storage_preflight
from .sfr import slanted_edge


def camera_check(output, pixel_format, frames=3):
    if pixel_format == 'RGB8' or not 2 <= frames <= 20:
        raise ValueError("Camera evidence requires a native raw format and 2..20 frames")
    camera = CameraAdapter(Recipe(pixel_format=pixel_format))
    path = Path(output)
    path.mkdir(parents=True, exist_ok=False)
    guard = FrameGuard()
    evidence = dict(hardware_validated=False, printer_motion=False, captures=[])
    try:
        camera.open()
        evidence['inventory'] = camera.camera.diagnostics()
        # One pre-restart sample anchors the sequence fence. It is retained too.
        for index in range(frames):
            if index:
                camera.fresh()
            capture = camera.capture()
            guard.check(capture)
            storage_preflight(path, frames-index, capture.pixels.nbytes)
            name = f'raw_{index:03d}.tiff'
            tifffile.imwrite(path / name, capture.pixels)
            evidence['captures'].append(dict(image=name, sha256=hashlib.sha256((path/name).read_bytes()).hexdigest(),
                dtype=str(capture.pixels.dtype), shape=list(capture.pixels.shape),
                minimum=int(capture.pixels.min()), maximum=int(capture.pixels.max()), metadata=capture.metadata))
    finally:
        camera.close()
        atomic_json(path / 'camera_evidence.json', evidence)
    return evidence


def compare_reference(image_path, reference, *, mtf50_relative_tolerance, fixed_mtf_absolute_tolerance):
    """Compare the exact, unwarped sensor ROI against an externally exported curve.

    Tolerances must be supplied by the investigator. Passing is a software
    comparison, not an optical pass/fail specification or ISO conformance.
    """
    if (not np.isfinite([mtf50_relative_tolerance, fixed_mtf_absolute_tolerance]).all()
            or not 0 < mtf50_relative_tolerance < 1 or not 0 < fixed_mtf_absolute_tolerance < 1):
        raise ValueError("Supply finite positive comparison tolerances below 1")
    for key in ('tool', 'tool_version', 'settings', 'image_sha256', 'pixel_format', 'bit_depth',
                'roi_sensor_xywh', 'horizontal', 'frequencies_cpp', 'mtf', 'fixed_frequency_cpp'):
        if key not in reference:
            raise ValueError(f"External reference missing {key}")
    if not reference['tool'] or not reference['tool_version'] or not reference['settings']:
        raise ValueError("Reference tool, version and processing/settings provenance are required")
    if type(reference['horizontal']) is not bool:
        raise ValueError("Reference edge orientation must be a boolean")
    path = Path(image_path)
    if hashlib.sha256(path.read_bytes()).hexdigest() != reference['image_sha256']:
        raise ValueError("Reference and analysis must use the same raw image SHA-256")
    pixels = tifffile.imread(path)
    from .acquisition import Capture, capture_metadata
    metadata = capture_metadata(reference['pixel_format'], 'REFERENCE', 1, 0)
    metadata['bit_depth'] = reference['bit_depth']
    Capture(pixels, metadata).validate()
    plane, pitch = analysis_plane(pixels, metadata)
    roi = reference['roi_sensor_xywh']
    if (len(roi) != 4 or any(type(v) is not int or v % int(pitch) for v in roi)
            or min(roi[:2]) < 0 or min(roi[2:]) < 16*pitch):
        raise ValueError("Reference ROI must fit sensor coordinates and the native analysis sampling lattice")
    x, y, w, h = [int(v/pitch) for v in roi]
    if x+w > plane.shape[1] or y+h > plane.shape[0]:
        raise ValueError("Reference ROI crosses image border")
    result = slanted_edge(plane[y:y+h, x:x+w], reference['fixed_frequency_cpp'],
                         horizontal=reference['horizontal'], sensor_pitch=pitch)
    frequencies, mtf = np.asarray(reference['frequencies_cpp'], float), np.asarray(reference['mtf'], float)
    if (frequencies.ndim != 1 or mtf.shape != frequencies.shape or len(mtf) < 3
            or not np.isfinite([frequencies, mtf]).all() or np.any(np.diff(frequencies) <= 0)
            or frequencies[0] != 0 or not 0.95 <= mtf[0] <= 1.05 or np.any(mtf < 0)
            or not frequencies[0] < reference['fixed_frequency_cpp'] < frequencies[-1] <= 0.5/pitch):
        raise ValueError("Invalid external curve or frequency units (cycles per original sensor pixel required)")
    crossing = np.flatnonzero(mtf <= 0.5)
    if not len(crossing) or crossing[0] == 0 or result.mtf50_cpp is None:
        raise ValueError("Both curves must bracket MTF50")
    i = crossing[0]
    reference50 = float(np.interp(0.5, mtf[i-1:i+1][::-1], frequencies[i-1:i+1][::-1]))
    reference_fixed = float(np.interp(reference['fixed_frequency_cpp'], frequencies, mtf))
    error50 = abs(result.mtf50_cpp-reference50)/reference50
    error_fixed = abs(result.fixed_mtf-reference_fixed)
    return dict(tool=reference['tool'], tool_version=reference['tool_version'], settings=reference['settings'],
                image_sha256=reference['image_sha256'], roi_sensor_xywh=roi,
                sensor_pitch=pitch, mtf50_relative_error=error50, fixed_mtf_absolute_error=error_fixed,
                mtf50_relative_tolerance=mtf50_relative_tolerance,
                fixed_mtf_absolute_tolerance=fixed_mtf_absolute_tolerance,
                passed=error50 <= mtf50_relative_tolerance and error_fixed <= fixed_mtf_absolute_tolerance,
                measurement_validated=False)
