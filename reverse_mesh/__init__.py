# SPDX-License-Identifier: GPL-3.0-or-later
"""Reverse — Mesh to Parametric.

A Blender 4.2+ extension that fits clean analytic CAD primitives
(plane / cylinder / cone / sphere) to selected regions of a mesh, in the
semi-automatic, human-in-the-loop style of the Reverse Fusion 360 add-in.

This is Tier 1 of the plan in ``mesh-to-parametric-plan.md``: geometry recovery
with zero external dependencies. STEP/BREP export via OCCT is a later tier.
"""

from . import operators, panels, properties

# Modules registered in order; unregistered in reverse.
_modules = (properties, operators, panels)


def register():
    for module in _modules:
        module.register()


def unregister():
    for module in reversed(_modules):
        module.unregister()
