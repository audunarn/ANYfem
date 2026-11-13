# Caller planning CI host scope

The dedicated release/manual `--profile caller` lane collects exactly the four
original required positives from `tools/ci_caller_planning_profile.py`. It does
not first collect or execute the historical full suite. Each original positive
executes once in its own fresh interpreter; fixtures, assertions, cancellation,
retirement and completion properties are preserved. No host caches are cleared.

The environment is Windows CPython3.14.2, numpy 2.4.6 and scipy 1.17.1. Native planning
is required at install time and qualification is required during execution.
Each child preflight validates the actual runtime/native identity. Serial fallback
cannot satisfy the original positives. Fresh-host evidence makes no shared-host
capacity claim and does not renew any campaign authority.

The original selected four-node inventory is retained. Merge requires its exact
JUnit identity union, no duplicate/missing/failure/error rows, known collection
skips exactly once, and the existing completion validators. Per-phase full logs,
XML and process-wall durations are retained; the1200 second overall process-wall
cap is shared across collection and all four fresh children, with remaining time
passed to every child. This prospective change is authorised in testing-regime-task.md.
