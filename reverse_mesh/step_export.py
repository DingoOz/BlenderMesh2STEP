# SPDX-License-Identifier: GPL-3.0-or-later
"""Pure-Python AP242 STEP (ISO 10303-21) writer for fitted analytic primitives.

No geometry kernel and no external dependencies: because each fitted primitive
has exact analytic parameters, we can emit genuine analytic surfaces
(PLANE / CYLINDRICAL / CONICAL / SPHERICAL / TOROIDAL) as valid B-rep solids,
assembled into one AP242 file with units, product structure and per-feature
colour styling.

A *feature* handed to :func:`build_step` is a dict with keys:
    kind   : 'PLANE' | 'CYLINDER' | 'CONE' | 'SPHERE' | 'TORUS'
    params : the kind's analytic parameters, in the target unit, world space
    name   : display name for the solid
    color  : optional (r, g, b) in 0..1
    op     : optional 'ADD' | 'SUBTRACT' — this writer has no boolean kernel, so
             SUBTRACT features are handled per ``cutter_mode`` (see build_step)

All vectors/points are plain tuples or lists of three floats.
"""

from __future__ import annotations

import math

AP242_SCHEMA = "AP242_MANAGED_MODEL_BASED_3D_ENGINEERING_MIM_LF"

DEFAULT_COLORS = {
    "PLANE": (0.60, 0.60, 0.63),
    "BOX": (0.70, 0.65, 0.40),
    "CYLINDER": (0.30, 0.55, 0.85),
    "CONE": (0.85, 0.55, 0.30),
    "SPHERE": (0.50, 0.80, 0.40),
    "TORUS": (0.80, 0.40, 0.62),
}

# Colour forced onto SUBTRACT features in cutter_mode='MARK' so they read as
# reference geometry, not part material.
CUTTER_COLOR = (0.85, 0.25, 0.25)

_EPS = 1e-9


# --- small vector helpers (pure Python) ---------------------------------------

def _add(a, b): return (a[0] + b[0], a[1] + b[1], a[2] + b[2])
def _sub(a, b): return (a[0] - b[0], a[1] - b[1], a[2] - b[2])
def _scale(a, s): return (a[0] * s, a[1] * s, a[2] * s)
def _dot(a, b): return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1],
            a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0])


def _norm(a):
    return math.sqrt(_dot(a, a))


def _unit(a):
    n = _norm(a)
    return (a[0] / n, a[1] / n, a[2] / n) if n > _EPS else (0.0, 0.0, 1.0)


def _perp(axis):
    """A unit vector perpendicular to ``axis``."""
    seed = (1.0, 0.0, 0.0) if abs(axis[0]) < 0.9 else (0.0, 1.0, 0.0)
    return _unit(_cross(axis, seed))


def _num(v):
    """Format a float as a STEP real (always with a decimal point)."""
    if v == 0:
        return "0."
    s = "{:.10g}".format(v)
    if "e" in s or "E" in s:
        mant, _, exp = s.partition("e" if "e" in s else "E")
        if "." not in mant:
            mant += "."
        return f"{mant}E{int(exp)}"
    if "." not in s:
        s += "."
    return s


# --- entity writer ------------------------------------------------------------

