"""Oriented mesh -> 2D top-view image of the face. No Qt here, so it can be swapped or tested alone."""
from dataclasses import dataclass

import numpy as np
from PIL import Image, ImageDraw

from . import config
from .model_io import model_to_face


@dataclass
class FaceImage:
    rgba: np.ndarray       # H x W x 4, uint8
    width_mm: float
    height_mm: float


class ShadedTopRenderer:
    """Upward-facing triangles painted low to high, shaded by height, with sharp edges drawn on top."""

    def __init__(self, edge_angle_deg=30.0):
        self.edge_angle = np.radians(edge_angle_deg)

    def render(self, mesh, px_per_mm=config.RENDER_PX_PER_MM):
        w_mm, h_mm = (mesh.bounds[1] - mesh.bounds[0])[:2]
        px_per_mm = min(px_per_mm, config.RENDER_MAX_PX / max(w_mm, h_mm, 1e-6))
        W, H = max(1, int(round(w_mm * px_per_mm))), max(1, int(round(h_mm * px_per_mm)))
        img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)

        tris = mesh.triangles                          # F x 3 x 3
        up = mesh.face_normals[:, 2] > 1e-6
        zmin, zmax = mesh.bounds[0][2], mesh.bounds[1][2]
        zspan = max(zmax - zmin, 1e-9)
        def to_px(pts):
            f = model_to_face(mesh, pts) * px_per_mm
            return [tuple(p) for p in f]

        idx = np.where(up)[0]
        idx = idx[np.argsort(tris[idx, :, 2].mean(axis=1))]
        for i in idx:
            t = tris[i]
            shade = int(110 + 120 * (t[:, 2].mean() - zmin) / zspan)
            light = 0.75 + 0.25 * mesh.face_normals[i, 2]
            g = int(shade * light)
            draw.polygon(to_px(t), fill=(g, int(g * 0.95), int(g * 0.6), 255))

        # sharp edges where at least one side faces up
        if len(mesh.face_adjacency):
            sharp = mesh.face_adjacency_angles > self.edge_angle
            pairs = mesh.face_adjacency[sharp]
            visible = up[pairs].any(axis=1)
            for a, b in mesh.face_adjacency_edges[sharp][visible]:
                draw.line(to_px(mesh.vertices[[a, b]]), fill=(40, 40, 40, 255), width=1)
        return FaceImage(np.array(img), float(w_mm), float(h_mm))
