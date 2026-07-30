# ANYfem implementation plan

ANYfem is a shell and beam finite-element application: geometry modelling,
meshing, loads, boundary conditions and materials (preprocessing), solving, and
postprocessing. It is built on two existing packages and owns everything
between them.

| Package | Role | Owned by |
| --- | --- | --- |
| [`ANYsolver`](https://github.com/audunarn/ANYsolver) | Headless FE solver. 6 DOF/node, SI units, shells and beams, linear/modal/buckling/nonlinear/transient. | External, consumed as-is |
| [`ANYtk3D`](https://github.com/audunarn/ANYtk3D) | Tkinter 3D viewport. Batched field plots, lighting, transparency, animation. | External, consumed as-is |
| **ANYfem** | Geometry kernel, mesher, attributes, application, postprocessing. | This repo |

The dependency direction is strictly one-way:

```text
ANYfem  ->  ANYsolver
        ->  ANYtk3D
```

**ANYfem must never import ANYstructure.** See "Relationship to ANYstructure"
below for why that matters and what it buys.

## Why a new geometry and mesh layer is needed

ANYsolver deliberately has no general mesher and no arbitrary CAD topology --
its own architecture document says so, and `mesh_gen.py` covers only
rectangular panels, stiffened panels and single beams. ANYstructure's
`fem_integration.py` is already a complete FE GUI on the same two packages, but
it binds loads and boundary conditions to *parametric patch identities* (panel
index, edge key) and hit-tests by projecting those known patches to screen.
That is precisely what caps its geometry scope.

ANYfem's central architectural change is that attributes bind to **persistent
topology entity IDs** (vertex / edge / face), and are transferred to mesh
entities at mesh time:

```text
Geometry (stable IDs) ──attributes──> Loads / BCs / Sections / Materials
       │                                          │
     mesher ──> Mesh + geometry association ──────┤
                                                  ▼
                                         anysolver.FEModel
```

Re-meshing must never lose a load. Everything else in the design follows from
getting this one property right.

## Scope boundary

ANYfem is a **shell and beam** application. Not solids, not tetrahedra, not a
NURBS kernel, not general contact. The solver's qualified envelope is thin flat
and cylindrical shell structure with beam stiffeners and girders; the
preprocessor must not be able to build models the solver cannot defend.

This constraint is a feature. It removes most of what makes a general-purpose
FE preprocessor hard.

## Locked decisions

### 1. Mapped / transfinite meshing only -- owned, no external mesher

No gmsh, no unstructured quad paver. The solver's qualified element envelope is
full-integration Q4 and Q8; an unstructured paver is months of work producing
elements the solver will not vouch for, and stiffened panels and cylinders --
the actual domain -- are naturally mapped.

This is not merely a mesher choice. It propagates upward and becomes a
**geometry-layer constraint**:

- A face carries four *logical sides*, each an ordered chain of edges. A
  six-edge face is still mappable if its edges group into four chains.
- Corners are auto-detected from boundary turn angle and are user-overridable.
- **Imprint and split tooling is core, not a convenience.** It is the user's
  only route out of a non-mappable face.
- Mappability diagnostics must point at *geometry* ("this face has five sides,
  split here"), never at the mesh ("meshing failed").

Two escape hatches are built in rather than left to the user:

- **Three-sided face -> three quads** via the centroid. Keeps everything
  quadrilateral; the solver never sees a degenerate element.
- **Plate with a hole -> automatic butterfly / O-grid decomposition.** Four
  mapped patches around the hole. Common enough in real work that requiring a
  manual imprint every time would be the application's worst rough edge.

### 2. Eventual replacement of the ANYstructure FE GUI, executed later

ANYfem aims to replace `fem_integration.py`, but that migration happens only
once ANYfem is complete and verified. Two things must therefore exist from the
start, or the replacement never happens:

**The seam.** ANYfem's headless API must be complete enough to build a full
stiffened panel -- geometry, stiffeners, loads, BCs, solve -- with no GUI
involved. If that holds, ANYstructure's parametric panels and cylinders later
become *just another caller*: geometry builders emitting ANYfem geometry plus
attributes. If it does not hold, the migration becomes a rewrite. It is a
testable property, so it is tested.

**The gate.** "Done and verified" is defined up front:

1. Every `fem_integration` analysis path reproduced in ANYfem, matching numbers
   within tolerance on a fixed set of ANYstructure models.
2. Feature-parity ledger green.
3. Existing `save_runtime_fem_state` files importable.
4. Headless API builds every ANYstructure model type without a GUI.
5. No performance regression on representative models.

Until then, every feature added to `fem_integration.py` is migration debt.

## Geometry modelling paradigm

Bottom-up, point driven:

- **Points** are placed by coordinate and are the only primitive the user
  positions directly.
- **Lines** connect points. Line shape is straight or a circular arc through a
  third point; a full circle is modelled as two arcs so every edge stays open.
- **Beams** are lines carrying a cross-section. Section shapes are the typical
  structural profiles -- flat bar, angle, L-bulb, T-bar -- resolved through the
  solver's own `StiffenerCrossSection.from_geometry` so section properties
  cannot diverge from the solver's conventions. That function implements
  exactly these four; anything else would silently fall through to a bare-web
  section, so ANYfem rejects it rather than passing it on.
- **Plates** are faces bounded by a closed loop of lines, organised into four
  sides.
- **Extrusion** sweeps a line or line chain along a vector, producing faces.
  **Revolution** sweeps about an axis, producing arcs and therefore curved
  faces (cylinders, cones).

A single surface type covers this: the **Coons patch** blended from the four
boundary sides. Where the side curves are straight, a Coons patch reduces
exactly to the ruled surface, so cylinders and cones bounded by arc-arc-line-line
loops are represented exactly rather than faceted. Planar plates, ruled
surfaces, cylinders and cones all fall out of one implementation.

## Layers

```text
anyfem/
  geometry/   Vertex / Edge / Face with persistent IDs, curve evaluation,
              Coons surfaces, primitives, extrude & revolve, imprint & split
  mesh/       edge seeding constraint solver -> conformal mapped meshing ->
              beam meshing; geometry<->mesh association map
  model/      materials, plate & beam sections, BCs, loads, cases and
              combinations -- all keyed to geometry entity IDs
  solve/      FEModel builder, analysis dispatch, worker thread, cancel,
              validate_production_model preflight
  post/       result database keyed (case, step/mode/time), field derivation,
              deformed shapes, envelopes, probes
  io/         project file, SESAM import/export, CalculiX deck export
  ui/         ANYtk3D viewport, model tree, property panel, stage tabs
  commands/   command pattern -> undo/redo, and the scripting API
```

Two rules that are cheap now and expensive later:

- **Headless-first.** Everything below `ui/` works without Tk. This is how the
  solver is built, it is the only way to test a preprocessor sanely, and it
  provides the scripting console for free.
- **Undo from the start.** Command pattern over the headless model. GUI actions
  emit commands; the scripting API calls the same commands.

## Picking

`anytk3d.canvas` already writes per-face `tags` onto its Tk canvas items, so
selection came almost free. Delivered in ANYtk3D 0.2.2:

- entities are tagged `ent_<kind><id>`; a click reads the tags of the topmost
  item under the cursor. Tk's own hit testing gives correct occlusion, because
  painter's-algorithm ordering means the topmost item *is* the nearest;
- a click is a press and release without a drag, so picking coexists with pan
  and orbit;
- highlighting is applied *while rendering* rather than by reconfiguring Tk
  items, so it survives the next redraw. Resolution from tags to faces is
  cached per scene and highlight generation;
- `tags` were plumbed through `add_line` as well, in both the depth-sorted and
  the overlay path, so lines are pickable too;
- picking is opt-in: with no callback set, the canvas behaves exactly as before.

Still to come: a numpy ray-cast against the compiled scene for marquee/box
select and vertex snapping.

## Phases

| Phase | Content |
| --- | --- |
| **0** | Scaffolding: pyproject, src layout, pytest, CI. |
| **1** | ✅ **Headless walking skeleton.** Points/lines/arcs/faces, extrude, seeding constraint solver, TFI mesher, sections, BCs and loads on geometry, FEModel build, `solve_linear`, results. Validated against analytical cases. |
| **2** | ✅ **Application.** Viewport, model tree, stage panels, threaded solve. Picking upstreamed to ANYtk3D. Selection modes. Command stack with undo/redo. |
| **3** | ✅ **Decomposition tooling.** Split, corner assignment, tri-to-3-quad, butterfly hole, revolve, stiffener strips, mappability diagnostics, and attribute transfer across edits. |
| **4** | ✅ **Loads and BC breadth.** Prescribed displacement, follower pressure, surface traction, line loads, acceleration, masses, load cases and combinations, geometric imperfections, and a loads/supports overlay in the viewport. |
| **5** | ✅ **Analyses.** Modal, buckling, nonlinear static, arc-length and transient, with live progress, cancel, eigenmode imperfections, and shape browsing in the results. |
| **6** | ✅ **Postprocessing depth.** Stress contouring, probes, along-line extraction, envelopes, animation and Markdown/CSV export, all over one field abstraction. |
| **7** | ✅ **Interop.** Versioned project files, SESAM import, CalculiX deck export, and a File menu. ANYstructure handoff belongs to the migration in Phase 8. |
| **9** | ✅ **Impact.** Rigid-sphere collision with automatic penalty and time step, contact history, momentum and energy accounting, damage and erosion, and the sphere drawn in the viewport. |
| **8** | ✅ **Close-out.** Verification suite writing dated evidence, the ANYstructure parity ledger and migration gate, packaging with CI, architecture and quality-control docs. |

### Phase 1 detail

The piece to get right first is the **edge-seeding constraint solver**, because
under mapped-only meshing it has no fallback.

Every edge carries a division count `n`. Each face requires
`sum(n over side A) == sum(n over side C)` and `sum(n over side B) ==
sum(n over side D)`. A shared edge has exactly one `n`, which is where
persistent topology pays off: conformity becomes automatic rather than a
tolerance-matching problem. But those constraints **propagate across the whole
assembly** -- seeding one edge forces its opposite, which forces the neighbour
face's edge across the shared boundary, and so on.

Approach:

1. Derive a desired `n` per edge from target element size, or take an explicit
   override.
2. Union-find the forced equalities (opposite sides that are both single edges)
   into equivalence classes; a class holds one `n`. Conflicting explicit
   overrides inside a class are reported, not silently resolved.
3. Bounded iterative repair for multi-edge chains: raise the coarser side to
   match the finer, distributing the deficit across unlocked edges in
   proportion to arc length.
4. Re-verify. Unresolvable configurations raise a diagnostic naming the face.

Because repair only ever refines, the process is monotone; pathological cyclic
topologies are caught by an iteration cap rather than looping forever.

### Phase 2 detail

The application is a thin layer over the headless core. Three things carry
most of the weight:

**Everything goes through the command stack.** GUI buttons build the same
command objects a script would. Geometry commands share one inverse: snapshot
the ID counters and the entity set, undo removes what appeared and rewinds the
counters, redo rewinds and re-runs the same deterministic operation. Entities
therefore return with *exactly* the IDs they had, which is what keeps loads and
sections pointing at the right things across an undo.

**The scene builder produces data, not draw calls.** Polygons, polylines and
markers, each tagged with the entity it came from; the viewport executes them.
So what gets drawn is testable without a display, and a picked tag always
resolves back to a geometry entity. Display tessellation calls the same
`coons_grid` the mesher uses, so a plate is never drawn as a different surface
from the one that gets meshed.

**Selection is shared state, not a widget property.** The tree and the viewport
both listen to one `Selection`, so they cannot disagree.

Three hazards worth recording, because they will recur:

- Tk objects must be finalized on the main thread. A destroyed window leaves
  `tkinter.Variable` instances alive until GC, and if that lands inside an
  allocation on the solver's worker thread, `Variable.__del__` calls into Tcl
  from the wrong thread and wedges the interpreter. `tests/conftest.py`
  collects after every test for exactly this reason.
- `<<TreeviewSelect>>` is delivered *asynchronously*, so a "currently syncing"
  flag is already cleared when it arrives. Both sides of the tree/selection
  round trip must be idempotent, or they bounce forever.
- Do not write `selection or Selection()`. `Selection` defines `__len__`, so an
  empty one was falsy and that quietly created a second, unshared selection.
  It now defines `__bool__` returning True to close that off.

### Phase 3 detail

Decomposition is what makes the mapped-only decision workable, so the tools
are held to one standard: **the model may only ever contain mappable faces.**
Every operation either produces four-sided faces or refuses.

**Cuts must lie on the surface, not merely span it.** A dividing edge is
fitted as a straight line first and a circular arc through the surface
midpoint second, each checked against the sampled Coons isoparametric curve. A
chord across a cylinder hoop would leave the surface and mesh to a different
shape, so it is rejected; the arc fit catches that case exactly. This covers
planar cuts, cylinder generators and cylinder hoops — the whole qualified
domain — and anything outside it is refused with an explanation rather than
approximated.

**A full revolve closes back onto its profile.** The last angular segment
reuses the original profile edges instead of making new ones. Without that, a
360° sweep produces a slit cylinder: a seam of coincident-but-separate points
that mesh into two unconnected edges.

**Attributes follow the geometry through an edit.** Splitting a line that
carries a load, or a plate that carries a pressure, must not throw the
attribute away — it is the same principle as re-meshing never losing a load,
applied to editing. The geometry model keeps a *replacement log* recording what
superseded what; the command layer reads it and re-targets sections, supports
and loads. Pressures and distributed line loads keep their intensity on every
piece, because they are per area and per length. A point load cannot be shared
out that way, so it follows the first replacement only rather than being
silently doubled.

**Undo is a topology snapshot, not a list of what was created.** Splitting an
edge rewrites the loops of every face that used it, and splitting a face
deletes the original, so a create-only inverse cannot express it. The snapshot
handles creation, deletion and in-place rewriting uniformly and costs only a
dictionary of references. Redo re-runs the same deterministic operation from
the same ID counters, so IDs are stable across the round trip.

The escape hatches promised above are built: a three-sided region becomes
three quads via edge midpoints and a centroid (never a degenerate quad with two
coincident corners), and a plate with a hole becomes the four-patch butterfly
in one call.

### Phase 4 detail

Most of this phase is *exposing* solver capability rather than building it. The
solver already constrains a DOF to any value, assembles a consistent inertial
load from an acceleration field, and applies stress-free imperfection offsets;
what was missing was a way to say so in terms of geometry.

Four things needed care:

**A prescribed displacement and a restraint are the same object.** The solver
constrains a DOF to a value; zero happens to mean "hold". So `prescribed()`
returns the same `Support` type, and the only difference is the numbers.

**Follower pressure belongs to a case, not to a load.** The solver assembles a
case in either the reference configuration or the current one — a single
pressure cannot opt in on its own. So it is a load-case setting, and a
combination that mixes follower and dead cases is refused rather than silently
resolved one way.

**Combinations must accumulate, not assign.** The solver's `add_pressure_load`
*overwrites* an element's pressure, so folding two cases that both load the
same plate would otherwise keep only the last one. Combinations are folded into
one equivalent case by summing into the load dictionaries directly. Acceleration
fields sum as fields, not as the forces they produce.

**Overlay geometry is deliberately untagged.** Load arrows and support symbols
draw over the model, and if they carried entity tags they would steal clicks
from the plates underneath. Untagged items are skipped by the pick walk, so the
geometry behind stays selectable — there is a test for exactly that.

A surface traction is distinct from a pressure: its direction is fixed in space
rather than following the plate normal, so on a sloping plate the two give
different answers. Both are available because both are real load types.

### Phase 5 detail

Every analysis produces one or more **shapes** -- a static deflection, a
vibration mode, a buckling mode, an instant of a transient. They all share
`ShapeView`, so the scene builder, the results panel and the animation display
a mode or a time step without knowing which analysis produced it. That one
abstraction is why adding four analyses needed almost no new display code.

Three things worth recording:

**Modal analysis defaults to a shift.** Without shift-invert, ARPACK routinely
fails to converge on ordinary shell models -- the drilling degrees of freedom
carry almost no mass, and the unshifted smallest-magnitude search stalls. A
1 m square plate at a 12x12 mesh simply returned *no modes*. Shipping that as
the default would be indefensible, so `solve_modal` passes `shift=0.0` and
documents why; `shift=None` restores the solver's unshifted path. Shift-invert
at zero also works free-free, where the stiffness matrix is singular.

**A buckling factor only means something with its reference case.** The factor
multiplies the load case that produced the prestress, so the result carries the
case name, and doubling the reference load halves every factor -- there is a
test for exactly that. The static solve and the stress-to-element-state
recovery are the solver's own (`recover_prestress_from_static_result`); this
layer only sequences them.

**Eigenmode imperfections are runtime objects, not project attributes.** A
buckling-shaped imperfection only means anything alongside the buckling run
that produced it, so it is built from a `BucklingSolution` and passed to the
nonlinear solve, rather than being stored on the project like a plate mode or
a member bow.

Progress reporting goes through the worker's queue: `solve_*` takes a
`progress` callable, the worker supplies one that only enqueues, and nothing
on the solver thread ever touches a widget.

### Phase 6 detail

Everything postprocessing does is expressed as a **`Field`**: a name, a unit,
and values keyed either by node or by element. The contour, the probe, the
path plot, the envelope, the CSV and the report all consume the same object,
so they cannot disagree about what "von Mises" means.

The node/element split is kept honest rather than smoothed over. A displacement
is known at nodes; a stress is recovered on elements, several Gauss points
deep. `Field` populates exactly one of the two, because pretending a stress
exists at a node would invent data. Where a nodal stress *is* needed -- for a
path plot along a line -- each node takes the average of the elements meeting
it there, and that is documented as a stated choice, not a recovered value.

Two related refusals:

- **An element that cannot carry a component is left out, not zeroed.** A shell
  has no torsional stress and a beam has no membrane stress; reporting zero
  would look like a real answer. Those elements land in `Field.missing`.
- **A single number for a whole plate can only honestly be an extreme**, so
  that is what a plate probe reports, and it says so.

Derived surface stresses (`top_xx`, `bottom_xx`, ...) come from membrane plus
or minus bending, which is a definition rather than a recovery, so it lives
here rather than being asked of the solver.

The report states what was asked for and what the solver returned. It does not
say whether the answer is acceptable -- that is an engineering judgement, and a
generated document should not appear to make it.

### Phase 7 detail

**Entity IDs are part of the saved data.** Loads, supports and sections
reference geometry by ID, so a save/load cycle that renumbered anything would
silently re-target them. The ID *counters* are saved too, so an entity created
after a reload cannot collide with one that was already there. A reloaded model
solves bit-identically; there is a test that asserts equality, not closeness.

The file stores the model, not its consequences. The mesh and the results are
regenerable, and storing them would make a saved file go stale the moment the
model changed.

**An imported model has no geometry, and says so.** A SESAM file carries nodes
and elements, not the plates and lines someone drew. Inventing a BRep to sit
underneath would be a guess dressed up as a model, so ANYfem does not:
`ImportedModel.has_geometry` is False and its stand-in project is empty.

What an imported model *does* get is the same **association** an ANYfem mesh
has -- elements grouped into addressable sets, taken from the file's own
element properties rather than from geometry inference. That is enough for
every analysis, field, probe, contour and report to work unchanged, because
they all go through the association rather than through the geometry. The only
supporting change was letting `Mesh.nodes_on` fall back to element
connectivity when there is no structured grid, and letting the analyses accept
an already-built model.

**SESAM export is refused, not approximated.** ANYsolver states that semantic
export from an arbitrary FEModel is outside its supported gate. A file written
anyway would look like an interchange file without being one, so ANYfem
declines and points at the alternatives. A generated CalculiX deck is likewise
labelled for what it is: a reproducibility handoff, not evidence, until it has
actually been run and compared.

### Phase 8 detail

Two artefacts turn judgements into measurements.

**The verification suite** (`anyfem-verify`) runs twenty-one cases, each stating
what it is checked against and how close it has to be, and writes
dated, environment-stamped evidence. It is explicit about what it does *not*
claim: it is not a general correctness claim, and it does not restate
ANYsolver's own qualification.

**The parity ledger** (`anyfem-parity`) is the migration gate from decision 2,
made computable. `fem_integration.py` exposes 176 options; the ledger tracks 49
capabilities across them and marks each covered, partial or missing. It
reports **92% coverage with nothing blocking**, and the migration gate still
answers **"not ready"** on evidence rather than on features — which is the honest answer, and the reason the ledger exists.
A capability counts as covered only when ANYfem can do the same job; grading on
a curve would defeat the purpose.

The gate reads the ledger rather than hard-coding a verdict — there is a test
that flips every entry to covered and asserts the gate then clears. Three
entries are excluded as ANYstructure's own domain logic rather than ANYfem
gaps, and they are named in `OUT_OF_SCOPE` so the exclusion is visible instead
of achieved by quietly leaving them untracked.

Two invariants are now enforced by tests rather than merely stated: no module
outside `ui/` imports Tk or ANYtk3D, and no module anywhere imports
ANYstructure.

The largest open items were rigid-sphere collision (about 50 of the 176
options), fracture and element erosion, eccentric beam-shell coupling through
MPCs, and 8-node shell elements. All are supported by ANYsolver. Phase 9
closed the first two to *partial*; the rest remain.

### Phase 9 detail

Impact is the analysis where the settings, not the model, decide whether the
answer means anything -- so ANYfem computes both of the ones that matter and
reports what it used.

**The penalty comes first, because the time step depends on it.** The contact
period is `2 pi sqrt(m/k)`, and a step near that period does not merely lose
accuracy: the contact iteration fails to converge and the run reports a peak
force that is nonsense. Measured on a 200 kg sphere at the solver's
recommended penalty, `dt = T/5` and `T/10` both failed with momentum balance
errors of 6.3 and 3.2; `T/20` completed with an error of 4.6e-6. Twenty steps
per contact period is therefore the default, and the penalty is taken from the
solver's own `recommend_sphere_contact_penalty` rather than left at zero.

**The approach is skipped, and said so.** Free flight is exact -- constant
velocity, no forces -- so integrating a half-metre approach at a
contact-resolving step costs thousands of steps and yields nothing. The sphere
is moved to a small standoff and the move is reported in the timing notes
rather than made silently.

**A sphere that misses is refused.** Aimed past the structure it otherwise runs
to completion and reports a perfectly clean nothing, which looks like a
result. The timing check finds no node within a radius of the travel line and
says so, naming how close it actually passed.

**A rejected contact configuration stops the run.** The solver's own
`validate_contact_configuration` runs before anything is integrated, and its
errors are fatal by default. A badly conditioned contact does not fail loudly;
it produces a plausible-looking answer, which is worse.

Verified by invariants rather than a closed form, since there is none: momentum
balance closes to within 1e-3 of the sphere's momentum, absorbed energy is
positive and never exceeds what the sphere arrived with, peak displacement
scales linearly with speed and absorbed energy as its square -- both of which a
linear-elastic structure has to obey.

Node generation order guarantees conformity by construction:

1. one node per used vertex,
2. `n - 1` interior nodes per edge, in the edge's own direction,
3. face interior nodes from the Coons blend.

Faces look boundary nodes up from the vertex and edge registries and reverse the
list when they traverse an edge backwards, so coincident-node merging and
tolerance matching are never needed.

## Remaining work

**None of it is ANYfem's.** With every phase done the parity ledger reports
**92% coverage with nothing blocking** — every capability is covered or named in
`OUT_OF_SCOPE` with a reason — and the migration gate still answers "not ready"
on exactly two criteria:

1. analysis paths reproduced within tolerance on a fixed set of models;
2. no performance regression on those models.

Both need the same input, and it is not code: **a fixed set of models run
through ANYstructure with their results and timings recorded.** The harness that
consumes those numbers is built and tested; its case list is empty because the
numbers do not exist. Nothing on the ANYfem side can produce them, and inventing
a comparison set from ANYfem's own output would make the gate self-certifying.

Until that happens, `anyfem-gate` reports three of five met and stays closed.
Running `anyfem-parity` and `anyfem-gate` gives the live state; the sections
below record what each phase did and why.

Ordering was by whether an item changes *answers*, then by what it unblocked.

| Phase | Theme | Ledger entries closed |
| --- | --- | --- |
| **10** ✅ | Structural fidelity | eccentricity, hardening curve, fracture in nonlinear static |
| **11** ✅ | Meshing depth | graded refinement, adaptive impact meshing, S8/B3 elements |
| **12** ✅ | Modelling and workflow breadth | symmetry, capacity workflow, recovery policy |
| **13** ✅ | Result interop and plotting | FRD/INP import, SIF import, time-history plot |
| **14** ✅ | The migration gate | state importer, comparison model set, performance |

### Phase 10 — Structural fidelity ✅

This one came first because it was the only remaining gap that made ANYfem
give a *different answer*, rather than merely lacking a convenience.

**Eccentric beam-shell coupling.** A stiffener modelled with its neutral axis
in the plate midsurface is materially wrong for a stiffened panel, which is
the core domain. The solver already provides the coupling:
`CoupledBeamShellElement` for a one-to-one offset and
`InterpolatedBeamShellMPCElement` where the beam node projects into a shell
element. Because ANYfem carries beams on plate *edges*, the beam node projects
onto a plate node exactly, so the one-to-one coupling covers the common case
and the interpolated form is only needed for a beam crossing a face interior.

Delivered: an `eccentricity` on `BeamSection`; a plate normal at the edge taken
from the adjacent face's Coons surface; offset beam nodes generated instead of
shared ones; one coupling element per station; `constrained_nodes_on` keeping
supports off the slaved offset nodes, since a prescribed DOF on a slave is a
contradiction the solver rejects.

Verified against a transformed section on a 4 m plate strip with a flat bar
down its centre, supported at the ends only:

- **ECC-01** — neutral axis from the axial-strain zero crossing at midspan:
  0.05790 m against 0.05778 m by hand, **0.20%**.
- **ECC-02** — deflection ratio, shared-node against eccentric: 2.405 against
  the transformed-section inertia ratio 2.440, **1.5%**.
- The MPC kinematics themselves (`u_beam = u_shell + θ_shell × r`) hold to
  machine precision at every coupled station.

**Hardening curve.** `steel(..., nonlinear=True)` attaches
`dnv_c208_steel_curve(grade, thickness)`. `Material` stores the *recipe*
`("dnv_c208", grade, thickness)` rather than a live curve object, so it
survives a project file; a material that silently lost its hardening on save
would turn a plastic analysis elastic without saying so. **MATL-01** checks the
curve and the elastic properties resolve the same table row.

Worth knowing: plasticity is the solver's layered-shell path. Beams stay
elastic, so a stiffener carrying most of the moment will not yield — the
material nonlinearity test loads plating for that reason.

**Fracture in a nonlinear static solve.** Passthrough of the solver's
`FractureConfig`, matching what an impact already accepts. Eroded element IDs
come back on the result. The trigger is equivalent plastic strain, so a
threshold has to be reachable: the test plate peaks near 0.008, and a test
using a threshold above that would pass for the wrong reason.

### Phase 11 — Meshing depth ✅

All three touched the same machinery -- the seeding solver and node generation
-- so they belonged together.

**Graded refinement.** A `Refinement` binds to a point, line or plate (or a raw
coordinate, which is what an impact zone needs) and asks for a smaller element
size within a radius, growing back to the global target outside it. A
`SizeField` answers "what size here" as the minimum over zones. Seeding then
integrates `ds / size(s)` along each edge instead of dividing length by target,
and node placement puts stations at equal spacing in the *unit mesh* -- the
coordinate in which one unit is one element. With no zones both reduce exactly
to what the mesher did before, so adding the field cannot move a model that
does not use it, and there is a test asserting that node for node.

Opposite-side constraints still close: only the counts feed the constraint
solver, and grading changes counts, not the constraint. Repair now ranks edges
by how many elements the field wants along them rather than by raw length,
which under a uniform field is the same ordering scaled by a constant.

**The limit worth writing down.** A Coons interior is the blend of its
boundary, so a size zone in the *middle* of a plate refines nothing at all --
the interior grid is entirely determined by the four sides. Refining locally
therefore means decomposing locally, which is the same answer this mesher gives
to every other awkward region. `RefineForImpact` does exactly that: it locates
the contact point, cuts the struck plate isoparametrically to bracket the
contact patch, and puts the zone on the resulting sub-plate. It is a *command*,
not a solve option, because it changes the geometry and that belongs in the
undo history rather than happening on the way past.

**Adaptive refinement around an impact.** Every impact result now reports
`contact_resolution` -- the element size at the contact point and how many
elements lie across the sphere radius -- whether or not the mesh was refined,
because that number decides whether a peak contact force means anything. On the
verification plate it goes from 1.20 elements per radius to 3.47.

Two things had to be fixed before this worked, both worth recording:

- **A refined contact needs a finer time step, and `auto_timing` did not know
  it.** Refining under the sphere raises the local frequencies without changing
  the contact period, so the step chosen from the contact period alone became
  too coarse and the contact iteration diverged. The step is now also bounded by
  the wave transit time `h / c` of the smallest element at the contact, which
  binds only on a locally refined mesh and leaves a uniform one bit-identical.
  Lowering the penalty *also* makes it converge, and that was the first thing
  tried -- but it changes the contact rather than resolving it: absorbed energy
  came out at 0.06 kJ against the correct 0.45 kJ. Worth remembering, because
  the wrong fix looked like it worked.
- **`solve_impact` was returning failed runs as though they were answers.** A
  run ending in `contact_iteration_failed` reported a peak force and an absorbed
  energy that were whatever the integration reached before giving up: the right
  order of magnitude and completely meaningless. `strict` now refuses them and
  names the likely cause. One existing test had been written to *accept* that
  status, which is how it had gone unnoticed; it now asks for the partial result
  explicitly and asserts only the contract.

With both in place the refined and uniform meshes agree on the global response
and disagree on the local one, which is the expected shape of the answer:
absorbed energy 0.452 kJ against 0.460 kJ, and peak contact force 5.5e5 N
against 2.1e6 N. The energy was already converged on the coarse mesh; the peak
force was an artefact of spreading the patch over one element.

**8-node shells and 3-node beams.** Element order is a project setting, saved
with the model. An edge with `n` divisions emits `2n - 1` interior nodes, faces
blend a doubled Coons grid, and every position is instantiated except the
element centre, which serendipity interpolation does not use. Ordering is the
solver's: four corners, then mid-sides from edge 0-1. Mid-side nodes sit *on the
curve*, so a Q8 on a cylinder stays exactly on the cylinder.

Q8 is the accuracy win, and it is a large one: **1.07% on 16 elements**, where
STAT-02 needs 256 Q4 elements for the same 2% tolerance. Q8R stays out, being
experimental in the solver.

B3 is a different story, and the honest version is worth stating. ANYsolver's
2-node Timoshenko beam is *exact* for a tip-loaded cantilever; the 3-node one
gives `PL³/4EI` against the exact `PL³/3EI` with one element, because a
tip-loaded cantilever is cubic and a parabola cannot be. Both are exact in pure
bending, so the element is correct -- it simply has nothing to offer over the
2-node element for straight members. **B3 exists in ANYfem so a stiffener can
share the mid-side nodes of a Q8 shell edge**, which is compatibility, not
accuracy. A quadratic beam on a curved line is refused at mesh time, because
the solver's B3 is straight-sided and requires its middle node at the chord
midpoint.

Two load-lumping bugs fell out of the Q8 work, both of the same shape: the
consistent load vector is not an equal share. A uniform traction on a Q8 gives
**minus** one twelfth to each corner and one third to each mid-side; a uniform
line load on a 3-node beam gives one sixth, two thirds, one sixth. Splitting
either evenly drags the quadratic element back to the convergence rate of a
linear one, so the extra nodes would cost something and buy nothing.

### Phase 12 — Modelling and workflow breadth ✅

**Symmetry.** `project.add_symmetry(ref, normal)` restrains the normal
translation and the two in-plane rotations, leaving the rotation about the
normal free; `antisymmetric=True` gives the complementary set. **SYMM-01**
compares a quarter plate against the same plate solved in full and they agree
to nine figures, which is a stronger check than a series coefficient because
the reference is the same structure without the simplification.

Two refusals, both because the failure they prevent is silent:

- **A tilted plane is refused.** The solver applies boundary conditions in
  global axes with no nodal transformation, so a symmetry plane off the global
  axes can only be approximated -- and a half model with slightly wrong
  symmetry still solves and still looks reasonable.
- **An entity that does not lie in the plane is refused.** A symmetry condition
  on an edge that *crosses* the plane restrains the wrong degrees of freedom
  everywhere it touches. The check samples the entity; for a plate the boundary
  suffices, since a Coons patch is an affine combination of points on its four
  sides.

**Capacity workflow.** `solve_capacity()` drives the solver's packaged
`run_nonlinear_capacity_workflow` rather than re-chaining static → prestress →
buckling → imperfection → nonlinear here. That is deliberate: every stage exists
separately in this module, and chaining them by hand would mean maintaining a
second opinion about the order of operations, the prestress recovery between
the static and buckling solves, and the mesh-adequacy check on the chosen mode.

The result is a `CapacitySolution` -- a nonlinear solution carrying the buckling
stage alongside it, so everything that displays a nonlinear result displays this
unchanged -- reporting the elastic critical factor and the achieved capacity
separately. Their ratio is the point of running it, and *either side of one is a
real answer*: below one is imperfection sensitivity, above one is post-buckling
reserve. The verification plate comes out at 1.50, which is a compressed plate
shedding load into membrane action after it buckles, not an anomaly. The
property was first called `knockdown`, which was the wrong name for a number
that legitimately exceeds one.

**Recovery and resource policy.** `recovery_policy()` and `resource_policy()`
build the solver's own configuration objects. Two things here are load-bearing:

- **No default component list.** Recovery's `components` filter drops
  everything it does not name. A whitelist frozen in ANYfem would silently
  discard components the solver later adds -- which is not hypothetical: the
  orthotropic Hill utilisation arrived mid-development and broke ten tests
  through exactly this mechanism.
- **History modes are read from the solver**, not copied. The copy written
  first was wrong within the hour.

Where the resource policy applies is narrower than it looks and is documented
rather than glossed: of the analyses ANYfem drives, the solver accepts one on
the nonlinear static solve (and hence the capacity workflow) and on stress
recovery. The others do not take one.

**Orthotropic materials.** The solver gained them during this phase. ANYfem
models isotropic steel and does not author orthotropic materials, but results,
recovery, fields, probes and the impact time step all pass through material
objects, and each is a place where reading an isotropic-only attribute fails
silently rather than loudly. `_wave_speed` was one: an `OrthotropicMaterial` has
no `elastic_modulus`, so it would have returned zero and quietly dropped the
impact time-step bound. It now takes the stiffest directional modulus. A group
of tests drives the whole pipeline on a model whose material ANYfem cannot
itself build.

### Phase 13 — Result interop and plotting ✅

Reading results *back*, so a model exported as a deck, solved elsewhere, and
returned as a result file is postprocessed through the same contours, probes,
paths and reports an ANYfem solve uses. `import_calculix_results` reads FRD and
DAT; `import_sesam_results` reads SIF RVSTRESS shell stresses. Both parse
through ANYsolver — ANYfem owns the adapter, not the format.

**INTR-01** writes a solution to an FRD, reads it back and matches it to the
model by node ID: the deflection comes back identical.

The interesting part was not the parsing. It was that an external result is
*not* the same object as a solved one, and three differences had to be held
rather than smoothed:

- **An FRD carries three displacement components. There are no rotations in
  it.** A shell rotation of zero is a plausible number and a wrong one, so
  `ImportedSolution.component("rx")` raises with "it is not zero -- it is
  absent", and the underlying array holds NaN there so anything that indexes it
  directly cannot mistake the gap either.
- **Stresses arrive per node, already averaged by the writing solver**, not per
  element from a recovery here. They stay node-valued so nothing downstream
  mistakes them for something this layer computed, and `evaluate_field` looks
  them up *before* it would recover — recomputing would quietly substitute
  ANYfem's opinion for the imported answer.
- **Component names come from the file**, carried through as found. Same rule
  as recovery, for the same reason: SESAM names them `SXX`, `SYY` and so on,
  and a list frozen here would drop whatever it did not recognise.

Matching is by node ID, and a file for a different mesh is refused with the
overlap rather than attached partially — a field covering a third of the model
still draws a picture, and the picture is a lie. `require_all_nodes=False`
attaches the overlap deliberately.

**The time-history plot.** Decided in favour of a hand-written Tk canvas rather
than matplotlib. The reasoning: ANYtk3D already draws a full 3D viewport on a
bare `Canvas`, so this is the house style rather than a novelty; the GUI's whole
dependency set stays Tk, which ships with Python; and what a plotting stack
would buy — log axes, multiple y scales, vector export — is not needed to read a
load-displacement path. If any of that is wanted later, matplotlib as a GUI
extra remains open, and nothing here forecloses it.

`history_series` reduces a transient, an impact and an incremental solve to one
`Series` type — two arrays with labels and units — so the widget never asks
which analysis it is looking at, exactly as `Field` means the contour does not.
The axis arithmetic (`nice_ticks`, `padded_range`, `map_to_canvas`) is
module-level and pure, so the part that can be wrong in a way a screenshot
would not reveal is tested without a display.

Two bugs the tests caught, both mine: `has_history` asked for the series with
the node trace *suppressed*, so it answered "no history" for a transient whose
node trace is the whole point; and the first `nice_ticks` rounded the ideal step
up, drawing three ticks on an axis with room for six.

### Phase 14 — The migration gate ✅ (built; three of five criteria met)

`anyfem.migration` is the part of the gate that needs code rather than a ledger
entry, and `anyfem-gate` reports all five criteria.

**The state importer.** `read_runtime_fem_state` reads
`save_runtime_fem_state` files — plain or gzipped JSON with a format tag — and
**does not import ANYstructure**. It does not need to, which is the one-way
dependency paying for itself: ANYfem consumes the old application's output
without depending on the old application. There is a test asserting no
`anystruct` module is ever loaded.

Of the 176 options, **144 map** onto ANYfem settings, **24 are out of scope by
decision** and **8 are solver internals** ANYfem does not surface. The file
reports which is which. That distinction is the point: a debt list and a to-do
list are different things, and a migration that silently dropped a setting
would run a different analysis from the one the file asked for and look like it
had worked.

It restores **settings and recorded numbers, not the model**. Two reasons that
are really one: the snapshot describes a parametric panel, which is out of
scope, and the stored `visualization` is a plotting grid rather than a mesh, so
there is no topology in the file to rebuild from.

**The headless seam** (criterion 4) is proved rather than asserted: a stiffened
panel with eccentric T-bar stiffeners and a revolved cylinder, both built,
meshed and solved with no Tk imported. This is the property the whole migration
rests on — if it holds, ANYstructure's parametric front end becomes just another
caller; if it does not, the migration is a rewrite.

**The comparison harness** (criterion 1) is built and its case list is *empty*,
which is the honest state of it. The numbers have to come from running the
models through ANYstructure's own FE path and recording what it produced, and
that has not been done. Leaving the list empty keeps the gate closed for the
real reason rather than passing on nothing.

**Where the gate actually stands.** Three of five criteria met:

| criterion | met | why |
| --- | --- | --- |
| analysis paths reproduced on recorded models | no | no recorded ANYstructure numbers exist yet |
| parity ledger clear | **yes** | 0 blocking, 0 partial, 92% covered |
| `save_runtime_fem_state` importable | **yes** | built this phase |
| headless API builds every model type | **yes** | panel and cylinder, no GUI |
| no performance regression | no | ANYfem's timings are recorded; there is nothing to compare them against |

Both unmet criteria need the same thing: **someone runs a fixed set of models
through ANYstructure and records the results.** That is not ANYfem work, and it
cannot be faked from this side. `gate_report` reports them unmet with the
reason and never as passed by default — a gate that opened because nobody
supplied the evidence would be worse than no gate, so there is a test for that
too.

One correctness fix fell out: `parity.gate_status()` carried a hard-coded
reason saying the ledger had open entries and the importer was unbuilt. Both
were true when written and neither is now. It computes its reason from the
ledger, and states plainly that it answers one criterion of five.

### Decisions

**1. Section resultants do not belong in ANYfem. Decided: out of scope.** The
ledger records "axial force,
moment and shear resultants on a section" as missing, but it is a
*parametric-panel shorthand*: `fem_integration` applies them because its
geometry is a panel with an obvious cut. ANYfem applies loads to geometry
directly, which makes the shorthand unnecessary and its implementation
awkward -- it would need a cut plane and a distribution rule. The consistent
choice is to move it to `OUT_OF_SCOPE` alongside parametric geometry, and let
the parametric front end apply resultants when it calls in.

Done in Phase 14. It stays in the ledger marked missing — excluded on the
record with the reason, not quietly dropped — and `OUT_OF_SCOPE` names it so
the gate skips it. A capability removed from the ledger entirely would be
untraceable later; one excluded by name is a decision anyone can find and
disagree with.

**2. The time-history plot needed a plotting decision. Decided: a hand-written
Tk canvas.** ANYtk3D already draws a full 3D viewport on a bare `Canvas`, so a
2D plot on one is the house style rather than a novelty; the GUI's dependency
set stays Tk, which ships with Python; and what matplotlib would buy -- log
axes, multiple y scales, vector export -- is not needed to read a
load-displacement path. The cost is maintaining roughly two hundred lines of
drawing code, which is bounded and testable: the axis arithmetic is pure and
tested without a display. Taking matplotlib as a GUI extra remains open if any
of the missing capability is ever wanted; nothing in the widget forecloses it.

## Testing

Everything below `ui/` is headless, so it is directly testable:

- mesh invariants: conformity, orientation, Jacobians, `verify_mesh_quality`;
- attribute-transfer round-trips: a load survives a re-mesh;
- numerical comparison against analytical cases and against the solver's own
  QC cases.

The GUI gets smoke tests only.

## Relationship to ANYstructure

ANYstructure currently owns a 12k-line FE GUI in `fem_integration.py` plus
plate-field and buckling logic in `fe_plate_fields.py`. ANYfem generalises the
first; the buckling-code logic stays in ANYstructure.

Harvesting is done by **rewriting against the topology-bound model, not by
copy-paste**. The parametric geometry assumptions in `fem_integration.py` are
threaded through every level, including the picking. Copying the code would
import the very limitation ANYfem exists to remove.
