# Experimental B2 acceleration consumer — 2 October 2026

ANYfem now saves/reopens and executes reference-linear B2 acceleration cases
through the public solver's typed body loads. Three explicit schemas distinguish
Q4 points, mixed/all-S3 points and Q4 nodal footprints. They retain physical
density, member inertia/origin, frames, supports, ordered acceleration triples
and selected recovery. Reopen reconstructs detached inputs without assembly or
factorization; it does not restore an accepted solver state.

`ExperimentalB2BodyAccelerationPanel` uses the existing B2 serializer/decoder
and Project bridge. On execution a fresh public owner prepares model-bound loads;
the consumer preserves their context and separate equilibrated section fields.
No nodal load expansion or compiled identity is persisted, and no solver physics
is duplicated. Legacy schemas/selectors and modal dispatch keep their meanings.
Unsupported finite/modal requests fail explicitly. Errors close the owner;
callback cancellation preserves the complete-case prefix.

Source base: consumer `cabb6ed`, paired with solver `613e43f7` in its isolated
development worktree. This remains `EXPERIMENTAL_NOT_QUALIFIED`. Loaded interior
pose, finite gravity, material-state restart, physical accuracy, scale/platform
and aggregate programme acceptance are not supplied by this consumer slice.

## Executed verification and independent review

Initial source integration: **279 passed / 2 failed** across seven affected
consumer/Project modules. Both failures were test assertions assuming callbacks
receive results; the established solver calls `on_case(case_id)`. Only the tests
were repaired, checking callback/result IDs and completed typed context. The
affected checks then passed **2/2**. Failed fixtures and diagnostics are retained.

Independent Read review identified a real compatibility gap: the preceding solver
could prepare body loads but did not yet accept typed records or recover loaded
fields. Admission now requires the public loaded result, selected-case and field
exports as well as the body-load operation. Five preparation-only old-solver
counterexamples plus the original unavailable-operation check passed **6/6**,
including cleanup and no body generation/solve after rejected admission.

Final installed integration passed **286/286**, with no skips, across the new
module and six existing consumer/Project modules. These cover standalone and
Project roundtrips; public-owner state/recovery parity; ownership and mutation;
strict schema, nonfinite/boolean/duplicate/missing-input negatives; 1/10/100
cases; selected outputs; cancellation and failure/exception cleanup; and legacy
static/modal routes. The underlying solver campaign was not repeated because
its source and paired wheel are unchanged from the installed 263-check delivery.

All **12 installed Project examples**, Q4/mixed/S3/footprint × 1/10/100 cases,
completed with one eligible factorization and typed selected section recovery.
Different geometries/attachment hosts are illustrative workflows, not equivalent
discretization or converged engineering references. Full load-vector preparation
and practical-scale batching/resource qualification remain G5 obligations.

Final separate OpenAI Read review inspected the repaired source and actual
records, reporting no remaining consequential finding in this slice. The reviewer
ran no jobs, edited no files and performed no Git operations. It verified both
artifact identities, source/site bytes, actual imports/settings and all example
counts. This is independent development scrutiny, not different-model-family
scientific acceptance.

## Exact evidence and limits

- Consumer wheel SHA256:
  `02a419916f093ab8711a943cb96f5495b44503a4158717f100c960984db5bab4`.
- Unchanged solver wheel:
  `4c14d0942410f97b6e2ab397eb0d4e567cbff4169c05b391c5685c4df40d3b7c`.
- 98 consumer and 391 solver modules bind source, wheel and installed site;
  all 275 recorded imports are onsite. Python 3.14.2; actual numerical pools
  and Numba one thread, JIT disabled. Runtime dependency versions are retained.
- Installed test supervision: 16.0567564 s, sampled peak process-tree RSS
  190918656 bytes, inside 600 s / 1 GiB. These are execution records, not gains.
- [Raw inventory](raw-v1.json) and [archive](raw-v1.zip): 156 members, 918776
  bytes, SHA256
  `15e6b4f92e56a7e794b44edda1908e191c959229ae63d299cef557ee763cf134`.
  Source failures, repairs, invoked product bytes, fixture/code manifests,
  commands, helpers, consumer wheel and all installed example/Project records
  are retained. Pre-review adapters are reconstructed only where their exact
  original manifest hash matches. Known credential values were scanned in
  memory; the new payload/wheel scan is clear. Contaminated history is not copied.

No scientific criterion, formal F03 authority, legacy identity/default, package
version, main branch or release was changed. The complete native GE/E4/B2 goal
remains active. Underlying [solver recovery evidence](C:/Github/ANYsolver/.perf2-worktrees/stiffened-main-reconcile-20261001/docs/agent_plans/development/r8-b2-equilibrated-body-sections-20261002/results.md)
retains its physical and numerical boundaries, including the endpoint mixed-load
resolution issue and unreconstructed loaded interior pose.
