import numpy as np
import pytest
from scipy.special import erf

from focusfield.acquisition import analysis_plane
from focusfield.sfr import slanted_edge


def gaussian_edge(sigma, angle=5, shape=(180, 64)):
    yy, xx = np.indices(shape)
    slope = np.tan(np.radians(angle))
    normal = (xx-shape[1]/2-slope*(yy-shape[0]/2)) / np.sqrt(1+slope*slope)
    return 0.2 + 0.6 * (1 + erf(normal/(sigma*np.sqrt(2)))) / 2


@pytest.mark.parametrize("sigma", [0.7, 1, 1.5, 2, 3])
@pytest.mark.parametrize("horizontal", [False, True])
def test_sfr_matches_analytic_gaussian_reference(sigma, horizontal):
    edge = gaussian_edge(sigma)
    result = slanted_edge(edge.T if horizontal else edge, horizontal=horizontal)
    expected_mtf50 = np.sqrt(2*np.log(2)) / (2*np.pi*sigma)
    assert result.mtf50_cpp == pytest.approx(expected_mtf50, rel=0.08)
    assert result.fixed_mtf == pytest.approx(np.exp(-2*np.pi**2*sigma*sigma*0.1**2), abs=0.035)
    assert result.angle_deg == pytest.approx(5, abs=0.15)


@pytest.mark.parametrize("image", [np.ones((50, 50))*0.5, gaussian_edge(1, angle=0),
                                  gaussian_edge(1, angle=20), np.zeros((5, 5)), np.full((50, 50), np.nan)])
def test_bad_edge_data_is_rejected(image):
    with pytest.raises(ValueError):
        slanted_edge(image)


def test_bayer_green_sampling_has_correct_sensor_frequency_units():
    mosaic = np.full((360, 128), 4095, dtype=np.uint16)
    green = gaussian_edge(1.5)
    mosaic[::2, 1::2] = np.uint16(green*4095)
    plane, pitch = analysis_plane(mosaic, dict(pixel_format="BayerRG12", bit_depth=12))
    assert pitch == 2
    np.testing.assert_allclose(plane, green, atol=1/4095)
    result = slanted_edge(plane, sensor_pitch=pitch)
    assert result.mtf50_cpp == pytest.approx(np.sqrt(2*np.log(2))/(2*np.pi*1.5)/2, rel=0.08)
    with pytest.raises(ValueError, match="Nyquist"):
        slanted_edge(plane, sensor_pitch=2, fixed_frequency_cpp=0.3)
