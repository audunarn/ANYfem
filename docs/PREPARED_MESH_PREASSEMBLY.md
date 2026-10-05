# Prepared mesh preassembly — isolated candidate

`anyfem.prepared_mesh_preassembly.preassemble_prepared_project_mesh` composes
the witnessed application reference manifest, detached source remapper, fresh
public mesher source-bound S3 qualification and existing FE build adapter.
It has no production caller, runs no solver, publishes no Project/mesh record,
and imports neither Qt nor Tk. The tested owner inputs are the exact geometry
`f752cd8` wheel and mesher `6ff16ee` source snapshot. This remains an isolated
candidate, without an active workflow caller or installed-FEM acceptance.

Parameters match `remap_prepared_mesh_to_project`, with a named Project
`load_case` (default `"default"`) and optional `current_project` callback.
Standalone load-case objects and legacy S3 policies refuse. Replacement is
checked by Project object identity; edits are checked by the complete live
manifest. User edits are detected and retained, not undone by this service.

The original remapper continues refusing raw nonempty structural preparation.
The mesher derives a separate new production record from live source/current
geometry and receipt context. FEM never creates ADMITTED fields or rebinds owner
geometry identity. The qualified mesh must retain every physical/association
field, with only `structural_preparation={"qualified_s3": owner_record}` added.

FE construction uses a detached, initially identical Project copy: validation
may normalize legacy section caches there without editing authored input.
The selected case remains identified by its witnessed name and ID. Built element
IDs must match the complete mesh, and every triangle must consume the qualified
S3 formulation. The solver model is discarded; the result contains the detached
mesh, source-binding ledger, owner record, complete field inventory and checked
element IDs. `solver_executed` and `publication_qualified` remain false.

All copy/output work precedes final callbacks. Complete schemas and snapshots,
coordinate detachment and live owner associations are rechecked afterwards.
Project validation is last; no payload work or supplied callbacks follow it.
The result is invocation-local evidence, not a durable permission: later
execution or workflow publication needs fresh integration and its own gates.

Tests in `tests/test_prepared_mesh_preassembly.py` cover one real reopened-child
composition, exact 26 cells/six pressure cells, cancellation, replacement,
late Project edits, late coordinates/record/instance fields, coordinate aliases,
raw preparation refusal, foreign cases, legacy formulation and detached legacy
normalization on success/refusal. Existing remapper evidence is reused; isolated
guard cases clone its already validated binding rather than repeat unchanged
preparation. No scientific tolerance, native/b032 authority or release gate is
changed. Current evidence and next action remain in `QT_CONVERSION.md`.

On Windows and WSL, all 16 focused adapter/copy checks pass. A separate, explicit
26-T3 S3-V2D linear-static probe also converges on each platform, with finite
nonzero displacements and zero fixed-support DOFs. Its prescribed inputs are
S355, 10 mm thickness, 300 Pa pressure bound to six source cells, and a fixed
source-face support. The pressure-cell list records reference binding; the probe
does not independently inspect solved pressure values or formulation IDs.
Preassembly verifies formulation IDs before the later detached solve build.
This is integration evidence, without a reference-solution comparison, stress
acceptance, native meshing, physical Linux GPU testing or release qualification.
Exact artifact identities, runtime origins, failed evidence and results are in
`reports/prepared-mesh-preassembly/6ff16ee/implementation-evidence.json`.

The prepared candidate supervisor reuses reviewed control mechanics from
portable sources checked into `tools/prepared_candidate_controls/`: the exact
reviewed Windows Job helper module copied unchanged, plus minimal extracts
carrying only `OwnedDeadlineBackstop` and `_cleanup_owned` from the reviewed
geometry driver and only `_suspended_thread_id` and `_wait_job_empty` from the
reviewed supervisor helpers, as exact AST source segments with the standard
library imports they require. The extracts contain no `main` entry and no
module-level reviewed gate, record the original source hashes as provenance,
and are hash-pinned by the supervisor and the freezer. The gitignored
`reports/.../control-source` evidence checkouts and all existing frozen
reports stay untouched and are never imported or executed. This makes the
mocked supervisor suite (118 tests) independent of ignored evidence on a
clean checkout. `CONTROL_REVIEWED` and the integration
`EXECUTION_ENABLED` flag both remain false; this is prepare-only work with no
launch, child, kernel/OS probe, meshing, preassembly, solver or runtime
acceptance.
