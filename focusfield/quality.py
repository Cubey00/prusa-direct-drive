"""Data-usability gates; these are not lens acceptance specifications."""
from dataclasses import dataclass
import math

import numpy as np


@dataclass(frozen=True)
class QualityThresholds:
    min_contrast: float = 0.1
    min_edge_span: float = 0.05
    max_clipped_fraction: float = 0.02
    min_angle_deg: float = 2.0
    max_angle_deg: float = 12.0
    max_edge_residual_px: float = 0.8
    max_marker_reprojection_px: float = 3.0
    min_valid_frame_fraction: float = 0.9
    min_edges_per_region: int = 2
    min_peripheral_regions: int = 4

    def __post_init__(self):
        if not all(math.isfinite(v) for v in vars(self).values()):
            raise ValueError("Quality thresholds must be finite")
        if not (0 < self.min_contrast < 1 and 0 < self.min_edge_span < 1
                and 0 <= self.max_clipped_fraction < 0.5
                and 0 < self.min_angle_deg < self.max_angle_deg < 45
                and 0 < self.max_edge_residual_px <= 2
                and 0 < self.max_marker_reprojection_px <= 10
                and 0 < self.min_valid_frame_fraction <= 1
                and type(self.min_edges_per_region) is int and 1 <= self.min_edges_per_region <= 4
                and type(self.min_peripheral_regions) is int and 1 <= self.min_peripheral_regions <= 4):
            raise ValueError("Invalid data-quality thresholds")


def required_regions(chart):
    """Center plus the nearest actual squares to the four field corners."""
    f = chart["visible_field"]
    xy = np.array([s["center_mm"] for s in chart["slanted_squares"]])
    normalized = (xy - [f["left"] + f["width"]/2, f["top"] + f["height"]/2]) / [f["width"]/2, f["height"]/2]
    targets = [[0, 0], [-1, -1], [1, -1], [1, 1], [-1, 1]]
    indices = [int(np.argmin(np.linalg.norm(normalized-t, axis=1))) for t in targets]
    return [f"r{chart['slanted_squares'][i]['row']}_c{chart['slanted_squares'][i]['col']}" for i in indices]


def assess_field(chart, fits, detections, recipe):
    q = recipe.quality
    center, *peripheral = required_regions(chart)
    reasons = []
    # Every sweep must bracket the required regions; a good repeat cannot hide a bad one.
    for rep in range(recipe.repetitions):
        usable = {region for region in [center, *peripheral]
                  if usable_edges({f['edge'] for f in fits if f['valid'] and f['region'] == region
                                   and f['repetition'] == rep}, q)}
        if center not in usable:
            reasons.append(f"Sweep {rep}: center peak missing, unbracketed or unusable ({center})")
        missing = [r for r in peripheral if r not in usable]
        if len(set(peripheral) & usable) < q.min_peripheral_regions:
            reasons.append(f"Sweep {rep}: insufficient peripheral peak coverage: {', '.join(missing)}")
    fraction = sum(d['valid'] for d in detections) / max(1, len(detections))
    if fraction < q.min_valid_frame_fraction:
        reasons.append("Too many frames with missing markers or failed registration")
    return reasons


def usable_edges(edges, thresholds):
    return (len(edges) >= thresholds.min_edges_per_region and bool(set(edges) & {'top', 'bottom'})
            and bool(set(edges) & {'left', 'right'}))
