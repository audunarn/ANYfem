# NumPy bit-change canary (S3 job T7)

Why: exact results are bit-sensitive and NumPy 2.4.6 is pinned.  T7 asks whether a newer NumPy (2.5.x) would be refused or detected.

## Layers and what is proven (tests/test_numpy_canary_layers.py, runs on ordinary CI, no NumPy 2.5 installed)
| Layer | Behaviour with a NumPy 2.5.x claim | Test |
|---|---|---|
| lock parse (`runtime_lock.parse_lock`) | refused: pin and wheel list must both be 2.4.6 | `test_l1_parse_*` |
| installed bundle (`check_installed_lock`) | refused, reason names numpy; also refused when the caller expects another NumPy | `test_l1_installed_*` |
| route lock check (`thread_route._check_lock`) | reason names NumPy | `test_l1_route_lock_*` |
| per-job child check (`_check_runtime`) | "child NumPy ..." | `test_l1_per_job_*` |
| end to end route | declines before any child is spawned, serial result identical; a numpy tree swapped into a built bundle fails the bundle digest | `test_l1_route_declines_*`, `test_l1_a_numpy_swapped_*` |
| `build_runtime build / requirements` | refuse a 2.5 pin (nothing created), a foreign wheel fails both hash barriers | `test_l2_*` |
| canary verdict logic | identical result = NOT DETECTED, differing = DETECTED, wrong inputs = input error | `test_l3_*` |

## The measurement (is there an observable bit change?)
`tools/ci_numpy_canary.py measure OUT` prepares the sector60 closure serially (route OFF) and records the masked result digest
(UUIDs and 64-hex strings masked); `compare` expects the pinned record and a 2.5.x record on the same interpreter to differ.
Manual procedure: run `.github/workflows/numpy-canary.yml` by hand (workflow_dispatch only), or locally build two environments that
differ only in NumPy (hash-pinned wheels; `tools/numpy_canary_requirements.txt` holds the 2.5.4 cp314t hash) and run `measure` in each.
Results of the first local run are in the T7 report (thread-s3-t7-report.md).

## Not covered
The full-model N2c gate (tools/ci_n2c_gate.py) under NumPy 2.5 has not been run (ledger spend needs the owner's go).

## Nightly canary (T7b, permanent)
`.github/workflows/numpy-canary.yml` runs on a nightly schedule (03:17 UTC, default branch) and by hand; never on push or pull request.
It is one small job (20 min timeout; the measurement is ~15-25 s per interpreter once the two venvs exist) and is NOT part of any heavy test
regime: no suites, no thread-route T-jobs, no full-model gate; no other workflow calls it (tests/test_numpy_canary_layers.py enforces this).
A red run means NOT DETECTED (the cheap measurement lost its teeth) or an input problem; DETECTED is the expected green.
The canary wheel hash was verified by download on 2026-11-15 (12,901,584 bytes, sha256 067374eb...).

## Measured on 2026-11-15 (this machine, SkylakeX kernel; real cp314t child; see thread-s3-t7b-report.md)
- sector60 serial, cp314t, NumPy 2.4.6 vs 2.5.4: a6ca5747f11e vs 580e36988ece, DETECTED (120 float leaves), same digests as on cp314.
- T3 (ON == OFF in one run) with a scratch bundle whose parent and child are both 2.5.4 (guards edited in a scratch copy only): 7 passed, i.e. NOT detected.
- Full-model N2c under 2.5.4: the gate FAILS (exit 1) on model and plan digests and on the NumPy version; counters sha unchanged.
