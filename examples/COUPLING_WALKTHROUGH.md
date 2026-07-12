# Worked example: a flange coupling, feature by feature

This example shows how BlenderMesh2STEP's individual features **combine** into a
single manufacturable STEP solid. The part is a **square flange coupling** — a
common mechanical demo part (a keyed hub on a bolted plate).

Files in this folder:

| File | What it is |
|------|-----------|
| `BlenderMesh2STEP_coupling.blend` | The scene: the assembled part + an *exploded* "how it's made" strip |
| `flange_coupling.step` | The final export — **one watertight AP242 solid**, 35 analytic faces, 33 746 mm³, BRepCheck-valid |
| `build_coupling.py` | Regenerates both headless: `blender --background --python examples/build_coupling.py` |

Open the `.blend`, press `N` for the **Reverse** tab, and switch the viewport
to **Object** colour shading (Viewport Shading dropdown → Color → Object) so the
red *Subtract* cutters read at a glance.

## The recipe: 9 features, 2 roles

Every solid you see carries exact analytic parameters and a **role** — *Add*
(material) or *Subtract* (a cutter). The exporter fuses all the Adds into one
body, then cuts every Subtract out of it.

| # | Feature | Primitive | Role | What it contributes |
|---|---------|-----------|------|---------------------|
| 01 | Plate | **Extrude** | Add | The rounded-square base — a profile of 4 lines + 4 corner arcs, extruded 8 mm |
| 02 | Hub | **Cylinder** | Add | The central boss the shaft runs through |
| 03 | Bore | **Cylinder** | Subtract | The through-hole down the axis |
| 04 | Keyway | **Box** | Subtract | The rectangular key slot cut into the bore wall |
| 05 | Relief | **Revolve** | Subtract | A *turned* undercut ring at the hub base (a line/arc profile spun 360°) |
| 06 | Bolt 1–4 | **Cylinder** ×4 | Subtract | The corner mounting holes — each with a **counterbore** recess for a cap-screw head |

That is **five of the tool's primitive types working in one part** — Extrude,
Cylinder, Box, Revolve — plus the counterbore preset and a bolt pattern.

## How they combine into one solid

The boolean algebra the exporter runs (OCCT path, *Merge into one solid* on):

```
final =  (Plate ∪ Hub)                     ← the two Add solids, fused
           − Bore − Keyway − Relief         ← the turned/slotted subtractions
           − Bolt1 − Bolt2 − Bolt3 − Bolt4  ← the counterbored hole pattern
```

Because each feature is an exact analytic primitive, the result is not a mesh
approximation — it is a real B-rep with planar, cylindrical, conical (the
counterbores), and toroidal/planar (the revolve) faces that a CAD package opens
as editable, measurable geometry. Verified output:

```
solids = 1   faces = 35   valid = True   volume = 33 746.5 mm³
```

The `.blend` stores this as the **Validation Report** so you see it in the panel
the moment you open the file.

## Reproduce it interactively (the manual workflow)

You do not have to build it forward — this is exactly the *reverse* workflow on a
real mesh, just pre-assembled here. To recreate it yourself from scratch:

1. **Plate.** In *Build → STEP Primitives*, pick **Extrude**, or in the reverse
   workflow select a prism's faces and **Fit** (Auto → Extrude).
2. **Hub.** Add a **Cylinder** with role **Add**, centred on the plate.
3. **Bore.** Add a **Cylinder**, role **Subtract**, **Through** — the axial hole.
4. **Keyway.** Add a **Box**, role **Subtract**, poking into the bore wall.
5. **Relief groove.** Add a **Revolve** ring, role **Subtract** — a turned undercut.
6. **Bolt holes.** Add one **Cylinder** subtract at a corner, set its **Hole**
   preset to **Counterbore**, then **Propagate Pattern** to find and fit the
   other three automatically.
7. **Export.** *Fitted Features → Export STEP (AP242)*, backend **OCCT**, tick
   **Merge into one solid**, and read the **Validation Report** — you should get
   `1 solid · valid` with a plausible volume.

## The point

Nine simple, exact features — chosen and placed by you — compose through boolean
operations into a single watertight solid a machine shop can quote from. No
feature here is a guess: each is a primitive you named, fitted to (or built at)
its exact dimensions. That is the whole idea — *you supply the intent, the tool
supplies the precision.*
