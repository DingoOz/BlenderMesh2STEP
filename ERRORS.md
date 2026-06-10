# Error Log

### NumPy broadcast mismatch between vertex and face arrays — 2026-05-29

- **Severity:** Medium
- **Category:** Logic
- **File(s):** `reverse_mesh/fitting/primitives.py`
- **Pattern:** Element-wise combining two per-mesh-element arrays (e.g. `normals * points`) while assuming they have the same length, when one is per-vertex and the other is per-face. In Blender a selected region has N unique vertices but M faces (N ≠ M).
- **Root cause:** The standalone test built points and normals as equal-length paired samples, masking that the real Blender extraction yields per-vertex points and per-face normals of different counts. `fit_cone` paired them directly.
- **Fix applied:** Introduced a `Region` type carrying vertices *and* paired face-centroids/face-normals; fitters that need point↔normal pairs use the face arrays, geometry-only fits use vertices.
- **Prevention rule:** Never element-wise combine two mesh-derived arrays without asserting they index the same elements; carry paired data (point+normal per face) in one structure rather than as separate positional arguments.

### AUTO primitive selection by RMS alone is ambiguous — 2026-05-29

- **Severity:** Medium
- **Category:** Logic
- **File(s):** `reverse_mesh/fitting/primitives.py`
- **Pattern:** Choosing among competing model fits using a single point-distance residual, when distinct models fit the same sample equally well (two rings of points lie exactly on both a cylinder and a sphere). Point-only residual cannot disambiguate.
- **Root cause:** Blender's default cylinder has vertices on only two rings; a sphere passes through both, giving a near-zero RMS, so AUTO picked SPHERE over CYLINDER. Face centroids sit on the sphere's equator, so normals alone didn't break the tie either.
- **Fix applied:** Added a normal-agreement gate (predicted vs actual face normals) plus an Occam tie-break preferring the simpler primitive (plane > cylinder > cone > sphere) among essentially-exact fits.
- **Prevention rule:** When auto-selecting between models, use all available signal (normals, not just point distance) and add an explicit simplicity tie-break for genuinely ambiguous data; document that ambiguity rather than trusting the lowest residual.

### Single-primitive fit to a multi-surface selection — 2026-05-29

- **Severity:** Medium
- **Category:** Logic
- **File(s):** `reverse_mesh/operators.py`, `reverse_mesh/fitting/primitives.py`
- **Pattern:** Fitting one analytic primitive to a selection that actually contains several distinct surfaces. A cube (6 planes) is fit as a single sphere because all 8 corners are equidistant from the cube centre AND the sphere's predicted normals match the face-centre normals, defeating the normal-alignment guard.
- **Root cause:** The fitter assumes the selection is one smooth surface; nothing splits a multi-face selection into per-surface regions first.
- **Fix applied:** Added crease-angle region segmentation (`_segment_faces`): edge-adjacent faces join a region only if their normals agree within a threshold; each region is fit independently. A cube → 6 planes. Single-fit mode now warns when the selection spans multiple regions.
- **Prevention rule:** Before fitting a single model to a region, verify the region is one surface (connected + smooth); when in doubt, segment first and fit per region rather than forcing one global fit.

### STEP CONICAL_SURFACE axis/semi-angle direction — 2026-05-29

- **Severity:** High
- **Category:** API Misuse
- **File(s):** `reverse_mesh/step_export.py`
- **Pattern:** Emitting a STEP `CONICAL_SURFACE` with the placement axis pointing the wrong way. STEP requires `semi_angle > 0` and radius = `ref_radius + u·tan(semi_angle)` *increasing* along the placement axis. A cone that narrows along its build axis must have its surface placement axis pointing apex→base (toward increasing radius), or the surface is inconsistent with the bounding cap circles.
- **Root cause:** Used the base→top axis directly; for a downward-narrowing cone that implies a negative semi-angle, so the emitted surface effectively became near-cylindrical (frustum volume wrong: 139 vs 68).
- **Fix applied:** Place `CONICAL_SURFACE` at the wide end with axis = −(base→top) and positive semi-angle, so both cap circles lie on the surface. Caught by an OpenCASCADE round-trip volume check.
- **Prevention rule:** Validate hand-authored BREP/STEP by importing it into a real kernel (OCCT) and checking `BRepCheck_Analyzer.IsValid()` AND volume — topological validity alone passed while the geometry was wrong.