class StepWriter:
    """Accumulates DATA-section entities and assigns sequential ids."""

    def __init__(self):
        self._lines = []
        self._id = 0
        self._dir_cache = {}
        self._pt_cache = {}

    def add(self, body):
        self._id += 1
        self._lines.append(f"#{self._id}={body};")
        return f"#{self._id}"          # a STEP entity reference, e.g. '#5'

    def point(self, p):
        key = tuple(round(c, 9) for c in p)
        if key not in self._pt_cache:
            self._pt_cache[key] = self.add(
                f"CARTESIAN_POINT('',({_num(p[0])},{_num(p[1])},{_num(p[2])}))"
            )
        return self._pt_cache[key]

    def direction(self, d):
        d = _unit(d)
        key = tuple(round(c, 9) for c in d)
        if key not in self._dir_cache:
            self._dir_cache[key] = self.add(
                f"DIRECTION('',({_num(d[0])},{_num(d[1])},{_num(d[2])}))"
            )
        return self._dir_cache[key]

    def axis2(self, origin, axis_z, ref_x):
        return self.add(
            f"AXIS2_PLACEMENT_3D('',{self.point(origin)},"
            f"{self.direction(axis_z)},{self.direction(ref_x)})"
        )

    def vertex(self, p):
        return self.add(f"VERTEX_POINT('',{self.point(p)})")

    def line(self, p, direction):
        vec = self.add(f"VECTOR('',{self.direction(direction)},1.)")
        return self.add(f"LINE('',{self.point(p)},{vec})")

    def circle(self, axis2_id, radius):
        return self.add(f"CIRCLE('',{axis2_id},{_num(radius)})")

    def edge_curve(self, v1, v2, curve, same_sense=True):
        flag = ".T." if same_sense else ".F."
        return self.add(f"EDGE_CURVE('',{v1},{v2},{curve},{flag})")

    def oriented_edge(self, edge, forward=True):
        flag = ".T." if forward else ".F."
        return self.add(f"ORIENTED_EDGE('',*,*,{edge},{flag})")

    def edge_loop(self, oriented_edges):
        ids = ",".join(str(e) for e in oriented_edges)
        return self.add(f"EDGE_LOOP('',({ids}))")

    def face_outer_bound(self, loop, orientation=True):
        return self.add(f"FACE_OUTER_BOUND('',{loop},{'.T.' if orientation else '.F.'})")

    def advanced_face(self, name, bounds, surface, same_sense=True):
        ids = ",".join(str(b) for b in bounds)
        flag = ".T." if same_sense else ".F."
        return self.add(f"ADVANCED_FACE('{name}',({ids}),{surface},{flag})")

    def closed_shell(self, faces):
        ids = ",".join(str(f) for f in faces)
        return self.add(f"CLOSED_SHELL('',({ids}))")

    def open_shell(self, faces):
        ids = ",".join(str(f) for f in faces)
        return self.add(f"OPEN_SHELL('',({ids}))")

    def text(self):
        return "\n".join(self._lines)


# --- per-primitive solids -----------------------------------------------------

def _solid_name(base, p):
    """A solid's STEP name, annotated with a thread spec when one is tagged.

    The name is what CAD shows for the solid, so 'cylinder thread M8x1.25' makes
    the thread visible without needing full AP242 semantic thread features.
    """
    spec = p.get("thread_spec")
    name = f"{base} thread {spec}" if spec else base
    name = p.get("name_prefix", "") + name
    return name.replace("'", "''")


def _full_circle_edge(w, center, axis, ref, radius):
    """A closed circular edge (one vertex, start == end). Returns (edge, vertex)."""
    start = _add(center, _scale(ref, radius))
    vtx = w.vertex(start)
    crv = w.circle(w.axis2(center, axis, ref), radius)
    return w.edge_curve(vtx, vtx, crv, True), vtx


def _plane_item(w, p):
    """Bounded planar quad → ADVANCED_FACE inside an OPEN_SHELL surface model."""
    c = tuple(p["point"])
    e1 = _unit(tuple(p["e1"]))
    e2 = _unit(tuple(p["e2"]))
    n = _unit(tuple(p["normal"]))
    hu, hv = p["half_u"], p["half_v"]
    corners = [
        _add(_add(c, _scale(e1, -hu)), _scale(e2, -hv)),
        _add(_add(c, _scale(e1, hu)), _scale(e2, -hv)),
        _add(_add(c, _scale(e1, hu)), _scale(e2, hv)),
        _add(_add(c, _scale(e1, -hu)), _scale(e2, hv)),
    ]
    verts = [w.vertex(pt) for pt in corners]
    oeds = []
    for i in range(4):
        a, b = corners[i], corners[(i + 1) % 4]
        ln = w.line(a, _unit(_sub(b, a)))
        ec = w.edge_curve(verts[i], verts[(i + 1) % 4], ln, True)
        oeds.append(w.oriented_edge(ec, True))
    loop = w.edge_loop(oeds)
    bound = w.face_outer_bound(loop, True)
    surf = w.add(f"PLANE('',{w.axis2(c, n, e1)})")
    face = w.advanced_face("plane", [bound], surf, True)
    shell = w.open_shell([face])
    # An open shell must be wrapped in a surface model to be a representation item.
    model = w.add(f"SHELL_BASED_SURFACE_MODEL('',({shell}))")
    return model, False  # (item, is_solid)


