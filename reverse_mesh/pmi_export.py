# SPDX-License-Identifier: GPL-3.0-or-later
"""PMI (dimension) sidecar export.

Writes the fitted features' measurable dimensions — radii, diameters, lengths,
taper angles, thread specs — plus pairwise relationships (axis angles, reference-
point distances, e.g. hole-to-hole spacing) to a ``.pmi.json`` and a flat
``.pmi.csv`` next to the STEP file. This gives a machine shop real numbers
alongside the geometry without depending on full AP242 semantic PMI (#11b).

Pure Python (stdlib only) — no Blender, no OCCT — so it works on every path and
is unit-testable. The feature dicts are the same ones handed to the STEP writers.
"""

from __future__ import annotations

import csv
import json
import math

# Above this many features, skip the O(n²) pairwise relationships (and say so).
_MAX_PAIRWISE = 40


def feature_dimensions(feat):
    """Named scalar dimensions for one feature (mm / degrees)."""
    kind = feat["kind"]
    p = feat["params"]
    d = {}
    if kind == "CYLINDER":
        d = {"radius": p["radius"], "diameter": 2.0 * p["radius"], "height": p["height"]}
    elif kind == "SPHERE":
        d = {"radius": p["radius"], "diameter": 2.0 * p["radius"]}
    elif kind == "CONE":
        d = {"radius1": p["radius1"], "radius2": p["radius2"], "height": p["height"],
             "half_angle_deg": math.degrees(p.get("half_angle", 0.0))}
    elif kind == "TORUS":
        d = {"major_radius": p["major_radius"], "minor_radius": p["minor_radius"]}
    elif kind == "BOX":
        d = {"size_x": 2.0 * p["hx"], "size_y": 2.0 * p["hy"], "size_z": 2.0 * p["hz"]}
    elif kind == "PLANE":
        d = {"extent_u": 2.0 * p["half_u"], "extent_v": 2.0 * p["half_v"]}
    elif kind == "EXTRUDE":
        try:
            from .fitting import profile as profile2d
        except ImportError:                  # standalone (pure-Python tests)
            from fitting import profile as profile2d
        d = {"height": p["height"],
             "profile_area": profile2d.profile_area(p["profile"]),
             "profile_perimeter": profile2d.profile_perimeter(p["profile"])}
    if p.get("thread_spec"):
        d["thread"] = p["thread_spec"]
    return {k: (round(v, 6) if isinstance(v, (int, float)) else v) for k, v in d.items()}


def _ref_point(feat):
    p = feat["params"]
    for key in ("base", "center", "apex", "point"):
        if key in p:
            return tuple(float(c) for c in p[key])
    return None


def _axis(feat):
    p = feat["params"]
    for key in ("axis", "normal"):
        if key in p:
            return tuple(float(c) for c in p[key])
    return None


def _angle_deg(a, b):
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na < 1e-12 or nb < 1e-12:
        return None
    c = max(-1.0, min(1.0, dot / (na * nb)))
    return round(math.degrees(math.acos(abs(c))), 4)   # 0..90, undirected


def _distance(a, b):
    return round(math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b))), 6)


def build_pmi(features):
    """Return the PMI report as a plain dict: per-feature dims + relationships."""
    feats = []
    for i, f in enumerate(features):
        feats.append({
            "index": i,
            "name": f.get("name", f"feature_{i}"),
            "kind": f["kind"],
            "dimensions": feature_dimensions(f),
        })

    relationships = []
    truncated = len(features) > _MAX_PAIRWISE
    if not truncated:
        for i in range(len(features)):
            for j in range(i + 1, len(features)):
                fi, fj = features[i], features[j]
                pi, pj = _ref_point(fi), _ref_point(fj)
                if pi is not None and pj is not None:
                    relationships.append({"a": i, "b": j, "type": "distance",
                                          "value": _distance(pi, pj)})
                ai, aj = _axis(fi), _axis(fj)
                if ai is not None and aj is not None:
                    ang = _angle_deg(ai, aj)
                    if ang is not None:
                        relationships.append({"a": i, "b": j, "type": "axis_angle_deg",
                                              "value": ang})

    report = {"features": feats, "relationships": relationships}
    if truncated:
        report["note"] = (f"pairwise relationships skipped "
                          f"({len(features)} features > {_MAX_PAIRWISE})")
    return report


def write_sidecar(features, step_path):
    """Write ``<step>.pmi.json`` and ``<step>.pmi.csv``; return their paths."""
    report = build_pmi(features)
    base = step_path
    for ext in (".step", ".stp", ".STEP", ".STP"):
        if base.endswith(ext):
            base = base[: -len(ext)]
            break
    json_path = base + ".pmi.json"
    csv_path = base + ".pmi.csv"

    with open(json_path, "w", encoding="utf-8") as fp:
        json.dump(report, fp, indent=2)

    with open(csv_path, "w", encoding="utf-8", newline="") as fp:
        writer = csv.writer(fp)
        writer.writerow(["feature", "kind", "dimension", "value"])
        for f in report["features"]:
            for name, value in f["dimensions"].items():
                writer.writerow([f["name"], f["kind"], name, value])
        for rel in report["relationships"]:
            writer.writerow([f"{rel['a']}↔{rel['b']}", "relationship",
                             rel["type"], rel["value"]])

    return json_path, csv_path
