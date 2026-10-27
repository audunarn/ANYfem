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
- [**ANYfileio**](https://github.com/audunarn/ANYfileIO) — SESAM and CalculiX
  parsing/writing.
- [**ANY3dView**](https://github.com/audunarn/ANY3dView) — shared viewer
  contracts and the optional ModernGL viewport.
- [**ANYtk3D**](https://github.com/audunarn/ANYtk3D) — the software Tk viewport.

The dependency direction is one-way. ANYfem never imports ANYstructure.

### Qualified latest-only release graph

| Distribution | Qualified version | Role |
| --- | ---: | --- |
| ANYmaterial | 0.2.0 | material definitions and nonlinear curves |
| ANYgeometry | 0.4.3 | exact frozen features, topology, structural ownership and automation contracts |
| ANYmesher | 0.5.0 | global structured/hybrid planning, native-v2 surface filling, repair and mixed-topology provenance |
| ANYfileio | 0.3.2 | neutral and solver-file semantics |
| ANY3dView | 0.5.5 | viewer contract and ModernGL renderer |
| ANYtk3D | 0.5.5 | compatible software renderer |
| ANYsolver | 0.4.6 | qualified shell/beam formulations, analyses, recovery and provenance |
| ANYfem | 0.4.1 | topology-aware shell defaults, migration, meshing, solving and results |
| ANYbuckling | 0.1.1 | compatible independent buckling adapter |
| ANYstructure | 6.3.1 | downstream application consumer |

Only these latest package generations are qualified together. Historical
ANYfem project formats 1--6 remain readable through the v7 migration path;
that file compatibility does not qualify older installed package generations.
ANYtimeseries remains independent of this release graph.

Current development requires ANYsolver >=0.4.7. CI and the solver installation
command below pin `bac246c2c55e82101efc7b99d0f3cf6fd3fea5a5`, which includes
the Q4 assumed-strain plastic route, unified Q4 elastic response and S3 von
Karman response. Version 0.4.7 alone does not identify these law updates.
The application integration regressions exercise public build, nonlinear solve,
committed recovery, cancellation and S3 admission; they do not extend the
qualified release graph above. Solver capability and material-domain refusals
remain authoritative; ANYfem does not substitute a legacy formulation.

The updated qualified Q4 plastic route refuses warped reference facets,
including facets warped by a buckling eigenmode imperfection in the capacity
workflow. That combination currently requires an explicit user choice of
`ShellFormulationPolicy(q4="legacy", s3=project.shell_formulation_policy.s3)`
on the project, or another solver-supported model. ANYfem preserves the
owner's refusal and does not switch formulations automatically.

## Status

The complete application workflow is implemented: model geometry, cut it into
mappable pieces, mesh it, apply loads and supports, solve, and inspect the
results.

| Layer | What works today |
| --- | --- |
| `anyfem.geometry` | Temporary compatibility imports over ANYgeometry's shared `GeometryModel`, `EntityRef`, curves and topology; new code may import the same owner objects directly from `anygeometry` |
| `anyfem.geometry.operations` | Compatibility imports: surface evaluation comes from ANYgeometry; historical axis/fraction splits, strips, butterfly decomposition and mappability checks come from ANYmesher |
| `anyfem.mesh` | Compatibility surface over ANYmesher: edge seeding, Coons mapped meshing, conformal node sharing, refinement, shell/beam topology and couplings |
| `anyfem.model` | Stable-ID materials/sections/assignments, reusable geometry/mesh regions, named Cartesian/cylindrical coordinates, SI-backed unit profiles, six-component supports/loads, masses, combinations and imperfections |
| `anyfem.solve` | FEModel construction; linear static, modal, buckling, nonlinear static, arc-length, transient, rigid-sphere impact and the packaged capacity workflow; recovery and resource policy |
| `anyfem.post` | Displacement and stress fields, probes, along-line extraction, envelopes, deformed shapes, mode and time-step browsing, history series, Markdown and CSV export |
| `anyfem.io` | v7 project intent, schema-v4 feature geometry, portable SESAM embedding, atomic HDF5 mesh/result sidecars, validation, locks and autosave recovery |
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

The parity ledger records the historical proposal to replace ANYstructure's FE
application workflow. That proposal was cancelled on 2026-10-02: ANYstructure
retains its current Tk-based FEM interface, and ANYfem remains separate. It tracks 49 capabilities across that GUI's 176 options
and reports **92% covered with nothing blocking**: every capability is either
covered or named in `OUT_OF_SCOPE` with a reason. A capability counts as
covered only when ANYfem can do the same job.

The ledger is one of five historical migration criteria; the recorded **gate is
still closed**, not a remaining product-roadmap requirement or an accepted migration.
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
python -m pip install --upgrade -e "C:\Github\ANYmaterial" -e "C:\Github\ANYgeometry[planar]" -e "C:\Github\ANYmesh" -e "C:\Github\ANYfileIO" -e "C:\Github\ANY3dView[gpu,qt]" -e "C:\Github\ANYtk3D" -e "C:\Github\ANYsolver" -e "C:\Github\ANYsolver\packages\results" -e "C:\Github\ANYpost" -e "C:\Github\ANYfem[qt,tk]"
```

The launcher uses the sibling ANY3dView and ANYtk3D 0.5 source trees directly,
so the application can switch between their coordinated GPU and software
implementations without mixing installed generations.
Use the `qt` extra for editable source development: it accepts the sibling viewer.
The `gui` extra instead pins its published viewer source for installed use.

The ANYmesher source is selected the same way. The shared `ANYmesh` checkout
is used whenever it declares version 0.5.0 or newer; newer compatible releases
are not rejected by an obsolete upper version bound. `ANYMESHER_SOURCE` can
name another compatible checkout. The historical `ANYMESHER_025_SOURCE`
variable remains accepted for one compatibility cycle. Launcher compatibility
uses the selected checkout's declared version because that is the code placed
on `sys.path`; the independent origin gate still rejects an installed package
that appears in place of the selected source tree.

```powershell
python -m anyfem.ui.qt
```

The default PySide6 workbench uses the shared workflow and the Qt host in the updated
ANY3dView checkout at candidate commit
[`d8b82686e477920cb485a7d32e6f04699b6f4d6f`](https://github.com/audunarn/ANY3dView/commit/d8b82686e477920cb485a7d32e6f04699b6f4d6f).
Install this exact source candidate before the ANYfem GUI extra:

```bash
python -m pip install "ANYsolver @ git+https://github.com/audunarn/ANYsolver.git@bac246c2c55e82101efc7b99d0f3cf6fd3fea5a5" "ANYgeometry[planar] @ git+https://github.com/audunarn/ANYgeometry.git@660430a5041a8315c523bdb542d64271f9445f7a" "ANYmesher @ git+https://github.com/audunarn/ANYmesh.git@b9814874c692bc675306c90176c03c5fbd05c5a0"
python -m pip install "ANY3dView[gpu,qt] @ git+https://github.com/audunarn/ANY3dView.git@d8b82686e477920cb485a7d32e6f04699b6f4d6f"
python -m pip install -e ".[gui]"
```

The published ANY3dView 0.5.5 wheel does not supply its Qt host; the `gui` extra
therefore pins the published viewer source commit above until an owner release
provides it. The GUI extra also pins the coordinated ANYmesher source because
the published 0.5.0 wheel lacks the workbench's meshing-control APIs.
The committed owner snapshots above match the candidate
CI inputs; they do not change the qualified release graph. In that environment,
launch with `anyfem`, `anyfem-qt`, `python -m anyfem.ui.qt`, or
`python run_gui.py` (also accepts `--qt`). Qt is the default frontend.
For the retained legacy frontend, install `.[tk]` and use `anyfem-tk`,
`python -m anyfem.ui.tk`, or `python run_gui.py --tk`. Conflicting frontend flags
are rejected. The View menu provides **Reset workspace layout**; saved custom
dock arrangements remain supported.
Current main requires ANYgeometry's batch-intersection API for mesh preparation.
ANYgeometry 0.4.5 now publishes that API. The coordinated pins above match merged
PR10 and its paired mesher/solver inputs; package version numbers alone do not
identify this candidate. The scientific matrix and full Qt acceptance remain
separate gates; merging the owner integration does not establish either passed.
See [the living conversion record](docs/QT_CONVERSION.md) for tested capabilities
and remaining acceptance work.

Whole-project component-parallel meshing defaults to Automatic. Select Off for
serial execution or Custom count in the Mesh workspace; headless jobs accept
`MeshExecutionPolicy`/`ANYFEM_MESH_WORKERS` overrides. Automatic retains serial
fallbacks and component/size eligibility gates. See [parallel meshing](docs/PARALLEL_MESHING.md) for eligible
routes, worker containment, measured evidence and remaining acceptance gates.

From a source checkout, `python run_gui.py` first checks both imported module
origins and installed distribution versions, then launches the application.
The version check enforces minimum API generations and accepts newer releases.
It fails before importing Tk when source and editable metadata are missing,
older than those floors, or resolve from a different checkout.

The Tk window is an adapter over `anyfem.application.WorkbenchController`.
Commands, document state, selection, jobs, meshes and retained results remain
headless, while scheduling, dialogs, clipboard, status and viewer creation use
small toolkit ports. The Qt candidate binds the same controller and `anyfem.presentation`
scene/result models, sharing engineering workflow services with the Tk adapter. See [`docs/FRONTEND_ARCHITECTURE.md`](docs/FRONTEND_ARCHITECTURE.md).

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

Multi-entity generators such as cylinders, cones, and stiffened panels appear
as one user-authored feature in the model tree and as one selectable surface in
the viewport. Their exact generated plates, lines, and points remain available
to geometry, meshing, and saved-project references, but are treated as internal
topology by default. Selecting the collapsed surface targets all of its plates,
and curved display tessellation is kept coarse while collapsed. Use **Explode
generated topology** on a feature when individual subplates or construction
entities must be inspected or selected; **Collapse generated topology** returns
to the lightweight representation. Explode/Collapse is a view operation and
does not modify geometry or stale a mesh.

The authored/composite/modifier classification and exact lineage ownership
come from ANYgeometry's public `feature_entity_owners()` contract. ANYfem owns
only the presentation state and grouped viewport rendering.

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

### Load series and envelopes: one load for a whole time history

A load case per time step, each holding its own copy of the loads, does not scale to hundreds of
steps. A **load series** is a load case that spans a time history: add loads to it once, and
every step derives them at its own time.

```python
stack.run(commands.AddLoadSeries("Seaway", duration=60, step=0.1,          # 600 steps, 0 ... 59.9 s
                                 parameters={"omega": 1.047, "swing": 0.4}))
stack.run(commands.AddConnectionLoad(
    "End section", moment=(0, 41.5e6, 0), case="Seaway",
    envelope={"by": "time", "start": 10, "stop": 50,                       # acts 10 s ... 50 s
              "expression": "min(1, tau / 4) * (0.3 + swing * sin(omega * t))"}))
```

The steps (`Seaway_000` ... ) are derived cases: they are what is solved, hold no loads, are never
edited and are written to the project file only as a regenerated list (the file stores the
series once, with its times and loads). The series itself is a definition and is not solvable;
asking for it says so and names its steps. In the model tree the steps hang under their series.

Every load type (`point`, `line`, `pressure`, `surface traction`, `wet pressure`, `connection
load`) may carry an **envelope** (the [ANYloads](https://github.com/audunarn/ANYloads) `Envelope`):

- a **window**, `start` and `stop`, both inclusive and either optional, counted in load case
  numbers (`by="number"`: from case 20 to case 300) or in case time (`by="time"`);
- a **factor** inside the window: 1 by default, an `expression` of `t` (case time), `number`,
  `k` (steps since the window opened), `tau` (seconds since it opened) and the case parameters,
  or `points` as `(time, factor)` pairs, linear between them and held at the ends, for a ramp in,
  a plateau and a ramp out.

**Solving many steps fast.** The stiffness is the same for every step; only the loads change, and the
response is linear in them. `solve_linear_static_many` (the *Batch linear static* analysis) therefore
does not solve every step. It takes each step's loads (a pressure per element, a force per node),
finds the few dominant step-to-step patterns of the *nodal forces* with a singular value
decomposition, solves one real load case per pattern through the solver (one factorization, every
solver check kept), and recombines every step from those solutions. Accuracy is judged on
displacements: two real steps (the largest load and the middle one) are also solved in full, and if
the recombined result disagrees by more than `max(10 * load_tolerance, 1e-8)` of the largest step's
displacement the next tier of patterns is added (reusing everything already solved) until they
agree. If compressing would not pay, or there are fewer than 8 steps, gravity, follower pressure or
prescribed non-zero motion, every step is solved in full. `load_tolerance` (default `1e-6`) is the
first tier's cut-off on the singular values; `0` or `None` always solves every step. What was done is
in `batch.info["load_compression"]` (shapes solved, solver calls, dropped load norm, check error).
On the 600-step barge seaway it solves 46 patterns in two calls; the two checks agree to 3e-6 and the
rotations match the full solve to 3e-9 of the peak. Measured on an otherwise idle machine, all 600
cases take 1933 s with ANYsolver at git 1a0ae901 (about 3 s of per-case load-vector guard work each)
and 142 s once those guards are amortised per lease; the compressed solve takes 185 s and 42 s
respectively. The gain grows with the cost of each case and shrinks for rough, high-rank loads.

Loads add, so a load that spans the whole record, a ramped one from case 20 and a pulse between
12 s and 14 s need no knowledge of each other. A load with no envelope acts in every step.
Gravity or acceleration on a series is constant for all steps. Files that use series or envelopes
are format 11, written only then. The older `add_load_case_series(template=..., builder=...)`
still creates real cases that carry copies of their loads, which is fine for a handful; they
are grouped under the series name in the tree.

### Connections: one moment on several lines

A bending moment is one action, not a pair of line loads. A *connection* ties
the selected points, lines or plates to a reference point (by default the
length-weighted geometric centre of the selection), and a load — force and
moment — is applied at that point. The reference point is a hidden solver node;
its displacement and rotation are read back with `solution.connection_motion(name)`.

```python
from anyfem import commands

stack = commands.CommandStack(project)
stack.run(commands.AddConnection(
    refs=(project.edge(deck_end), project.edge(bottom_end)),
    name="End section", kind="rigid", dofs="full"))            # or "distributing"
stack.run(commands.AddConnectionLoad("End section", moment=(0, 41.5e6, 0)))
solution = solve_linear_static(project)
print(solution.connection_motion("End section")["rotation"])   # section rotation
```

Three behaviours are offered, as in other finite-element packages:

| `kind` | Also known as | What it does |
|---|---|---|
| `rigid` | Nastran RBE2, Abaqus kinematic coupling, Ansys rigid remote point | The tied nodes move with the reference point as one rigid body. It **adds stiffness** and holds the section plane; use it for a stiff diaphragm, flange or crane base. |
| `distributing` | Nastran RBE3, Abaqus distributing coupling, Ansys deformable remote point | The reference point follows the weighted average motion of the nodes. It **adds no stiffness**; a load at the point is spread over the nodes, in proportion to their weight and distance from the point (a linear distribution across the selection, like a beam section). Use it to apply a resultant. |

| `spring` | Abaqus connector / Ansys spring or bushing at a remote point | A **flexible link**. The selection is tied (`tie="rigid"` or `"distributing"`) to an inner section point, and the loaded point is joined to it by six springs: `stiffness` is six numbers — ux, uy, uz in N/m and rx, ry, rz in N·m/rad — or two (translational, rotational). Use it for a compliant pedestal, bearing or support of known stiffness; a very large stiffness approaches the rigid case. A component with zero stiffness carries no load. `connection_motion` reports the section point (`section_displacement`, `section_rotation`) next to the loaded point; their difference is the spring deformation. |

```python
stack.run(commands.AddConnection(
    refs=(project.edge(deck_end), project.edge(bottom_end)), name="Pedestal",
    kind="spring", tie="distributing", stiffness=(5e8, 2e9)))
```

`dofs` chooses which motions are tied, in global axes: `full` (all six), `translations`
(a pin-like ball joint), `plane_section_x` / `_y` / `_z` (one translation, so a plane
section stays plane while the section may still warp and contract in its own plane),
or a list such as `"ux, uy, rz"`. The reference point is the `centroid` of the
selection (lines by length, plates by area, points evenly), the centre of its
`bounding_box`, or an explicit `point`. Distributing weights are the tributary length
or area of each node (`weighting="tributary"`, mesh independent) or one per node
(`"uniform"`). Moving the reference point off the centre makes a force there also
load the section with the moment of the offset.

A connection is stated by what it cannot do. A load component on a degree of freedom the
connection does not tie is refused, and a distributing connection over geometry that
cannot carry a moment (a moment about the axis of one straight line has no force couple)
refuses that moment rather than losing part of it. A rigid connection may not tie a
degree of freedom that a support also holds, and a node belongs to one rigid connection;
distributing connections may share nodes with supports and with each other. Constraints
are linear in the small-rotation sense, which is what the linear solvers use. Connection
files are format 10, written only when connections are used.

For the barge example, `floating_barge_section("distributing")` applies the end moment once
at the centre of the deck and bottom end lines; it reproduces the two-line-load result
(200.000 MPa peak, the same displacement field) because both put the same nodal forces on
the model. `floating_barge_section("rigid")` is the same moment on a rigid end section; on
the default 700 mm mesh it measures a 213.6 MPa peak and a 5.4 mm maximum displacement
(against 200.0 MPa and 18.7 mm), because holding the deck and bottom end lines plane stops
their local end deformation.

`floating_barge_large()` is the same barge at three times the length (15 m) with two bulkheads, three
access openings with rounded corners in each, a flat bar FB 120×10 on every opening edge and vertical
T-bar stiffeners on the bulkheads between the openings; `floating_barge_section` takes `length`,
`bulkheads`, `girders`, `openings` (`BulkheadOpening`, with a corner `radius`) and `bulkhead_stiffener`
to build other variants. Its default mesh target is 250 mm (about 15 500 elements; meshing takes a
couple of minutes) because the mesher's divisions per stiffener bay depend on the model size.

`floating_barge_seaway()` is the expanded example, built on a load series (below): the barge in a
regular head sea as a quasi-static time history, **600 steps over 60 s at 0.1 s** by default,
defined by **one** series `Seaway` that stores two loads once. A wave travels along X and presses
on the bottom and both sides (three wet surfaces, `rho * g * max(0, draft + amp * sin(omega * t -
kw * x) - z)`), fading in over the first 6 s (an envelope of points); the bending moment on a
**rigid** end connection acts only from 10 s to 50 s (a window by time), ramped in over 4 s, as
`mean + swing * sin(omega * t - lag)` of the calibration moment. Choose *Batch linear static* in
the Solve task to solve the steps `Seaway_000` ... `Seaway_599` with one factorization, and read
the reference point through the record with `batch.connection_history("End section X = 5 m")`.
It is also in the Examples menu.

### Wet surfaces, fluid pressure and load-case series

Plates that carry fluid pressure are marked as *wet surfaces*; a *wet pressure*
in a load case is applied to exactly those plates. The pressure is a magnitude
pressing into the plate, and the wet surface records which side the fluid is on,
so the solver's normal-signed pressure gets the right sign:

```python
from anyfem.model import WetSurface

# Fluid outside a closed hull: the side is decided per element from the point
# inside, so inconsistent element orientation cannot flip a load.
hull = project.add_wet_surface(WetSurface(
    ref=project.face(side), name="shell",
    fluid_side="away_from_point", interior_point=(0.0, 0.0, 2.0)))
# or fluid_side="positive_normal" / "negative_normal" for an explicit side

case = project.load_case("still water")
case.set_parameter("rho", 1025.0)
case.set_parameter("draft", 2.0)
case.add_wet_pressure(expression="rho * g * max(0, draft - z)")    # an equation

case.add_wet_pressure(mapping={face_a: 12_000.0, face_b: 8_500.0})  # per plate
case.add_wet_pressure(mapping={element_id: p}, mapping_key="element")  # per element
case.add_wet_pressure(value=30_000.0, surfaces=(tank.id,))         # one surface
```

An equation reads the element-centroid position `x, y, z` (metres), the case time
`t`, the case's named parameters and `g`, `pi`, `e`; it may use `abs sqrt exp log
sin cos tan tanh sinh cosh min max clip where sign` and nothing else, so a project
file cannot execute code. It is evaluated once per element at the centroid and
applied uniformly over the element, so a waterline inside an element is resolved
only to the mesh size; a mapping lists the wet plates or elements that carry
pressure, and the rest are dry. Naming a plate or element that is not on a wet
surface, a plate on two wet surfaces, an unknown name in an equation, or a
non-finite result is refused.

Pressure can also come from **your own Python code** or from a **CSV/Excel file**.
The definitions and their evaluation (equations, code, tables, trust, fluid side) are
owned by [ANYloads](https://github.com/audunarn/ANYloads), so ANYfem and ANYworkspaceAI
compute the same pressure for the same definition; ANYfem attaches them to plates,
meshes and the solver:

```python
# Code: receives the load case number and the element centre, returns pressure.
case.add_wet_pressure(code="""
def pressure(case, x, y, z):
    return 1025 * 9.81 * max(0.0, 2.0 + 0.1 * case - z)
""")
# Optional extra arguments: t (case time), name (case name), parameters (dict).
# code_vectorized=True calls it once with numpy arrays x, y, z instead.

# Table: rows of  case, x, y, z, pressure  (CSV, TSV, text or .xlsx).
from anyfem.model import PressureTable
table = PressureTable.from_file("loads.csv", key="time", interpolate_time=True)
case.add_wet_pressure(table=table)                     # uses the case's own time
project.add_load_cases_from_table("run", table)        # one case per time in the file
```

A table's case identifier (`key`) is `"number"` (the load case number), `"time"`
(the case time; with `interpolate_time` a time between two rows' times is
interpolated linearly) or `"name"`; with no key the file is one map for every case.
Pressure is mapped to each wet element's centre from the nearest table point
(`method="nearest"`) or inverse-distance weighted (`"idw"`, `neighbours=`); a
`max_distance` refuses an element with no point near it. Columns are found by
header (`case/time/number/name`, `x`, `y`, `z`, `pressure`) or named with
`columns={"x": "X [mm]", ...}`; without a header the order is `[key,] x, y, z,
pressure`. Convert units with `length_scale` (e.g. `0.001` for mm) and the
pressure `scale` (e.g. `1000` for kPa). The project stores the file's path and
SHA-256 and refuses a file that has changed; use an absolute path and keep the
file with the project. Excel needs `pip install ANYfem[excel]` (openpyxl).

The load case **number** is 1, 2, 3 ... in creation order (kept when a project is
saved), and a series step is numbered `step + 1`.

**Code is never run just because a project says so.** Opening a project, checking
it and drawing it only *parse* the code. It runs only when its SHA-256 is in your
per-user trust list (`platformdirs` config dir, `trusted_code.json`; override with
`ANYLOADS_TRUSTED_CODE_FILE`), which lives outside the project file. Code you type
into ANYfem (`add_wet_pressure(code=...)`, the Add wet pressure form, an edit) is
trusted automatically; code that arrives in a file is not, and starting a solve
shows it and asks (headless: `anyloads.trust_code(code)`). Trust is
per exact text, so any edit needs approving again. Trusting code means it runs
with your account's full access, like any script you run.

Many load cases, for example a quasi-static sequence, are generated from a
template and solved together, since the stiffness is the same at every step:

```python
template = project.load_case("wave template")
for name, value in {"rho": 1025.0, "draft": 2.0, "period": 8.0}.items():
    template.set_parameter(name, value)
template.add_wet_pressure(
    expression="rho * g * max(0, draft + 1.2*sin(2*pi*t/period) - z)")
project.add_load_case_series("wave", times=[0, 1, 2, 3, 4, 5, 6, 7],
                             template="wave template")
# or build each step's loads yourself:
project.add_load_case_series("tide", [0, 6, 12],
    builder=lambda case, t, step: case.add_wet_pressure(mapping=table[step]))

batch = solve_linear_static_many(project, load_cases=[f"wave_{i}" for i in range(8)])
```

Each step is an ordinary load case named `<series>_<step>` that carries its `time`,
parameters and `series`/`step`, so it can also be solved, combined and reported on
its own. Files that use any of this are written as project format 9; files that do
not stay format 8, and an older ANYfem refuses a format-9 file instead of silently
dropping its loads. In the Qt workbench the Loads task offers Add wet surface, Add
wet pressure and Add load case series, the model tree lists "Wet surfaces" like
sets (select to highlight, edit, delete), the viewport outlines wet plates and draws
the active case's pressure as arrows into the plate, and the Batch linear static
analysis solves the generated cases.

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

## Reading saved ANYstructure FEM state

The planned replacement/integration was cancelled on 2026-10-02. ANYstructure
retains its current Tk-based FEM interface; ANYfem remains a separate application.
The existing `anyfem.migration` compatibility utility remains available. It reads
ANYstructure's saved runs — **without
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

**The historical migration gate is closed.** It is retained as evidence/tooling,
not an active requirement to replace ANYstructure. Three criteria are met: the ledger is clear, saved state
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
intersection preparation, and ANYmesher 0.5.0 component strategy, alignment,
fallback, global-layout, and quality measures including scaled Jacobian, angle
range, poor-element count, and optimization provenance.

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

As of 2026-10-27, ANYfem source code on this development line is licensed under
the [GNU General Public License, version 3 only](https://github.com/audunarn/ANYfem/blob/main/LICENSE) (`GPL-3.0-only`).
Earlier published releases retain the license terms that applied when they were
published, including MPL-2.0 starting with version 0.4.0. This change does not
retroactively relicense those releases. Original project documentation
is licensed separately as described in [docs/LICENSE.md](https://github.com/audunarn/ANYfem/blob/main/docs/LICENSE.md), and
third-party components retain their own terms as recorded in
[THIRD_PARTY_NOTICES.md](https://github.com/audunarn/ANYfem/blob/main/THIRD_PARTY_NOTICES.md).

Ordinary commercial engineering use of the GPL version does not require payment.
The rights holder may negotiate a separate commercial license for specifically
cleared owned code; this repository grants no such alternative rights. See the
[commercial policy](docs/COMMERCIAL_LICENSING.md),
[historical transition](docs/LICENSING_TRANSITION.md),
[ownership/dependency audit](docs/LICENSING_AUDIT.md) and
[source/documentation scope](SOURCE_LICENSES.md). The
[sample term sheet](docs/drafts/COMMERCIAL_LICENSE_TERM_SHEET.md) is
**NON-OPERATIVE / LEGAL REVIEW REQUIRED**.

## Native archive implementation notes


Native archive preparation prefers ANYpost's bounded `metadata_summary()` on its
I/O worker. Small complete pool-free records remain prefetched; pooled records
use summaries. `ArchiveSession.cases()` preserves detailed original values and
loads them only when explicitly requested; `case_metadata(case_id)` loads one
record and `case_summaries()` serves compact listing. The demo case selector and
`--list` use summary APIs, avoiding implicit expansion of shared reaction ID lists.
Legacy/providers lacking these APIs keep their existing metadata fallback.
PreparedArchive's four positional arguments remain compatible. Oversized pooled
whole-detail queries raise the reader's `WorkingSetError`; no incomplete details
are substituted into the old API. Source certification/field rendering is unchanged.

Native archive sample maps read associations cooperatively on the loader without holding their state lock. Geometry/header proofs are cached at attachment. Counts/diagnostics for cached maps stay responsive; uncached synchronous selectors refuse while worker I/O is active, directing callers to `request_native_sample`. Only the latest generation installs a sample map.