def _cylinder_item(w, p):
    axis = _unit(tuple(p["axis"]))
    ref = _perp(axis)
    r, h = p["radius"], p["height"]
    base = _sub(tuple(p["base"]), _scale(axis, h / 2.0))   # bottom centre
    top = _add(base, _scale(axis, h))

    surf = w.add(f"CYLINDRICAL_SURFACE('',{w.axis2(base, axis, ref)},{_num(r)})")
    e_bot, v_bot = _full_circle_edge(w, base, axis, ref, r)
    e_top, v_top = _full_circle_edge(w, top, axis, ref, r)
    seam = w.line(_add(base, _scale(ref, r)), axis)
    e_seam = w.edge_curve(v_bot, v_top, seam, True)

    lat_loop = w.edge_loop([
        w.oriented_edge(e_bot, True),
        w.oriented_edge(e_seam, True),
        w.oriented_edge(e_top, False),
        w.oriented_edge(e_seam, False),
    ])
    lateral = w.advanced_face("side", [w.face_outer_bound(lat_loop, True)], surf, True)

    pl_bot = w.add(f"PLANE('',{w.axis2(base, axis, ref)})")
    bot = w.advanced_face("cap", [w.face_outer_bound(
        w.edge_loop([w.oriented_edge(e_bot, False)]), True)], pl_bot, False)
    pl_top = w.add(f"PLANE('',{w.axis2(top, axis, ref)})")
    top_f = w.advanced_face("cap", [w.face_outer_bound(
        w.edge_loop([w.oriented_edge(e_top, True)]), True)], pl_top, True)

    return w.add(f"MANIFOLD_SOLID_BREP('{_solid_name('cylinder', p)}',"
                 f"{w.closed_shell([lateral, bot, top_f])})"), True


def _fillet_item(w, p):
    """Edge fillet → a *trimmed* partial cylindrical face (an open surface patch).

    Bounded by two partial-circle arcs (at the axial ends) and two straight seams
    (at the angular ends u_min/u_max), wrapped in a surface model like a plane.
    """
    axis = _unit(tuple(p["axis"]))
    e1 = _unit(tuple(p["ref"]))
    e2 = _unit(_cross(axis, e1))
    r, h = p["radius"], p["height"]
    u0, u1 = p["u_min"], p["u_max"]
    base = tuple(p["base"])
    c_bot = _sub(base, _scale(axis, h / 2.0))
    c_top = _add(base, _scale(axis, h / 2.0))

    def on_arc(center, u):
        d = _add(_scale(e1, math.cos(u)), _scale(e2, math.sin(u)))
        return _add(center, _scale(d, r))

    pb0, pb1 = on_arc(c_bot, u0), on_arc(c_bot, u1)
    pt0, pt1 = on_arc(c_top, u0), on_arc(c_top, u1)
    vb0, vb1 = w.vertex(pb0), w.vertex(pb1)
    vt0, vt1 = w.vertex(pt0), w.vertex(pt1)

    arc_bot = w.edge_curve(vb0, vb1, w.circle(w.axis2(c_bot, axis, e1), r), True)
    arc_top = w.edge_curve(vt0, vt1, w.circle(w.axis2(c_top, axis, e1), r), True)
    seam0 = w.edge_curve(vb0, vt0, w.line(pb0, axis), True)
    seam1 = w.edge_curve(vb1, vt1, w.line(pb1, axis), True)

    surf = w.add(f"CYLINDRICAL_SURFACE('',{w.axis2(c_bot, axis, e1)},{_num(r)})")
    loop = w.edge_loop([
        w.oriented_edge(arc_bot, True),
        w.oriented_edge(seam1, True),
        w.oriented_edge(arc_top, False),
        w.oriented_edge(seam0, False),
    ])
    face = w.advanced_face("fillet", [w.face_outer_bound(loop, True)], surf, True)
    model = w.add(f"SHELL_BASED_SURFACE_MODEL('',({w.open_shell([face])}))")
    return model, False