### STEP open shell not wrapped in a surface model — 2026-05-29

- **Severity:** Medium
- **Category:** API Misuse
- **File(s):** `reverse_mesh/step_export.py`
- **Pattern:** Placing an `OPEN_SHELL` directly as a representation item. A shell is a topological item; to appear in a shape representation it must be wrapped in `SHELL_BASED_SURFACE_MODEL` (or `MANIFOLD_SURFACE_SHAPE_REPRESENTATION`). Importers silently dropped the faces.
- **Root cause:** Returned the open shell as the plane's representation item without a geometric surface-model wrapper.
- **Fix applied:** Wrap the open shell in `SHELL_BASED_SURFACE_MODEL` before adding it to the representation; planes then import correctly (face count rose from solids-only to include them).
- **Prevention rule:** Representation items must be geometric/topological *model* entities (solid_model, surface_model, geometric_set) — never a raw shell; verify by counting imported faces, not just file validity.

### Box orientation via normal covariance is degenerate — 2026-05-29

- **Severity:** High
- **Category:** Logic
- **File(s):** `reverse_mesh/fitting/primitives.py`
- **Pattern:** Recovering an oriented box's axes from the eigenvectors of the face-normal covariance `Σ nᵢnᵢᵀ`. For a cube the three eigenvalues are equal (isotropic), so eigenvectors are arbitrary — a rotated cube collapses to the world-axis bounding box (extents too large).
- **Root cause:** Covariance eigen-decomposition is ill-posed when contributions along the axes are balanced (the usual case for boxes).
- **Fix applied:** Recover axes by *clustering* the face normals into three ±directions instead of eigen-decomposition; orthonormalise the two best-supported clusters.
- **Prevention rule:** Don't use PCA/covariance eigenvectors when the expected structure has symmetric/equal variance — cluster or search for the discrete directions instead.

### Box fit accepted on points that merely lie on a box surface — 2026-05-29

- **Severity:** Medium
- **Category:** Logic
- **File(s):** `reverse_mesh/fitting/primitives.py`
- **Pattern:** A two-ring selection (cylinder side) has all its vertices on a box's top/bottom caps, so a point-only box residual is ~0 and the box wins over the cylinder. The AUTO normal-alignment gate is fooled because the side-face centroids sit where the box predicts radial normals.
- **Root cause:** Box acceptance used only point-to-surface distance; it ignored whether the face normals actually align with the box's three axes.
- **Fix applied:** Reject a box fit unless ≥80% of face normals are within ~10° of one of the three box axes. Cylinders/spheres fail this; real boxes pass.
- **Prevention rule:** For a multi-face primitive (box), require the *normals* to match its faces, not just that points lie on its surface; a low point residual alone is not sufficient evidence of the shape.

### bmesh select_flush(True) re-selects unwanted faces sharing all verts — 2026-06-03

- **Severity:** Medium
- **Category:** API Misuse
- **File(s):** `reverse_mesh/operators.py`
- **Pattern:** After selecting a set of faces in a bmesh, calling `bm.select_flush(True)` to "finish" the selection. The upward flush selects any face whose vertices are *all* already selected — so selecting a cylinder's wall quads also grabs the end-cap n-gon (its every vertex is a shared rim vertex), even though the cap was deliberately excluded.
- **Root cause:** `select_flush(True)` propagates selection vert→edge→face (upward); a face fully surrounded by selected geometry becomes selected as a side effect, regardless of the intended face set.
- **Fix applied:** Dropped the upward flush. `BMFace.select_set(True)` already selects the face's own verts/edges, which is all that's needed; for explicit face sets, never flush selection upward.
- **Prevention rule:** When you have computed an exact face set, set `face.select_set(True)` per face and do NOT call `select_flush(True)`. Reserve upward flush for genuine vert/edge-driven selections; if you must flush, use `select_flush_mode()` and verify it doesn't capture fully-enclosed faces.

