# SPDX-License-Identifier: GPL-3.0-or-later
"""Scene-unit → STEP-unit scale derivation. Pure Python, no Blender dependency.

Blender geometry is always stored in Blender Units (BU). When the scene's unit
system is METRIC or IMPERIAL, 1 BU = ``scale_length`` meters; ``length_unit``
only affects how lengths are *displayed*, so it never enters the coordinate
scale — it only informs the default STEP unit choice.
"""

from __future__ import annotations

# How many of each STEP unit make up one meter.
UNITS_PER_METER = {
    "MM": 1000.0,
    "M": 1.0,
    "IN": 1.0 / 0.0254,
}


def effective_scale(step_unit, *, system, scale_length):
    """Factor that converts Blender-unit coordinates to ``step_unit``.

    ``system``/``scale_length`` come from ``scene.unit_settings``. With
    system 'NONE' the scene has no physical scale, so coordinates pass
    through unchanged (legacy behaviour).
    """
    if system == "NONE":
        return 1.0
    return float(scale_length) * UNITS_PER_METER.get(step_unit, 1000.0)


def step_unit_for_scene(system, length_unit):
    """The most natural STEP unit ('MM' | 'M' | 'IN') for a scene's unit display."""
    if system == "IMPERIAL":
        return "IN"
    if system == "METRIC" and length_unit in ("METERS", "KILOMETERS"):
        return "M"
    return "MM"
