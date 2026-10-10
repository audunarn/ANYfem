# Submodel workflow contract (C0, proposed)

Status: proposed contract for owner review, 2026-10-09. Slice S4a-1 (the cut-independent
in-memory workflow in `src/anyfem/submodel.py`) is implemented as section 10 describes; the
volume cut (S2a), persistence and staleness (S4b), curved faces (S3b), the GUI (S5) and
acceptance (S6) are not.
Plan: [`handoff/submodelling-20261009/PLAN.md`](handoff/submodelling-20261009/PLAN.md)
(revision 3). Evidence: [`s0/RESULTS.md`](handoff/submodelling-20261009/s0/RESULTS.md) and
`c0/results/verify_c0.json` in the same folder.

ANYfem owns the submodel workflow: the definition, source admission, identity-map
composition, attribute carry-over, staleness, persistence, jobs and reporting. Mechanics,
correspondence and geometry belong to their owners:

- `C:/Github/ANYsolver/docs/SUBMODEL_TRANSFER_CONTRACT.md`
- `C:/Github/ANYmesh/docs/SUBMODEL_CORRESPONDENCE_CONTRACT.md`
- `C:/Github/ANYgeometry/docs/SUBMODEL_VOLUME_CUT_CONTRACT.md`

Names below are proposals.

## 1. `SubmodelDefinition`

| Field | Meaning |
|---|---|
| `id`, `name` | Stable identity and label |
| `volume` | Serialized `BoxVolume` |
| `target_size`, `order` | Fine mesh size; `order = "linear"` in v1 |
| `transfer_policy` | `drive_all` (P2) or `release_normal_rotation` (P1) |
| `tolerances` | Owner defaults unless overridden: correspondence and allowance settings |
| `source` | Source job id and source case id |

## 2. Flow

1. `CreateSubmodel` is an undoable command that stores the definition. It does not mutate
   the source geometry.
2. Preview: `plan_volume_cut` is read-only, so the cut and its refusals can be shown before
   anything is created.
3. Create: `apply_volume_cut` and `extract_bounded_closure` on a detached clone give the
   submodel geometry. ANYfem composes the identity map and carries attributes (section 4).
   The result is a child project with lineage.
4. Mesh the child project with the normal route.
5. A submodel job: admit the source, locate, interpolate, build the driven trace, solve,
   compute interface forces and diagnostics, and write a result artifact.

## 3. Source admission

The source job must be completed and successful, with analysis type linear static and a
result artifact present. Its hash must match, it must carry the full six-component nodal
field, and its model and mesh hashes must be current. ANYsolver's capability check must
admit every source element the hits touch. Anything else is a typed refusal before any work.

## 4. Attribute carry-over

| Attribute | Rule |
|---|---|
| Materials, plate and beam sections, beam offsets and orientation | Carried to every inside descendant through the identity map; an unresolved assignment refuses |
| Coordinate systems, units | Copied unchanged; geometry is not transformed |
| Supports inside, including on original boundaries | Carried |
| Supports touching the cut | Carried. The driven trace treats a consistent support row as redundant and refuses an inconsistent one (ANYsolver contract, section 3). This replaces the plan's blanket refusal; S0 and C0 showed carried supports on the cut are consistent with the global solution |
| Pressure, surface traction, line load (intensities) | Carried to inside descendants with the same intensity, side and frame |
| Point load, mass, `per_target` | Carried when the target is inside; refused when on the cut |
| Point load, mass, `total_distributed` | Carried only when the whole original scope is inside; otherwise refused, since clipping would raise the density |
| Gravity and accelerations | Re-applied; mass comes from the sections inside |
| Wet surface and wet pressure | Carried only when the whole wet scope is inside, with the same fluid side and interior point |
| Connections | Carried only when every tied target is inside |
| Refinements | Kept when their zone overlaps the volume |
| Imperfections, thermal or initial-strain loads, activity changes | Refused in v1 |

## 5. Identity map composition

ANYfem composes the geometry map (source to clone to descendants to submodel) with its own
bindings: regions, section assignments, feature-output anchors and attribute targets. The
composed map is persisted with the submodel. An unresolved entry refuses every attribute that
needs it; nothing is dropped silently. `structural_preparation.source_work_mapping` is a
precedent only: it resolves vertices, edges and faces, and only checks existence for other
kinds.

## 6. Staleness

A submodel result is bound to: the source job and case, the source result artifact hash, the
source node and DOF layout, source formulation and section identities, element activity,
geometry revision, units and frame, and the submodel's own volume, tolerances and transfer
policy. Any change invalidates the submodel result. Editing the submodel invalidates only the
submodel. Submodel definitions are excluded from the parent model hash, so adding one does
not stale the parent's jobs. `WorkbenchWorkflow._job_is_stale` covers ordinary inputs only
and must be extended for this cross-project dependency.

## 7. Persistence

Project format 9 adds a `submodels` list. Each entry holds the definition, the child project,
the composed identity map (using `ModelClosure.to_transport` for the geometry part) and the
source binding hashes. Format 8 files read unchanged.

## 8. Reporting

