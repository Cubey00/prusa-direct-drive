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


PATTERN_LEVEL = 0.5     # of the peak fringe density: the part of the pattern that counts for its centre
FRINGE_BLUR = 1.5       # downsampled px: smoothing before looking at the fine structure (the fringes)
BUSY_SIGMA = 10.0       # downsampled px: how wide "busy" is measured


def contact_centre(before, after, factor=DOWNSAMPLE, blur=BLUR_PX, min_strength=MIN_STRENGTH,
                   disc=None, polarity=None, inner=0.85):
    """Centre of the imprint: where the pattern of after - before is BUSIEST (densest fringes).
    On the printer a touch leaves a ring, or a star of four lobes (stress in the gel) whose bright
    lobes reach millimetres away; the fringe lines of either crowd together where the tip presses.
    So: the fine structure (|Laplacian| of the lightly smoothed difference), averaged over
    BUSY_SIGMA, and the centre of its part above PATTERN_LEVEL of the maximum. The contrast check
    (no imprint -> AutoCalError) uses the plain difference.
    disc: only look inside the sensor's window (inner x its radius: the rim and the corners are no
    imprint). polarity is accepted for compatibility; a star has both signs, so both count.
    Raises AutoCalError when there is no clear imprint."""
    b, a = small(to_gray(before), factor), small(to_gray(after), factor)
    if b.shape != a.shape:
        raise AutoCalError("The two photos have a different size")
    signed = ndimage.gaussian_filter(a - b, blur)
    d = np.abs(signed)
    if disc is not None:
        yy, xx = np.mgrid[0:d.shape[0], 0:d.shape[1]]
        cx, cy, r = (disc.x + 0.5) / factor - 0.5, (disc.y + 0.5) / factor - 0.5, disc.r / factor
        inside = (xx - cx) ** 2 + (yy - cy) ** 2 <= (inner * r) ** 2
        d = np.where(inside, d, 0.0)
    med = float(np.median(d))
    noise = float(np.median(np.abs(d - med))) * 1.4826 + 1e-6
    peak = float(d.max())
    strength = (peak - med) / noise
    if strength < min_strength:
        raise AutoCalError(f"No imprint of the cone in the photos (contrast {strength:.1f}, need "
                           f"{min_strength:g}): did the sensor touch it, and is the light on?")
    busy = ndimage.gaussian_filter(np.abs(ndimage.laplace(ndimage.gaussian_filter(a - b, FRINGE_BLUR))),
                                   BUSY_SIGMA)
    if disc is not None:
        busy = np.where(inside, busy, 0.0)
    w = np.clip(busy - PATTERN_LEVEL * busy.max(), 0, None)
    cy, cx = ndimage.center_of_mass(w)
    py, px = np.unravel_index(int(np.argmax(d)), d.shape)
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
    """The difference image as an 8-bit PNG: a GREEN DOT where the centre of the imprint was found,
    a red cross on the target (the centre of the sensor window)."""
    from PIL import Image, ImageDraw
    d = contact.diff
    span = float(d.max() - d.min()) or 1.0
    img = Image.fromarray(((d - d.min()) / span * 255).astype(np.uint8)).convert("RGB")
    draw = ImageDraw.Draw(img)

    def px(x, y):
        return (x + 0.5) / factor - 0.5, (y + 0.5) / factor - 0.5
    if centre_px is not None:
        x, y = px(*centre_px)
        draw.line([(x - 10, y), (x + 10, y)], fill=(230, 0, 0), width=2)
        draw.line([(x, y - 10), (x, y + 10)], fill=(230, 0, 0), width=2)
    x, y = px(contact.x, contact.y)
    draw.ellipse([x - 6, y - 6, x + 6, y + 6], fill=(0, 230, 0), outline=(0, 90, 0))
    img.save(path)


def gray_png(frame, path, factor=DOWNSAMPLE):
    """A small 8-bit grey copy of a frame, for the run folder."""
    from PIL import Image
    g = small(to_gray(frame), factor)
    lo, hi = np.percentile(g, (1, 99))
    Image.fromarray(np.clip((g - lo) / max(hi - lo, 1e-6) * 255, 0, 255).astype(np.uint8)).save(path)


def _gray8(a):
    lo, hi = np.percentile(a, (1, 99))
    return np.clip((a - lo) / max(hi - lo, 1e-6) * 255, 0, 255).astype(np.uint8)


def touch_panel(before, after, contact, target, path, title="", factor=DOWNSAMPLE):
    """One image per touch, four panels: photo before, photo after, the difference, and the
    difference with a GREEN DOT on the centre of the imprint (as found) and a RED CROSS on the
    target (the centre of the sensor window), plus a legend. contact None: no imprint found."""
    from PIL import Image, ImageDraw
    b, a = small(to_gray(before), factor), small(to_gray(after), factor)
    if contact is not None and contact.diff is not None:
        d = contact.diff
    else:
        d = np.abs(ndimage.gaussian_filter(a - b, BLUR_PX))
    h, w = b.shape
    tiles = [Image.fromarray(_gray8(b)).convert("RGB"), Image.fromarray(_gray8(a)).convert("RGB"),
             Image.fromarray(_gray8(d)).convert("RGB"), Image.fromarray(_gray8(d)).convert("RGB")]
    marked = ImageDraw.Draw(tiles[3])

    def px(x, y):
        return (x + 0.5) / factor - 0.5, (y + 0.5) / factor - 0.5
    size = max(6, w // 90)
    if target is not None:
        x, y = px(*target)
        marked.line([(x - 2 * size, y), (x + 2 * size, y)], fill=(230, 0, 0), width=max(2, size // 3))
        marked.line([(x, y - 2 * size), (x, y + 2 * size)], fill=(230, 0, 0), width=max(2, size // 3))
    if contact is not None:
        x, y = px(contact.x, contact.y)
        marked.ellipse([x - size, y - size, x + size, y + size], fill=(0, 230, 0), outline=(0, 90, 0))
    labels = ["1  Photo before the touch", "2  Photo after the touch", "3  Difference (after - before)",
              "4  Difference with the points"]
    head, foot = 28, 70
    sheet = Image.new("RGB", (2 * w + 12, 2 * (h + head) + 12 + foot), "white")
    draw = ImageDraw.Draw(sheet)
    for i, (tile, lab) in enumerate(zip(tiles, labels)):
        x0, y0 = 4 + (i % 2) * (w + 4), 4 + (i // 2) * (h + head + 4)
        draw.text((x0 + 2, y0 + 6), lab, fill="black")
        sheet.paste(tile, (x0, y0 + head))
    ly = 2 * (h + head) + 16
    draw.text((8, ly), title, fill="black")
    draw.ellipse([10, ly + 22, 24, ly + 36], fill=(0, 230, 0), outline=(0, 90, 0))
    found = (f"centre of the imprint, found at ({contact.x:.0f}, {contact.y:.0f}) px"
             if contact is not None else "no imprint found")
    draw.text((32, ly + 22), "Green dot: " + found, fill="black")
    draw.line([(10, ly + 50), (24, ly + 50)], fill=(230, 0, 0), width=3)
    draw.line([(17, ly + 43), (17, ly + 57)], fill=(230, 0, 0), width=3)
    aim = f"({target[0]:.0f}, {target[1]:.0f}) px" if target is not None else "unknown"
    draw.text((32, ly + 44), "Red cross: aim point, the centre of the sensor window " + aim, fill="black")
    sheet.save(path)
