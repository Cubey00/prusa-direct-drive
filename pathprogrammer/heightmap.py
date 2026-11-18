"""Height map of the touched (upward-facing) surface of an oriented mesh. No Qt.

Grid in face coordinates (the print XY the points and the calibration use: origin back-left corner of
the face, x right, y toward the front); value = height above the bottom of the oriented model
(bed = 0), NaN where there is no surface.
"""
import numpy as np

from . import config
from .model_io import model_to_face


def compute(mesh, res_mm=config.HEIGHTMAP_RES_MM, max_px=config.HEIGHTMAP_MAX_PX):
    """-> (xs, ys, H): cell-centre coordinates (mm) and H[len(ys), len(xs)] in mm."""
    ext = mesh.bounds[1] - mesh.bounds[0]
    w, h = float(ext[0]), float(ext[1])
    res = max(res_mm, max(w, h) / max_px)
    nx, ny = max(1, int(np.ceil(w / res))), max(1, int(np.ceil(h / res)))
    xs = (np.arange(nx) + 0.5) * (w / nx)
    ys = (np.arange(ny) + 0.5) * (h / ny)
    H = np.full((ny, nx), -np.inf)
    zmin = mesh.bounds[0][2]
    tris = mesh.triangles
    up = mesh.face_normals[:, 2] > 1e-9
    for t in tris[up]:
        f = model_to_face(mesh, t)                      # 3 x 2 face coordinates
        z = t[:, 2] - zmin
        x0, x1 = f[:, 0].min(), f[:, 0].max()
        y0, y1 = f[:, 1].min(), f[:, 1].max()
        i0, i1 = np.searchsorted(xs, x0 - 1e-9), np.searchsorted(xs, x1 + 1e-9)
        j0, j1 = np.searchsorted(ys, y0 - 1e-9), np.searchsorted(ys, y1 + 1e-9)
        if i1 <= i0 or j1 <= j0:
            continue
        gx, gy = np.meshgrid(xs[i0:i1], ys[j0:j1])
        d = (f[1, 1] - f[2, 1]) * (f[0, 0] - f[2, 0]) + (f[2, 0] - f[1, 0]) * (f[0, 1] - f[2, 1])
        if abs(d) < 1e-12:
            continue
        a = ((f[1, 1] - f[2, 1]) * (gx - f[2, 0]) + (f[2, 0] - f[1, 0]) * (gy - f[2, 1])) / d
        b = ((f[2, 1] - f[0, 1]) * (gx - f[2, 0]) + (f[0, 0] - f[2, 0]) * (gy - f[2, 1])) / d
        c = 1 - a - b
        inside = (a >= -1e-9) & (b >= -1e-9) & (c >= -1e-9)
        zz = np.where(inside, a * z[0] + b * z[1] + c * z[2], -np.inf)
        sub = H[j0:j1, i0:i1]
        np.maximum(sub, zz, out=sub)
    H[np.isinf(H)] = np.nan
    return xs, ys, H


def save_csv(path, xs, ys, H):
    """First row: face x (mm); first column: face y (mm); cells: height (mm), empty = no surface."""
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write("y/x," + ",".join(f"{x:.3f}" for x in xs) + "\n")
        for y, row in zip(ys, H):
            f.write(f"{y:.3f}," + ",".join("" if np.isnan(v) else f"{v:.4f}" for v in row) + "\n")


def load_csv(path):
    lines = open(path, encoding="utf-8").read().splitlines()
    xs = np.array([float(v) for v in lines[0].split(",")[1:]])
    ys, rows = [], []
    for ln in lines[1:]:
        parts = ln.split(",")
        ys.append(float(parts[0]))
        rows.append([np.nan if v == "" else float(v) for v in parts[1:]])
    return xs, np.array(ys), np.array(rows)


def save_png(path, xs, ys, H, points=()):
    """Colour map (y down like the face view) with the touch points numbered. points: [(x, y), ...]."""
    import matplotlib
    matplotlib.use("Agg")
    from matplotlib.figure import Figure
    fig = Figure(figsize=(8, 8 * max(ys[-1], 1) / max(xs[-1], 1) + 1))
    ax = fig.add_subplot(111)
    dx, dy = xs[1] - xs[0] if len(xs) > 1 else 1, ys[1] - ys[0] if len(ys) > 1 else 1
    im = ax.imshow(H, extent=(xs[0] - dx / 2, xs[-1] + dx / 2, ys[-1] + dy / 2, ys[0] - dy / 2),
                   cmap="viridis", interpolation="nearest")
    for i, (x, y) in enumerate(points, 1):
        ax.plot(x, y, "r+", ms=8)
        ax.annotate(str(i), (x, y), color="white", fontsize=8, xytext=(3, 3), textcoords="offset points")
    ax.set_xlabel("face x (mm)")
    ax.set_ylabel("face y (mm)")
    fig.colorbar(im, ax=ax, label="height above bed (mm)")
    fig.savefig(path, dpi=150, bbox_inches="tight")
