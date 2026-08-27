# ANYfem

A shell and beam element Finite Element Method tool: geometry modelling,
meshing, loads and boundary conditions, solving, and postprocessing.

ANYfem owns the application layer across the focused ecosystem packages:

- [**ANYsolver**](https://github.com/audunarn/ANYsolver) — the headless FE
  solver (6 DOF per node, SI units, linear through nonlinear and transient).
- [**ANYmaterial**](https://github.com/audunarn/ANYmaterial) — validated,
  serializable isotropic and orthotropic material specifications.
- [**ANYgeometry**](https://github.com/audunarn/ANYgeometry) — the shared
  persistent-ID geometry model, curves, surfaces, topology and serialization.
- [**ANYmesher**](https://github.com/audunarn/ANYmesh) — neutral meshes,
  mapped decomposition and meshing, refinement and coupling records.
- [**ANYfileio**](https://github.com/audunarn/ANYio) — SESAM and CalculiX
  parsing/writing.
- [**ANY3dView**](https://github.com/audunarn/ANY3dView) — shared viewer
  contracts and the optional ModernGL viewport.
- [**ANYtk3D**](https://github.com/audunarn/ANYtk3D) — the software Tk viewport.

The dependency direction is one-way. ANYfem never imports ANYstructure.

### Coordinated S3 activation-candidate graph

| Distribution | Qualified version | Role |
| --- | ---: | --- |
| ANYmaterial | 0.1.1 | material definitions and nonlinear curves |
| ANYgeometry | 0.2.4 | exact frozen features, topology and structural ownership |
| ANYmesher | 0.3.0 | qualified S3 admission, owner normals, repair, global block layouts and interface diagnostics |
| ANYfileio | 0.2.1 | neutral and solver-file semantics |
| ANY3dView | 0.5.3 | viewer contract and ModernGL renderer |
| ANYtk3D | 0.5.3 | compatible software renderer |
| ANYsolver | 0.4.0 | qualified Q4/S3 analyses, outcomes, progress, reactions and quantities |
| ANYfem | 0.4.0 | topology-aware shell policy, workflow, persistence, jobs and results |
| ANYbuckling | 0.1.1 | compatible independent buckling adapter |
| ANYstructure | 6.3.1 | downstream application consumer |

These exact versions form the frozen default-activation candidate; they are
not release-qualified until both mixed-mesh cycles pass. Historical
ANYfem project formats 1--7 remain readable through the v8 migration path;
that file compatibility does not qualify older installed package generations.
ANYtimeseries remains independent of this release graph.

## Status

The complete application workflow is implemented: model geometry, cut it into
mappable pieces, mesh it, apply loads and supports, solve, and inspect the
results.

| Layer | What works today |
| --- | --- |
| `anyfem.geometry` | Temporary compatibility imports over ANYgeometry's shared `GeometryModel`, `EntityRef`, curves and topology; new code may import the same owner objects directly from `anygeometry` |
| `anyfem.geometry.operations` | Compatibility imports: surface evaluation comes from ANYgeometry; historical axis/fraction splits, strips, butterfly decomposition and mappability checks come from ANYmesher |
| `anyfem.mesh` | Compatibility surface over ANYmesher: edge seeding, Coons mapped meshing, conformal node sharing, refinement, shell/beam topology and couplings |
| `anyfem.model` | Stable-ID materials/sections/assignments, topology-aware Q4/S3 formulation policy, reusable geometry/mesh regions, named Cartesian/cylindrical coordinates, SI-backed unit profiles, six-component supports/loads, masses, combinations and imperfections |
| `anyfem.solve` | FEModel construction; linear static, modal, buckling, nonlinear static, arc-length, transient, rigid-sphere impact and the packaged capacity workflow; recovery and resource policy |
| `anyfem.post` | Displacement and stress fields, probes, along-line extraction, envelopes, deformed shapes, mode and time-step browsing, history series, Markdown and CSV export |
| `anyfem.io` | v8 project intent with explicit shell-formulation identity, schema-v4 feature geometry, portable SESAM embedding, atomic HDF5 mesh/result sidecars, validation, locks and autosave recovery |
| `anyfem.commands` | Atomic document transactions and command-stack undo/redo, including feature edit/suppress/regenerate |
| `anyfem.migration` | Reads ANYstructure's saved FE state without importing it; measures the migration gate |
| `anyfem.selection` | Geometry and mesh domains; point/edge/plate/node/element/element-face filters; replace/add/toggle/remove and ordered picks |
| `anyfem.ui` | Persistent Tree + Viewport + Details workspace, commercial mouse profile, contextual selection strip, FIFO jobs and lazy results |

Verified, with dated evidence under `reports/`:

| Check | Reference | Result |
| --- | --- | --- |
| Cantilever tip deflection | `PL³/3EI` | 0.01% |
| Plate under pressure | Timoshenko `0.00406 q a⁴/D` | 0.13% |
| Cantilever natural frequency | `1.875² / 2πL² · √(EI/ρA)` | 0.17% |
| Euler strut buckling | `π²EI/L²` | 0.03% |
| Suddenly applied load | undamped peak = 2 × static | 2.04 |
| Plate bending stress | `6M/t²`, `M = 0.0479 q a²` | 0.3% |
| Beam axial stress | `P/A` | exact |
| Eccentric stiffener neutral axis | transformed section | 0.20% |
| Q8 plate on 16 elements | converged FE answer | 1.07% |
| Graded element size at a zone | the size asked for | 3.5% |
| Quarter model with symmetry | the same plate solved in full | exact |
| Result written as FRD and read back | the solution it came from | exact |

Documentation:

- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — layers, invariants, data flow
- [`docs/QUALITY_CONTROL.md`](docs/QUALITY_CONTROL.md) — how to verify, and what
  the evidence does not claim

## Verification and parity

```bash
python -m anyfem.verification      # twenty-one cases, dated evidence
python -m anyfem.parity            # ANYstructure capability ledger
python -m anyfem.migration         # the migration gate, all five criteria
```

All three write reports under `reports/`, and are installed as `anyfem-verify`,
`anyfem-parity` and `anyfem-gate`.

The parity ledger is the gate for replacing ANYstructure's FE application
workflow. It tracks 49 capabilities across that GUI's 176 options
and reports **92% covered with nothing blocking**: every capability is either
covered or named in `OUT_OF_SCOPE` with a reason. A capability counts as
covered only when ANYfem can do the same job.

The ledger is one of five migration criteria, and the **gate is still closed**.
Three are met — ledger clear, `save_runtime_fem_state` files importable, the
headless API builds every model type. Two are not, and both need the same
thing: a fixed set of models run through ANYstructure with the results
recorded, so ANYfem's numbers and timings have something to be compared
against. `gate_report` reports those unmet with the reason rather than passing
them by default.

## Install and run

Until the compatible ecosystem packages are published, bootstrap the complete
latest-only graph with one command.  The arguments are kept in dependency
order so the same line is also printed by `run_gui.py` when metadata is stale:

```powershell
python -m pip install --upgrade -e "C:\Github\ANYmaterial" -e "C:\Github\ANYgeometry[planar]" -e "C:\Github\ANYmesh" -e "C:\Github\ANYio[semantics]" -e "C:\Github\ANY3dView[gpu]" -e "C:\Github\ANYtk3D" -e "C:\Github\ANYsolver" -e "C:\Github\ANYfem[gui]"
```

The launcher uses the sibling ANY3dView and ANYtk3D 0.5 source trees directly,
so the application can switch between their coordinated GPU and software
implementations without mixing installed generations.

The ANYmesher source is selected the same way. The shared `ANYmesh` checkout
is used whenever it declares version 0.3.0 or newer; newer compatible releases
are not rejected by an obsolete upper version bound. `ANYMESHER_SOURCE` can
name another compatible checkout. The historical `ANYMESHER_030_SOURCE`
variable remains accepted for one compatibility cycle. Installed pre-0.3
metadata is rejected before the GUI imports.

```powershell
python -m anyfem.ui.app
```

From a source checkout, `python run_gui.py` first checks both imported module
origins and installed distribution versions, then launches the application.
The version check enforces minimum API generations and accepts newer releases.
It fails before importing Tk when source and editable metadata are missing,
older than those floors, or resolve from a different checkout.

The default workspace keeps the model tree on the left, the retained 3D view in
the centre, and contextual Details/tasks on the right. Geometry/features,
materials/sections, coordinate systems, regions, meshes, load cases, analyses,
jobs and results remain visible throughout the workflow. Common controls are
shown first; solver-specific controls live under **Advanced**.

The toolbar's **Renderer** selector switches live between **Automatic**,
**GPU**, and **Tk**. Automatic prefers ModernGL and falls back to Tk with the
reason available in the status bar. A failed explicit switch leaves the current
renderer, camera, scene, selection, clipping and construction preview intact.

The commercial interaction profile is implemented by both viewer backends and
enabled by ANYfem:

- LMB click selects; LMB drag uses box or lasso selection.
- MMB drag pans, RMB drag orbits, the wheel zooms, and RMB click opens context actions.
- No modifier replaces; Shift adds; Ctrl toggles; Alt removes.
- Left-to-right windows enclose; right-to-left windows cross.
- `Esc` cancels, `Enter` applies, `Ctrl+A` selects matching entities, `F` frames
  selection, and `Delete` invokes the contextual delete command.

The always-visible selection strip chooses geometry/mesh domain, entity filter,
Single/Box/Lasso tool, Visible/Through depth and set operation. Hover
prehighlight, repeated-click candidate cycling, geometry/mesh multi-owner
picks, named regions, hide/isolate and tree synchronization use the same
selection state. Every committed edit is one undo item.

The **Loads & BC** viewport toggle works in geometry, mesh and result views.
Pressure, force, moment, translational/rotational restraint, mass and
acceleration each have a directional symbol and color listed in the tab's
**Viewport key**. On dense assignments the display samples at most 256 symbols
per category, spread across the complete model; this limits canvas work only
and never removes loads or boundary conditions from the analysis.

## Modelling paradigm

Bottom-up and point-driven: place points, connect them with lines, bound plates
with line loops, and carry beams on lines.

```python
from anyfem import Project, pinned, solve_linear_static, steel

project = Project(name="plate")
project.add_material(steel("S355", thickness=0.010))
project.add_plate_section("deck", thickness=0.010, material="S355")

geometry = project.geometry
points = geometry.add_points([(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)])
edges = geometry.add_polyline(points, close=True)
face = geometry.add_face(edges)

project.assign_plate(face, "deck")
for edge in edges:
    project.add_support(pinned(project.edge(edge)))
project.load_case().add_pressure(project.face(face), 10_000.0)

solution = solve_linear_static(project, target_size=1 / 16)
print(solution.summary())
```

Curved geometry comes from arcs, and surfaces from sweeping:

```python
import numpy as np

r = 2.0
start = geometry.add_point(r, 0, 0)
via = geometry.add_point(r / np.sqrt(2), r / np.sqrt(2), 0)
end = geometry.add_point(0, r, 0)
arc = geometry.add_arc(start, via, end)

geometry.extrude([arc], (0, 0, 3.0))                     # a cylindrical panel
geometry.revolve([line], (0, 0, 0), (0, 0, 1), 2*np.pi)  # a closed cylinder
```

The Geometry Details page also exposes ANYgeometry's owner operations through
the undoable command stack: translated copy, mirror, linear/circular pattern,
edge/face orientation reversal, and typed position/distance/angle/length/area/
perimeter/normal measurements. Structural generators are editable feature
records with semantic outputs and groups:

```python
from anyfem import DocumentSession, commands

feature = commands.AddStiffenedPanel(
    length=12.0,
    width=6.0,
    longitudinal_spacing=0.6,
    transverse_spacing=3.0,
    semantic_group="deck",
)
session = DocumentSession(project)
session.execute(feature)  # one atomic undo item
```

Interactive point, line and polyline construction uses a session-owned
`Workplane` resolved from Global or a named Cartesian/cylindrical coordinate
system. The Geometry Details controls expose plane offset, unit-aware grid and
snap tolerance, grid/axis snapping, and endpoint/midpoint/projected-edge
intersection snapping. LMB collects a working preview, Enter/Apply commits one
undo item, and Escape/Cancel leaves the live model unchanged. The same
deterministic, Tk-free contracts are available to scripts:

```python
from anyfem import ConstructionTask, SnapEngine, Workplane

workplane = Workplane("global", grid_spacing=0.25, snap_tolerance=0.05)
frame = workplane.resolve(project.coordinate_systems)
snap = SnapEngine().snap((0.49, 0.51, 0.0), workplane, frame)

task = ConstructionTask("line")
task.add(snap)
task.add((1.0, 0.5, 0.0))
task.apply(session.execute)  # no project mutation occurred before this line
```

There are two intentionally separate circular-opening tools. **Neutral trim
hole** keeps one structural face and adds an inner ANYgeometry boundary;
**butterfly mesh decomposition** replaces the plate with mapped patches for
element control. The labels and status messages always identify which one is
being applied.

General topology belongs to ANYgeometry; decomposition whose purpose is to
produce mapped quadrilateral regions belongs to ANYmesher:

```python
from anygeometry import strip_face
from anymesher.decomposition import punch_circular_hole, triangle_to_quads

# A plate with a hole becomes the four-patch butterfly decomposition.
patches, hole_arcs = punch_circular_hole(geometry, face, (2, 1.5, 0), 0.6)

# Split a plate into strips; the dividing lines are shared, so giving one a
# beam section makes a stiffener on that line.
strips, dividers = strip_face(geometry, face, axis=0, count=3)

# A three-sided region becomes three real quads, never a degenerate one.
faces = triangle_to_quads(geometry, three_edges)
```

ANYgeometry records the intended surface explicitly: `Plane`, `Cylinder`,
`Cone`, `RuledSurface`, or `CoonsSurface`. Coons patches provide mapped
transfinite interpolation where that is the selected surface; cylinders and
cones use their analytical surfaces rather than a faceted approximation.

Project format v7 stores editable intent, exact structural-ownership intent,
compatibility-recovery diagnostics, and an artifact index; embedded
ANYgeometry documents use canonical schema v4. Meshes and
results are immutable, checksummed HDF5 sidecars under
`model.anyfem-data/meshes` and `model.anyfem-data/results`. Results are read
frame-by-frame and unavailable quantities are never manufactured. Imported
SESAM source semantics are embedded with the mesh, so reopening does not depend
on the original file. Legacy ANYfem formats 1--6 and ANYgeometry schemas v1--v3
remain readable and are migrated deterministically on the next save. The
qualified release therefore requires ANYgeometry 0.2.4 or newer within the
0.2 generation; older readers must reject newer schema-v4 materializations
rather than guessing at their feature or structural identity.

### Symmetry

Half and quarter models, with the condition checked rather than assumed:

```python
project.add_symmetry(project.edge(cut_edge), "x")               # symmetry
project.add_symmetry(project.edge(other), "y", antisymmetric=True)
```

A symmetry plane restrains the normal translation and the two in-plane
rotations, leaving the rotation about the normal free. A quarter plate built
this way matches the same plate solved in full to nine figures.

Two things are refused rather than approximated, because both fail silently.
**A plane not normal to a global axis**: the solver applies boundary conditions
in global axes with no nodal transformation, so a tilted plane could only be
approximated, and a half model with slightly wrong symmetry still solves and
still looks reasonable. **An entity that does not lie in the plane**: a symmetry
condition on an edge that crosses the plane restrains the wrong degrees of
freedom everywhere it touches.

### Element order and local refinement

Both are project settings, saved with the model:

```python
from anyfem.mesh import refine_around, refine_at

project.set_element_order("quadratic")   # Q8 shells and 3-node beams
project.add_refinement(refine_around(project.point(corner), size=0.02, radius=0.1))
project.add_refinement(refine_at((2.0, 1.5, 0.0), size=0.02, radius=0.1))
```

A zone binds to a point, line or plate and asks for a smaller element size
within a radius, growing back to the global target outside it. Seeding
integrates the resulting size field along each edge rather than dividing length
by target, and node placement follows the same field, so counts and positions
cannot disagree.

**Q8 is a large accuracy win**: 1.07% on a 16-element plate, where Q4 needs 256
elements for the same 2%. Mid-side nodes sit on the curve, so a Q8 on a cylinder
stays exactly on the cylinder. The 3-node beam is a different matter and worth
being plain about — ANYsolver's 2-node Timoshenko beam is already *exact* for a
tip-loaded cantilever, which a parabola cannot be, so B3 exists here so a
stiffener can share the mid-side nodes of a Q8 shell edge, not because it is
more accurate. A quadratic beam on a curved line is refused: the solver's B3 is
straight-sided.

One limit is inherent to mapped meshing rather than to this implementation. A
Coons patch interior is the blend of its four sides, so **a zone in the middle
of a plate refines nothing** — there is no interior degree of freedom to refine.
Refining locally means decomposing locally, which is the same answer this mesher
gives to every other awkward region:

```python
from anyfem.commands import CommandStack, RefineForImpact

# Cuts the struck plate to bracket the contact patch, then refines it.
CommandStack(project).run(
    RefineForImpact(collision=collision, target_size=0.125, elements_per_radius=4)
)
```

Every impact result reports `info["contact_resolution"]` — the element size at
the contact point and how many elements lie across the sphere radius — because a
contact patch spread over one element gives a peak force that belongs to the
mesh rather than to the structure.

### Automatic structural connectivity

Meshing works on a temporary geometry clone. Crossing plates are imprinted into
conformal shell edges; independently drawn coincident beam ends share a mesh
node; a beam ending on or crossing another straight beam splits the receiving
span; and a beam meeting a shell away from an existing shell node receives an
exact interpolation MPC. The editable model geometry and its IDs are unchanged.
The Mesh details report how many plate intersections and beam connections were
created. Remesh after changing any of these intersections.

### Editable sketches on plates

In Geometry → Guiding geometry, select one flat Model Geometry Plate and choose
**Start sketch on selected plate**. The plate becomes a face-aligned grid.
Clicks may be inside or outside the plate; clicks near its vertices or edges
receive persistent coincidence constraints. Any two numbered sketch points can
receive a distance or coincidence constraint. Apply creates one editable
`geometry.sketch.extrude` feature and extrudes its profile along the plate
normal. Right-click the sketch feature in the model tree and choose **Edit** to
reopen its points, constraints, closed/open state, and extrusion distance.

### Stiffener eccentricity

A stiffener with its neutral axis in the plate midsurface is a different
structure from one standing proud of the plating, so eccentricity is a section
property rather than an afterthought:

```python
from anyfem.model import BeamSection

project.add_beam_section(BeamSection(
    name="stiffener", profile="T-bar", material="S355",
    web_height=0.20, web_thickness=0.010,
    flange_width=0.10, flange_thickness=0.012,
    web_direction=(0, 0, 1),
    eccentricity=0.104,          # neutral axis offset along the plate normal
))
project.assign_beam(dividers[0], "stiffener")
```

With zero eccentricity the beam shares the plating nodes. With an offset the
mesher generates its own nodes along the plate normal and ties every station
back to the plating with the solver's MPC, so the section picks up its transfer
terms. For the verification strip that is a factor of 2.4 in stiffness — not a
detail. Supports on the plate edge stay on the plating: a prescribed degree of
freedom on a slaved node is a contradiction, and it is avoided rather than
discovered at solve time.

## Scripting

The GUI is a thin layer over the headless core. Buttons build the same command
objects a script does, so anything the application can do is scriptable — and
anything scripted is undoable.

```python
from anyfem import Project, steel
from anyfem.commands import AddPlate, AddPoint, AssignPlate, CommandStack

project = Project()
project.add_material(steel("S355", 0.010))
project.add_plate_section("deck", thickness=0.010, material="S355")

stack = CommandStack(project)
points = [stack.run(AddPoint(x, y)) for x, y in ((0, 0), (2, 0), (2, 1), (0, 1))]
face = stack.run(AddPlate(points))
stack.run(AssignPlate(face, "deck"))

stack.undo()          # the plate goes away
stack.redo()          # and comes back with the same IDs
```

Undo restores IDs exactly, which is what keeps loads and sections pointing at
the things the user attached them to.

## Loads

Loads attach to geometry and live in named cases, which combine with factors:

```python
from anyfem.model import Mass, plate_mode, prescribed

dead = project.load_case("dead")
dead.add_pressure(project.face(face), 10_000.0)     # follows the plate normal
dead.set_gravity()                                   # consistent inertial load

live = project.load_case("live")
live.add_surface_traction(project.face(face), (500, 0, 0))   # fixed direction
live.set_follower_pressure(True)     # pressures act on the deformed shape

project.add_support(prescribed(project.edge(edge), uz=0.005))  # push, not hold
project.add_mass(Mass(ref=project.face(face), value=1_000.0))
project.add_imperfection(plate_mode(project.face(face), amplitude=0.004))

project.add_combination("ULS", {"dead": 1.2, "live": 1.5})
solution = solve_linear_static(project, target_size=0.25, combination="ULS")
```

An imperfection moves the *stress-free* geometry — the shape the structure
would have with no load on it — not the result. Follower pressure is a property
of a case rather than of one load, because that is how the solver models it;
combining a follower case with a dead one is refused rather than quietly
resolved.

## Analyses

```python
from anyfem import (solve_modal, solve_buckling, solve_nonlinear_static,
                    solve_arc_length, solve_transient, solve_capacity,
                    solve_impact, eigenmode_imperfection, steel)
from anyfem.model import fracture

modal = solve_modal(project, target_size=0.1, num_modes=6)
print(modal.frequencies)                 # Hz, one per mode

buckling = solve_buckling(project, target_size=0.1, num_modes=3)
print(buckling.critical_factor)          # multiplies its reference load case

nonlinear = solve_nonlinear_static(project, target_size=0.1, num_steps=10)
print(nonlinear.history()["load_factor"])

# Material nonlinearity is asked for, not assumed: without a hardening curve a
# nonlinear solve is geometrically nonlinear and elastically linear, which is a
# different analysis. Plasticity is the layered-shell path — beams stay elastic.
project.add_material(steel("S355", 0.008, nonlinear=True))
eroded = solve_nonlinear_static(project, target_size=0.1, num_steps=10,
                                fracture=fracture(0.05))
print(eroded.deleted_elements)           # element erosion, if any triggered

# DNV properties depend on product thickness. This factory gives each
# grade/thickness combination a deterministic material identity, so several
# S355 thicknesses safely coexist and identical specifications are reused.
from anyfem import dnv_steel_material
dnv_10 = project.add_material(dnv_steel_material("S355", 0.010))
dnv_20 = project.add_material(dnv_steel_material("S355", 0.020))
project.add_plate_section("deck 10", 0.010, dnv_10.name)
project.add_plate_section("deck 20", 0.020, dnv_20.name)

# The GUI's Sections page performs this automatically for new plate sections.
# Clear "Auto DNV nonlinear material from thickness" to choose a custom
# ANYmaterial specification instead.

# The same page shows the complete selected material law (elastic constants,
# density, yield, DNV thickness and sampled flow stresses) and a section-usage
# table listing the Model Plates/Lines assigned to every definition. Assigning
# a section commits the displayed definition before applying its scope.

# A buckling-shaped imperfection, then trace past the limit point.
imperfection = eigenmode_imperfection(buckling, 1, amplitude=0.004)
path = solve_arc_length(project, target_size=0.1, imperfection=imperfection)
print(path.peak_load_factor)

# The whole assessment in one call: static, prestress, buckling, imperfection,
# collapse. The solver packages the sequence, so ANYfem does not re-chain it.
# The amplitude is the setting that matters: too small and it is a perfect-shape
# analysis, too large and the return mapping stops converging.
capacity = solve_capacity(project, target_size=0.1, imperfection_amplitude=span / 500)
print(capacity.summary())            # capacity and elastic critical, separately
print(capacity.capacity_ratio)       # <1 imperfection sensitive, >1 post-buckling reserve

transient = solve_transient(project, target_size=0.1, dt=2e-4, t_end=0.02)
print(transient.node_history(transient.peak_node, "uz"))

# A rigid sphere. The contact penalty and time step are computed, not guessed.
from anyfem.model import Collision
impact = solve_impact(project, target_size=0.1, collision=Collision(
    mass=200.0, radius=0.15, start=(0.5, 0.5, 0.6), direction=(0, 0, -1), speed=4.0
))
print(impact.summary(), impact.energy()["absorbed"])
```

An impact is the one analysis where the *settings* decide whether the answer
means anything. The contact penalty comes from the solver's own
recommendation, and the time step resolves the contact period `2π√(m/k)` into
twenty increments — a step near that period makes the contact iteration fail
outright rather than merely lose accuracy. The free-flight approach is skipped
(it is exact), and a sphere that would miss the structure is refused rather
than run to a clean-looking nothing.

Every result is made of **shapes** — a deflection, a mode, a time step — and
they all share one interface, so anything that can display a static result
displays a mode or a time instant unchanged. Long analyses take a `progress`
callable and can be cancelled from the GUI.

## Postprocessing

Everything is a **field** — one object the contour, the probe, the path plot,
the envelope and the report all agree on:

```python
from anyfem.post import (evaluate_field, probe, along_line, envelope,
                         report_markdown, write_report, field_to_csv)

stress = evaluate_field(solution, "von_mises")     # per element
print(stress.range(), stress.extreme())

reading = probe(solution, project.face(face))      # every component at once
print(reading.text())

path = along_line(solution, project.edge(edge), "uz")   # distance vs value
print(path.to_csv())

worst = envelope(transient, "von_mises")           # over every time step
print(worst.field.extreme(), worst.worst_shape())

write_report(solution, "report.md")
```

A displacement lives at nodes and a stress lives at elements; `Field` populates
exactly one of the two rather than pretending otherwise. An element that cannot
carry a component — torsion in a shell, membrane in a beam — is left out rather
than reported as zero.

A **history** is the same idea one dimension over: a transient, an impact and an
incremental solve all reduce to one `Series` type, so the plot never asks which
analysis produced it.

```python
from anyfem.post import history_series

for curve in history_series(transient):        # peak node by default
    print(curve.name, len(curve), curve.peak())

path = history_series(capacity)[-1]            # load factor vs displacement
```

The Results panel draws whichever series a result has, on a hand-written Tk
canvas — no matplotlib, so the GUI's dependency set stays Tk, the same choice
ANYtk3D makes for the 3D viewport.

Result values remain stored in SI. The Display controls can switch contours,
probes, summaries and histories instantly between SI (m/Pa) and engineering
units (mm/MPa), and offer Cool-warm, Viridis, Plasma, Turbo and Grayscale
colour maps with a matching viewport legend.

Retained sidecar fields can be exported directly to deterministic CSV without
loading every frame. Persisted node/element association tables supply the IDs;
if an artifact has no association, the export says `row_index` instead of
inventing an ID. The visible result view can also be captured as PNG or as an
asynchronously assembled GIF when Pillow is installed. Global tables and
histories remain CSV-only, so an unavailable spatial view is never replaced by
a screenshot of stale geometry. Section-plane controls appear automatically
when the active ANY3dView or ANYtk3D backend exposes the shared clipping API.

Immutable result sidecars also produce reproducible Markdown or standalone
HTML reports without loading every frame at once:

```python
from anyfem.io import ArtifactStore
from anyfem.post import result_report_context, write_result_report

store = ArtifactStore("deck.anyfem")
dataset = store.open_result(project.artifacts[job.result_artifact_id])
context = result_report_context(dataset, project=project, job=job, stale=False)
write_result_report(dataset, "deck-result.html", context=context)
```

The report records submission hashes and producer versions, typed quantity
descriptors, recovery/reduction/basis provenance, frame-wise extrema,
histories, retained-table previews and diagnostics. Requested missing or
malformed quantities stop export; they are never printed as zero.

## Files

```python
from anyfem.io import (save_project, load_project, import_sesam,
                       export_calculix_deck, import_calculix_results,
                       import_sesam_results)

save_project(project, "deck.anyfem")
project = load_project("deck.anyfem")          # identical IDs, solves the same

model = import_sesam("hull.FEM")               # nodes and elements, no geometry
solution = solve_linear_static(built=model.built(model.load_case()))

export_calculix_deck(built, "deck.inp")

# Solved elsewhere? Read the answers back onto the same model.
results = import_calculix_results("deck.frd")
imported = results.attach(built)               # matched by node ID
print(imported.summary())
stresses = import_sesam_results("hull.SIF")    # RVSTRESS shell stresses
```

An imported result goes through the same contours, probes, paths and reports as
a solved one — but it is not pretending to be one. A CalculiX FRD carries three
translation components and **no rotations**, so `imported.component("rx")`
raises rather than returning a plausible zero, and the raw array holds NaN there
so nothing that indexes it directly can mistake the gap either. Stresses arrive
per node, already averaged by the writing solver, and stay node-valued rather
than being passed off as an element recovery done here. Component names are the
file's own. A result file for a different mesh is refused with the overlap
rather than attached partially.

A project file stores the model, not its consequences — the mesh and results
are regenerable. **Entity IDs and their counters are part of the data**, because
loads reference geometry by ID and a round trip that renumbered anything would
silently re-target them.

An imported model has no geometry behind it and says so. Inventing plates and
lines under an imported mesh would be a guess dressed as a model; instead the
mesh gets the same *association* an ANYfem mesh has — elements grouped from the
file's own properties — which is all the analyses, fields, probes and reports
need.

SESAM **export** is deliberately refused: the solver states that semantic
export from an arbitrary model is outside its supported gate, and a file
written anyway would look authoritative without being so.

## Migrating from ANYstructure's FE GUI

ANYfem is meant to replace `fem_integration.py`, and `anyfem.migration` is the
part of that which needs code. It reads the old GUI's saved runs — **without
importing ANYstructure**, because a `save_runtime_fem_state` file is plain or
gzipped JSON, so it is read as data:

```python
from anyfem.migration import read_runtime_fem_state, gate_report

state = read_runtime_fem_state("run.anystructure.json.gz")
print(state.summary())
print(state.target_size, state.element_order)   # settings ANYfem can act on
print(state.buckling_factors)                   # what ANYstructure recorded
print(state.unmapped_options)                   # what ANYfem cannot honour yet
print(state.out_of_scope_options)               # and what it never will
```

Of the 176 options, **144 map** onto ANYfem settings, **24 are out of scope** by
decision and **8 are solver internals** ANYfem does not surface. Reporting which
is which is the point: a migration that silently dropped a setting would run a
different analysis from the one the file asked for, and would look like it
worked.

It restores **settings and recorded numbers, not the model** — the snapshot
describes a parametric panel, which is out of scope, and the stored
visualisation is a plotting grid rather than a mesh, so there is no topology in
the file to rebuild from.

```bash
anyfem-gate run.json      # all five migration criteria
```

**The gate is closed.** Three criteria are met: the ledger is clear, saved state
is importable, and the headless API builds every ANYstructure model type — a
stiffened panel and a cylinder, meshed and solved with no Tk loaded. Two are
not, and both need a fixed set of models run through ANYstructure with results
and timings recorded. The gate reports those unmet with the reason and never
passes them by default.

## Two properties worth knowing

**Attributes bind to geometry, not to the mesh.** Loads, supports and sections
reference persistent entity IDs; the mesh association map resolves them at
build time. Re-meshing never loses a load.

**Meshing method is an explicit project setting.** Open **Mesh** and choose the
prominent **Meshing method** control before Generate mesh:

- **Automatic (recommended)** first uses existing mapped blocks, then safe
  four-sided partitions on a
  detached working copy, and finally the quad-dominant native surface mesher.
  The **Automatic priority** control selects Balanced, Prefer mapped quads, or
  Prefer target size. Balanced caps partition-driven element growth at 25%.
- **Mapped quadrilateral** requires every plate region to have four logical
  sides and no holes. The task explains which face must be partitioned when
  this requirement is not met.
- **Unstructured / native** sends every plate through the native surface
  method and is the direct choice for holes and general boundaries.

The lower-level **Triangulator** control is shown only when an unstructured
route can be used; it is not the mapped/native method selector. Shared model
edges use one node sequence, so automatic mixed-method interfaces remain
conformal without coincident-node merging. After completion, Mesh details show
the requested method, the actual method used per face, native backend routes,
intersection preparation, and ANYmesher 0.3.0 global layout/quality measures
including scaled Jacobian, angle range, poor-element count, and optimization
provenance.

Use **Preview layout** to inspect the global blocks, shared interfaces, native
residual regions, estimated element count, and plan hash without changing the
model. **Commit partitions** records that exact preview as one undoable frozen
geometry feature; normal mesh generation keeps the partitions mesh-only. The
Automatic hard quality gate rejects a proposed structured layout before it can
publish elements when the configured scaled-Jacobian, aspect-ratio, angle, or
warpage limits are missed.

## Testing

```bash
python -m pytest tests -q
```

Real Tk tests are opt-in so an ordinary run never opens application windows.
Set `ANYFEM_RUN_GUI_TESTS=1` when deliberately exercising the desktop. They
still skip rather than fail when no display is available. The current test
count belongs in CI output, because extraction work moves tests to their owning
repositories.

Ordinary tests cap native numerical libraries at one worker so a development
run does not monopolize the workstation. Set `ANYFEM_TEST_THREADS` to an
intentional higher value when needed. The 50,000-owner / 250,000-node scale
qualification is not part of the normal regression run; enable it explicitly
with `ANYFEM_RUN_SCALE_GATES=1`, and add `ANYFEM_RUN_HARDWARE_GATES=1` only on
the representative workstation used for timing acceptance.

## License

GPL-3.0-or-later. See [LICENSE](LICENSE).