### Trimmed least-squares can't reject outliers from a corrupted fit — 2026-06-03

- **Severity:** Medium
- **Category:** Logic
- **File(s):** `reverse_mesh/fitting/primitives.py`
- **Pattern:** Implementing "robust" fitting as fit-all → drop high-residual points → refit. When outliers are numerous/far enough to drag the initial least-squares fit, that fit *mis-ranks* residuals (outliers look like inliers and true inliers look like outliers), so trimming removes the wrong points and never converges to the true model.
- **Root cause:** The trim threshold is computed from a model already biased by the outliers; iterative trimming has no way to escape a bad basin.
- **Fix applied:** Replaced trimmed-LSQ with RANSAC consensus: fit many small random samples, keep the model with the most inliers, then refit once on that consensus set. Added a short-circuit returning the plain fit when it is already clean (rel_rms < 1e-3) so machine-precision fits are never disturbed.
- **Prevention rule:** For outlier rejection, find the model by minimal/small-sample consensus (RANSAC), not by trimming a global fit. Only trim/reweight once you already have an outlier-free model estimate.

### normal_alignment() raises for FILLET — predicted_normals has no FILLET branch — 2026-06-06

- **Severity:** Medium
- **Category:** API Misuse
- **File(s):** `reverse_mesh/fitting/primitives.py`, `reverse_mesh/fitting/decompose.py`
- **Pattern:** Calling `normal_alignment(result, region)` (or `predicted_normals(result, pts)`) on an arbitrary `FitResult` without checking its `kind`. `predicted_normals` implements every primitive *except* FILLET and raises `ValueError(result.kind)` on the fallthrough, so any alignment/normal-agreement check crashes for fillet fits. Easy to miss because FILLET is excluded from the `FITTERS` registry, so most code paths never feed a fillet through these helpers.
- **Root cause:** FILLET was added as a special-case fit (outside `FITTERS`) and `predicted_normals` was never extended to cover it; the trimmed partial-cylinder shares the cylinder's radial normal but has no branch.
- **Fix applied:** In `decompose.py` the alignment gate routes FILLET through a local `_fillet_alignment` that computes the radial (cylinder-like) normal directly, and only calls `normal_alignment` for the other kinds.
- **Prevention rule:** Before calling `predicted_normals`/`normal_alignment` on a `FitResult` of unknown kind, guard for FILLET (and any future non-`FITTERS` kind). Better long-term: add a FILLET branch to `predicted_normals` (radial normal, same as CYLINDER) so the helpers are total over all kinds.

### Plane patch sized by arbitrary-axis bounding box overshoots the region — 2026-06-06

- **Severity:** Medium
- **Category:** Logic
- **File(s):** `reverse_mesh/fitting/primitives.py`
- **Pattern:** Representing a fitted planar patch as a rectangle sized along *arbitrary* axes (e.g. `orthonormal_basis(normal)`, seeded from a world axis) via a centroid-centred axis-aligned bounding box. For an elongated or diagonally-oriented region the rectangle is far larger than the region; its empty corners extend past the true surface, so the exported flat patch visibly juts outside the volume on elongated/freeform parts (a plain sphere was fine — one exact primitive — but a stretched one fragmented into oversized plane patches).
- **Root cause:** The quad's in-plane axes were not aligned to the data, and its extent was the symmetric max-abs about the centroid, so a thin diagonal patch got a large square-ish bounding box.
- **Fix applied:** Align the in-plane axes to the region's principal directions (SVD of the points projected into the plane), size the quad to the actual per-axis min/max, and centre it on that bounding rectangle (a shift that stays within the fitted plane, so residuals/normal are unchanged).
- **Prevention rule:** When turning a fitted region into a bounded patch, derive the patch frame from the data (PCA/principal axes), not from a fixed world seed, and size it to the real min/max extent. Treat any centroid-centred, axis-arbitrary bounding box over a non-square region as an overshoot bug.

