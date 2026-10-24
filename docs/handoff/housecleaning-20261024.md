# ANYfem housecleaning and main delivery — 2026-10-24

User authorizes housecleaning, committing all pending ANYfem updates and bringing
local/GitHub main up to date. This supersedes the earlier no-publication scope
for ANYfem only. Preserve source behavior, scientific tolerances, evidence and
repository gates. No sibling publication authority is inferred.

Policy: ANY_ECOSYSTEM_RISK_PROPORTIONATE_ENGINEERING_V1 revision 2026-09-24.2,
canonical C:/Github/ANYopenSoft/governance/ECOSYSTEM_PHILOSOPHY.md.
Risk: integrated development delivery to main, no new scientific/release claims.

Plan / acceptance:
1. Inspect all pending tracked/untracked source, tests and docs; retain intentional
   updates and evidence, exclude generated caches/secrets. Fetch and reconcile
   GitHub without discarding work or overwriting remote commits.
2. Repair concrete integrated regressions, including the known headless archive
   GUI boundary; run full repository and applicable Qt checks with bounded
   supervised execution and retained failures. Review meaningful source changes.
3. Commit intended updates and push main after applicable checks/review; verify
   identical local/remote main and a clean working tree. Report blockers honestly.

Initial identity: main 075c4a4, origin/main identical after successful fetch.
48 tracked modified files, 105 untracked files (18 Markdown, 87 Python); no large
binary artifacts. User scope explicitly includes all pending ANYfem work.
Initial state/diff: C:/ANYworkspaceWork/anyfem-houseclean-20261024.

Auth/network: sandboxed gh auth and git fetch were misleading/denied; approved
outside-sandbox gh auth succeeded via keyring and git fetch succeeded.

Dependency boundary: pending ANYfem sketch features import ANYgeometry APIs
whose implementation files are untracked in the sibling checkout. User answered
"Keep delivery limited to ANYfem; report the dependency blocker." No sibling
mutation/publication is authorized. Independent review confirmed the pinned
ANYgeometry `dda8c70` lacks ReferenceLink/SketchReference/capture_reference_link
and reference_links_from_parameters; the latter is imported unconditionally by
Qt construction. A clean pinned GitHub installation cannot start Qt or pass CI
with these unpublished dependencies. Preserve this finding; do not conceal it
by weakening tests, replacing owner physics or silently changing dependency pins.

Worker owns the connected archive-boundary and regression repairs; parent owns
combined review, full checks and Git integration. Prior Claude subscription
service ENOTFOUND cached; OpenAI owner continued, no API fallback/probes.

Cleanup: concrete archive viewer moved from `post/archive.py` to
`ui/archive.py`; the original CLI/session/headless API remains lazy and
compatible. Archive regression and unchanged AST boundary checks: 29 passed,
13.761112 seconds process wall; owned process cleanup verified. New tests cover
headless list/import, lazy dispatch and prepared-resource cleanup on success and
refusal. Parent reviewed the implementation and checks.

Independent Read review found no additional confirmed defect in the selected
geometry/property/mesh/load transactions, undo/redo, Boolean regions, sketch
reference restoration, table editing and captured UI scope. It is a scoped
review, not exhaustive or different-family acceptance. No new file over 1MB or
unexpected untracked path outside src/tests/docs was found.

Full repository and changed-Qt software suites running against current local
sibling sources, with writable temporary directories and one numerical thread.
Evidence is outside source roots. GitHub reproducibility remains blocked by the
explicitly out-of-scope unpublished ANYgeometry dependency.

## Integrated cleanup

In addition to preserving all pending sketch, geometry, meshing, loads, sets,
tables and workbench updates:

- Aligned the solver license-inventory requirement with pyproject/preflight.
- Removed trailing whitespace/extra EOF blank lines from seven Python files.
- Fixed malformed legend separators and updated stale test host interfaces.
- Coalesced source metadata requests on their own channel so header-driven
  control normalization cannot lose playback/inspection continuations.
- Bound each metadata waiter to one terminal callback, including a real request
  regression where one continuation raises; other waiters still settle and the
  original error remains visible.
- Prevented cold archive-header reads in the Qt result browser, retained damaged
  artifact diagnostics, and preserved workbench refresh events raised during
  another refresh.
