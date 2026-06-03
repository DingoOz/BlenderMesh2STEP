# SPDX-License-Identifier: GPL-3.0-or-later
"""Primitive-fitting core. Pure NumPy, no Blender dependency."""

from .common import FitResult, Region
from .primitives import (
    FITTERS,
    fit_auto,
    fit_box,
    fit_cone,
    fit_cylinder,
    fit_plane,
    fit_robust,
    fit_sphere,
    fit_torus,
    predicted_normals,
    signed_distances,
    snap_result,
    summarize,
)

__all__ = [
    "FitResult",
    "Region",
    "FITTERS",
    "fit_auto",
    "fit_plane",
    "fit_box",
    "fit_cylinder",
    "fit_cone",
    "fit_sphere",
    "fit_torus",
    "fit_robust",
    "predicted_normals",
    "signed_distances",
    "snap_result",
    "summarize",
]
