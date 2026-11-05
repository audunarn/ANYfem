# Test scopes

The existing default `python tools/test_anyfem.py` remains the seven-module
`quick` development check. `--profile unit` (alias `fast`) reuses those curated
modules and adds pure suite-tooling tests. Paths are chosen before pytest imports
tests; neither mode first collects the whole suite.

`--profile integration` runs remaining ordinary integration modules.
`--profile qualification` runs the explicit scientific/whole-model modules and
`*_acceptance.py` family recorded in `tools/test_suite_plan.py`. Unknown modules
always default to integration. Classification never changes test bodies,
assertions, scientific tolerances, fixtures, holds or skip policy. In particular,
`test_spline_mesh_ownership_acceptance.py`, including the failing four-face spline
station-balancing case, remains in qualification and the full release universe.

These tiers are disjoint and exhaustive at module and original-node identity
level. `suite-plan.json` records the entire static module inventory, assignments,
selected/excluded paths and classification rules without importing tests.
`suite-collected.json` records actual node identities and partition counts for the
chosen scope. Partial profiles describe only their selected collected modules;
their static plan still records all excluded modules. Collection skips/errors and
ordinary outcomes remain in pytest/JUnit; a collected inventory is not a pass.
The runner retains ignored inherited `PYTEST_ADDOPTS` in `run.json`.

`--profile full` still selects `tests`, retains all original cases, scientific
verification and existing fresh-interpreter caller-positive union and receipt
validators. Its node receipt records the original whole collection, not a final
positive subset. Full release requires the existing complete gates and all four
original planner positives under their qualified profile; development tiers,
including a green qualification tier, never establish release acceptance.

On push/PR the tests workflow runs one Ubuntu/Python 3.13 unit lane. Manual
`scope` defaults to `full`: the original twelve platform/Python lanes, licensing,
caller receipts, numerical profile, parity and wheel/build checks remain intact.
Manual integration or qualification selects a partial development lane. The Qt
workflow is unchanged. No failed historical evidence or execution authority is
consumed, reset or waived by this structural change.
When `ANYGEOMETRY_REQUIRE_QUALIFIED_PLANNING_TESTS=1`, the partial qualification
profile refuses execution and directs the operator to full. The required four
positives must use the existing full fresh-H route, never a shared development
interpreter. Running this development tier does not claim scientific qualification.
Plans and collected receipts bind the ordered inventory, assignment mapping and
classification rules with SHA-256 digests. Unit CI has a ten-minute job cap;
explicit integration/qualification retains the original 360-minute Actions job
cap. These limits do not renew separately bounded scientific execution authority.
Collected receipts bind the runner's owned pytest root and preserve the first
original collection in its fresh evidence directory. Nested scalar pytest runs
cannot write or overwrite that parent inventory through inherited plugin settings.
