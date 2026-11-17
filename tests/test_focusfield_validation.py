from dataclasses import asdict, replace
import hashlib
import json

import cv2
import numpy as np
import pytest
from scipy.special import erf
import tifffile

from focusfield.acquisition import Capture, capture_metadata
from focusfield.camera_info import peak_inventory, validate_raw_geometry
from focusfield.chart import load_chart, regional_metrics, register_chart
from focusfield.config import Recipe, simulation_fixture
from focusfield.hardware import SimCamera, SimStage
from focusfield.quality import QualityThresholds, assess_field, required_regions
from focusfield.validation import compare_reference


def test_inventory_filters_unavailable_native_formats_and_records_unknown_processing():
    from types import SimpleNamespace
    class Entry:
        def __init__(self, name, status): self.name, self.status = name, status
        def SymbolicValue(self): return self.name
        def AccessStatus(self): return self.status
    class Node:
        def Value(self): raise RuntimeError('enum')
        def CurrentEntry(self): return Entry('BayerRG12g24IDS', 2)
        def Entries(self): return [Entry('BayerRG8', 2), Entry('Mono12', 0), Entry('BayerRG12g24IDS', 2)]
    class Map:
        def FindNode(self, name):
            if name == 'PixelFormat': return Node()
            raise RuntimeError('not available')
    inventory = peak_inventory(Map(), SimpleNamespace(NodeAccessStatus_NotAvailable=0, NodeAccessStatus_NotImplemented=1))
    assert inventory['available_pixel_formats'] == ['BayerRG8', 'BayerRG12g24IDS']
    assert inventory['native_pixel_format'] == 'BayerRG12g24IDS'
    assert inventory['features']['Gamma'] is None and not inventory['hardware_validated']


@pytest.mark.parametrize('fmt', ['BayerRG12g24IDS', 'BayerGR12', 'Mono12p', 'BayerRG16', 'Unknown12'])
def test_unsupported_native_formats_are_not_silently_interpreted(fmt):
    with pytest.raises(ValueError, match='Unsupported format'):
        capture_metadata(fmt, 'CAM', 1, 0)


@pytest.mark.parametrize('feature,value', [('OffsetX', 1), ('OffsetY', None), ('ReverseX', True),
                                         ('BinningHorizontal', 2), ('DecimationVertical', 2)])
def test_raw_bayer_geometry_must_match_sampling_lattice(feature, value):
    f = dict(OffsetX=0, OffsetY=0, ReverseX=False, ReverseY=False,
             BinningHorizontal=1, BinningVertical=1, DecimationHorizontal=1, DecimationVertical=1)
    f[feature] = value
    with pytest.raises(ValueError):
        validate_raw_geometry({'features': f}, 'BayerRG12')


def test_capture_format_dtype_and_cfa_are_strict():
    m = capture_metadata('BayerRG12', 'CAM', 1, 0)
    with pytest.raises(ValueError, match='dtype'):
        Capture(np.ones((16, 16), dtype=np.uint8), m).validate()
    with pytest.raises(ValueError, match='CFA'):
        Capture(np.ones((16, 16), dtype=np.uint16), dict(m, cfa_layout='GRBG')).validate()


def test_quality_thresholds_snapshot_and_required_field_coverage():
    recipe = Recipe(quality={'min_peripheral_regions': 3})
    assert Recipe(**recipe.to_dict()).quality == recipe.quality
    assert recipe.to_dict()['quality']['min_peripheral_regions'] == 3
    chart = load_chart(recipe.chart_path)
    center, *peripheral = required_regions(chart)
    fits = [dict(valid=True, region=r, edge=e, repetition=rep) for r in [center, *peripheral]
            for e in ['left', 'top'] for rep in range(recipe.repetitions)]
    assert not assess_field(chart, fits, [{'valid': True}], recipe)
    fits = [f for f in fits if f['region'] != center]
    assert any('center' in r for r in assess_field(chart, fits, [{'valid': True}], recipe))
    with pytest.raises(ValueError):
        QualityThresholds(max_clipped_fraction=0.9)


