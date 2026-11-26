"""Automatic calibration from photos: the pure parts (no Qt, no hardware).

The tactile sensor's camera looks at the gel from above. Touching a cone tip leaves a local
imprint, so the photo after the touch minus the photo before it (both with the head at the same
X/Y) shows a blob where the tip pressed. Its centre in pixels, compared with the image centre,
says how far the head is off the cone. A 2x2 matrix J (pixels per mm of head movement, measured
on the first cone with two touches 1 mm apart) turns that into a head correction in mm.

Two cones are enough to place the plate: the bed -> head map is taken as a similarity (shift,
rotation, uniform scale), which two points fix; the third cone follows from it.
"""
from dataclasses import dataclass, field

import numpy as np
from scipy import ndimage

DOWNSAMPLE = 4          # analyse at 1/4 resolution: plenty for a blob of millimetres, 16x faster
BLUR_PX = 3.0           # gaussian sigma (downsampled pixels) against sensor noise
MIN_STRENGTH = 10.0     # blob peak / noise level below this: no imprint seen (pure noise reaches ~5)


class AutoCalError(Exception):
    pass


def to_gray(frame):
    """Float32 grey image (H x W) from a camera frame (H x W or H x W x C, any integer type)."""
    a = np.asarray(frame)
    if a.ndim == 3:
        a = a.astype(np.float32).mean(axis=2)
    return a.astype(np.float32)