def _cone_item(w, p):
    axis = _unit(tuple(p["axis"]))
    ref = _perp(axis)
    r1, r2, h = p["radius1"], p["radius2"], p["height"]
    base = tuple(p["base"])                       # at the r1 end
    # Orient so 'bottom' has the larger radius; apex (if any) sits at the top.
    if r2 > r1:
        base = _add(base, _scale(axis, h))
        axis = _scale(axis, -1.0)
        r1, r2 = r2, r1
    top = _add(base, _scale(axis, h))
    half_angle = math.atan2(abs(r1 - r2), h) if h > _EPS else 0.0

    # STEP's CONICAL_SURFACE radius grows along its placement axis and the
    # semi-angle must be positive. Since r1 >= r2 (base is the wide end), the
    # surface axis must point apex→base (direction of increasing radius).
    surf = w.add(
        f"CONICAL_SURFACE('',{w.axis2(base, _scale(axis, -1.0), ref)},"
        f"{_num(r1)},{_num(half_angle)})"
    )
    e_bot, v_bot = _full_circle_edge(w, base, axis, ref, r1)

    pl_bot = w.add(f"PLANE('',{w.axis2(base, axis, ref)})")
    bot = w.advanced_face("cap", [w.face_outer_bound(
        w.edge_loop([w.oriented_edge(e_bot, False)]), True)], pl_bot, False)

    if r2 <= _EPS * (1.0 + abs(r1)):              # pointed cone
        apex = w.vertex(top)
        gen = w.line(_add(base, _scale(ref, r1)), _unit(_sub(top, _add(base, _scale(ref, r1)))))
        e_seam = w.edge_curve(v_bot, apex, gen, True)
        lat_loop = w.edge_loop([
            w.oriented_edge(e_bot, True),
            w.oriented_edge(e_seam, True),
            w.oriented_edge(e_seam, False),
        ])
        lateral = w.advanced_face("side", [w.face_outer_bound(lat_loop, True)], surf, True)
        shell = w.closed_shell([lateral, bot])
    else:                                         # frustum
        e_top, v_top = _full_circle_edge(w, top, axis, ref, r2)
        p_b = _add(base, _scale(ref, r1))
        p_t = _add(top, _scale(ref, r2))
        seam = w.line(p_b, _unit(_sub(p_t, p_b)))
        e_seam = w.edge_curve(v_bot, v_top, seam, True)
        lat_loop = w.edge_loop([
            w.oriented_edge(e_bot, True),
            w.oriented_edge(e_seam, True),
            w.oriented_edge(e_top, False),
            w.oriented_edge(e_seam, False),
        ])
        lateral = w.advanced_face("side", [w.face_outer_bound(lat_loop, True)], surf, True)
        pl_top = w.add(f"PLANE('',{w.axis2(top, axis, ref)})")
        top_f = w.advanced_face("cap", [w.face_outer_bound(
            w.edge_loop([w.oriented_edge(e_top, True)]), True)], pl_top, True)
        shell = w.closed_shell([lateral, bot, top_f])

    return w.add(f"MANIFOLD_SOLID_BREP('{_solid_name('cone', p)}',{shell})"), True


def _sphere_item(w, p):
    c = tuple(p["center"])
    r = p["radius"]
    axis = (0.0, 0.0, 1.0)
    x = (1.0, 0.0, 0.0)
    y = _cross(axis, x)                           # meridian-plane normal

    surf = w.add(f"SPHERICAL_SURFACE('',{w.axis2(c, axis, x)},{_num(r)})")
    south = w.vertex(_sub(c, _scale(axis, r)))
    north = w.vertex(_add(c, _scale(axis, r)))
    meridian = w.circle(w.axis2(c, y, x), r)      # great circle in the x-z plane
    e_mer = w.edge_curve(south, north, meridian, True)
    loop = w.edge_loop([w.oriented_edge(e_mer, True), w.oriented_edge(e_mer, False)])
    face = w.advanced_face("sphere", [w.face_outer_bound(loop, True)], surf, True)
    return w.add(f"MANIFOLD_SOLID_BREP('{_solid_name('sphere', p)}',"
                 f"{w.closed_shell([face])})"), True


