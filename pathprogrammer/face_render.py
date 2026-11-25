"""Oriented mesh -> 2D top-view image of the face. No Qt here, so it can be swapped or tested alone."""
from dataclasses import dataclass

import numpy as np
from PIL import Image, ImageDraw

from . import config


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

        # everything computed once, as arrays: asking the mesh (bounds, normals) per triangle
        # makes trimesh re-hash the whole mesh every time, which takes minutes on a full plate
        (x0, y0, zmin), (_x1, y1, zmax) = np.asarray(mesh.bounds, dtype=float)
        zspan = max(zmax - zmin, 1e-9)
        verts = np.asarray(mesh.vertices, dtype=float)
        vpx = np.column_stack([verts[:, 0] - x0, y1 - verts[:, 1]]) * px_per_mm   # = model_to_face
        faces = np.asarray(mesh.faces)
        normals_z = np.asarray(mesh.face_normals)[:, 2]
        up = normals_z > 1e-6

        idx = np.where(up)[0]
        zmean = verts[faces[idx]][:, :, 2].mean(axis=1)
        order = np.argsort(zmean)
        idx, zmean = idx[order], zmean[order]
        shade = (110 + 120 * (zmean - zmin) / zspan).astype(int)
        g = (shade * (0.75 + 0.25 * normals_z[idx])).astype(int)
        for i, gi in zip(idx.tolist(), g.tolist()):
            draw.polygon([tuple(pt) for pt in vpx[faces[i]].tolist()],
                         fill=(gi, int(gi * 0.95), int(gi * 0.6), 255))

        # sharp edges where at least one side faces up
        if len(mesh.face_adjacency):
            sharp = mesh.face_adjacency_angles > self.edge_angle
            pairs = mesh.face_adjacency[sharp]
            visible = up[pairs].any(axis=1)
            for a, b in mesh.face_adjacency_edges[sharp][visible].tolist():
                draw.line([tuple(vpx[a]), tuple(vpx[b])], fill=(40, 40, 40, 255), width=1)
        return FaceImage(np.array(img), float(w_mm), float(h_mm))
