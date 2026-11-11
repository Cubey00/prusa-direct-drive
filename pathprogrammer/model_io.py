"""Load .3mf models and keep the 90-degree orientation state.

The view always looks down -Z (the head approaches from above). Face coordinates:
origin at the back-left corner of the visible face, x to the right, y toward the front,
so face x = X - xmin and face y = ymax - Y of the oriented mesh.
"""
import hashlib
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import trimesh


@dataclass
class LoadedModel:
    mesh: trimesh.Trimesh
    path: Path
    sha256: str
    units_note: str = ""

    @property
    def filename(self):
        return self.path.name


def file_sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_model(path):
    path = Path(path)
    mesh = trimesh.load(path, force="mesh")    # merges build items with their transforms
    if not isinstance(mesh, trimesh.Trimesh) or len(mesh.faces) == 0:
        raise ValueError(f"No mesh found in {path.name}")
    units = getattr(mesh, "units", None)
    note = "" if units in (None, "mm", "millimeter", "millimeters") else \
        f"Model units are '{units}', treated as mm anyway"
    return LoadedModel(mesh, path, file_sha256(path), note)


# 90-degree rotations as integer matrices
_RX = np.array([[1, 0, 0], [0, 0, -1], [0, 1, 0]])     # +90 about screen X
_RY = np.array([[0, 0, 1], [0, 1, 0], [-1, 0, 0]])     # +90 about screen Y
_RZ = np.array([[0, -1, 0], [1, 0, 0], [0, 0, 1]])     # +90 about the view axis


class Orientation:
    def __init__(self, matrix=None):
        self.matrix = np.eye(3, dtype=int) if matrix is None else np.array(matrix, dtype=int)

    def _apply(self, r):
        self.matrix = r @ self.matrix

    def tilt_up(self):    self._apply(_RX)
    def tilt_down(self):  self._apply(_RX.T)
    def turn_left(self):  self._apply(_RY.T)
    def turn_right(self): self._apply(_RY)
    def spin(self):       self._apply(_RZ.T)    # clockwise as seen from above

    def to_list(self):
        return self.matrix.tolist()

    @classmethod
    def from_list(cls, m):
        return cls(m)

    def __eq__(self, other):
        return isinstance(other, Orientation) and np.array_equal(self.matrix, other.matrix)


def oriented_mesh(mesh, orientation):
    """Rotated copy with its bounding-box minimum at the origin."""
    m = mesh.copy()
    t = np.eye(4)
    t[:3, :3] = orientation.matrix
    m.apply_transform(t)
    m.apply_translation(-m.bounds[0])
    return m


def face_size(mesh):
    """(width, height) in mm of the face seen from above, for an oriented mesh."""
    ext = mesh.bounds[1] - mesh.bounds[0]
    return float(ext[0]), float(ext[1])


def model_to_face(mesh, xyz):
    """Oriented-mesh coordinates -> face (x, y)."""
    xyz = np.asarray(xyz, dtype=float)
    return np.column_stack([xyz[..., 0] - mesh.bounds[0][0], mesh.bounds[1][1] - xyz[..., 1]])
