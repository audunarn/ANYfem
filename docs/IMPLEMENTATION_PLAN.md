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

**The verification suite** (`anyfem-verify`) runs eleven closed-form cases,
each stating what it is checked against and how close it has to be, and writes
dated, environment-stamped evidence. It is explicit about what it does *not*
claim: it is not a general correctness claim, and it does not restate
ANYsolver's own qualification.

**The parity ledger** (`anyfem-parity`) is the migration gate from decision 2,
made computable. `fem_integration.py` exposes 176 options; the ledger tracks 49
capabilities across them and marks each covered, partial or missing. It
currently reports **71% coverage with 11 open entries, and the gate answers
"not ready"** — which is the honest answer, and the reason the ledger exists.
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

With Phase 10 done the parity ledger reports **71% coverage with 11 open
entries** and the gate still answers "not ready". Running `anyfem-parity` gives
the live state; this section turns that list into a sequence.

Ordering is by whether an item changes *answers*, then by what it unblocks.

| Phase | Theme | Ledger entries closed |
| --- | --- | --- |
| **10** ✅ | Structural fidelity | eccentricity, hardening curve, fracture in nonlinear static |
| **11** | Meshing depth | graded refinement, adaptive impact meshing, S8/B3 elements |
| **12** | Modelling and workflow breadth | symmetry, capacity workflow, recovery policy |
| **13** | Result interop and plotting | FRD/INP import, SIF import, time-history plot |
| **14** | The migration gate | state importer, comparison model set, performance |

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

### Phase 11 — Meshing depth

All three touch the same machinery -- the seeding solver and node generation --
so they belong together.

**Graded refinement** is the substantial one. Edge nodes are currently placed
at uniform parameters; graded zones need a per-edge *distribution* driven by a
size field, plus a seeding solver that still closes its opposite-side
constraints when the counts are non-uniform. The Coons blend is unaffected: it
uses logical parameters and takes whatever boundary positions it is given.

**Adaptive refinement around an impact** follows directly, and closes the
collision entry's remaining shortfall.

**8-node shells and 3-node beams** need mid-side nodes from a half-step Coons
grid and the solver's node ordering. The solver qualifies Q8 for full
integration; Q8R stays out, being experimental there.

### Phase 12 — Modelling and workflow breadth

**Symmetry** generates the boundary conditions for a half or quarter model.
**Capacity workflow** is mostly sequencing -- the solver packages
static to prestress to buckling to imperfection to nonlinear capacity, and
ANYfem can already do each step separately. **Recovery policy controls** are
passthrough.

### Phase 13 — Result interop and plotting

Reading results *back* from CalculiX FRD/INP and SESAM SIF, so an externally
solved model can be postprocessed through the same field abstraction. Both are
solver-supported and become adapters plus an association strategy like the one
SESAM import already uses.

The time-history plot needs a decision recorded below.

### Phase 14 — The migration gate

The three criteria that were never built: an importer for
`save_runtime_fem_state` files, a fixed set of ANYstructure models with
recorded numbers to compare against, and a performance comparison. Criterion 2
depends on phases 10 to 13 landing first.

### Two decisions to make before starting

**1. Section resultants may not belong in ANYfem at all.** The ledger records
"axial force, moment and shear resultants on a section" as missing, but it is a
*parametric-panel shorthand*: `fem_integration` applies them because its
geometry is a panel with an obvious cut. ANYfem applies loads to geometry
directly, which makes the shorthand unnecessary and its implementation
awkward -- it would need a cut plane and a distribution rule. The consistent
choice is to move it to `OUT_OF_SCOPE` alongside parametric geometry, and let
the parametric front end apply resultants when it calls in. That drops the
blocking count from 14 to 13 without writing anything.

**2. The time-history plot needs a plotting decision.** ANYtk3D is a 3D canvas,
not a plotter, and ANYfem currently has no plotting dependency.
`fem_integration` uses matplotlib. Either write a small Tk XY plot -- keeping
ANYfem dependency-free, at the cost of maintaining it -- or take matplotlib as
a GUI extra. The first preserves a property worth having; the second is less
code. Worth deciding deliberately rather than by whichever gets written first.

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
