# Submodel workflow contract (C0, proposed)

Status: proposed contract for owner review, 2026-10-09. Slices S4a-1 (section 10, the
cut-independent in-memory workflow in `src/anyfem/submodel.py`) and S4a-2 (section 11:
persistence, job-bound source and staleness) are implemented. The volume cut (S2a), attached
stiffeners (S4b), curved faces (S3b), the GUI (S5) and acceptance (S6) are not.
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


## 11. Slice S4a-2: persistence, job-bound source, staleness (design revision 5, implemented)

S4a-2 is still headless and independent of the cut. It persists submodel definitions and
runs, drives a submodel from a completed source *job*, and answers staleness. Not in this
slice: the volume cut (S2a; the child project and lineage stay caller-supplied and are
persisted as given), undoable commands, the workflow job table and the GUI (S5). Section 7's
"format 9" is superseded: formats 9-11 are taken (wet loads, connections, envelopes), so
submodels are format 12.

### 11.1 What a job-bound source guarantees

A job-bound source is a **revalidation**, not a faithful replay of the job: the parent problem
is rebuilt now by one fixed recipe (11.3), and the archived displacement field is admitted only
if it is certified as the solution of that rebuilt problem. That is the property the submodel
needs (its boundary motion is the solution of the parent as it is now). Provenance links the
field to its job, but the job's execution is not claimed to be reproduced. The archived field is
never replaced by a new solve. Jobs whose recorded recipe differs from the fixed recipe refuse,
so a revalidation never silently compares against a problem the job did not solve.

### 11.2 Records

`SourceReference(job_id, case)`: the expected source job and the load case it solved.

`SubmodelDefinition` (schema version 1):

| Field | Meaning |
|---|---|
| `id`, `name` | Identity and label; neither is hashed |
| `volume` | `{"kind": "box", "lower": [3], "upper": [3], "transform": null or 4x4}`; finite, lower < upper, transform rigid when given. Stored for S2a and the GUI; not used by the run until S2a supplies the cut |
| `target_size`, `order` | Child mesh size (> 0); `order` is `"linear"` |
| `policy` | `drive_all` or `release_normal_rotation`; required, no default |
| `tolerances` | `physical` (or none), `natural`, `weight` |
| `source` | `SourceReference` |
| `child_case` | The child load case to solve; required. It must exist in the child (a case with no loads is allowed and stated in the run record) |
| `child` | The child `Project`, detached (11.4) |
| `lineage` | `SubmodelLineage` |

`definition_hash` = canonical hash of everything but `id` and `name`; the child contributes its
model hash and loading hash.

`SubmodelRun` (schema version 1; only successful runs are recorded) holds an immutable
**dependency manifest**:

- `definition_id`, `definition_hash`, `child_case`;
- source: job id, analysis id and the analysis' hash, the job's recorded model, mesh, analysis
  and input hashes, status and partial flag; the pinned mesh record id and a canonical
  fingerprint of its admission-relevant fields (id, kind, status, source model hash, mesh input
  hash, mesh hash, structural preparation, artifact id; name, summary and diagnostics are
  presentation and excluded); the
  source mesh and result artifact descriptors (id, uri, SHA-256, byte size); the S4a-1
  `SourceBinding`; the revalidation measurements (scaled backward error, kappa, threshold);
- child: model hash, loading hash, mesh fingerprint; the child mesh and result artifact
  descriptors (id, uri, SHA-256, byte size), stored in the run itself so a replaced registry
  entry cannot vouch for a replaced file;
- `identity` (S4a-1 comparison identity), JSON-safe diagnostics (11.4), `created_utc`;
- producer versions twice: the job's (`JobRecord.producer_versions`) and those that performed
  the revalidation and the child solve.

### 11.3 Job-bound source

`job_source(project, reference, store, *, mesh_record_id=None) -> JobSource` (a
`LinearSolution` plus the manifest fields above) refuses with a typed `SubmodelError` unless:

1. **Job**: the record exists, status completed, not partial, and its analysis definition
   exists, is `linear_static` with target kind `load_case`, target id `reference.case`, and its
   current `analysis_hash` (recomputed from the current document) equals the job's.