Results use the ordinary `ShapeView` and `Field` path on the child project. The
**transfer/equilibrium diagnostic** reports:

- interface force and moment resultants per cut face, the corner records and the whole-cut
  resultant (ANYsolver contract, section 5);
- the free-body residual against the applied loads inside;
- interior errors near the cut and away from it separately. S0 found a shear boundary layer:
  rotation error near the cut is 2 to 10 times the error further in;
- when two global refinements exist, the change in submodel results between them. S0's hole
  case showed that coarse-transfer error dominates (−5.4 %, −3.5 % and −1.4 % for three global
  refinements, against a submodel discretisation error near 0.2 %).

The diagnostic is labelled as a diagnostic. Matching prescribed displacements does not prove
the global model was adequate at the cut.

## 9. Open for owner review

- Child document in the same project file (proposed) or a separate file.
- Whether `drive_all` is the default before S1's drilling decision.
- The two-refinement diagnostic: always, or only on request.

## 10. Implementation decisions (slice S4a-1, `src/anyfem/submodel.py`)

The cut-independent part is implemented; the volume cut (S2a) will supply the submodel
geometry and lineage that callers pass explicitly today.

- **Source route**: an in-memory `LinearSolution` admitted by `bind_source`, which checks
  convergence and certification, refuses element activity states, checks the six-DOF layout,
  and records the solver-affecting model hash (ANYfem's `_model_payload`), the loading hash
  (the jobs' recipe), a problem hash (the model hash without meshing controls), a layout
  fingerprint and a field hash. It also records fingerprints of what was actually solved:
  - the mesh: nodes, connectivity, face-element and edge-node associations;
  - the complete public state of the solved nodes, elements, materials, supports and
    equations. Every public value is serialized (nested maps, sequences and full arrays
    included); a value that cannot be serialized (callable, opaque or self-referencing
    object) refuses (`SOURCE_STATE_UNSUPPORTED`). Private attributes are runtime caches
    and are not hashed;
  - the solved operator itself: the assembled stiffness and every audited constraint row
    (supports, model equations and element-provided couplings such as eccentric
    beam-shell MPC rows);
  - the assembled load vector of the solved load case.
  Binding assembles the source stiffness once more; the check repeats it. Binding refuses
  unless every shell element of the source faces (the only elements correspondence uses) is
  a solved element with the same node ids and node coordinates (`SOURCE_INCONSISTENT`).
  Beams, couplings and solver-generated entities are not used by the transfer and are bound
  through the state and operator fingerprints. `solve_submodel` checks the binding before lineage and
  correspondence and refuses a source that no longer matches it (`SOURCE_STALE`, naming the
  fingerprint that changed).
  Job and artifact provenance belongs to the persisted route (S4b) and is not claimed here.
- **Lineage**: `SubmodelLineage(source_faces, cut_faces)`; `cut_faces` names each physical
  cut face as a group of submodel edges, so collinear segments are one face and corner records
  are nodes shared by different cut faces. A cut face with no edges refuses
  (`LINEAGE_EMPTY_CUT_FACE`).
- **Candidates**: for each driven node, every submodel face that meets it (from target
  connectivity), through its lineage, restricted to elements of the solved operator. This keeps
  a face that meets the cut only at a node, so the junction rule sees all incident faces.
- **Normals for `release_normal_rotation`**: from the source face's mesh nodes by a best-fit
  plane, once per face. A face whose nodes deviate more than 1e-9 of its size from that plane
  refuses (`SUBMODEL_FACE_NOT_PLANAR`), and one that spans no plane refuses
  (`SUBMODEL_FACE_DEGENERATE`); curved faces wait for S3b.
- **Unsupported in v1**: child imperfections refuse (`SUBMODEL_UNSUPPORTED`); the build runs
  with imperfections off only after that check.
- **Diagnostic**: per cut face resultants about the face centroid (corners excluded), corner
  records, the whole-cut resultant, a check that the reported records rebuild it (each face
  record moved to the centre by M + (about - centre) x F, plus the corners), and the free body
  (interface forces, retained external reactions, applied loads) with separate force and
  moment ratios normalised by the total absolute force and moment involved.
- **Section 8 near/far errors** need a reference solution and are marked unavailable in the
  diagnostic; `refinement_change` reports the change between two source refinements near the
  cut and away from it, labelled as a change, not an error. Each result carries a comparison
  identity (child model and loading hashes, child mesh, lineage, cut grouping, policy,
  transfer settings, load case); the two results must share it (`REFINEMENT_INCOMPATIBLE`),
  their sources must share the physical problem, i.e. problem hash, loading hash and
  selected load case (`REFINEMENT_SOURCE_PROBLEM`, naming what differs), and the source
  meshes must differ (`REFINEMENT_SAME_SOURCE`). Record UUIDs are part of ANYfem's model
  hash, so a refinement is the same project meshed again, not a rebuilt copy.
- **Finding**: plain four-sided faces outside the xy-plane failed ANYfem structural
  preparation in a test model ("topology-backed Coons evaluation requires
  GeometryModel.face_point"); the tests give such faces an explicit `Plane`.
