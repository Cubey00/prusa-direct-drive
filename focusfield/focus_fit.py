"""Bracketed local focus fits and separately labelled apparent tilt residuals."""
import numpy as np


def fit_peak(distances, metrics):
    x, y = np.asarray(distances, float), np.asarray(metrics, float)
    good = np.isfinite(x) & np.isfinite(y)
    x, y = x[good], y[good]
    unique = np.unique(x)
    # Collapse repeated frames at a distance before fitting; repeats are fitted separately.
    values = np.array([np.mean(y[x == z]) for z in unique])
    if len(unique) < 5:
        return {"valid": False, "reason": "fewer_than_five_positions"}
    i = int(np.argmax(values))
    if i < 2 or i > len(unique) - 3:
        return {"valid": False, "reason": "peak_not_bracketed"}
    if np.ptp(values) < 1e-5:
        return {"valid": False, "reason": "flat_curve"}
    xx, yy = unique[i-2:i+3], values[i-2:i+3]
    origin = float(unique[i])
    coeff, cov = np.polyfit(xx - origin, yy, 2, cov=True)
    a, b, c = coeff
    if a >= -1e-8:
        return {"valid": False, "reason": "non_concave_fit"}
    peak = origin - b / (2 * a)
    if not unique[i-1] <= peak <= unique[i+1]:
        return {"valid": False, "reason": "unstable_peak"}
    gradient = np.array([b / (2*a*a), -1 / (2*a), 0])
    sigma = float(np.sqrt(max(0, gradient @ cov @ gradient)))
    residual = float(np.sqrt(np.mean((np.polyval(coeff, xx - origin) - yy)**2)))
    return {"valid": True, "reason": "", "best_wd_mm": float(peak),
            "fit_uncertainty_mm": sigma, "fit_rmse": residual,
            "best_metric": float(np.polyval(coeff, peak - origin))}


def apparent_plane(points):
    """points are (x_mm, y_mm, best_wd_mm), not intrinsic lens curvature."""
    points = np.asarray(points, float)
    if len(points) < 3:
        return None
    design = np.column_stack([points[:, :2], np.ones(len(points))])
    if np.linalg.matrix_rank(design) < 3:
        return None
    coeff = np.linalg.lstsq(design, points[:, 2], rcond=None)[0]
    return coeff, points[:, 2] - design @ coeff
