import numpy as np
import pytest

from focusfield.focus_fit import apparent_plane, fit_peak


def test_known_peak_and_repeated_frames():
    x = np.repeat(np.linspace(19.5, 20.5, 21), 3)
    y = 0.8 - (x-20.12)**2
    fit = fit_peak(x, y)
    assert fit["valid"]
    assert fit["best_wd_mm"] == pytest.approx(20.12)
    assert fit["fit_uncertainty_mm"] < 1e-8


@pytest.mark.parametrize("x,y,reason", [([1, 2, 3], [1, 3, 2], "fewer_than_five_positions"),
    ([1, 2, 3, 4, 5], [1, 2, 3, 4, 5], "peak_not_bracketed"),
    ([1, 2, 3, 4, 5, 6, 7], [0, 0, 1e-7, 0, 0, 0, 0], "flat_curve")])
def test_invalid_peaks_are_flagged(x, y, reason):
    result = fit_peak(x, y)
    assert not result["valid"] and result["reason"] == reason


def test_apparent_tilt_is_fitted_and_labelled_separately():
    points = [[x, y, 20+0.1*x-0.2*y] for x in [-1, 0, 1] for y in [-1, 0, 1]]
    coefficients, residuals = apparent_plane(points)
    np.testing.assert_allclose(coefficients, [0.1, -0.2, 20], atol=1e-12)
    assert np.max(np.abs(residuals)) < 1e-12
    assert apparent_plane([[0, 0, 1], [1, 1, 2], [2, 2, 3]]) is None
