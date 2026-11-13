# Manager-focused testing handoff

The ANYfem-only implementation is on `codex/anyfem-manager-tests-final`.
Routine checks now cover application state, dispatch, pressure assembly,
persistence, cancellation, stale results, undo and small real application routes.
Numerical campaigns remain explicit diagnostics; no owner algorithm or tolerance
was changed. The frozen reported-spline source remains byte-identical in Git.

The runner enforces 600 seconds for quick, 1200 for the primary application
suite and 300 per compatibility lane. Required non-skipped Qt/caller positives,
native subtest accounting and owned process-tree cleanup are retained. Publishing
requires both application and Qt release workflows; no package was published.

## Verification

| Check | Source and environment | Observed result |
|---|---|---|
| Final isolated quick | `44779de`, Windows/Python 3.14.2, current workspace owners | 387 passed, 24 subtests passed, two expected skips; 204.706603 seconds process-wall |
| Runner, receipt and hold contracts | Same implementation, parent focused check | 105 passed, 24 subtests passed, one POSIX-only skip |
| Final Ubuntu unit verification | `a378b96`, published owners, Python 3.13 | 177 passed, 24 subtests passed, no skips; 31.32 seconds |
| Compatibility matrix | `e8a54bf`, three OS/four Python combinations | All 12 passed; 23 tests per lane, 6.49–13.72 seconds |
| Qt application routes | `e8a54bf`, Windows/Ubuntu, Python 3.11/3.14 | All four passed; 44 tests per lane, 52.29–68.14 seconds |
| Linux GPU application smoke | `e8a54bf`, Python 3.13 | Passed; 16.01 seconds |
| Exact caller profile | `e8a54bf`, Windows/Python 3.14.2, NumPy 2.4.6/SciPy 1.17.1 | All four original positives passed, no skips; 118.92 seconds |

The two local quick skips are the POSIX inherited-pipe regression on Windows and
the installed-wheel check reserved for release/compatibility lanes. Installed-wheel
import, tiny solve and project reopening passed in the compatibility lanes.
The final helper/receipt changes leave the executed compatibility/Qt/caller test
modules and application source unchanged; raw selected-node sets and native JUnit
counters were audited separately. These remain their original CI executions,
not fabricated executions of a later source revision.

The original expensive angular-remesh pressure case remains unchanged as a
diagnostic. Its routine replacement uses independently triangulated source panels
offset along their normals and preserves the analytical force/moment assertions.
Historical test-start intervals include fixtures, teardown and runner gaps;
they are not isolated body timings or like-for-like speedup measurements.

## Blocked acceptance

The user chose to keep owner work separate. Published owner revisions lack APIs
needed by existing application features: `anyhydro.direct_lid_profile`,
`anymesher.floating_quad_source`, `anygeometry.cleaner`, and
`anysection.SectionChoice`. The stored-family route also needs the unpublished
`anyhydro.family_pressure_bank` adapter. Full published-graph CI failed collection
before running its application cases; the missing local helper import discovered
in that run has been repaired separately.

Consequently, full/release acceptance and the 20-minute full-suite target remain
unverified. Owner-dependent positive checks are not reclassified as passing.
The published-owner Ubuntu quick attempt finished in 244.47 seconds, with six
stored-family failures from the absent adapter and one POSIX-only test-double
cleanup defect. The latter was repaired without changing production cleanup;
its Ubuntu unit verification passed, including the real POSIX pipe regression.
The owner-dependent routine
positives remain blocked as well. No owner repository update or publication is
part of this work. This task has not merged the testing branch into main or reused
the earlier failing-snapshot exception.

## Evidence and source isolation

Evidence is retained at
`C:/ANYworkspaceWork/anyfem-testing-regime-20261113`, including exact commands,
runtime paths, process-wall receipts, failed/timeout logs, raw CI artifacts and
source-preservation audits. Application CI run: `43409879337`; Qt run:
`43409884259`; final Ubuntu unit run: `43415067104`. The initial `9f8254f` CI runs were stopped after a shared installed
ezdxf license-label mismatch; the equivalent MIT metadata alias was repaired
without changing permitted licenses.

A concurrent drawing update was merged into the original shared branch during
the task. That work is preserved there. The final isolated branch contains the
testing changes without incorporating or reverting the drawing update.
