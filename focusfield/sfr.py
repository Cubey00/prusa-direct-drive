"""Slanted-edge SFR prototype with explicit sampling units.

Fit a straight edge from derivative centroids, project original samples onto its
normal, bin at 4x, differentiate, Tukey-window and correct the central derivative
transfer function. No image rotation/resampling. Validated against Gaussian edges
in tests; this is not an ISO 12233 conformance claim.
"""
from dataclasses import dataclass
import numpy as np
from scipy.signal.windows import tukey
from .quality import QualityThresholds


@dataclass
class SFRResult:
    mtf50_cpp: float | None
    fixed_mtf: float
    angle_deg: float
    contrast: float
    frequencies_cpp: np.ndarray
    mtf: np.ndarray


def slanted_edge(roi, fixed_frequency_cpp=0.1, oversampling=4, *, horizontal=False,
                 sensor_pitch=1.0, quality=None):
    quality = quality or QualityThresholds()
    a = np.asarray(roi, dtype=float)
    if horizontal:
        a = a.T
    if a.ndim != 2 or min(a.shape) < 16 or not np.isfinite(a).all():
        raise ValueError("ROI must be finite, 2D and at least 16 pixels on each side")
    if not 0 < fixed_frequency_cpp < 0.5 / sensor_pitch:
        raise ValueError("Fixed frequency exceeds analysis sampling Nyquist")
    low, high = np.percentile(a, [10, 90])
    contrast = (high - low) / max(high + low, 1e-12)
    if contrast < quality.min_contrast or high - low < quality.min_edge_span:
        raise ValueError("Low edge contrast")
    if np.mean((a <= 0) | (a >= 1)) > quality.max_clipped_fraction:
        raise ValueError("Clipped edge ROI")
    derivative = np.abs(np.diff(a, axis=1))
    x = np.arange(a.shape[1] - 1) + 0.5
    # Restrict fitting to the strongest local edge in each row.
    peak = derivative.argmax(axis=1)
    weights = derivative * (np.abs(x[None, :] - peak[:, None]) <= 5)
    mass = weights.sum(axis=1)
    y = np.arange(a.shape[0], dtype=float)
    good = mass > (high - low) * 0.3
    if good.sum() < 0.8 * len(y):
        raise ValueError("Edge not visible throughout ROI")
    centres = (weights * x).sum(axis=1) / np.maximum(mass, 1e-12)
    slope, intercept = np.polyfit(y[good], centres[good], 1)
    if np.std(centres[good] - (slope * y[good] + intercept)) > quality.max_edge_residual_px:
        raise ValueError("Edge is curved, distorted or poorly localized")
    angle = float(np.degrees(np.arctan(abs(slope))))
    if not quality.min_angle_deg <= angle <= quality.max_angle_deg:
        raise ValueError(f"Edge angle {angle:.2f} degrees outside configured limits")
    yy, xx = np.indices(a.shape)
    distance = (xx - slope * yy - intercept) / np.sqrt(1 + slope * slope)
    radius = min(32, int(min(-distance.min(), distance.max())) - 2)
    if radius < 4:
        raise ValueError("Insufficient edge margin")
    bins = np.arange(-radius, radius + 1 / oversampling, 1 / oversampling)
    sums, _ = np.histogram(distance, bins=bins, weights=a)
    counts, _ = np.histogram(distance, bins=bins)
    if np.any(counts == 0):
        raise ValueError("Insufficient subpixel phase coverage")
    esf = sums / counts
    lsf = np.gradient(esf, 1 / oversampling)
    # A broad flat centre avoids artificially sharpening broad, defocused LSFs.
    lsf *= tukey(len(lsf), alpha=0.25)
    mtf = np.abs(np.fft.rfft(lsf))
    if mtf[0] < 1e-10:
        raise ValueError("Degenerate edge")
    mtf /= mtf[0]
    frequency = np.fft.rfftfreq(len(lsf), d=1 / oversampling)
    mtf *= np.minimum(10, 1 / np.maximum(np.sinc(2 * frequency / oversampling), 1e-9))
    use = frequency <= 0.5
    frequency, mtf = frequency[use] / sensor_pitch, mtf[use]
    crossing = np.flatnonzero(mtf <= 0.5)
    mtf50 = None
    if len(crossing) and crossing[0] > 0:
        i = crossing[0]
        mtf50 = float(np.interp(0.5, mtf[i-1:i+1][::-1], frequency[i-1:i+1][::-1]))
    return SFRResult(mtf50, float(np.interp(fixed_frequency_cpp, frequency, mtf)), angle,
                     float(contrast), frequency, mtf)
