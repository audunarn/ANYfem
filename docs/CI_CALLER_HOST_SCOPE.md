# Caller planning CI host scope

The full profile uses separate interpreters for the four required caller planning
positives only when `ANYGEOMETRY_REQUIRE_QUALIFIED_PLANNING_TESTS=1`. The workflow
preflight establishes the declared runtime. The authoritative cases and required
completion properties remain in `tools/ci_caller_planning_profile.py`.

The runner collects the complete original inventory, executes its core without
those four cases, then executes each original positive once in a fresh host.
Original fixtures, bodies, assertions, cancellation checks and per-fixture cache
histories remain intact. It does not clear host caches or increase memory limits.
Every positive must still enter its required path; serial admission refusal does
not satisfy it. This scope does not qualify a host carrying the lifetime memory
peak of the preceding full suite, and a fresh host must still fit admission.

Each phase validates its collection before fixtures run. Merging requires the
exact complete original node/JUnit identity union, the known collection-skip
identities exactly once, no failed/error rows, and all four original positive
completion properties. Partial execution is not full acceptance. Collection,
core and positive logs/XML remain separate; only actual rows are merged.

The existing workflow whole-job limit remains authoritative. Phase and cumulative
driver durations do not measure setup time or renew an execution allowance.
Quick, focused, Qt and non-required full profiles retain their original route.