2. **Recipe**: the parent is a modelled project (not imported or mesh-only). The analysis'
   recorded settings, normalised once (a missing mapping is empty), are either empty or exactly
   `{"load_case": reference.case}`; any other key, a `combination`, or a `load_case` value that
   differs from `reference.case` or from the target id refuses. `beam_formulation` and
   `beam_formulation_options` must equal `AnalysisDefinition`'s defaults (`"legacy-b3"`, empty).
   `resource_policy` and output requests do not define the numerical problem and are not
   restricted (they remain in the analysis hash). Anything else refuses
   (`SOURCE_RECIPE_UNSUPPORTED`); widening this is an explicit later decision. The recipe is the job route's own: `build_fe_model(project, mesh,
   load_case=case)` followed by the job route's `preflight`.
3. **Parent model**: the job's model hash equals the parent's current model hash.
4. **Mesh**: the mesh record is the one with id `mesh_record_id` when given, otherwise the
   unique record whose mesh hash equals the job's (several or none refuse). Its status is
   completed, its source model hash equals the job's model hash, and its artifact is registered,
   verifies (SHA-256), has the mesh schema and its own id, decodes, and the decoded mesh's
   `mesh_semantic_hash` (with the record's mesh input hash and structural preparation) equals
   the job's mesh hash. When `mesh_record_id` is given and differs from the job's mesh, refuse
   (the rule `_job_is_stale` applies to an active mesh); when it is omitted the job's own
   historical mesh is used deliberately, and the manifest says so.
5. **Result artifact**: registered, verifies, result schema, complete, not partial; its
   identity has artifact id = job id, document id = parent's, mesh id = the pinned record,
   model, mesh and analysis hashes = the job's, frame kind static. The stored outcome (summary)
   is converged with results.
6. **Field**: the displacement field has exactly one frame, components exactly
   (ux, uy, uz, rx, ry, rz), global basis and the unit the writer assigns to such a field, a node-id table with unique ids, and
   stored global DOF indices; for every node of the rebuilt solver layout (solver-generated
   nodes included) and every component, the stored index equals the rebuilt DOF manager's,
   with no extra or missing entries and no duplicate DOF. Values are finite. The full vector is
   placed exactly by these indices.
7. **Numerical gate**: S1's `estimate_static_error_allowance` on the rebuilt operator; it
   checks that the field satisfies supports, model equations and element couplings
   (reconstruction check) and estimates the scaled backward error and kappa on the solver's
   equilibrated operator. Admitted only when every measurement is finite, the estimate is
   conclusive, and the scaled backward error is at most S1's certification threshold (1e-10).

Only then does the returned solution carry a certified convergence record marked
`revalidated_from_job` with the measurements, so S4a-1's `admit_source` and `bind_source`
apply unchanged.

### 11.4 Run: stage, then compare-and-publish

The run never serializes or edits the live project except in its final publish step. It uses
ANYfem's `DocumentSession`, the boundary job submission uses, under these rules:

- **Session ownership (host contract, not enforced by ANYfem).** `DocumentSession` has no lock
  and no owner-thread guard: any thread can open an ordinary transaction. The isolation of
  publication therefore holds **under the host contract** that every document mutation happens
  on one owning thread (the desktop app's event thread; a headless script's single thread).
  This slice does not enforce that contract and does not claim to. What it does check:
  `submodel_snapshot(session)` refuses inside an active transaction (`SUBMODEL_SESSION_BUSY`)
  and records the calling thread, and `publish_submodel_run` refuses on a different thread or
  inside an active transaction. Those checks catch misuse of the run API itself; they cannot
  stop another thread from editing the session. Staging may run on any thread because it
  touches only detached copies. Enforcing a session owner for all mutation entry points is a
  `DocumentSession` change for its owner.
- **Stage.** `stage_submodel_run(snapshot, definition_id, store, *, mesh_record_id=None,
  cancellation_token=None) -> StagedRun` thaws a detached parent and child from the snapshot
  document (`ProjectSnapshot.thaw`), runs `job_source`, `bind_source`, meshes the detached child
  at `target_size` and runs `solve_submodel` with `child_case`, checking cancellation between
  stages. A fresh run id (uuid4) reserves the attempt's output namespace: result artifact id =
  run id (`results/<run id>.anyres.h5`), mesh artifact id = `<run id>-mesh`
  (`meshes/<run id>-mesh.anymesh.h5`); both destinations must not exist. Both files are written
  (each atomically), re-read, and their SHA-256 and embedded identity verified. The run record
  and the two artifact descriptors are built and serialized once here (JSON-checked), so
  publication inserts prepared values. No project is touched.
- **Publish.** `publish_submodel_run(session, staged) -> PublishResult` is the single commit
  point. Every check happens **before** the transaction opens, so a refusal never enters the
  session's rollback path: cancellation; owning thread and no active transaction; revision id
  equal to the snapshot's (any committed edit since refuses, `SUBMODEL_INPUTS_CHANGED`); the
  source mesh and result files re-verified against the manifest's descriptors and SHA-256
  (a source file changed or removed during the run refuses, `SUBMODEL_INPUTS_CHANGED`); the
  staged child files re-verified; the run id and both artifact ids not yet registered. Then one
  `session.transaction(..., solver_affecting=False)` whose body only inserts the prepared
  artifact descriptors and run record into their dictionaries. The transaction can still fail
  after the insertions, when the session serializes and hashes the updated project for the new
  revision; the session's own rollback then restores through the project reader, which applies
  reopen migrations (queued, running and cancelling jobs become interrupted; an existing
  `DocumentSession` defect reported to its owner). Publication does not rely on that path:
- **Exact restoration.** Before opening the transaction, publication keeps the project's
  attribute mapping (`dict(project.__dict__)`: the original objects, including the job records
  and the registries it will insert into). If the transaction raises without committing,
  publication puts those original objects back (`project.__dict__` cleared and refilled),
  removes exactly its own insertions from the original registries, leaves the revision (which
  did not move) untouched, and re-raises; the caller then discards the attempt's files. The
  session's command-stack and selection rollback are unaffected. A reload never replaces the
  live records that existed before publication.
- **Commit outcome.** The session commits and then calls its listeners; a listener can raise
  after the commit. Publication decides the outcome from the session, not from the exception:
  committed when the revision id moved past the checked id and the run is registered. A
  committed publication returns `PublishResult(run, notification_error)` carrying any listener
  exception; it is never reported as uncommitted and its files are never removed. An exception
  with no commit is re-raised.
- **Discard.** `discard_staged_run(staged)` removes exactly the staged files and refuses if the
  run is registered. `run_submodel(session, ...)` composes snapshot, stage and publish, discards
  the staged files only when publication did not commit (failure, refusal or cancellation), and
  re-raises the original error. Existing runs and artifacts are never touched; no failed or
  cancelled attempt leaves a run record.

The snapshot document is produced by `session.snapshot()`, which serializes the project as
every job submission does; for a project edited through the session this is the committed
state (every commit already serialized and canonicalized it). Direct edits made outside the
session's transactions are outside this contract, as they are for jobs.

Durability boundary: publication changes the in-memory project; saving it is the caller's
separate step. A process that dies between staging and a save leaves unreferenced files named by
a run id; `submodel_orphans(project, store)` lists files under the store that no run or job
references, and deletes nothing.

Diagnostics are stored as JSON: arrays as lists, integer keys as strings, nonfinite numbers as
the strings "inf", "-inf", "nan".

### 11.5 Persistence and ownership

`Project.submodels` and `Project.submodel_runs` (dicts by id). `project_to_dict` writes
`"submodels"` and `"submodel_runs"` (sorted by id) only when either is non-empty, and then
`SUBMODELS_FORMAT = 12`; `MAX_READ_FORMAT` becomes 12. Files of format 8-11 read as before
(their migrations, including job-status migration, are unchanged).

Child ownership in v1: a child is **detached**: it has no jobs, artifacts, mesh records,
analyses, submodels or submodel runs; anything else refuses when the definition is added and
when it is read. The child is written with `project_to_dict` nested in its definition and read
with `project_from_dict`. Child artifacts belong to runs and live in the parent's store.

Reading validates schema versions, unique ids and the structure of every definition and
manifest (types, descriptor fields, SHA-256 format); violations refuse with `ProjectFileError`
naming the entry. A structurally valid run is **kept** even when its definition or an artifact
it names no longer exists: its frozen manifest stays readable and staleness reports the missing
dependency. Deleting a definition does not cascade to its runs.

Hash isolation: both keys are outside `_model_payload` and the loading hash, so creating or
editing a submodel stales no parent job. The workflow functions take parent hashes from the
session revision (`revision.model_hash`, computed at commit) and from the snapshot document;
they never serialize a live project to hash it. The full document (saving, undo, snapshots)
includes the children, which is why a child is validated as detached and serializable when it
is added.

### 11.6 Staleness

`submodel_run_staleness(snapshot, run, store, *, mesh_record_id=None)` returns typed reasons;
empty means current. It reads the current state from a `ProjectSnapshot` (document and
revision, so a host can reuse one snapshot per revision) and compares it with the run's
manifest, never with the current records alone. File integrity and the active mesh are always
checked fresh (an artifact can change without a revision).

| Reason | When |
|---|---|
| `DEFINITION_MISSING`, `DEFINITION_CHANGED` | Removed, or hash differs |
| `SOURCE_JOB_MISSING`, `SOURCE_JOB_CHANGED` | The job record is gone, or its analysis id, status, partial flag, recorded model, mesh, analysis or input hash, or result artifact id differs from the manifest |
| `SOURCE_ANALYSIS_MISSING`, `SOURCE_LOADING_CHANGED` | The analysis definition is gone, or its hash recomputed from the current document differs |
| `SOURCE_MODEL_CHANGED` | The parent's current model hash differs from the manifest |
| `SOURCE_MESH_CHANGED` | The pinned mesh record is gone, its admission fingerprint differs, or `mesh_record_id` is given and is not the pinned record |
| `SOURCE_ARTIFACT_CHANGED` | A source mesh or result artifact descriptor differs from the manifest, or its file no longer matches the manifest's SHA-256 |
| `RESULT_ARTIFACT_CHANGED` | A child artifact is unregistered, its descriptor differs, or its file does not match the manifest's SHA-256 or embedded run id |
| `REVALIDATION_REQUIRED` | The installed producer versions differ from those that revalidated (no claim of currency across versions without rerunning) |

Editing the child stales only its runs; editing the parent stales every run driven from it.

### 11.7 Tests planned

- Format: 12 round trip (definition, child, lineage, runs) with fresh-process reopen and
  verification of all referenced artifacts; byte-stable re-save; format promotion only when
  used; runs-only and definitions-only documents; format 8-11 documents read as before;
  malformed, duplicate and non-detached-child entries refuse (dangling run references are kept); adding a submodel
  leaves parent model and analysis hashes unchanged; a child that is not detached or not serializable refuses when added.
- Job source: each refusal in 11.3, including a changed recipe (an extra recorded setting)
  refused even when the field would revalidate; artifact replacement with a new checksum-valid
  file; foreign or mismatched mesh/result identity; ambiguous mesh records; missing, duplicate
  and permuted components or nodes; extra frames; wrong units/basis; and numerical-gate
  failures reached with checksum-valid artifacts: a perturbed free value, a violated prescribed
  DOF and a violated coupling.
- Run: a run from a real job reproduces the in-memory S4a-1 result exactly; a non-default
  source case and child case with distinguishable interior loads; fault injection after each
  artifact write and during publication, cancellation, a definition edit during the run, and a
  retry, each leaving no record, no files of the attempt, and existing artifacts intact.
- Staleness: each reason alone, including artifact corruption and active-mesh change without a
  document revision; each admission-relevant mesh-record field changed alone (status, source
  model hash, mesh input hash, structural preparation, artifact id) without a numerical-model
  change; a deleted definition and an unregistered artifact survive save and reopen with the
  run's manifest and the right reason; a child label edit does not stale.
- Recipe: a recorded `load_case` setting that conflicts with the target, where the archived
  field would still revalidate, refuses; non-default beam formulation options refuse.
- Snapshot and publication: staging leaves the live parent, the supplied child and the session
  revision unchanged, including after an injected failure; a committed edit injected between
  staging and publication makes publication refuse and the staged files are discarded; a source
  file corrupted or removed after staging (no revision change) makes publication refuse;
  snapshot and publication inside an active transaction, and publication from another thread
  than the snapshot's, refuse; a listener that raises during publication leaves the run
  committed with its files and reports the listener error; with unrelated queued and running
  jobs in the project, every publication refusal, and an exception injected into the session's
  revision step after the insertions, leave their records (the same objects, same statuses),
  the registries and the session revision exactly as they were, and remove only the attempt's
  files.

### 11.8 Implementation notes (S4a-2)

- Code: records in `src/anyfem/model/submodels.py`; persistence in `io/project_file.py`
  (`SUBMODELS_FORMAT = 12`, `_read_submodels`); `Project.submodels` and
  `Project.submodel_runs`; job source, stage, publish, staleness and orphans in
  `src/anyfem/submodel_jobs.py`. Tests: `tests/test_submodel_jobs.py`.
- **Stored outcome (finding).** Current writers store no solver outcome for a linear static
  result: `write_solution_artifact` resolves none for a `LinearSolution`, and the job manager
  completes such a job on its plain branch with an empty `JobRecord.outcome`. 11.3 step 5 is
  therefore: when an outcome is stored (on the job or in the artifact summary) it must be
  converged with results; when none is stored, the numerical gate (step 7) is the only
  certification, which is what the gate is for.
- Artifact checksums use ANYfem's form `sha256:<64 lowercase hex digits>`.
- The beam-formulation guard of the recipe is reachable only directly: ANYfem's own record
  checks refuse options on `legacy-b3` and tie `b3-ge` to the `b3_ge_native` analysis type,
  which the type check refuses first.
- Definition identity is computed from the serialized entry (`definition_hash`), so a live
  definition and a snapshot document agree; the child contributes the hash of its model
  payload and of its serialized load cases and combinations.
