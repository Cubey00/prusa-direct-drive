"""Meshes made from scratch: the fiducial cone and the simple test parts.
All shapes are convex hulls, so they are watertight and need no extra libraries."""
import math

import numpy as np
import trimesh

from . import config as cfg


def _hull(points):
    return trimesh.convex.convex_hull(np.asarray(points, dtype=float))


def _circle(r, n, z=0.0, phase=0.0):
    a = phase + np.arange(n) * 2 * math.pi / n
    return np.column_stack([r * np.cos(a), r * np.sin(a), np.full(n, z)])


def prism(polygon_xy, height):
    """Straight prism over a convex polygon (list of (x, y))."""
    p = np.asarray(polygon_xy, dtype=float)
    lo = np.column_stack([p, np.zeros(len(p))])
    hi = np.column_stack([p, np.full(len(p), height)])
    return _hull(np.vstack([lo, hi]))


def regular_prism(n, width, height):
    """n-gon prism whose circumscribed circle is `width` across."""
    return prism(_circle(width / 2, n, phase=math.pi / 2)[:, :2], height)


def cone(base_d, height, n=64):
    """Cone with its tip on the axis at z = height."""
    return _hull(np.vstack([_circle(base_d / 2, n), [[0, 0, height]]]))


def pyramid(width, height):
    s = width / 2
    return _hull([[-s, -s, 0], [s, -s, 0], [s, s, 0], [-s, s, 0], [0, 0, height]])


def half_sphere(diameter, n=48):
    """Dome, flat side down."""
    r = diameter / 2
    pts = [[0, 0, r]]
    for k in range(n // 4 + 1):                 # rings from the equator up
        phi = k * (math.pi / 2) / (n // 4 + 1)
        pts.extend(_circle(r * math.cos(phi), n, r * math.sin(phi)))
    return _hull(pts)


def wedge(width, height):
    """Ramp: square footprint, slopes from 0 to `height` along +x (sloped face looks up)."""
    s = width / 2
    return _hull([[-s, -s, 0], [s, -s, 0], [s, s, 0], [-s, s, 0], [s, -s, height], [s, s, height]])


def fiducial():
    return cone(cfg.FIDUCIAL_BASE_D, cfg.FIDUCIAL_HEIGHT)


# ---------------- test set ----------------

PRISM_H = 2.0       # mm, height of the flat test prisms

TEST_SHAPES = {
    "driehoek": lambda s: regular_prism(3, s / math.cos(math.radians(30)), PRISM_H),  # side = s
    "vierkant": lambda s: prism([(-s / 2, -s / 2), (s / 2, -s / 2), (s / 2, s / 2), (-s / 2, s / 2)],
                                PRISM_H),
    "rechthoek": lambda s: prism([(-s / 2, -s / 4), (s / 2, -s / 4), (s / 2, s / 4), (-s / 2, s / 4)],
                                 PRISM_H),
    "zeshoek": lambda s: regular_prism(6, s, PRISM_H),
    "cilinder": lambda s: regular_prism(64, s, PRISM_H),
    "kubus": lambda s: trimesh.creation.box((s, s, s)),
    "halve_bol": half_sphere,
    "kegel": lambda s: cone(s, s),
    "piramide": lambda s: pyramid(s, s),
    "wig": lambda s: wedge(s, s / 2),
}
TEST_SIZES = (3.0, 4.0, 5.0)     # mm


def simple_parts():
    """[(name, mesh)]: every shape in every size, 30 parts."""
    return [(f"{shape}_{size:g}mm", make(size))
            for shape, make in TEST_SHAPES.items() for size in TEST_SIZES]
