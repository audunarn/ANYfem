# ANYfem architecture

This describes the package as built. See
[`QUALITY_CONTROL.md`](QUALITY_CONTROL.md) for how to check it.

## Boundaries

```text
ANYfem  ->  ANYsolver     analysis
        ->  ANYmaterial   material specifications
        ->  ANYmesher     geometry and neutral meshing
        ->  ANYfileio     FE interchange formats
        ->  ANYtk3D       viewport (only anyfem.ui needs it)
```

The dependency direction is one-way and **ANYfem never imports ANYstructure** —
including `migration.py`, which reads the old GUI's saved state as plain JSON.
That is the rule paying for itself: ANYfem consumes ANYstructure's output
without depending on ANYstructure, and there is a test asserting no
`anystruct` module is ever loaded.
ANYsolver owns the physics and its qualification. ANYmaterial owns material
behavior, ANYmesher owns the geometry kernel and neutral mesh, and ANYfileio
owns interchange syntax. ANYfem owns attribution, application workflow and
postprocessing, retaining its historical geometry/mesh import paths as shims.

## Layers

| Module | Responsibility |
| --- | --- |
| `geometry/` | Compatibility paths over ANYmesher's persistent-ID geometry, curves, surfaces, primitives, sweeps and decomposition. |
| `mesh/` | Compatibility paths over ANYmesher's seeding, refinement and mapped-mesh implementation; ANYfem retains the application association between geometry and analysis attributes. |
| `model/` | Materials, sections, supports (including symmetry planes), loads, cases, combinations, imperfections — all keyed to geometry entity IDs. |
| `solve/` | FEModel construction, analysis dispatch, and recovery/resource policy. |
| `post/` | Fields, probes, paths, envelopes, history series, results and reports. |
| `io/` | Project files, SESAM model and result import, CalculiX deck export and result import. |
| `commands.py` | The command stack: one path for every model change, and therefore undo. |
| `selection.py` | Selection state and the tag encoding shared by every view. |
| `ui/` | Viewport, model tree, stage panels, XY plot, worker thread. |
| `verification.py`, `parity.py` | Closed-form evidence and the capability ledger. |
| `migration.py` | Reads ANYstructure's saved FE state as data; measures the migration gate. |

Everything below `ui/` works without Tk.

## Invariants

1. **Attributes bind to geometry, never to the mesh.** Loads, supports and
   sections reference persistent entity IDs; the mesh association resolves
   them at build time. Re-meshing never loses a load.
2. **Entity IDs are never reused, and never renumbered.** Undo, redo and a
   save/load cycle all restore IDs exactly, because an attribute pointing at a
   renumbered entity would be silently wrong rather than loudly broken.
3. **Every face is mappable, or it is not a face.** Four logical sides, each an
   ordered chain of edges. The decomposition tools exist so a user always has
   a route to that state.
4. **Conformity is structural.** Nodes are generated vertices, then edges, then
   face interiors; neighbouring faces share node objects. There is no
   coincident-node merge and no tolerance anywhere in the mesher.
5. **A cut lies on the surface it divides.** A dividing edge is fitted as a
   line or an arc and checked against the sampled surface; anything that
   cannot be represented exactly is refused rather than approximated.
6. **One field abstraction.** The contour, probe, path plot, envelope, CSV and
   report all consume the same `Field`, so they cannot disagree.
7. **A displacement lives at nodes; a stress lives at elements.** `Field`
   populates exactly one of the two. An element that cannot carry a component
   is omitted, not reported as zero.
8. **An imported model has no geometry, and says so.** It gets the mesh
   association, not an invented BRep. An imported *result* is held to the same
   rule one level down: it reports only the components its file carried, and a
   component the format does not store raises rather than reading as zero.
9. **The GUI is a thin layer.** Panels build the same command objects a script
   does; nothing the application can do is unavailable headlessly.
10. **Refusals are explicit.** Where the solver states a limit — SESAM export,
    unsupported beam profiles, mixing follower and dead pressure in one
    combination, a tilted symmetry plane, a quadratic beam on a curve — ANYfem
    declines and explains rather than producing something plausible.
11. **ANYfem never narrows what the solver reports.** No list held here decides
    which stress components exist or which material symmetries are allowed.
    ANYfem authors isotropic steel, but a model may arrive carrying anything the
    solver supports — orthotropic elasticity, Hill yield, components added after
    this was written — and every path that touches materials or recovered
    results must pass them through. A whitelist frozen in this layer does not
    fail; it silently discards, which is worse.

## Data flow

```text
points -> lines -> faces            (geometry, persistent IDs)
        attributes bind here
              |
        edge seeding                (division counts, constraint-solved)
              v
        mapped meshing              (conformal by construction)
              |
        association map             (entity -> nodes, elements)
              v
        FEModel + LoadCase          (anysolver)
              v
        analysis                    (static, modal, buckling, nonlinear,
              |                      arc length, transient, impact, capacity)
              v
        ShapeView(s)                (one or many displacement fields)
              v
        Field                       (contour, probe, path, envelope, report)
```

## Threading

Analyses run on a worker thread and report through a queue the Tk main loop
drains on a timer. Nothing on the worker thread touches a widget. Cancelling
abandons the *result*, not the computation — the solver has no interruption
point, and the status text says so rather than implying otherwise.

Two Tk hazards are handled deliberately and documented where they bite:
`tkinter.Variable` finalisers must run on the main thread, and
`<<TreeviewSelect>>` is delivered asynchronously so both sides of the
tree/selection round trip are idempotent.

## Scope

Shell and beam structure, within ANYsolver's qualified envelope. Not solids,
not tetrahedra, not a NURBS kernel, not general contact. Meshing is mapped
only, by decision, because the solver's qualified element envelope is
full-integration Q4 and Q8 and the domain is naturally mapped.
