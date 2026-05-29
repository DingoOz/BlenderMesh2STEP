# SPDX-License-Identifier: GPL-3.0-or-later
"""Shared math helpers and the common result type for primitive fitting.

Everything here operates on plain NumPy arrays in *world* coordinates, so the
fitters stay independent of Blender. The operator layer is responsible for
pulling geometry out of a bmesh and handing clean ``(N, 3)`` arrays in.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np


# A primitive fit is rejected if its RMS residual exceeds this fraction of the
# region's bounding-box diagonal. Keeps obviously-wrong fits from being offered.
DEFAULT_REL_TOLERANCE = 0.02


@dataclass
class Region:
    """A selected patch of mesh, in world coordinates.

    ``points`` are the unique surface vertices (exact on the intended surface —
    best for radius/centre fits). ``face_points`` and ``face_normals`` are paired
    per-face centroids and unit normals (needed where a point must be associated
    with a direction, e.g. the cone apex equation).
    """

    points: np.ndarray          # (P, 3)
    face_points: np.ndarray     # (F, 3)
    face_normals: np.ndarray    # (F, 3)

    @classmethod
    def from_points(cls, points: np.ndarray, normals: np.ndarray | None = None) -> "Region":
        """Build a Region from paired point/normal samples (used by tests).

        Treats every point as both a surface vertex and a normal sample.
        """
        points = np.asarray(points, dtype=float)
        if normals is None:
            normals = np.zeros_like(points)
        normals = np.asarray(normals, dtype=float)
        return cls(points=points, face_points=points, face_normals=normals)


@dataclass
class FitResult:
    """Outcome of fitting one analytic primitive to a region of mesh.

    ``params`` carries the geometry in a kind-specific schema that
    :mod:`reverse_mesh.build` knows how to turn into a clean Blender object.
    Distances (``rms``, ``max_error``) are in scene/world units.
    """

    kind: str                       # 'PLANE' | 'CYLINDER' | 'CONE' | 'SPHERE'
    rms: float
    max_error: float
    params: dict = field(default_factory=dict)
    summary: str = ""

    @property
    def rel_rms(self) -> float:
        """RMS as a fraction of the region scale (set by the caller)."""
        scale = self.params.get("_scale", 1.0) or 1.0
        return self.rms / scale


def region_scale(points: np.ndarray) -> float:
    """Bounding-box diagonal of a point set — a stable notion of 'size'."""
    if len(points) == 0:
        return 1.0
    diag = float(np.linalg.norm(points.max(axis=0) - points.min(axis=0)))
    return diag if diag > 1e-12 else 1.0


def residual_stats(distances: np.ndarray) -> tuple[float, float]:
    """Return ``(rms, max_abs)`` for a vector of signed/unsigned residuals."""
    if len(distances) == 0:
        return 0.0, 0.0
    rms = float(np.sqrt(np.mean(distances ** 2)))
    return rms, float(np.max(np.abs(distances)))


def best_plane_normal(points: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Least-squares plane through ``points`` via SVD.

    Returns ``(centroid, unit_normal)``. The normal is the singular vector
    with the smallest singular value — the direction of least spread.
    """
    centroid = points.mean(axis=0)
    centred = points - centroid
    # Right singular vectors are the principal axes; last row = smallest.
    _, _, vh = np.linalg.svd(centred, full_matrices=False)
    normal = vh[-1]
    return centroid, _unit(normal)


def smallest_singular_axis(vectors: np.ndarray) -> np.ndarray:
    """Unit direction least represented in ``vectors`` (smallest singular value).

    For a cylinder the surface normals are all perpendicular to the axis, so the
    axis is exactly the direction with the least projection onto the normal set.
    """
    _, _, vh = np.linalg.svd(vectors, full_matrices=False)
    return _unit(vh[-1])


def orthonormal_basis(axis: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Two unit vectors spanning the plane perpendicular to ``axis``."""
    axis = _unit(axis)
    # Pick the world axis least aligned with `axis` to avoid degeneracy.
    seed = np.array([1.0, 0.0, 0.0]) if abs(axis[0]) < 0.9 else np.array([0.0, 1.0, 0.0])
    e1 = _unit(np.cross(axis, seed))
    e2 = _unit(np.cross(axis, e1))
    return e1, e2


def fit_circle_2d(uv: np.ndarray) -> tuple[np.ndarray, float, np.ndarray]:
    """Algebraic (Kåsa) circle fit to 2D points.

    Returns ``(center_2d, radius, radial_residuals)``. Solves the linear system
    derived from ``u^2 + v^2 + a*u + b*v + c = 0``; center = ``(-a/2, -b/2)``.
    """
    u = uv[:, 0]
    v = uv[:, 1]
    a_mat = np.column_stack([u, v, np.ones_like(u)])
    rhs = -(u ** 2 + v ** 2)
    sol, *_ = np.linalg.lstsq(a_mat, rhs, rcond=None)
    cx, cy = -sol[0] / 2.0, -sol[1] / 2.0
    radius = float(np.sqrt(max(cx ** 2 + cy ** 2 - sol[2], 1e-18)))
    center = np.array([cx, cy])
    radial = np.linalg.norm(uv - center, axis=1) - radius
    return center, radius, radial


def _unit(v: np.ndarray) -> np.ndarray:
    n = float(np.linalg.norm(v))
    return v / n if n > 1e-12 else v
