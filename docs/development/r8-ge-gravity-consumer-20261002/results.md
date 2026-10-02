# Native GE gravity consumer — 2 October 2026

ANYfem now persists the public solver GEBodyLoad through an explicit
`anyfem.experimental-ge-point-panel-body-load-v1` wrapper. Existing point inputs,
staged loads and native Project format retain their meanings. The new keyword-only
body_load field does not alter old positional constructors. Nodal actions, staged
programmes and body acceleration cannot be silently combined. Unsupported
footprint wrapping, unknown schemas, stale model identity and absent public solver
support fail explicitly. Mechanics remain in ANYsolver.

Project dispatch uses reference-linear gravity by default and finite solving only
with explicit targets. It forwards cancellation, control, failure and completion
status rather than replacing them with an estimated success. Owner cleanup is
preserved. The returned numerical result is not a durable checkpoint.

## Executed checks

Earlier source candidate:66 passed in30.89s, complete supervised route31.924s,
peak184475648B. Final isolated installed candidate: **67 passed, no failures or
skips**, pytest23.51s; complete route25.893s, sampled peak tree196497408B.
This is validation, not an alternating timing study or a development-speed claim.

Tests cover strict/deep-owned input, canonical serialization, native Project and
standalone round-trip, mutation rejection, legacy compatibility, missing solver
contract, model binding, supported dispatch, exception/accepted-prefix preservation
and existing staged/Project regressions. The actual public example is executed
inside the installed workflow test; its main function was not run as a redundant
second numerical job.

The example uses the solver's two-Q4/two-native-element eccentric-mass definition.
It saves and reopens `gravity.anyfem`, solves through the Project reference-linear
dispatcher, cancels after complete accepted factor0.25, writes an independent
checkpoint file, reads that file into a fresh public owner, and completes factors
0.5/1 on original and restored branches. Both final checkpoints are identical and
differ from the prefix. Model identity, native beam bytes/hashes and acceleration
survive persistence. Every accepted observation meets equilibrium1e-8 and gap1e-9.
Mass1256kg and applied force(0,0,-12321.36)N are recovered by the solver.

Final compatible wheels:
- ANYfem `9d96881dbdd7dacc10577db82873d3acb48e7d5765afa04855090e7cfc8a2586`.
- ANYsolver `4a287d5596f0aab7c16056e47932730ce95aba9971317dd198158ba754b72b06`
  from solverb9ea639e; unchanged product in this consumer slice.

98 consumer and393 solver Python modules match source/wheel/site manifests;
280 actual imported package modules are onsite. Windows/Python3.14.2, effective
one numerical thread and JIT disabled verified at runtime. Jobs supervised
serially at600s/1GiB. Full dependencies, commands, fixture hashes, JUnit, raw
Project/checkpoint/observation outputs and wheel are retained in [raw-v1.zip](raw-v1.zip),
with [member identities](raw-v1.json). Archive SHA256
`056a86227214d66a76677e63a6a640def3c86512d8d6235f20bcfa226c9d55c7`.
55members; in-memory inherited-sensitive-value scan found0matches. Historical
solver credential dissemination restriction remains unchanged.

## Scope

Independent Read review found no consequential source defect and recommended
explicit disk checkpoint restoration; final installed example includes it.
[Review](review.md). Different-family review was unavailable; this independent
implementer review is not scientific qualification.

This is experimental M1/M4/G6 consumer integration, not aggregate acceptance.
No GUI expansion, line/footprint gravity, combined/staged body acceleration,
full material/curved/external/scale/platform qualification or release is claimed.
No defaults, versions, physical tolerances or element equations changed.
No contact-worker coordination, main merge, push or publication occurred.
