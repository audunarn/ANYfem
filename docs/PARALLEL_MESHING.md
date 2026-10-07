# Parallel whole-project meshing

Status on 2026-10-07: implemented, default **Automatic** by user decision.
Release and wider performance qualification remain separate. This is the living record for
`ANYmesh/docs/ANYFEM_PARALLEL_INTEGRATION_PLAN.md`.

## Scope and adopted contract

ANYfem chooses the execution route, contains application jobs and reports the
effective route. ANYmesh owns component planning and exclusive worker-pool
leases. Mistral authored the connected ANYfem implementation; root handled
cross-repository integration, independent checks and small review repairs.
Existing connection, load-series, solver and structural work is excluded.

Use `MeshExecutionPolicy.off()`, `.auto()` or `.explicit(workers)` from
`anyfem.mesh_execution`. Counts must be positive integers; booleans, floats and
strings are refused. Per-call and Qt settings take precedence over
`ANYFEM_MESH_WORKERS`; otherwise Automatic is the default. The synchronous project and
workflow APIs accept a caller-local `pool_lease`; asynchronous workflows accept
the policy only. Qt offers Off, Automatic and Custom count with hover help.

Only whole-project hybrid meshing is eligible. Mapped fast paths, automation,
change sets, quad-first and strict certification remain serial with a reported
reason. Missing APIs, unsupported controls and unavailable containment are
detected before dispatch. Dispatched failure or cancellation never retries
serially or attaches a partial mesh. Component progress reaches the run monitor.

Automatic thresholds remain provisional: four independent components, about
3,000 estimated elements cold or 500 warm, and
`min(components, cpu_count - 2, 16)` workers (Windows also caps at 61).
The user authorized Automatic as the default on 2026-10-07, accepting possible
small-case misclassification in exchange for measured gains on larger cases.
Qt starts and resets to Automatic; omitted async policies are resolved at
submission before worker containment is chosen. Explicit Off and Custom count
remain available. This decision does not change eligibility, serial fallbacks,
scientific tolerances or the recorded performance measurements.

Execution settings do not enter persistence or the mesh input hash. Semantic
hashing removes only `hybrid_diagnostics.parallel.runtime`, preserving
deterministic route information. Parallel hashes are invariant across 4/8/16
workers; serial and parallel numbering can differ.

Windows uses a checked kill-on-close Job Object. Linux uses an owned process
group, checked parent-death signal and independent cleanup guardian. Only
contained mesh workers become non-daemonic; solve workers remain unchanged.
Polling is 100 ms; cleanup shares one five-second deadline. Verified retirement
precedes success. Cleanup failures remain visible and prevent attachment.

## Exact dependencies and packaging

| Owner | Source commit |
| --- | --- |
| ANYmesh | `b9814874c692bc675306c90176c03c5fbd05c5a0` |
| ANYgeometry | `660430a5041a8315c523bdb542d64271f9445f7a` |
| ANYloads | `8fdb08de69c8e2ccfe7be9a746e6b4cd10c68d0b` |
| ANY3dView | `d8b82686e477920cb485a7d32e6f04699b6f4d6f` |
| ANYsection license-only | `940fe01ab15fb997e642aa7d6a3f0fa6e79f82eb` |

Fresh ANYmesh 0.5.1 native wheels from b981487 were compiled with native required and disabling
off, using Python 3.14 on Windows and 3.12 on WSL Ubuntu 24.04. SHA-256:

- Windows: `9825f6567f7a60a6008ca93feb7c0b44f27af34c13841c1156f5fa3b0f5e95e8`
- Linux: `9b586d603c5aaa3218d422e6a2ddde4fd2c330346ead6b9cf9ee408db3202b57`

ANYsection now declares MPL-2.0 source and CC BY 4.0 narrative documentation
licensing. The approved license-only commit was pushed to its main; unrelated
GUI/catalogue/beam work remains uncommitted. Its wheel was built from clean
baseline b3f77e4 plus licensing only, declares MPL-2.0 and includes all four
license artifacts. It does not publish that unfinished implementation. No PyPI
release is included. ANYfem retains its existing optional sections extra.

## Verification and limitations

Full commands, stdout/stderr, runtime settings, process-wall accounting and
failed attempts remain under `reports/parallel-meshing-20261007/`.
These separate run counts overlap and must not be summed as unique tests.

| Check | Recorded outcome |
| --- | --- |
| Real public caller parity, beam/quadratic/refinement/seeding and failure contracts | Windows 6 passed; WSL 7 passed including worker hash |
| Parallel hash across 4/8/16 workers | Windows 1 passed |
| Policy/submission/stale-job/fallback/selection contracts | Author final suite 59 passed; separate Qt selection 5 passed |
| Changed Linux containment/parent death/deadline/cancellation | 33 passed, 4 platform skips |
| Windows real Qt cancellation after pool dispatch and PID retirement | 1 passed |
| Windows injected pool death and effective serial refusal reporting | 2 passed |
| WSL injected pool-worker death without retry/partial attachment | 1 passed |
| Final WSL b981487 Qt completion and cancellation/PID retirement | 2 passed, 2 deselected; completed process exit 0 in 23.159 seconds |
| Legacy mesh caller/process contracts | 30 passed; obsolete fake-project fixture repaired and its failed case passed separately |
| Installed license inventory | Windows and WSL passed: MPL-2.0, 20 direct dependencies |
| Packaging and isolated installed headless imports before default change | Passed; then-default Off, neither Qt nor Tk imported |
| Independent final source delta review | No new actionable findings; reviewer ran no tests |

