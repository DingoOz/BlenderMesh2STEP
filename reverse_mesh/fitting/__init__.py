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
    fit_sphere,
    fit_torus,
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
]
