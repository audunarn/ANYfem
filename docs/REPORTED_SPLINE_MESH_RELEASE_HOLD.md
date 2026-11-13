# Owner-held reported four-face spline mesh capability

Owner decision: 2026-11-06, "Hold the capability; finish bounded CI". Only this
capability is held outside release acceptance:

`tests/test_spline_mesh_ownership_acceptance.py::test_unassigned_reported_spline_mesh_has_exact_boundary_and_shared_nodes`

The capability is currently **unsupported**. The original positive body,
reported fixture, source counts, assertions and worker defaults are retained
exactly. An unconditional literal `pytest.mark.skip` records the explicit owner
hold. It is not a pass, expected failure or numerical qualification. There is
no environment flag or checkbox that bypasses this hold.

## Preserved failed evidence and limits

The original fixed-case run
[42103849028](https://github.com/audunarn/ANYfem/actions/runs/42103849028)
failed after **13549.020236332 seconds** (approximately 3h46): **1 failed**,
0 errors and 0 skipped. Its complete failed logs, JUnit and source receipts
remain preserved; this hold does not rewrite them or turn the failure into
success. Original test bytes and source-contract proof are retained under the
integration evidence directory `r10-fourface-capability-hold`.

The observed station-balancing failure remains unresolved. The existing
32-round and 4096 limits and all scientific tolerances remain unchanged.
No numerical kernel, mesh admission, model geometry, fixture input, source
count, worker setting or scientific assertion is changed by this hold.

## Active CI and honest capability scope

The exact manual `reported-spline-four-face` request fails during dispatch
validation, before dependency installation or meshing. It cannot be reported
as a successful focused gate while held. Default qualification `all` still
selects the other unheld positive gates; ordinary integration and full release
checks remain required for their supported scope. The principal/dome NURBS
holds remain unchanged. No broader spline or NURBS capability is qualified.

## Future unhold

Fresh owner-approved qualification requires a source-controlled amendment
removing the hold and its fixed-case dispatch refusal, a frozen source/runtime/
input profile and newly authorized bounded execution. Consumed execution authority is not reusable. The retained original positive body and every
scientific assertion must pass under that fresh authority before this
capability can enter acceptance. Removing the decorator alone is not evidence.

Prospective testing-regime migration: the unchanged held source now lives at
`diagnostics/test_spline_mesh_ownership_acceptance.py`; the original identity
above remains historical evidence. The unsupported hold and spent authority remain.