### Degenerate huge-radius sphere/cylinder beats the plane on gently-curved patches — 2026-06-06

- **Severity:** High
- **Category:** Logic
- **File(s):** `reverse_mesh/fitting/decompose.py`
- **Pattern:** Accepting a least-squares curved-primitive fit (sphere/cylinder/cone/torus) by RMS/tolerance alone, with no bound on its radius relative to the region. A near-flat patch fits a sphere (or cylinder) of enormous radius with very low RMS — and that degenerate fit beats the correct plane on raw RMS — so whole-mesh decompose covered a capsule's gently-curved wall in dozens of oversized spheres centred far from the part ("many oversized spheres at the mesh faces").
- **Root cause:** A flat surface is the limit of a sphere as radius→∞; the algebraic fit happily returns that giant sphere, and nothing distinguished it from a real one. fit_auto's Occam tie-break only prefers the plane when it is "essentially exact" (rel_rms < 1e-3); for a moderately curved patch the plane is not exact, so the lower-RMS giant sphere won.
- **Fix applied:** Reject a curved primitive whose characteristic radius exceeds `DEGENERATE_RADIUS_RATIO` (20) × the region's own scale, and fall back to fitting a plane for that patch. Genuine spheres/cylinders (radius on the order of the region) and large features captured by a coarse-scale candidate still pass.
- **Prevention rule:** When fitting an unbounded-radius primitive, gate on a physical-plausibility bound (radius not vastly larger than the data it was fit to), not on residual alone. Treat "radius ≫ region size" as a flat patch and represent it as a plane.

### STEP export scale ignored Blender scene units — silent ×1000 size error — 2026-06-10

- **Severity:** High
- **Category:** Logic
- **File(s):** `reverse_mesh/operators.py`, `reverse_mesh/units.py`
- **Pattern:** Writing world-space coordinates into a file that declares a physical unit (STEP `unit="MM"`) while leaving the coordinate scale at a constant default (`scale=1.0`), never consulting `scene.unit_settings`. In Blender's default metric scene 1 BU = 1 m, so every export was declared in mm but written in metres — a 50 mm part arrived in CAD as 0.05 mm. Invisible in tests because tests only checked headers/topology, never absolute dimensions.
- **Root cause:** The exporter treated unit choice as cosmetic metadata ("controls only the declared SI prefix; coordinates are written as given") and left unit correctness entirely to the user.
- **Fix applied:** New bpy-free `units.effective_scale()` derives BU→STEP-unit scale from `scene.unit_settings` (`scale_length`, `system`); the operator defaults to scene-derived scale, defaults the STEP unit from the scene display unit on invoke, keeps a Manual override, and shows the effective scale in the export dialog.
- **Prevention rule:** Any exporter that declares a physical unit must compute the coordinate scale from the scene's unit settings (or refuse/warn), and a test must assert an absolute dimension in the output file, not just structure.

### Pure-Python STEP writer wrote SUBTRACT cutters as additive solids — 2026-06-10

- **Severity:** High
- **Category:** Logic
- **File(s):** `reverse_mesh/step_export.py`, `reverse_mesh/operators.py`
- **Pattern:** A consumer (the pure-Python writer) silently ignoring a semantic field it cannot honour (`feature["op"]`). Lacking a boolean kernel, it wrote SUBTRACT features as ordinary solids, so a hole cutter became material filling the hole — geometrically wrong output with no warning anywhere.
- **Root cause:** The `op` field was added for the OCCT backend; the pure-Python writer's feature loop predates it and was never taught to even acknowledge the role.
- **Fix applied:** `build_step` gained `cutter_mode` (SOLID legacy / MARK red `cutter:` reference solids / SKIP); the export operator defaults to MARK on the pure-Python path and reports a warning whenever cutters cannot be subtracted.
- **Prevention rule:** When a schema field changes meaning of the geometry (boolean role, cut mode), every writer/consumer must either honour it or explicitly surface that it cannot — grep all consumers of the feature dict when adding such a field.