The final WSL completion/cancellation-only run printed both cases at 100%, then
exceeded its 25.066-second process limit during final cleanup/teardown. It is
**incomplete**, not a completed suite pass. With the approved additional
60-second diagnostic allowance and freshly installed b981487 native wheel, the
final targeted run completed successfully: 2 passed, 2 deselected, 23.158608
seconds process wall, no faulthandler timeout. This closes the final cleanup gate
for opt-in delivery; it does not replace the earlier failed evidence.
An earlier larger run also timed out
after those bodies. An initial fault test targeted a guardian; the corrected
pool-worker fault test passed. Both failures remain evidence.

The matched Windows Qt responsiveness gate failed: serial p99 12.5611 ms,
parallel p99 12.6357 ms. The criterion remains parallel p99 <= serial p99;
the 0.0746 ms miss is not waived. Both routes started from the same saved CAD
input and viewport. Earlier mismatched-viewport failure evidence is retained.

## Measured calibration

Single-host Windows wall times through `Project.generate_mesh`, in seconds:

| Components | Elements/component | Serial | Cold parallel | Warm parallel |
| --- | ---: | ---: | ---: | ---: |
| 4 | 73 | 0.314 | 1.565 | 0.240 |
| 16 | 73 | 1.316 | 2.554 | 0.446 |
| 32 | 73 | 2.667 | 2.875 | 0.816 |
| 4 | 723 | 4.668 | 3.520 | 1.999 |
| 16 | 723 | 18.573 | 6.807 | 4.288 |
| 32 | 723 | 40.920 | 13.097 | 11.566 |

Cold includes pool creation/retirement; warm excludes separately recorded pool
creation. All rows passed the semantic parity oracle. The earlier 417-element
pilot remains evidence and is not labeled as the requested 700-element class.
WSL 4 x 73 measured 0.327 serial, 4.731 cold, 0.130 warm (reuse 0.128).
The full Linux matrix has not run. Effective BLAS thread counts were one.
Installed b981487 wheel priority tests also passed on both platforms (Windows
14 passed/1 skip; WSL 13 passed/2 skips), without mesh generation.
All performance rows above bind to source `17754aa`; they are not measurements
of the priority-only `b981487` update. That update independently passed focused
priority checks: Windows 14 passed/1 platform skip; WSL 13 passed/2 platform skips.
Actual OS readback confirmed worker nice 0 -> 5 and inherited nice 8 -> 8,
unchanged parent priority and the same worker PID/nice over two warm jobs.
Permission/unsupported/readback failures remain explicit diagnostics. No native
meshes were generated by these priority checks.

## CI race repair

Main CI job 112749645158 (Ubuntu 24.04, Python 3.14, ANYfem `1412cad`, ANYmesh
`b981487`) failed `test_real_qt_worker_crash_fails_without_retry` with
`completed` instead of `failed`: the test synchronized on a delayed Qt
component-progress message that was processed only after the whole mesh job
had completed, so the external kill hit an idle/retiring pool worker. This is
a test synchronization race, not a product defect. The log is retained
outside this candidate's reports, in the main integration checkout at
`reports/parallel-meshing-20261007/integration/ci-qt-112749645158.log`.

The repair is test-only: a temporary module-level component interception
inside the contained worker, per-test PID ready/release files, a bounded
15-second gate, cancellation checks and a hard exit 3 on release. Production
pool, guardian, caller and error-handler code is unchanged. Changed files:
`tests/job_process_helpers.py`, `tests/test_qt_mesh_parallel_integration.py`
and `tests/test_qt_mesh_fault_injection.py` (seven fake-only tests).

Verification state: independent fake-only verification passed all seven
tests, and syntax checks passed for all three changed Python files. The
real Qt cases were not rerun locally because the
original 600-second allowance plus the single 60-second diagnostic are
exhausted in scope. The approved WSL completion evidence (2 passed,
23.158608 seconds process wall) remains. In main CI the original Qt tests
passed on Windows Python 3.11 and 3.14; Ubuntu 3.11 stalled at a lifecycle
step for over 20 minutes. A separate integration change added a five-minute
step timeout and a 30-second faulthandler to `.github/workflows/qt-tests.yml`;
this is a useful diagnostic bound and does not alter any pass criterion.

## Remaining gates and next slice

1. Resolve the exact Qt p99 performance gate; the user accepted the tradeoff for default enablement.
2. Finish Linux threshold calibration; effective worker priority is verified.
3. Coordinated main delivery is done: ANYfem
   `1412cad01e6ffd943c39fdddea55405131d9e004`, ANYmesh
   `b9814874c692bc675306c90176c03c5fbd05c5a0`.
4. Complete the next authorized main CI run of the repaired real Qt race tests.

The component-parallel implementation and final WSL completion/cancellation check are
verified. The repaired CI race tests are implemented but their new real Qt
verification is pending in the record above. Automatic is now the default by
user decision; wider performance acceptance remains deferred.

The original 600-second allowance used 588.506337 seconds. The user then
approved an additional 60 seconds solely for one WSL cleanup diagnostic, which
used 23.158608 seconds. Total recorded consumption is 611.664945 seconds against
660 seconds. Unused diagnostic allowance does not authorize new calibration or
broad numerical runs. No more native meshes are planned in this delivery.
Metadata checks and source review do not renew authority. No release, scientific
tolerance change is claimed. Default enablement is explicitly authorized by the
user decision recorded above; it does not renew numerical execution authority.
