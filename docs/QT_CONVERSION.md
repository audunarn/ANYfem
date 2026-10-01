# PySide6 conversion — living task record

Replace Tk while preserving supported engineering workflows, project/result
formats, numerical behavior and headless entry points on Windows and Linux.
**Full parity is not accepted.** Tk remains the default. No release, Qt default
switch or Tk removal has occurred.

Policy: `ANY_ECOSYSTEM_RISK_PROPORTIONATE_ENGINEERING_V1`, revision
`2026-09-24.2`, canonical ANYopenSoft philosophy. Preserve owner scientific
predicates, failed evidence and execution/resource restrictions. A focused pass
or a deferred hardware gate does not establish complete acceptance.

## Current implementation and evidence

ANYfem owns shared workflow services and Qt presentation; ANY3dView owns the
retained viewer. Widgets invoke existing commands/services. Each window has one
WorkbenchController. Qt Widgets provide the central viewport, project/task docks,
job/log panel, menus, shortcuts and status. GPU presentation uses QOpenGLWidget;
the shared viewer supplies a software fallback with explicit failure diagnostics.
PySide6 remains optional for headless use. Temporary launchers are `anyfem-qt`,
`anyfem-tk` and `python run_gui.py --qt`; `anyfem` remains Tk.

Published ANYfem main is `892507a3bcca98b73cbfdbd28f5906c2687774b3`.
The isolated integration branch is `codex/qt-coordinated-integration`; this
delivery is based on `ba3e5f4a2de998decc6e823ec28292a2801e21a3`. The current
slice brings the Boolean-region picker into parity with Tk.
Candidate viewer input is exactly `d65f47b264f3e28348a6309eea996db51d8bb901`.
ANYgeometry input is `26e7e3c98ac1a5573e19643d6658d00094bff0bc` (0.4.5).
Published owner main `db2947e02cb4fe243c52b13d6bec262b4f897378` has no runtime
`src/anygeometry` delta from that tested input. Owner publication stays with its
owner; unrelated dirty work is preserved.

Current picker candidate installed wheel SHA256:
`a7d2a03993803509fa50da60868a293bf30709480b39a6d75e596f3f3fee1481`.
Both packages' installed origins are asserted under Python `-I`, with pytest
source-path injection disabled. Python/PySide6: Windows 3.14/6.10.3 and WSL Linux
3.12.3/6.10.3. WSL uses XCB/llvmpipe OpenGL and is not physical GPU evidence.

| Current evidence | Windows | WSL Linux | Scope |
| --- | --- | --- | --- |
| Previous ba3e5f4 complete installed Qt suite | 152 passed | 152 passed | Explicit requested GPU backend activation, real windows, no skips/errors/failures |
| Previous ba3e5f4 installed headless contracts | 96 passed | 96 passed | Persistence, binding identity, rollback, hash/cache invalidation, original two repaired CI cases, frontend import boundaries |
| Current installed picker checks | 4 passed | 4 passed | Boolean creation, renamed/hidden scopes, stable selection, imported mesh labels, requested backend activation |
| Wheel/sdist checks | Passed | Same wheel | Safe artifacts, licensing/notice contents; no release |
| Independent changed-slice source review | Findings repaired | Portable code | No remaining concrete defect found; no final full-parity acceptance claim |

Complete logs/JUnit, input snapshots, wheel/sdist and failed attempts are retained
in the primary checkout's ignored `reports/qt/integration-pr10/`. Final evidence
is `raw-scope-final-{win,linux}-{headless,qt}` and `raw-scope-final-summary.json`.
The current focused evidence is `operand-installed-{win,linux}`; wheel/sdist are
in `operand-dist`. The initial no-isolation build lacked setuptools; the declared
isolated build succeeded. No scientific matrix was repeated for this widget edit.
Older `raw-delete-*` builds/runs are intermediate evidence. The failed Qt delete
run exposed revision serialization outside rollback; it is not superseded by
pretending that deletion-save is supported.

Current repairs bind raw Imperfection/owner Refinement authored references to
exact singleton feature-output regions. Optional project data persists that map;
staging and undo retain it; orphan keys are pruned before replay/persistence.
Only these raw record types consume it: loads/supports/masses keep their own
scopes even when they share the authored cache. Canonical binding overrides raw
lineage during deletion. Detach/undo restores the exact binding, and unrelated
inactive/orphan raw intent cannot block another deletion. Binding-only physical
changes participate in model hashing and invalidate mesh cache; label-only
changes remain presentation edits. Immutable snapshots retain their submitted
scope. Numerical node order, values and owner models are preserved.

Same-window reopen reuses its owned lock; failed reopen preserves the current
project and lock. Revision serialization now occurs inside the transaction's
rollback boundary before revision/cache publication. Owner refusal restores the
project, history, selection and caches; the window remains usable and saveable.
Nested transactions retain one outer revision. Review found and repaired stale
map deletion, hash omission and canonical-alias precedence defects.

## Workflow parity and acceptance ledger

Implemented controls and passing representative cases are evidence of that scope,
not qualification of arbitrary geometry or numerical models.
Unsupported coordinate/material transformations and constitutive models retain
their owner diagnostics in both frontends; GUI coverage cannot broaden them.
The original Tk inventory remains `reports/qt/tk-task-surface.txt`. Complete historical counts,
failures and earlier feature-by-feature investigations are preserved in the
archive linked below.

