# SPDX-License-Identifier: GPL-3.0-or-later
"""Primitive-fitting core. Pure NumPy, no Blender dependency."""

from .common import FitResult, Region, region_scale
from .decompose import MeshGraph, optimize_decomposition
from .patterns import classify_arrangement, match_cylinders
from .primitives import (
    FITTERS,
    fit_auto,
    fit_box,
    fit_cone,
    fit_cylinder,
    fit_fillet,
    fit_plane,
    fit_robust,
    fit_sphere,
    fit_torus,
    normal_alignment,
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
    "fit_fillet",
    "fit_robust",
    "normal_alignment",
    "predicted_normals",
    "signed_distances",
    "snap_result",
    "summarize",
    "region_scale",
    "match_cylinders",
    "classify_arrangement",
    "MeshGraph",
    "optimize_decomposition",
]