- Required exact live imported-object/mesh/group identity for imported loads,
  including refusal/no-mutation regressions instead of geometry-ID fallback.
- Inspection reads selected quantity-local rows and retains source provenance;
  benign panel refreshes retain accepted inspection reports. Root-global contour
  behavior was not redesigned.
- Repaired faithful test fixtures, event-driven repaint expectations and bounded
  async delivery waits while retaining numerical/value/identity assertions.
- Mesh quality tests now use canonical owner metrics at the original five
  thresholds and declared junctions. The layout comparison explicitly selects
  the existing layout before comparing it with adaptive layout.

## Retained verification

Exact commands, effective environment, full outputs, JUnit where complete,
precise process-wall duration and cleanup receipts are under
`C:/ANYworkspaceWork/anyfem-houseclean-20261024/<attempt>/`.

| Attempt | Observed result | Process wall seconds |
| --- | --- | --- |
| archive-boundary-01 | 29 passed | 13.761112 |
| result-display-repaired-01 | 68 passed | 22.4065013 |
| qt-lifecycle-repaired-01 | 4 passed | 12.0512027 |
| metadata-repaired-01 | 43 passed | 28.915258 |
| imported-load-repaired-01 | 18 passed | 24.5942869 |
| metadata-real-settlement-01 | 3 passed | 7.5380751 |
| face-snapshot-fixture | 2 passed | 7.141 |
| intersection-method-modules | 32 passed, 2 failed | 36.531 |
| frontend-final-focused-01 | 100 passed, 2 failed | 76.8582483 |
| inspection-final-focused-01 | 52 passed, 1 failed | 23.9896025 |

Overlapping checks are not additive. Earlier failures and diagnostic attempts
are preserved; some passed attempts precede later focused repairs.

The changed-Qt initial run timed out at 600.047 seconds, with no complete JUnit
inventory; owned processes were retired. The full headless profile collected
2,890 tests, then was intentionally stopped after known out-of-scope owner
failures; last captured progress was 47%, process wall 1295.984 seconds, owned
process cleanup verified. `full-01/intentional-stop.json` distinguishes this
from a service failure or timeout. Neither broad run passed. No full rerun was
launched after the concrete dependency boundary was established.

Source license check passes: MPL-2.0, 22 direct dependencies. Initial syntax
inspection parsed 471 Python source/test files without error. Final whitespace,
syntax and Git delivery checks are recorded separately in the evidence root.

Independent source review inspected metadata lifetime/error/cancellation,
deferred retirement, imported authority and refresh reentrancy. Its confirmed
double-settlement finding was returned to the author and repaired with a real
request-path regression. This is scoped review, not exhaustive acceptance or
different-family independence.

## Remaining blockers / source-checkpoint boundary

- Clean pinned GitHub startup/CI needs unpublished ANYgeometry sketch APIs.
- A nonplanar uncharted Coons face cannot supply the owner's required live
  generation-cell publication authority; no authority was fabricated or bypassed.
- Local dirty ANYmesh admits a planar boundary B3 route that its committed
  public quad-first implementation still refuses. The application refusal test
  remains unchanged. Automatic approval review rejected replacing this refusal
  with acceptance because it could weaken admission semantics; no bypass or
  replacement followed that rejection.
- Native cancellation still reaches owner recombination in the focused existing
  test; failure is retained (`native-cancel-diagnosis`). No owner edit authorized.
- Selected-output menu readiness after reopen and selected-quantity versus
  root-global playback/frame controls remain unresolved. Final browser check
  stopped on expected maximum frame 0 versus actual 1. Named/repeated inspection
  provenance checks pass after the narrow inspection repair; this does not
  establish the broader selector/playback contract.
- Initial NURBS fixture errors and the rest of the interrupted full suite are
  not independently resolved or claimed passing.

User explicitly retained ANYfem-only publication scope and requested all pending
updates committed with local/GitHub main current. Delivery is a **source
checkpoint**, not release, green CI, installed-artifact acceptance or scientific
requalification. All intentional pending source/tests/docs are included; failed
evidence and sibling repositories are preserved. Final commit, remote identity,
branch and clean-tree observations are saved in the external `delivery.json` so
the commit need not embed its own hash.
