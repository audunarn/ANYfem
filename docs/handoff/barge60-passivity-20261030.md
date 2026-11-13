# 60 m barge radiation passivity repair — 2026-10-30

State: implementation and damping admission verified; requested runnable example blocked by an unchanged waterline admission.

The coarse collocation source has a nearly non-radiating coupled sway/roll direction. Discretization/reciprocity errors made its
raw damping fail the existing relative -1e-9 gate. The explicit `refined_direct_quad_v1` profile uses 2176 physical quads
(48 x 32 x 4), the owner Green function and coherent direct radiation/diffraction solutions, pressures and retained recovery.
Legacy triangle/default routes and saved recipe identities remain available. No damping/eigenvalue correction or tolerance change.

All 32 original frequencies pass: worst relative eigenvalue -2.5343925518937395e-10. Production preparation used the frozen
20551-node structural mesh and real owner equilibrium, with BEM solves disabled: all 32 exact cached families recovered.
Response/static/source-closure checks passed before the waterline refusal: full conservative envelope 2.238 m versus 1 m.
No partial floating record, loaded project or FE result was published. No structural campaign is claimed.

Inputs remain Hs = 1 m/Tp = 6 s, JONSWAP gamma3.3, seed 0, heading 0 propagation+X, 32 components, 120s at dt0.1s; explicit ballast gives
307500 kg total and approximately 1 m mean draft. Hs/Tp/draft were user choices; heading 0 was an illustrative default.
User steering on heading/sea-state changes is pending. Changing admission logic or reducing Hs silently is not an option.

Implementation also adds true-polygon conservative pressure mapping, a job-owned sparse pressure operator and hydrostatic
basis, distinct cache/capacity identities, profile persistence/restore, exact retained-family request/frequency refusal and
a native replay re-solve test. Synthetic mapping agrees with direct integration to 4.96e-16 on the measured model; this is
not a real 32-component mapped-closure or full workflow speed claim.

## Verification receipts

All commands run through `C:/ANYworkspaceWork/evidence/barge60-passivity-fix-20261030/verify.py` with full commands, hashes,
precise process-wall receipts, thread settings, sampled memory and stdout/stderr retained per named directory.

| Receipt | Exact pytest target(s), relative to repository | Result |
|---|---|---|
| p18-review-repair-tests (ANYhydro) | `tests/test_floating_passivity.py` | 12 passed, including native replay re-solve |
| p19-fem-binding-tests (ANYfem) | `tests/test_floating_passivity.py tests/test_floating_pipeline.py::test_refined_linear_mapping_equals_the_direct_integration tests/test_floating_pipeline.py::test_prepare_from_pose_closes_saves_self_contained_and_remaps_without_cache` | 22 passed |
| t4-mesh-quad (ANYmesh) | `tests/test_floating_quad_source.py` | 5 passed |
| t7b-qt (ANYfem) | `tests/test_qt_floating_wave_load.py::test_floating_mode_locks_follow_restores_fixed_choices_and_builds_strict_worker_kwargs` | 1 passed; profile restore and worker arguments |
| t2-hydro-regression | full targets recorded in invocation.json | 46 passed on the earlier unchanged route |
| native w1–w7, p12/p13/p15/p17 | original realization; final missing count0 | 32 distinct frequencies passed |
| p20-production-prepare | `workflow.py prepare 0 --cached-only` through production worker | Expected physical admission refusal,101.4013398s; no BEM, no partial publication |

Do not sum repeated test runs as unique coverage. t3, t4's interrupted earlier pipeline, t7-example and p16 frequency timeout
remain failed/incomplete evidence, not passes. Full pipeline/example suites did not complete; selected current tests above did.
The numerical ledger charges 1955.7838165s of2100; this includes a conservative 115 s interrupted-run charge whose actual duration
is unknown. Effective recorded BLAS pools use one thread. No new numerical execution is scheduled without a changed input,
failure or concrete unresolved question.

## Review and disposition

Independent Claude Opus 5.5/medium reviews completed in `barge60-passivity-review-20261030-01` and
`barge60-passivity-review-20261030-final01`. The latter requested withheld full acceptance and predicted the now-confirmed
waterline refusal. Exact request/frequency checks and replay re-solve coverage were added afterward and passed. A same-owner
follow-up failed ENOTFOUND before file reads; parent made only those narrow integration repairs. No different-family review
is claimed for Claude-authored numerical implementation; the OpenAI integration owner assessed the source and evidence.

Full evidence: `C:/ANYworkspaceWork/evidence/barge60-passivity-fix-20261030/REPORT.md` and `REVIEW-RECONCILIATION.md`.
Prepared candidate model/cache: `C:/ANYworkspaceWork/examples/barge60-free-floating-Hs1-Tp6-fixed-20261030`.
Prior failed setup/evidence remain untouched. ANYsolver numerical criteria and unrelated dirty work are preserved.

Remaining before a runnable accepted example: user physical steering; preparation passing every gate; actual 32-component
mapped closure; Apply/undo/redo; offline save/reopen; selected first/last FE frames and result-artifact reopening. Corner/offset
waterline field accuracy, pressure convergence and full-size serialization remain qualification/performance limits. No release,
publication, full 1201-frame FE solve or full-barge performance extrapolation.
