# SPDX-License-Identifier: GPL-3.0-or-later
"""Viewport overlay manager — GPU-drawn feedback over the 3D view.

A single ``POST_VIEW`` draw handler renders every registered overlay, so there is
exactly one handler to leak no matter how many overlays are active. Overlays are
keyed strings (e.g. ``"heatmap:Reverse_Cylinder"``); each holds a triangle or
line soup in *world* coordinates plus per-vertex RGBA colours.

The geometry is cached as a GPU batch and lives only in this module's globals
(never a PropertyGroup — GPU batches don't serialise and must not enter the
blend file). It is captured at fit/validate time, so it goes stale if the user
edits the mesh afterwards; callers clear and rebuild it on the next action.

Used by the fit-quality heatmap (#1, filled triangles) and the export validation
report's "jump to open edge" highlight (#10, lines).
"""

from __future__ import annotations

import bpy

try:                                    # gpu is unavailable in background/no-GL builds
    import gpu
    from gpu_extras.batch import batch_for_shader
    _GPU_OK = True
except Exception:                       # pragma: no cover - headless without GL
    _GPU_OK = False


# key -> {"type": 'TRIS'|'LINES', "coords": [...], "colors": [...], "batch": batch|None}
_overlays: dict[str, dict] = {}
_handle = None
_shader = None


def _shader_obj():
    global _shader
    if _shader is None:
        # FLAT_COLOR: per-vertex "pos" + "color", no lighting — ideal for a heatmap.
        _shader = gpu.shader.from_builtin("FLAT_COLOR")
    return _shader


def _build_batch(entry):
    coords = entry["coords"]
    colors = entry["colors"]
    if not coords:
        return None
    return batch_for_shader(_shader_obj(), entry["type"], {"pos": coords, "color": colors})


def _draw():
    if not _overlays or not _GPU_OK:
        return
    shader = _shader_obj()
    gpu.state.blend_set("ALPHA")
    gpu.state.depth_test_set("LESS_EQUAL")
    gpu.state.face_culling_set("NONE")
    try:
        for entry in _overlays.values():
            if entry.get("batch") is None:
                entry["batch"] = _build_batch(entry)
            batch = entry["batch"]
            if batch is not None:
                if entry["type"] == "LINES":
                    gpu.state.line_width_set(float(entry.get("width", 2.0)))
                batch.draw(shader)
    finally:
        gpu.state.depth_test_set("NONE")
        gpu.state.blend_set("NONE")
        gpu.state.line_width_set(1.0)


def _ensure_handler():
    global _handle
    if _handle is None and _GPU_OK:
        _handle = bpy.types.SpaceView3D.draw_handler_add(_draw, (), "WINDOW", "POST_VIEW")


def _remove_handler():
    global _handle
    if _handle is not None:
        try:
            bpy.types.SpaceView3D.draw_handler_remove(_handle, "WINDOW")
        except (ValueError, ReferenceError):    # already gone (e.g. reload)
            pass
        _handle = None


def tag_redraw():
    """Ask every 3D viewport to repaint so overlay changes show immediately."""
    wm = bpy.context.window_manager
    if wm is None:
        return
    for window in wm.windows:
        for area in window.screen.areas:
            if area.type == "VIEW_3D":
                area.tag_redraw()


def set_tris(key: str, coords, colors):
    """Register/replace a filled-triangle overlay.

    ``coords`` is a flat sequence of ``(x, y, z)`` world points, 3 per triangle;
    ``colors`` the matching per-vertex ``(r, g, b, a)`` in 0..1.
    """
    _overlays[key] = {"type": "TRIS", "coords": list(coords),
                      "colors": list(colors), "batch": None}
    _ensure_handler()
    tag_redraw()


def set_lines(key: str, coords, colors, width: float = 2.0):
    """Register/replace a line overlay (2 points per segment)."""
    _overlays[key] = {"type": "LINES", "coords": list(coords),
                      "colors": list(colors), "batch": None, "width": width}
    _ensure_handler()
    tag_redraw()


def clear(key: str):
    """Remove one overlay; drop the draw handler when none remain."""
    if _overlays.pop(key, None) is not None:
        if not _overlays:
            _remove_handler()
        tag_redraw()


def clear_prefix(prefix: str):
    """Remove every overlay whose key starts with ``prefix``."""
    for key in [k for k in _overlays if k.startswith(prefix)]:
        _overlays.pop(key, None)
    if not _overlays:
        _remove_handler()
    tag_redraw()


def clear_all():
    """Remove every overlay and the draw handler."""
    _overlays.clear()
    _remove_handler()
    tag_redraw()


def active_keys():
    """The keys of currently-registered overlays (for tests/introspection)."""
    return list(_overlays)


def register():
    # Defensive: a stale handler from a previous load would draw against dead
    # globals. Start clean.
    _remove_handler()
    _overlays.clear()


def unregister():
    clear_all()