def _torus_item(w, p):
    c = tuple(p["center"])
    axis = _unit(tuple(p["axis"]))
    x = _perp(axis)
    y = _cross(axis, x)
    big_r, r = p["major_radius"], p["minor_radius"]

    surf = w.add(
        f"TOROIDAL_SURFACE('',{w.axis2(c, axis, x)},{_num(big_r)},{_num(r)})"
    )
    v = _add(c, _scale(x, big_r + r))             # outer-equator seam vertex
    vtx = w.vertex(v)
    major = w.circle(w.axis2(c, axis, x), big_r + r)
    e_major = w.edge_curve(vtx, vtx, major, True)
    spine = _add(c, _scale(x, big_r))
    tube = w.circle(w.axis2(spine, y, x), r)      # tube cross-section in x-z plane
    e_tube = w.edge_curve(vtx, vtx, tube, True)
    loop = w.edge_loop([
        w.oriented_edge(e_tube, True),
        w.oriented_edge(e_major, True),
        w.oriented_edge(e_tube, False),
        w.oriented_edge(e_major, False),
    ])
    face = w.advanced_face("torus", [w.face_outer_bound(loop, True)], surf, True)
    return w.add(f"MANIFOLD_SOLID_BREP('{_solid_name('torus', p)}',"
                 f"{w.closed_shell([face])})"), True


def _box_item(w, p):
    """Oriented cuboid as a closed B-rep solid: 8 vertices, 12 edges, 6 faces."""
    c = tuple(p["center"])
    ax = _unit(tuple(p["ax"]))
    ay = _unit(tuple(p["ay"]))
    az = _unit(tuple(p["az"]))
    hx, hy, hz = p["hx"], p["hy"], p["hz"]

    # 8 corners indexed by (i,j,k) bits → corner = c ± hx ax ± hy ay ± hz az.
    corners = {}
    verts = {}
    for i in (0, 1):
        for j in (0, 1):
            for k in (0, 1):
                pt = c
                pt = _add(pt, _scale(ax, hx if i else -hx))
                pt = _add(pt, _scale(ay, hy if j else -hy))
                pt = _add(pt, _scale(az, hz if k else -hz))
                corners[(i, j, k)] = pt
                verts[(i, j, k)] = w.vertex(pt)

    # Undirected edges, created once and shared (cached by the corner pair).
    edge_cache = {}

    def get_edge(a, b):
        key = frozenset((a, b))
        if key not in edge_cache:
            pa, pb = corners[a], corners[b]
            ln = w.line(pa, _unit(_sub(pb, pa)))
            edge_cache[key] = (w.edge_curve(verts[a], verts[b], ln, True), a, b)
        return edge_cache[key]

    def face(cs, normal):
        # Order the 4 corners CCW as seen from outside (so the loop matches +normal).
        u = _perp(normal)
        v = _cross(normal, u)
        ctr = _scale((0, 0, 0), 0)
        for cc in cs:
            ctr = _add(ctr, corners[cc])
        ctr = _scale(ctr, 0.25)
        ordered = sorted(cs, key=lambda cc: math.atan2(
            _dot(_sub(corners[cc], ctr), v), _dot(_sub(corners[cc], ctr), u)))
        oeds = []
        for n in range(4):
            a, b = ordered[n], ordered[(n + 1) % 4]
            edge, ea, _eb = get_edge(a, b)
            oeds.append(w.oriented_edge(edge, ea == a))
        loop = w.edge_loop(oeds)
        surf = w.add(f"PLANE('',{w.axis2(ctr, normal, u)})")
        return w.advanced_face("box", [w.face_outer_bound(loop, True)], surf, True)

    faces = [
        face([(0, j, k) for j in (0, 1) for k in (0, 1)], _scale(ax, -1)),
        face([(1, j, k) for j in (0, 1) for k in (0, 1)], ax),
        face([(i, 0, k) for i in (0, 1) for k in (0, 1)], _scale(ay, -1)),
        face([(i, 1, k) for i in (0, 1) for k in (0, 1)], ay),
        face([(i, j, 0) for i in (0, 1) for j in (0, 1)], _scale(az, -1)),
        face([(i, j, 1) for i in (0, 1) for j in (0, 1)], az),
    ]
    return w.add(f"MANIFOLD_SOLID_BREP('{_solid_name('box', p)}',"
                 f"{w.closed_shell(faces)})"), True


_BUILDERS = {
    "PLANE": _plane_item,
    "BOX": _box_item,
    "CYLINDER": _cylinder_item,
    "CONE": _cone_item,
    "SPHERE": _sphere_item,
    "TORUS": _torus_item,
    "FILLET": _fillet_item,
}


