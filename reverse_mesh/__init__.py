# SPDX-License-Identifier: GPL-3.0-or-later
"""Reverse — Mesh to Parametric.

A Blender 4.2+ extension that fits clean analytic CAD primitives
(plane / cylinder / cone / sphere) to selected regions of a mesh, in the
semi-automatic, human-in-the-loop style of the Reverse Fusion 360 add-in.

This is Tier 1 of the plan in ``mesh-to-parametric-plan.md``: geometry recovery
with zero external dependencies. STEP/BREP export via OCCT is a later tier.
"""

import importlib.util

# The fitting core, STEP writers and units are plain Python and must stay
# importable outside Blender (CI, scripts, kernel tests); only the UI layer
# needs bpy.
_HAVE_BPY = importlib.util.find_spec("bpy") is not None

if _HAVE_BPY:
    from . import operators, overlay, panels, properties

    # Modules registered in order; unregistered in reverse. ``overlay`` owns
    # the GPU draw handler and must unregister (removing it) before the rest
    # tears down.
    _modules = (properties, overlay, operators, panels)
else:
    _modules = ()


def register():
    if not _modules:
        raise RuntimeError("reverse_mesh.register() requires Blender (bpy)")
    for module in _modules:
        module.register()


def unregister():
    for module in reversed(_modules):
        module.unregister()