| Requirement | Current evidence | Open requirement |
| --- | --- | --- |
| Shared open/save, locks, recovery, undo/redo, replacement/shutdown | Shared services; installed lifecycle and revision-refusal checks | Final complete-workbench review |
| Geometry, generators, sketches, materials/sections, loads/constraints | Typed controls, ID-preserving edits, construction selection, dependent-feature and raw suppression roundtrips | Edited sketch support suppression and selected feature-output deletion persistence (owner defects below) |
| Mesh controls, preview/recovery, cancellation | Four routes, inspection/admission diagnostics, commit/discard, stale/cancel/replace checks | Integrated owner acceptance and existing scientific limits |
| Analysis controls and solve lifecycle | Nine public analyses; snapshot/case routing, cancel/edit/replace, retained stale results | Free-modal scientific failure and complete scientific acceptance |
| Results and visualization | Stored/live fields, tables/histories/probes, display scale, FPS, clipping, PNG/GIF/CSV/Markdown/HTML, sidecar identity and provenance | Final review of the complete supported surface and physical Linux GPU acceptance |
| Imports/exports, scripting/navigation | SESAM/CalculiX and neutral FEM/SIF/INP/DAT/FRD workflows, unknown-preserving export, async failure/replacement, scripting/recording/palette/docks/shortcuts | Final integrated acceptance; bounded preview tests are not scalability qualification |
| Headless/installed compatibility, licensing | Import-boundary checks, installed wheel/sdist, unchanged 18-dependency MPL inventory | Clean final switched artifact once acceptance permits switching |
| Default and retirement | Temporary explicit dual launchers | Resolve every gate, independent final review, then switch defaults and remove Tk/ANYtk3D from ANYfem; update guidance/notices/CI |

## Concrete unresolved failures

1. **ANYgeometry suppression after parent edit.** Owner-only public API script
   creates a plate and extruded dependent sketch, edits plate length/origin, then
   suppresses the plate. Regeneration refuses with unresolved historical edge/
   vertex descendants. Undo/redo and save/reopen are not required to trigger it.
   Unedited suppression succeeds; all edited variants fail atomically on both
   installed platforms. The Qt combined failure is retained. No consumer store
   patch, lineage deletion or proximity workaround is permitted.
2. **ANYgeometry selected output deletion persistence.** After explicitly removing
   a singleton Sheet/empty Part and its generated face using public APIs, owner
   serialization refuses a missing active feature output. The owner-only script
   reproduces this too. ANYfem now rolls this refusal back safely; it does not
   claim a passing deletion-save workflow. The persistent public contract for
   selected output deletion, including compound features, is still required.
3. **Scientific free-modal outcome.** Completed full main run `36880776026` runs
   1212 tests per cell with 130 skips. Seven cells fail the two attachment cases
   repaired by candidate `8abf590`; Linux Python 3.13 additionally reports four
   rigid-body modes instead of the unchanged required six. Runtime there is
   Python 3.13.15, NumPy 2.5.3, SciPy 1.18.1, solver input
   `5ca31de9be3ca3ffa70b09612a5d98c0ef212a5b`. All eight separate verification
   reports pass 21/21; that inventory does not subsume the failed integration
   test. No tolerance or assertion has been weakened.

Owner-only reproduction and inputs:
`reports/qt/integration-pr10/reproduce_sketch_suppression.py`,
`owner-feature-lifecycle-final-{win,linux}/`. Forwardable owner requests are
`ANYgeometry-owner-request.md` and `ANYsolver-modal-owner-request.md` in the same
ignored evidence directory. Nothing has been messaged to an owner task.

## Next actions and delivery constraints

Current bounded surface audit asks whether any explicitly invoked Tk command
lacks a usable Qt or shared-workflow route, loses a required field, or has only
syntactic exposure. Compare the source inventories, trace differences to actual
controls and validate any concrete gap through a real window. Invocation presence
alone cannot establish interaction or scientific parity. The source audit found
shared/Qt routes for all 54 commands explicitly referenced by Tk; its apparent
CompositeCommand difference uses the shared batch wrapper. A concrete picker gap
is repaired: hidden regions are excluded, names/domain/kind/mesh labels refresh,
and selection follows stable IDs through list changes. Same-ID label changes
update existing Qt items. Real-window checks include hiding a selected operand
and creating the resulting union after restoring explicit selection.

The previous candidate `36907655926` on `ba3e5f4` completed successfully in all four
Windows/Linux Python 3.11/3.14 Qt cells. Publish the coalesced reviewed current
slice on the isolated branch and run its candidate matrix once. Exact delivery
commit and latest run handle are recorded in the retained
`reports/qt/integration-pr10/shared-feature-checkpoint.json`. Inspect that
same handle; never restart a live job because an observation timed out. Do not
repeat the unchanged expensive scientific matrix for each small frontend edit.

Obtain owner corrections/public API guidance for the two geometry cases and
adjudicate the modal failure under existing owner authorities. Retest exact
published inputs with the retained reproducers, installed workflows and applicable
scientific gates. Independently review complete lifecycle/persistence/viewer
integration and the supported Tk surface before default switching.

The user explicitly deferred physical Linux GPU acceptance because only WSL is
available. Preserve it as an open gate; WSL cannot close it. No Qt default switch,
Tk removal, package release, force push or branch-protection bypass is allowed by
passing candidate checks. Main and owner working-tree changes remain preserved.

## Preserved history

[Conversion history through d47d42a](archive/QT_CONVERSION_HISTORY_2026-10-01.md)
is a frozen archive of the previous 1195-line living record. Its Git-normalized
blob exactly matches `d47d42a:docs/QT_CONVERSION.md`:
`b92b1866926715115acd3ac2ef34284dc939bca7`. Working-file SHA256 before checkout
line-ending conversion: `a0b29b688b8af97c43929934ca2e7a869784c55889eb4392c768249e65d5a428`.
It retains every superseded result, failed experiment, review and procedural
transition. Historical proposals are evidence, not renewed execution authority.
This file is the single current conversion note.
