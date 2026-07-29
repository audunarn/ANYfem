# ANYfem

A shell and beam element Finite Element Method tool: geometry modelling,
meshing, loads and boundary conditions, solving, and postprocessing.

ANYfem owns the application layer between two existing packages:

- [**ANYsolver**](https://github.com/audunarn/ANYsolver) — the headless FE
  solver (6 DOF per node, SI units, linear through nonlinear and transient).
- [**ANYtk3D**](https://github.com/audunarn/ANYtk3D) — the Tkinter 3D viewport.

The dependency direction is one-way. ANYfem never imports ANYstructure.

## Status

**All nine phases complete.** The application runs: model geometry, cut it into
mappable pieces, mesh it, apply loads and supports, solve, and look at the
results.

| Layer | What works today |
| --- | --- |
| `anyfem.geometry` | Points, straight lines, circular arcs through a via point, faces from four edge chains with automatic corner detection, extrusion, revolution, deletion |
| `anyfem.geometry.operations` | Splitting lines and plates, corner override, three-sided regions to quads, butterfly hole, stiffener strips, mappability diagnostics |
| `anyfem.mesh` | Edge-seeding constraint solver, transfinite (Coons) mapped meshing, conformal node sharing, beams on lines |
| `anyfem.model` | DNV-RP-C208 steels, plate sections, typical beam profiles, supports and prescribed displacements, pressure (dead or follower), surface traction, line and point loads, acceleration, masses, load cases and combinations, geometric imperfections |
| `anyfem.solve` | FEModel construction; linear static, modal, buckling, nonlinear static, arc-length, transient and rigid-sphere impact |
| `anyfem.post` | Displacement and stress fields, probes, along-line extraction, envelopes, deformed shapes, mode and time-step browsing, Markdown and CSV export |
| `anyfem.io` | Versioned project files, SESAM import, CalculiX deck export |
| `anyfem.commands` | Command stack with undo/redo over every model change |
| `anyfem.selection` | Point/line/plate selection modes, shared by every view |
| `anyfem.ui` | 3D viewport with picking, loads and supports overlay, model tree, stage panels, threaded solve |

Verified against closed-form solutions:

| Check | Reference | Result |
| --- | --- | --- |
| Cantilever tip deflection | `PL³/3EI` | 0.01% |
| Plate under pressure | Timoshenko `0.00406 q a⁴/D` | 0.13% |
| Cantilever natural frequency | `1.875² / 2πL² · √(EI/ρA)` | 0.17% |
| Euler strut buckling | `π²EI/L²` | 0.03% |
| Suddenly applied load | undamped peak = 2 × static | 2.04 |
| Plate bending stress | `6M/t²`, `M = 0.0479 q a²` | 0.3% |
| Beam axial stress | `P/A` | exact |

Documentation:

- [`docs/IMPLEMENTATION_PLAN.md`](docs/IMPLEMENTATION_PLAN.md) — the phased
  plan and the decisions behind it
- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — layers, invariants, data flow
- [`docs/QUALITY_CONTROL.md`](docs/QUALITY_CONTROL.md) — how to verify, and what
  the evidence does not claim

## Verification and parity

```bash
python -m anyfem.verification      # eleven closed-form cases, dated evidence
python -m anyfem.parity            # ANYstructure coverage and migration gate
```

Both write reports under `reports/`, and both are installed as `anyfem-verify`
and `anyfem-parity`.

The parity ledger is the gate for eventually replacing ANYstructure's
`fem_integration.py`. It tracks 49 capabilities across that GUI's 176 options
and currently reports **71% covered, 11 entries open, gate not ready** — the
largest gaps being 8-node shells, graded mesh refinement, symmetry modelling
and result import from CalculiX and SESAM. A capability counts as covered only
when ANYfem can do the same job.

## Install and run

```bash
python -m pip install -e .[dev,gui]
```

```bash
python -m anyfem.ui.app
```

Model geometry on the **Geometry** tab, mesh on **Mesh**, attach materials and
sections on **Sections**, supports and loads on **Loads & BC**, then **Solve**
and **Results**. Click in the 3D view or the model tree to select; shift-click
extends. Everything is undoable.

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

Regions that cannot be mapped get cut up. That is core tooling, not a
convenience — it is the only route out of an awkward shape:

```python
from anyfem.geometry import punch_circular_hole, strip_face, triangle_to_quads

# A plate with a hole becomes the four-patch butterfly decomposition.
patches, hole_arcs = punch_circular_hole(geometry, face, (2, 1.5, 0), 0.6)

# Split a plate into strips; the dividing lines are shared, so giving one a
# beam section makes a stiffener on that line.
strips, dividers = strip_face(geometry, face, axis=0, count=3)

# A three-sided region becomes three real quads, never a degenerate one.
faces = triangle_to_quads(geometry, three_edges)
```

Faces are Coons patches blended from their four sides. Where the side curves
are straight, that reduces exactly to the ruled surface, so cylinders and cones
are represented exactly rather than faceted — one surface type covers flat
plates, ruled surfaces, cylinders and cones.

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
                    solve_arc_length, solve_transient, eigenmode_imperfection,
                    steel)
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

# A buckling-shaped imperfection, then trace past the limit point.
imperfection = eigenmode_imperfection(buckling, 1, amplitude=0.004)
path = solve_arc_length(project, target_size=0.1, imperfection=imperfection)
print(path.peak_load_factor)

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

## Files

```python
from anyfem.io import save_project, load_project, import_sesam, export_calculix_deck

save_project(project, "deck.anyfem")
project = load_project("deck.anyfem")          # identical IDs, solves the same

model = import_sesam("hull.FEM")               # nodes and elements, no geometry
solution = solve_linear_static(built=model.built(model.load_case()))

export_calculix_deck(built, "deck.inp")
```

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

## Two properties worth knowing

**Attributes bind to geometry, not to the mesh.** Loads, supports and sections
reference persistent entity IDs; the mesh association map resolves them at
build time. Re-meshing never loses a load.

**Meshing is mapped only.** No unstructured paver, by design: the solver's
qualified element envelope is full-integration Q4 and Q8, and the domain —
stiffened panels and cylinders — is naturally mapped. Faces therefore carry
four logical sides, and the edge-seeding solver propagates division counts
across shared edges so neighbouring faces are conformal by construction rather
than by coincident-node merging.

## Testing

```bash
python -m pytest tests -q
```

401 tests. GUI tests skip rather than fail when no display is available.

## License

GPL-3.0-or-later. See [LICENSE](LICENSE).
