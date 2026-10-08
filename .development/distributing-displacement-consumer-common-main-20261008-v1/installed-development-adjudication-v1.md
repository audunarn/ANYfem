# Pinned installed consumer development checks — 8 October 2026

Consumer source commit `345db0cb73e7e7683872188b2a5ee4df0fd84b86`;
consumer wheel SHA-256 `618e9c9dc553e0f789591b014e8a78cd447f977a8b68279dcee86fc73321718e`;
common-main solver wheel SHA-256
`866509295b400c71c398d569e95077d2a38798c8dcdb3e36d4d62b0b9e01412b`.
Both installed runs imported `anyfem` and `anysolver` from the pinned site
directories, not either source tree.

- The pre-existing full affected-file smoke passed **54/54**, including the
  public checkpoint/restore/continue example, in 14.6440475 s child wall
  time; [receipt](installed-smoke-full-v1-a1/receipt.json).
- The new legacy smoke passed **19/19** selected controls in 9.4482919 s
  child wall time, sampled peak process-tree RSS 186,146,816 B; [receipt](installed-legacy-smoke-v1-a1/receipt.json).
  The [runner](smoke-installed-legacy-v1.py) bound the unchanged frozen test
  hashes, manifest, wheel and prior ledger, verified actual installed import
  origins, set CPU0 affinity and requested a one-thread/JIT-off child
  environment, and checked the exact pass count. Effective numerical thread
  pools were not independently audited in this smoke. The child exited 0.
  Its stderr contains a Python314
  `pyreadline3` deallocator warning after pytest; this did not change the
  exit or test count and remains preserved.

The V2 and V3 exact verification attempts timed out before pytest at
78.0437588 and 40.0053873 s. They are retained as failed packet attempts,
not converted into passes. The new smoke is charged against the same logical
240 s development allowance: cumulative child wall 127.4974380 s,
remaining 112.5025620 s including the reserved 60 s, per
[ledger](ledger-installed-legacy-smoke-v1.json). It does not renew the
previous registration or establish full dependency/site catalog acceptance.

**Decision:** these 73 pinned-site controls are enough to continue consumer
development without rerunning the expensive inventory at every change.
Final G6 still requires its registered installed artifact graph, supported
platforms/Python versions and full acceptance. No solver mechanics gate or
public production qualification is closed by this development check.