def small(gray, factor=DOWNSAMPLE):
    """Block-average downsample (crops the remainder)."""
    h, w = (gray.shape[0] // factor) * factor, (gray.shape[1] // factor) * factor
    return gray[:h, :w].reshape(h // factor, factor, w // factor, factor).mean(axis=(1, 3))


@dataclass
class Disc:
    x: float                 # centre of the sensor's round window, full-resolution pixels
    y: float
    r: float                 # radius, full-resolution pixels


def sensor_disc(frame, factor=DOWNSAMPLE):
    """The round, lit sensor window in a photo (bright disc on a dark surround). Its centre is where
    the sensor's centre is in the image (the camera need not be centred on it). The centre comes
    from chord midpoints: in every row the middle of the lit stretch has x = cx, in every column
    y = cy; chords cut by the frame edge are left out. None when no disc is found."""
    g = ndimage.gaussian_filter(small(to_gray(frame), factor), 2.0)
    lo, hi = np.percentile(g, (2, 98))
    lit = g > lo + 0.4 * (hi - lo)
    labels, n = ndimage.label(lit)
    if n == 0:
        return None
    sizes = ndimage.sum(lit, labels, range(1, n + 1))
    disc = ndimage.binary_fill_holes(labels == 1 + int(np.argmax(sizes)))
    h, w = disc.shape
    if disc.mean() < 0.15:
        return None

    def mids(mask):
        out = []
        for i in range(mask.shape[0]):
            idx = np.flatnonzero(mask[i])
            if len(idx) > 10 and idx[0] > 0 and idx[-1] < mask.shape[1] - 1:
                out.append(((idx[0] + idx[-1]) / 2.0, idx[-1] - idx[0]))
        return out
    rows, cols = mids(disc), mids(disc.T)
    if len(rows) < 10 or len(cols) < 10:
        return None
    cx = float(np.median([m for m, _ in rows]))
    cy = float(np.median([m for m, _ in cols]))
    r = max(max(c for _, c in rows), max(c for _, c in cols)) / 2.0
    return Disc((cx + 0.5) * factor - 0.5, (cy + 0.5) * factor - 0.5, r * factor)


@dataclass
class Contact:
    x: float                 # blob centre, full-resolution pixels
    y: float
    strength: float          # peak / noise
    polarity: float = 1.0    # +1: the imprint makes the image brighter, -1: darker
    diff: np.ndarray = field(repr=False, default=None)   # the smoothed |after - before| (downsampled)


def contact_centre(before, after, factor=DOWNSAMPLE, blur=BLUR_PX, min_strength=MIN_STRENGTH,
                   disc=None, polarity=None, inner=0.85):
    """Centre of the imprint: where after differs from before. disc: only look inside the sensor's
    window (inner x its radius: the rim is no imprint). polarity (+1 / -1, learned from a first
    clean touch): count only that sign, so what is left of an earlier imprint (fading: the other
    sign) is ignored. Raises AutoCalError when there is no clear imprint."""
    b, a = small(to_gray(before), factor), small(to_gray(after), factor)
    if b.shape != a.shape:
        raise AutoCalError("The two photos have a different size")
    signed = ndimage.gaussian_filter(a - b, blur)
    if polarity is None:
        d = np.abs(signed)
    else:
        d = np.clip(polarity * signed, 0, None)
    if disc is not None:
        yy, xx = np.mgrid[0:d.shape[0], 0:d.shape[1]]
        cx, cy, r = (disc.x + 0.5) / factor - 0.5, (disc.y + 0.5) / factor - 0.5, disc.r / factor
        d = np.where((xx - cx) ** 2 + (yy - cy) ** 2 <= (inner * r) ** 2, d, 0.0)
    med = float(np.median(d))
    noise = float(np.median(np.abs(d - med))) * 1.4826 + 1e-6
    peak = float(d.max())
    strength = (peak - med) / noise
    if strength < min_strength:
        raise AutoCalError(f"No imprint of the cone in the photos (contrast {strength:.1f}, need "
                           f"{min_strength:g}): did the sensor touch it, and is the light on?")
    level = med + 0.5 * (peak - med)          # the blob: the part above half its height
    mask = d > level
    labels, n = ndimage.label(mask)
    py, px = np.unravel_index(int(np.argmax(d)), d.shape)
    blob = labels == labels[py, px]            # only the region around the peak
    w = np.where(blob, d - level, 0.0)
    cy, cx = ndimage.center_of_mass(w)
    sign = 1.0 if signed[py, px] >= 0 else -1.0
    return Contact((cx + 0.5) * factor - 0.5, (cy + 0.5) * factor - 0.5, strength, sign, d)


def image_centre(shape, centre_px=(0.0, 0.0)):
    """Where the sensor's centre is in the image: [camera] centre_px, or the middle of the frame."""
    if centre_px and (centre_px[0] or centre_px[1]):
        return float(centre_px[0]), float(centre_px[1])
    h, w = shape[:2]
    return (w - 1) / 2.0, (h - 1) / 2.0


def jacobian(c0, cx, cy, dx_mm, dy_mm):
    """Pixels per mm of head movement: column 0 from a touch dx_mm further in X, column 1 from one
    dy_mm further in Y (each a (x, y) pixel centre)."""
    j = np.array([[(cx[0] - c0[0]) / dx_mm, (cy[0] - c0[0]) / dy_mm],
                  [(cx[1] - c0[1]) / dx_mm, (cy[1] - c0[1]) / dy_mm]], dtype=float)
    if abs(np.linalg.det(j)) < 1e-6:
        raise AutoCalError("The camera did not see the imprint move when the head moved 1 mm: "
                           "cannot work out the camera scale")
    return j


def correction_mm(j, contact_px, centre_px):
    """Head move (dx, dy in mm) that puts the imprint on the image centre."""
    err = np.array([centre_px[0] - contact_px[0], centre_px[1] - contact_px[1]], dtype=float)
    d = np.linalg.solve(j, err)
    return float(d[0]), float(d[1])


def broyden(j, dh_mm, dc_px):
    """J corrected with one more observation: the head moved dh_mm and the imprint moved dc_px
    (Broyden's update: the smallest change to J that explains this move)."""
    dh, dc = np.asarray(dh_mm, float), np.asarray(dc_px, float)
    n = float(dh @ dh)
    if n < 1e-6:
        return j
    new = j + np.outer(dc - j @ dh, dh) / n
    return new if abs(np.linalg.det(new)) > 1e-6 else j


def mm_per_px(j):
    """Mean scale of J, for reports."""
    return float(1.0 / np.sqrt(abs(np.linalg.det(j))))


def third_from_two(bed_a, head_a, bed_b, head_b, bed_c):
    """Head X/Y over cone c from two measured cones: the similarity (shift, rotation, scale) that
    takes bed a, b to head a, b, applied to bed c."""
    ba, bb, bc = complex(*bed_a), complex(*bed_b), complex(*bed_c)
    ha, hb = complex(*head_a), complex(*head_b)
    if abs(bb - ba) < 1e-9:
        raise AutoCalError("The two cones are at the same place")
    k = (hb - ha) / (bb - ba)
    hc = ha + k * (bc - ba)
    return hc.real, hc.imag


def diff_png(contact, path, centre_px=None, factor=DOWNSAMPLE):
    """The difference image as an 8-bit PNG with the found centre (red) and the image centre (green)."""
    from PIL import Image, ImageDraw
    d = contact.diff
    span = float(d.max() - d.min()) or 1.0
    img = Image.fromarray(((d - d.min()) / span * 255).astype(np.uint8)).convert("RGB")
    draw = ImageDraw.Draw(img)

    def mark(x, y, colour):
        x, y = (x + 0.5) / factor - 0.5, (y + 0.5) / factor - 0.5
        draw.line([(x - 8, y), (x + 8, y)], fill=colour, width=2)
        draw.line([(x, y - 8), (x, y + 8)], fill=colour, width=2)
    if centre_px is not None:
        mark(centre_px[0], centre_px[1], (0, 200, 0))
    mark(contact.x, contact.y, (230, 0, 0))
    img.save(path)


def gray_png(frame, path, factor=DOWNSAMPLE):
    """A small 8-bit grey copy of a frame, for the run folder."""
    from PIL import Image
    g = small(to_gray(frame), factor)
    lo, hi = np.percentile(g, (1, 99))
    Image.fromarray(np.clip((g - lo) / max(hi - lo, 1e-6) * 255, 0, 255).astype(np.uint8)).save(path)