# --- top-level assembly + product structure -----------------------------------

def build_step(features, *, unit="MM", product_name="Reverse",
               author="", organization="", timestamp="", filename="", pmi=False,
               cutter_mode="SOLID"):
    """Return the full STEP file text for ``features``.

    ``unit`` is one of 'MM', 'M', 'IN' (controls only the declared SI unit/prefix;
    coordinates are written as given). Items are assembled into one shape
    representation with colour styling. With ``pmi=True`` each feature-of-size also
    gets a semantic AP242 dimension (DIMENSIONAL_SIZE) carrying its nominal value.

    This writer has no boolean kernel, so features with op='SUBTRACT' cannot be
    cut from the part. ``cutter_mode`` picks what to do with them instead:
      'SOLID' — write them as plain additive solids (legacy behaviour)
      'MARK'  — write them, but red and named 'cutter:…' so they read as reference
      'SKIP'  — omit them entirely
    """
    w = StepWriter()

    items = []          # (representation_item_id, is_solid)
    styled = []         # STYLED_ITEM ids for colour
    for feat in features:
        kind = feat["kind"]
        builder = _BUILDERS.get(kind)
        if builder is None:
            continue
        is_cutter = feat.get("op") == "SUBTRACT"
        if is_cutter and cutter_mode == "SKIP":
            continue
        params = feat["params"]
        color = feat.get("color") or DEFAULT_COLORS.get(kind, (0.7, 0.7, 0.7))
        if is_cutter and cutter_mode == "MARK":
            params = dict(params, name_prefix="cutter:")
            color = CUTTER_COLOR
        item_id, is_solid = builder(w, params)
        items.append((item_id, is_solid))
        styled.append(_style(w, item_id, color))

    # Geometric context with units + uncertainty.
    ctx, length_unit = _units_context(w, unit)

    # Representation: advanced brep if everything is a solid, else generic.
    all_solid = all(s for _, s in items) and bool(items)
    rep_origin = w.axis2((0.0, 0.0, 0.0), (0.0, 0.0, 1.0), (1.0, 0.0, 0.0))
    item_ids = ",".join(str(i) for i, _ in items) + (
        f",{rep_origin}" if items else str(rep_origin)
    )
    rep_type = "ADVANCED_BREP_SHAPE_REPRESENTATION" if all_solid else "SHAPE_REPRESENTATION"
    rep = w.add(f"{rep_type}('{product_name}',({item_ids}),{ctx})")

    # Colour presentation must reference the same context.
    if styled:
        w.add(
            "MECHANICAL_DESIGN_GEOMETRIC_PRESENTATION_REPRESENTATION('',("
            + ",".join(str(s) for s in styled) + f"),{ctx})"
        )

    _product, pds = _product_structure(w, rep, product_name)

    if pmi:
        _semantic_dimensions(w, features, pds, ctx, length_unit)

    header = _header(filename, author, organization, timestamp)
    return f"{header}\nDATA;\n{w.text()}\nENDSEC;\nEND-ISO-10303-21;\n"


def _style(w, item_id, color):
    r, g, b = color
    col = w.add(f"COLOUR_RGB('',{_num(r)},{_num(g)},{_num(b)})")
    fasc = w.add(f"FILL_AREA_STYLE_COLOUR('',{col})")
    fas = w.add(f"FILL_AREA_STYLE('',({fasc}))")
    ssfa = w.add(f"SURFACE_STYLE_FILL_AREA({fas})")
    sss = w.add(f"SURFACE_SIDE_STYLE('',({ssfa}))")
    ssu = w.add(f"SURFACE_STYLE_USAGE(.BOTH.,{sss})")
    psa = w.add(f"PRESENTATION_STYLE_ASSIGNMENT(({ssu}))")
    return w.add(f"STYLED_ITEM('',({psa}),{item_id})")