def test_strong_barrel_distortion_or_missing_corner_is_explicitly_rejected():
    recipe = Recipe(pixel_format='Mono16')
    chart = load_chart(recipe.chart_path)
    camera = SimCamera(SimStage(simulation_fixture()), recipe, chart)
    capture = camera.capture()
    image = capture.pixels/65535
    yy, xx = np.indices(image.shape, dtype=np.float32)
    nx, ny = (xx-800)/800, (yy-640)/640
    radial = 1+0.8*(nx*nx+ny*ny)
    distorted = cv2.remap(image, 800+nx*radial*800, 640+ny*radial*640, cv2.INTER_LINEAR)
    with pytest.raises(ValueError, match='markers|reprojection'):
        register_chart(distorted, chart)
    image[260:430, 340:510] = 0.82  # remove the configured top-left marker
    with pytest.raises(ValueError, match='All four'):
        register_chart(image, chart)


def test_perspective_tilt_retains_registration_without_warping_measurement_pixels():
    recipe = Recipe(pixel_format='Mono16')
    chart = load_chart(recipe.chart_path)
    camera = SimCamera(SimStage(simulation_fixture()), recipe, chart)
    frame = camera.capture()
    h = np.array([[1, 0.01, -5], [0.005, 1, -5], [0.000005, 0.000002, 1.]])
    # Warp only the synthetic image to create a test scene, never the SFR input.
    tilted = cv2.warpPerspective(frame.pixels, h, (1600, 1280), borderValue=50000)
    candidate = Capture(tilted, frame.metadata.copy())
    registration, metrics, _ = regional_metrics(candidate, chart, recipe)
    assert registration['full_field_visible'] and len(metrics) == 5


@pytest.mark.parametrize('fmt,pitch', [('Mono16', 1), ('BayerRG12', 2)])
def test_same_raw_roi_comparison_against_independent_analytic_transfer(tmp_path, fmt, pitch):
    yy, xx = np.indices((180, 64))
    slope = np.tan(np.deg2rad(5))
    sigma = 1.5
    normal = (xx-32-slope*(yy-90))/np.sqrt(1+slope*slope)
    edge = 0.2+0.6*(1+erf(normal/(sigma*np.sqrt(2))))/2
    bits = 12 if pitch == 2 else 16
    if pitch == 2:
        pixels = np.full((360, 128), 4095, np.uint16)
        pixels[::2, 1::2] = np.uint16(edge*4095)
    else:
        pixels = np.uint16(edge*65535)
    image = tmp_path / 'raw.tiff'
    tifffile.imwrite(image, pixels)
    frequencies = np.linspace(0, 0.5/pitch, 1001)
    reference = dict(tool='Independent analytic Gaussian transfer (not SFRMAT hardware validation)',
        tool_version='closed-form-1', settings='Linear normalized native samples, no gamma or resampling',
        image_sha256=hashlib.sha256(image.read_bytes()).hexdigest(), pixel_format=fmt, bit_depth=bits,
        roi_sensor_xywh=[0, 0, pixels.shape[1], pixels.shape[0]], horizontal=False,
        frequencies_cpp=frequencies.tolist(), mtf=np.exp(-2*np.pi**2*sigma*sigma*(frequencies*pitch)**2).tolist(),
        fixed_frequency_cpp=0.1)
    result = compare_reference(image, reference, mtf50_relative_tolerance=0.08, fixed_mtf_absolute_tolerance=0.035)
    assert result['passed'] and result['sensor_pitch'] == pitch
    assert not result['measurement_validated']
    with pytest.raises(ValueError, match='same raw image'):
        compare_reference(image, dict(reference, image_sha256='wrong'),
                          mtf50_relative_tolerance=0.08, fixed_mtf_absolute_tolerance=0.035)
