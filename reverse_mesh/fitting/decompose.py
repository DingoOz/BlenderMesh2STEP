# SPDX-License-Identifier: GPL-3.0-or-later
"""Whole-mesh decomposition as a global energy minimization. Pure NumPy, no Blender.

The manual workflow fits one primitive to one hand-picked region. This module
does the whole part at once: it generates an *over-complete pool* of competing
primitive hypotheses at several segmentation scales, then chooses the subset that
best explains the entire mesh by minimizing a single global energy

    E(S) =  Σ_f  w_f · data_cost(f, assign(f))     # area-weighted fit residual
          + μ · Σ_{f unassigned} w_f               # penalty for unexplained area
          + λ · |S|                                # MDL: penalty per primitive
          + ν · boundary_fraction                  # penalty for a fragmented border

where ``w_f`` is face ``f``'s fractional area, ``S`` the chosen primitives, and
``assign(f)`` the primitive a face is assigned to (or ∅). The true global optimum
is NP-hard, so we descend to a local minimum of this *globally-defined* objective:

    1. greedy MDL set-cover init   — repeatedly add the candidate that most reduces E
    2. label refinement            — reassign each face to its best-fitting chosen primitive
    3. merge/refit descent         — collapse adjacent same-kind primitives when it lowers E

Steps 2–3 iterate until E stops improving. A future hard-optimization variant could
swap the greedy/relabel descent for full alpha-expansion graph cuts; the energy and
the candidate pool would stay the same, so it is a drop-in upgrade.

The whole module operates on a :class:`MeshGraph` (plain arrays + an adjacency list),
so the optimizer is unit-testable headless — the bpy→MeshGraph extraction lives in
the operator layer.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from .common import FitResult, Region, region_scale
from .primitives import (
    FITTERS,
    fit_auto,
    fit_fillet,
    normal_alignment,
    signed_distances,
    snap_result,
)


# Default energy weights. Areas are normalized to fractions of the total surface,
# so these are mesh-size independent. λ ("merge pressure") is how much a primitive
# must reduce residual to be worth keeping; μ ("coverage pressure") makes leaving a
# face unexplained cost as much as fitting it at exactly the tolerance limit; ν
# ("boundary smoothness") gently discourages fragmented assignments.
DEFAULT_LAMBDA = 0.01
DEFAULT_MU = 1.0
DEFAULT_NU = 0.02

DEFAULT_ANGLES_DEG = (40.0, 25.0, 12.0, 6.0)


@dataclass
class MeshGraph:
    """A mesh snapshot the optimizer can chew on without holding a bmesh.

    All coordinates/normals are world-space (the operator applies the object's
    matrix when it builds this). ``adjacency[f]`` lists the edge-neighbour face
    ids of face ``f``.
    """

    verts: np.ndarray                 # (V, 3)
    face_vert_idx: list               # per-face int arrays indexing into verts
    centroids: np.ndarray             # (F, 3)
    normals: np.ndarray               # (F, 3) unit
    areas: np.ndarray                 # (F,)
    adjacency: list                   # per-face list[int] of neighbour face ids

    @property
    def n_faces(self) -> int:
        return len(self.centroids)


@dataclass
class Candidate:
    """One primitive hypothesis covering a set of faces."""

    result: FitResult
    faces: tuple                      # face ids this primitive was fit to
    cost: dict                        # face id -> normalized squared residual (no area weight)
    alignment: float


@dataclass
class DecompResult:
    """Outcome of a whole-mesh decomposition."""

    results: list = field(default_factory=list)   # chosen FitResults
    coverage: float = 0.0                          # explained area / total area
    n_primitives: int = 0
    energy: float = 0.0
    leftover_faces: list = field(default_factory=list)
    n_candidates: int = 0


# --- geometry helpers ----------------------------------------------------------

def region_from_faces(graph: MeshGraph, face_ids) -> Region:
    """Pure analogue of ``operators._region_from_faces`` for a face-id set."""
    ids = sorted({int(f) for f in face_ids})
    if ids:
        vids = np.unique(np.concatenate([graph.face_vert_idx[f] for f in ids]))
        points = graph.verts[vids]
    else:
        points = np.zeros((0, 3))
    return Region(points=points,
                  face_points=graph.centroids[ids],
                  face_normals=graph.normals[ids])


def _angle_between(a: np.ndarray, b: np.ndarray) -> float:
    """Angle (radians) between two (unit-ish) vectors, mirroring Vector.angle."""
    na, nb = np.linalg.norm(a), np.linalg.norm(b)
    if na < 1e-12 or nb < 1e-12:
        return 0.0
    c = float(np.clip(np.dot(a, b) / (na * nb), -1.0, 1.0))
    return math.acos(c)


def grow_regions(graph: MeshGraph, angle_rad: float, pool=None) -> list:
    """Flood-fill ``pool`` (default all faces) into smooth-connected regions.

    Crosses an edge only when the two face normals differ by ≤ ``angle_rad`` —
    the same rule as ``operators._grow_region``, so a cube splits at its 90° edges
    but a cylinder wall stays whole.
    """
    n = graph.n_faces
    pool = set(range(n)) if pool is None else set(int(f) for f in pool)
    normals = graph.normals
    adj = graph.adjacency
    visited = set()
    regions = []
    for seed in sorted(pool):
        if seed in visited:
            continue
        comp = [seed]
        visited.add(seed)
        stack = [seed]
        while stack:
            f = stack.pop()
            for nf in adj[f]:
                if nf in pool and nf not in visited:
                    if _angle_between(normals[f], normals[nf]) <= angle_rad:
                        visited.add(nf)
                        comp.append(nf)
                        stack.append(nf)
        regions.append(comp)
    return regions


def _fillet_alignment(result: FitResult, region: Region) -> float:
    """Mean |cos| between a fillet's radial (cylinder-like) normal and face normals.

    ``primitives.predicted_normals`` has no FILLET branch, so ``normal_alignment``
    raises for fillets — compute it here from the partial-cylinder geometry.
    """
    fn = region.face_normals
    if not len(fn) or not np.any(fn):
        return 1.0
    p = result.params
    axis = np.asarray(p["axis"], dtype=float)
    axis = axis / max(np.linalg.norm(axis), 1e-12)
    rel = region.face_points - np.asarray(p["base"], dtype=float)
    w = rel @ axis
    rho = rel - np.outer(w, axis)
    n = rho / np.clip(np.linalg.norm(rho, axis=1, keepdims=True), 1e-12, None)
    return float(np.mean(np.abs(np.sum(n * fn, axis=1))))


def _alignment(result: FitResult, region: Region) -> float:
    if result.kind == "FILLET":
        return _fillet_alignment(result, region)
    return normal_alignment(result, region)


def _accept(result, region, tolerance, alignment_gate) -> bool:
    """Quality gate: a primitive must fit its region within tolerance and agree
    with its face normals — the same two tests the manual AUTO path uses."""
    if result is None:
        return False
    if result.rel_rms > tolerance:
        return False
    return _alignment(result, region) >= alignment_gate


def _fit_region_variants(region, tolerance, alignment_gate, snap):
    """Every accepted primitive for a region: the AUTO best, plus a fillet if the
    region is actually a partial-cylinder arc (fit_fillet is outside FITTERS).

    A genuine arc (span < 330°) is read as a *trimmed* fillet rather than a full
    cylinder: where a fillet is accepted we suppress an AUTO CYLINDER for the same
    region, since a full cylindrical solid would wrongly include the missing wedge.
    """
    auto = fit_auto(region)
    fil = fit_fillet(region)
    auto_ok = _accept(auto, region, tolerance, alignment_gate)
    fil_ok = _accept(fil, region, tolerance, alignment_gate)
    out = []
    if auto_ok and not (fil_ok and auto.kind == "CYLINDER"):
        if snap is not None:
            snap_result(auto, step=snap)
        out.append(auto)
    if fil_ok:
        if snap is not None:
            snap_result(fil, step=snap)
        out.append(fil)
    return out


def _make_candidate(graph, result, faces, tol_dist) -> Candidate:
    ids = list(faces)
    resid = np.abs(signed_distances(result, graph.centroids[ids]))
    norm = (resid / tol_dist) ** 2
    cost = {int(f): float(norm[i]) for i, f in enumerate(ids)}
    return Candidate(result=result, faces=tuple(int(f) for f in ids),
                     cost=cost, alignment=_alignment(result, region_from_faces(graph, ids)))


def build_candidates(graph: MeshGraph, *, angles_rad, tolerance, alignment_gate,
                     min_faces, snap=None, progress=None):
    """Generate the competing primitive pool over a coarse→fine angle sweep.

    Every smooth region at every scale that fits a primitive within tolerance
    becomes a candidate. Identical face sets (a flat face is one region at every
    scale) are de-duplicated. Returns ``(candidates, tol_dist)``.
    """
    scale = region_scale(graph.verts)
    tol_dist = max(tolerance * scale, 1e-12)
    seen = set()
    cands = []
    for ang in angles_rad:
        for comp in grow_regions(graph, ang):
            if len(comp) < min_faces:
                continue
            key = frozenset(comp)
            if key in seen:
                continue
            seen.add(key)
            region = region_from_faces(graph, comp)
            if len(region.points) < 3:
                continue
            for result in _fit_region_variants(region, tolerance, alignment_gate, snap):
                cands.append(_make_candidate(graph, result, comp, tol_dist))
        if progress is not None:
            progress(0.1 + 0.5 * (angles_rad.index(ang) + 1) / max(len(angles_rad), 1))
    return cands, tol_dist


# --- energy --------------------------------------------------------------------

def _boundary_fraction(graph, label) -> float:
    """Fraction of adjacent face pairs whose labels differ (unassigned = its own
    label). 0 = every face agrees with all its neighbours."""
    pairs = 0
    crossed = 0
    for f, nbrs in enumerate(graph.adjacency):
        for nf in nbrs:
            if nf > f:
                pairs += 1
                if label[f] != label[nf]:
                    crossed += 1
    return (crossed / pairs) if pairs else 0.0


def decomposition_energy(graph, label, selected, candidates, w, *, lam, mu, nu):
    """Full global energy of an assignment (used for reporting and merge decisions)."""
    E = lam * len(selected)
    for f in range(graph.n_faces):
        l = label[f]
        if l < 0:
            E += mu * w[f]
        else:
            E += w[f] * candidates[l].cost[f]
    E += nu * _boundary_fraction(graph, label)
    return float(E)


# --- the optimizer -------------------------------------------------------------

def _greedy_cover(graph, candidates, w, lam, mu):
    """Greedy MDL set-cover: add the candidate that most reduces the data+MDL
    terms until none helps. Returns ``(label, selected)``.

    Boundary (ν) is intentionally left out of the marginal gain here — it is a
    second-order term handled by the refinement passes; folding it into the greedy
    score would make each step O(adjacency) for little benefit.
    """
    F = graph.n_faces
    label = np.full(F, -1, dtype=int)
    selected = []
    while True:
        best_gain = -1e-12          # require a strict improvement
        best_ci = None
        best_wins = None
        for ci, c in enumerate(candidates):
            if ci in selected:
                continue
            dE = lam                 # cost of introducing one more primitive
            wins = []
            for f in c.faces:
                cur = mu * w[f] if label[f] < 0 else w[f] * candidates[label[f]].cost[f]
                new = w[f] * c.cost[f]
                if new < cur:
                    dE += (new - cur)
                    wins.append(f)
            if dE < best_gain:
                best_gain = dE
                best_ci = ci
                best_wins = wins
        if best_ci is None:
            break
        for f in best_wins:
            label[f] = best_ci
        selected.append(best_ci)
    return label, selected


def _refine_labels(graph, candidates, selected, label):
    """Reassign every face to whichever *selected* candidate fits it cheapest,
    then drop candidates that end up owning nothing. Mutates ``label`` in place,
    returns the pruned ``selected``."""
    # face -> selected candidates that contain it
    contains = {f: [] for f in range(graph.n_faces)}
    for ci in selected:
        for f in candidates[ci].faces:
            contains[f].append(ci)
    for f in range(graph.n_faces):
        opts = contains[f]
        if not opts:
            label[f] = -1
            continue
        label[f] = min(opts, key=lambda ci: candidates[ci].cost[f])
    owned = set(int(l) for l in label if l >= 0)
    return [ci for ci in selected if ci in owned]


def _owned_faces(selected, label):
    out = {ci: [] for ci in selected}
    for f, l in enumerate(label):
        if l >= 0:
            out[int(l)].append(f)
    return out


def _merge_pass(graph, candidates, selected, label, w, tol_dist, *,
                lam, mu, nu, tolerance, alignment_gate, snap):
    """Try to collapse adjacent same-kind primitives into one refit. Applies a
    merge only when it strictly lowers the global energy. Returns
    ``(candidates, selected, label, changed)`` (candidates may gain merged entries).
    """
    owned = _owned_faces(selected, label)
    base_E = decomposition_energy(graph, label, selected, candidates, w,
                                  lam=lam, mu=mu, nu=nu)
    for a_i in range(len(selected)):
        for b_i in range(a_i + 1, len(selected)):
            a, b = selected[a_i], selected[b_i]
            ra, rb = candidates[a].result, candidates[b].result
            if ra.kind != rb.kind or ra.kind not in FITTERS:
                continue
            fa, fb = owned[a], owned[b]
            if not fa or not fb:
                continue
            # Adjacent only: some owned face of a borders some owned face of b.
            sb = set(fb)
            if not any(nf in sb for f in fa for nf in graph.adjacency[f]):
                continue
            union = fa + fb
            region = region_from_faces(graph, union)
            merged = FITTERS[ra.kind](region)
            if not _accept(merged, region, tolerance, alignment_gate):
                continue
            if snap is not None:
                snap_result(merged, step=snap)
            mc = _make_candidate(graph, merged, union, tol_dist)
            new_id = len(candidates)
            # Trial assignment: a, b → merged on the union faces.
            trial = label.copy()
            for f in union:
                trial[f] = new_id
            trial_selected = [c for c in selected if c not in (a, b)] + [new_id]
            trial_cands = candidates + [mc]
            new_E = decomposition_energy(graph, trial, trial_selected, trial_cands, w,
                                         lam=lam, mu=mu, nu=nu)
            if new_E < base_E - 1e-12:
                candidates.append(mc)
                label[:] = trial
                selected = trial_selected
                return candidates, selected, label, True
    return candidates, selected, label, False


def optimize_decomposition(graph: MeshGraph, *, angles=DEFAULT_ANGLES_DEG,
                           lam=DEFAULT_LAMBDA, mu=DEFAULT_MU, nu=DEFAULT_NU,
                           tolerance=0.02, alignment_gate=0.9, min_faces=4,
                           snap=None, merge=True, max_iter=6,
                           progress=None) -> DecompResult:
    """Decompose a whole mesh into an energy-minimizing set of primitives."""
    F = graph.n_faces
    if F == 0:
        return DecompResult()
    total_area = float(graph.areas.sum())
    if total_area <= 0:
        return DecompResult()
    w = graph.areas / total_area

    angles_rad = [math.radians(a) for a in angles]
    candidates, tol_dist = build_candidates(
        graph, angles_rad=angles_rad, tolerance=tolerance,
        alignment_gate=alignment_gate, min_faces=min_faces, snap=snap,
        progress=progress)
    if not candidates:
        return DecompResult(n_candidates=0,
                            leftover_faces=list(range(F)))

    label, selected = _greedy_cover(graph, candidates, w, lam, mu)
    selected = _refine_labels(graph, candidates, selected, label)

    if merge:
        for _ in range(max_iter):
            candidates, selected, label, changed = _merge_pass(
                graph, candidates, selected, label, w, tol_dist,
                lam=lam, mu=mu, nu=nu, tolerance=tolerance,
                alignment_gate=alignment_gate, snap=snap)
            selected = _refine_labels(graph, candidates, selected, label)
            if not changed:
                break
    if progress is not None:
        progress(0.7)

    energy = decomposition_energy(graph, label, selected, candidates, w,
                                  lam=lam, mu=mu, nu=nu)
    leftover = [int(f) for f in range(F) if label[f] < 0]
    assigned_area = total_area - float(graph.areas[label < 0].sum())
    # Preserve a stable, coarse→fine-ish order: by first owned face.
    owned = _owned_faces(selected, label)
    order = sorted(selected, key=lambda ci: min(owned[ci]) if owned[ci] else 1 << 30)
    results = [candidates[ci].result for ci in order]
    return DecompResult(results=results,
                        coverage=assigned_area / total_area,
                        n_primitives=len(results),
                        energy=energy,
                        leftover_faces=leftover,
                        n_candidates=len(candidates))
