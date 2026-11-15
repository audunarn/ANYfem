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