def _units_context(w, unit):
    """Return ``(context_id, length_unit_id)`` — the latter for PMI measures."""
    prefix = {"MM": ".MILLI.", "M": "$", "IN": ".MILLI."}.get(unit, ".MILLI.")
    length = w.add(f"( LENGTH_UNIT() NAMED_UNIT(*) SI_UNIT({prefix},.METRE.) )")
    angle = w.add("( NAMED_UNIT(*) PLANE_ANGLE_UNIT() SI_UNIT($,.RADIAN.) )")
    solid = w.add("( NAMED_UNIT(*) SI_UNIT($,.STERADIAN.) SOLID_ANGLE_UNIT() )")
    unc = w.add(
        f"UNCERTAINTY_MEASURE_WITH_UNIT(LENGTH_MEASURE(1.E-06),{length},"
        "'distance_accuracy_value','confusion accuracy')"
    )
    ctx = w.add(
        f"( GEOMETRIC_REPRESENTATION_CONTEXT(3) "
        f"GLOBAL_UNCERTAINTY_ASSIGNED_CONTEXT(({unc})) "
        f"GLOBAL_UNIT_ASSIGNED_CONTEXT(({length},{angle},{solid})) "
        f"REPRESENTATION_CONTEXT('Context','3D Context with UNIT and UNCERTAINTY') )"
    )
    return ctx, length


def _semantic_dimensions(w, features, pds, ctx, length_unit):
    """Emit AP242 semantic PMI: a DIMENSIONAL_SIZE per feature-of-size.

    Each becomes a SHAPE_ASPECT on the product definition shape, a DIMENSIONAL_SIZE
    naming it, and a SHAPE_DIMENSION_REPRESENTATION carrying the nominal value —
    so a CAD package reads the diameter/length as semantic, queryable PMI rather
    than just geometry. Returns the number of dimensions written.
    """
    n = 0
    for feat in features:
        kind = feat["kind"]
        p = feat["params"]
        dims = []                                       # (label, value)
        if kind in ("CYLINDER", "FILLET", "SPHERE"):
            dims.append(("diameter", 2.0 * p["radius"]))
        elif kind == "CONE":
            dims.append(("diameter", 2.0 * max(p["radius1"], p["radius2"])))
        elif kind == "TORUS":
            dims.append(("diameter", 2.0 * p["minor_radius"]))
        for label, value in dims:
            sa = w.add(f"SHAPE_ASPECT('{label}','',{pds},.T.)")
            ds = w.add(f"DIMENSIONAL_SIZE({sa},'{label}')")
            mri = w.add(f"MEASURE_REPRESENTATION_ITEM('{label}',"
                        f"LENGTH_MEASURE({_num(value)}),{length_unit})")
            sdr = w.add(f"SHAPE_DIMENSION_REPRESENTATION('',({mri}),{ctx})")
            w.add(f"DIMENSIONAL_CHARACTERISTIC_REPRESENTATION({ds},{sdr})")
            n += 1
    return n


def _product_structure(w, rep, name):
    app_ctx = w.add(
        "APPLICATION_CONTEXT('managed model based 3d engineering')"
    )
    w.add(
        "APPLICATION_PROTOCOL_DEFINITION('international standard',"
        f"'ap242_managed_model_based_3d_engineering_mim_lf',2020,{app_ctx})"
    )
    prod_ctx = w.add(f"PRODUCT_CONTEXT('',{app_ctx},'mechanical')")
    product = w.add(f"PRODUCT('{name}','{name}','',({prod_ctx}))")
    w.add(f"PRODUCT_RELATED_PRODUCT_CATEGORY('part','',({product}))")
    pdf = w.add(f"PRODUCT_DEFINITION_FORMATION('','',{product})")
    pd_ctx = w.add(f"PRODUCT_DEFINITION_CONTEXT('part definition',{app_ctx},'design')")
    pd = w.add(f"PRODUCT_DEFINITION('design','',{pdf},{pd_ctx})")
    pds = w.add(f"PRODUCT_DEFINITION_SHAPE('','',{pd})")
    w.add(f"SHAPE_DEFINITION_REPRESENTATION({pds},{rep})")
    return product, pds


def _header(filename, author, organization, timestamp):
    def s(v):
        return v.replace("'", "''")
    return (
        "ISO-10303-21;\n"
        "HEADER;\n"
        "FILE_DESCRIPTION(('Reverse mesh-to-parametric analytic export'),'2;1');\n"
        f"FILE_NAME('{s(filename)}','{s(timestamp)}',('{s(author)}'),('{s(organization)}'),"
        "'Reverse (Blender add-on)','Reverse','');\n"
        f"FILE_SCHEMA(('{AP242_SCHEMA}'));\n"
        "ENDSEC;"
    )
