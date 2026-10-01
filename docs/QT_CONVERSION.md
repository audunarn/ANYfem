# PySide6 conversion — living task record

Outcome: replace Tk after supported-workflow parity, installed-artifact checks,
Windows/Linux acceptance and independent review. Publication is separate.

Policy: `ANY_ECOSYSTEM_RISK_PROPORTIONATE_ENGINEERING_V1`, revision
`2026-09-24.2`, canonical ANYopenSoft governance philosophy. Existing numerical
criteria and execution authorities remain unchanged. Use integrated slices and
focused tests, with one record rather than separate administrative dossiers.

Starting identity: ANYfem `951fe61`. Preserve pre-existing changes to
`solve/build.py`, `solve/run.py`, E4 routing tests, quad-first gate and compatibility
candidates. ANY3dView has pre-existing untracked compatibility/evidence work;
do not modify it.

Principal uncertainty: can the shared retained viewer and existing workflow be
hosted by Qt without importing Tk or changing document/job semantics? First
experiment: Qt software/GPU host plus shared project lifecycle and a complete
small engineering workflow. Failures determine the next implementation change;
offscreen tests are not GPU or cross-platform acceptance.

## Parity checklist

Checked entries identify implemented, bounded-tested slices. Unchecked entries
remain full acceptance gates; they do not mean the corresponding Qt paths are
absent. The workflow ledger below separates measured coverage from open gates.

- [x] Shared project lifecycle: open/save, sidecars, locks, recovery, undo/redo
- [x] Qt shell, ports, explicit launcher, docked model/task/job views
- [x] ANY3dView Qt GPU/software hosts, shared camera and selection
- [ ] Geometry, material/section assignment, loads and constraints
- [ ] Mesh generation, preview/recovery and safe cancellation
- [ ] Analysis controls, asynchronous solve, stale/partial result handling
- [ ] Retained results, charts, animation and visualization
- [ ] Imports/exports, scripting, command palette and remaining modeling tools
- [ ] Windows/Linux desktop acceptance and installed-artifact checks
- [x] Independent review of the implemented lifecycle/persistence/viewer slice
- [ ] Default switch and Tk retirement

## Evidence and next action

Legacy/open-failure slice (2026-10-01): owner-codec format-2 coverage alone
does not prove Qt replacement, tree/selection or lock behavior. Exercise a
real format-2 project through open, edit/undo, save and reopen; invoke the
actual Open action for malformed JSON, missing headers and future formats
while a dirty document owns a lock. Require unchanged document/history/
selection and current lock, explicit diagnostics and released attempted-file
locks. These are compatibility/lifecycle checks, not numerical qualification.

Result: four installed Qt cases pass on Windows GPU and WSL XCB OpenGL
(`legacy-installed-windows-final` and `legacy-installed-linux`, logs/XML).
Format-2 identity, next-ID allocation, tree selection, edit/undo/redo and
current-format save/reopen pass. All three rejected file types preserve the
dirty current document, revision, stack, selection and held lock; attempted
locks are released and status/log diagnostics are visible. The first test
incorrectly expected a modal dialog instead of the existing status/log error
contract; retain `legacy-installed-windows` as test-setup failure evidence.
No production changes or numerical acceptance follow. Duplicate predecessor
push run 36873948341 was cancellation-requested after confirming all eight
jobs active on the identical 2b4514e head; original 36873947386 and newer
36880776026 remain active. Cancelled work is not a pass or measured saving.

Visual audit slice (2026-10-01): installed Windows GPU captures at 1400×900
and 1024×768 showed a restored layout leaving only a 160-pixel viewport at
the smaller size. The initial capture's default-format settings redirection
was ineffective; replace it with an explicit isolated settings file. Check
whether explicit initial dock
sizes preserve a useful viewport while retaining user-restored layouts. Run
focused docking/layout checks and repeat the installed captures; this does
not change numerical or physical Linux GPU acceptance. The user explicitly
defers physical Linux GPU testing because only WSL is available.

Result: Model/Tasks start at 220/360 pixels and Jobs/Messages at 140 pixels
high, before restoring user settings. The isolated installed Windows capture
has a 436×561 viewport at 1024×768 (812×693 at 1400×900), active GPU and no
backend diagnostics. Two focused installed widget tests pass on Windows GPU
and WSL XCB OpenGL: useful initial viewport space, saved dock width/visibility,
selection, shortcut and floating/redocking behavior. Numerical inputs are
unchanged. Evidence: `reports/qt/integration-pr10/layout-installed-{windows,linux}`
logs/XML and `layout-isolated-{windows,linux}` captures. Preserve earlier audit
and packaging setup failures; neither was a production test failure. Current
scientific and hosted Qt run handles remain 36880776026 and 36880882726;
neither pending run establishes acceptance.

Coordinated Qt integration resumed (2026-10-01): user authorizes continuation
after PR10 merge `e4fa3b4149ae96a9a29f9a3579bb20a70d5d5732`. ANYgeometry
0.4.5 is published; the missing-owner API gate is superseded. Use the exact
merged inputs: geometry `26e7e3c98ac1a5573e19643d6658d00094bff0bc`, mesher
`e21c0fc93662776762430e14450d54ac9192e2e8`, solver
`5ca31de9be3ca3ffa70b09612a5d98c0ef212a5b`, and reviewed Qt viewer
`64b39d5f45ca01c4dfd972af5a4acf592ffccad7`. Align candidate CI with main's
owner inputs and reuse the efficient Qt runner. Run the complete Qt workbench
scope once per relevant Windows/Linux backend, retaining actual failures;
repair application integration failures within this slice. Preserve the
Python3.11 free-modal test failure (5 versus 6 modes) in PR10's Windows and
Linux cells as scientific acceptance evidence; do not relax it. Other cells
are still pending. Use an isolated checkout/environment to preserve concurrent
owner work and the running WSL app. No default switch, Tk removal, package
release or physical Linux GPU acceptance is inferred.

Coordinated local integration evidence (2026-10-01): Windows Python3.14 real Qt
software and GPU workbench scopes each pass 136 cases. The newly added
requested/active-backend capture assertion passes separately on both Windows
backends. Linux Python3.12 real XCB scopes pass 137 cases on each backend,
including that assertion. Exact installed-package GPU mesh/solve/save/reopen
and backend tests pass two cases per OS with `-I`, empty pytest `pythonpath`,
and module origins under the isolated environment. Shared viewer host/neutral
contracts pass 32 cases per OS. Installed licensing passes on each OS; Windows
installed headless imports load neither Qt nor Tk. Evidence and exact owner
snapshots are under `reports/qt/integration-pr10/`; the installed Linux runtime
is `reports/qt/wsl/20261001T131220950719Z/` in the integration checkout.
It reports XCB, active GPU backend, successful capture/shutdown, and llvmpipe
with acceleration disabled. This is Linux OpenGL integration, not physical GPU
acceptance. Retain initial environment/selector/harness failures; an in-flight
edit of the disposable Linux harness caused a post-test shell parse error,
while the runner's complete GPU report records 137 passed with exit0. The
repaired harness passes syntax/startup checks. Independent source review found
no blocker and prompted the concrete backend assertion; it does not close the
final full-parity review. Hosted Qt coverage is the next check; the original
scientific/modal gate remains open.

First hosted Qt run `36867521739` passes Linux Python3.11/3.14 and Windows
Python3.14. Windows Python3.11 passes 136 cases and fails the automatic-budget
test while waiting for `incomplete`. The test combined an unsupported beam-only
quad-first input with a one-picosecond wall budget. A controlled coarse-clock
probe reaches the real owner refusal, `quad-first requires at least one selected
face`, rather than budget expiry. The repaired consumer test uses a valid plate
and a controlled clock crossing the unchanged owner deadline; it asserts the
same incomplete/error/admission semantics. A companion plate case retains an
admitted outcome while clock observations remain within budget. These are
application-policy wiring checks, not wall-clock/performance qualification;
no owner code, budget policy or scientific tolerance changes. Windows and Linux
affected tests each pass two cases; independent source review finds no blocker.
The counterexample proves the test premise was timing-sensitive; the exact
hosted terminal diagnostic was not recorded, so its cause is not fully measured.
Hosted rerun follows. Keep the original hosted
failure and local probe/temp-path failures in `reports/qt/integration-pr10/`.

Hosted rerun `36870078531` on candidate `398d49a` passes all four Windows/Linux
Python3.11/3.14 jobs. Each software scope passes 138 tests with no skips; each
Linux OpenGL scope also passes 138 tests with no skips. Shared Qt host checks,
installed licensing and headless boundaries pass in the same jobs. Downloaded
artifacts are retained under `reports/qt/integration-pr10/hosted-passed/`.
No unchanged matrix rerun is needed for this evidence-only record update.
The user has only WSL available and explicitly defers physical Linux GPU
acceptance to a future suitable machine; the gate remains open. Scientific
acceptance, complete published-artifact parity and final independent acceptance
review remain open. Keep Tk as the default and publish no package release.

Parity closure audit (2026-10-01): compare supported Tk controls with current Qt
controls and measured tests to identify concrete omissions behind broader ledger
entries. Independently inspect source without rerunning unchanged suites.
For the open free-modal case, discriminate frontend counting from owner result
classification: run its unchanged plate/mesh/modal input once per existing
isolated Windows3.14/Linux3.12 installation, retaining raw mode frequencies,
rigid flags/correlations and original physical predicates. This bounded
diagnostic does not repair owner mathematics, consume a frozen capacity gate,
relax scientific tolerances or qualify either implementation. A mismatch between
wrapper count and owner flags warrants a consumer fix; matching flags with a
failed invariant warrants exact owner evidence, not a consumer override.
The unchanged diagnostic passes all four original predicates on Windows3.14
and Linux3.12; wrapper and owner each report six rigid modes. Raw frequencies,
correlations, flags, residuals and runtime versions are retained in
`integration-pr10/modal-windows.json` and `modal-linux.json`. This does not
reproduce or close the hosted Python3.11 failure.

Independent Tk/Qt surface audit identifies two concrete construction omissions:
length inputs bypass project units, and created/edited sketch extrusions do not
select generated faces. Three new real-window cases reproduce the original
failures (`construction-before`). All dimensional construction fields now use
the shared unit parser; explicit SI defaults preserve their physical size, labels
show the active unit, and editing formats existing extrusion in that profile.
Raw constraint records remain owner SI. Successful create/edit selects the exact
extrusion output faces, enabling selection-based downstream loading. Failed
preview/command publication remains atomic. Source review finds no additional
defect. The built wheel passes all fourteen affected construction/sketch cases
on installed Windows GPU and Linux XCB OpenGL, with module origins under the
isolated environments and pytest source injection disabled. New cases include
bare/explicit millimetres, exact SI persistence/save/reopen, output selection,
follow-on pressure and undo/redo. Intermediate test setup failures (multiple
selection, missing pressure value, wrong save helper) are retained separately;
they are not new application failures. Evidence is under
`reports/qt/integration-pr10/construction-*`. Broader combined sketch constraints
and dependent-topology combinations remain coverage questions; scientific,
physical Linux GPU and final complete-parity acceptance remain open.

Next bounded parity evidence: exercise a successful open sketch with automatic
boundary vertex/edge anchors plus distance and coincident closing-endpoint
constraints through actual Qt controls, then persist/edit/undo/reopen its owner
definition and exact topology identity. Separately edit a generated support
through the Qt feature form with a dependent sketch, section, pressure and
support attached; verify owner output lineage, records, undo/redo and portable
reopen. These checks resolve specific ledger questions, not unrestricted owner
geometry/scientific qualification. Preserve refusal evidence and do not repair
an owner failure by dropping an attachment or constraint.
The initial compatible-combination/length-only dependency cases pass two
installed checks per OS. Independent review identifies under-discrimination:
already-satisfied constraints do not prove adjustment; parent growth alone does
not prove replay of an interior descendant; attachment refs do not prove exact
engineering values. Strengthen the fixture by translating the parent and
checking descendant coordinates plus exact values. This reveals the same
installed Windows/Linux failure: a legacy support reference still names deleted
vertex5 during post-commit viewport refresh (`dependent-translated-*`). Before
repair, inspect the actual owner replacement/region resolution for this input;
valid owner lineage with stale consumer refs warrants atomic shared attachment
rebinding, while missing lineage remains an owner refusal. Preserve UUIDs,
canonical regions, quantities and coordinate systems; undo/redo must restore
geometry and attachments together.
The headless lineage discriminator confirms valid unique owner outputs:
support5 resolves to17 and pressure2 to7 (`dependent-lineage.json`), while
consumer compatibility refs remain old. Shared staging/binding helpers now live
in `model/feature_bindings.py`; both feature commands and public project
regeneration use them. Detached containers preserve canonical regions, section
bindings, record UUIDs, values, constraints and coordinate systems. Empty or
ambiguous missing representatives refuse before publication; an unchanged valid
representative in a wider canonical region remains one record without duplication.
Section suppression preserves inactive intent and its existing mesh/solve gate.
Commands snapshot geometry and attachment state together for undo/redo. Public
regeneration protects entry state; prior caller edits to feature intent occur
outside that call. No owner code or scientific tolerance changes.

Source contracts pass64 cases on Windows before final record-kind additions,
and68 on Linux; all nine final attachment contracts subsequently pass in the
installed wheel on each OS, including every supported attachment record type,
non-global coordinates, both APIs, exact logical undo with validated checksums,
and empty/ambiguous refusal. Owner revisions and allocator high-water marks
remain monotonic; tests validate the actual checksum and compare all other
persisted design data. The complete real-window Qt scope passes143 cases on
Windows GPU and Linux XCB OpenGL, with no skips/failures. Independent final
source review finds no consequential defect. Built-wheel backend capture,
combined-constraint persistence and translated-dependent edit/load/support/
section/undo/reopen pass three cases on each OS under `-I`, no source injection,
and verified installed origins; headless imports load neither Tk nor Qt.
Evidence is `integration-pr10/feature-*` and `dependent-*`. Initial selector,
test setup/monotonic-state assertions and repaired section-suppression/refusal
checks remain retained. Final frozen-artifact binding follows the small public
API adjustment that captures the staged snapshot before publishing geometry;
successful command/Qt inputs are unchanged. Scientific and physical Linux GPU
acceptance and final complete-parity review remain open.
Frozen wheel from source `e75de5599e09fc8e4b21f32ecc85ee30bda58237` has SHA256
`1bec0c3d18e4982344d249592c723e918f21f512829134a3df2e00ad3e4502f2`.
Its final installed checks pass nine attachment/headless contracts and three
real Qt/backend checks per OS (`feature-frozen-*`). The tiny public-API snapshot
ordering is covered by these final contracts; command/Qt production inputs are
unchanged from the complete143-case source runs. Superseded own main CI run
`36871722907` is cancelled to release runner capacity; it establishes no
scientific pass. PR10/main owner run `36863904261` remains untouched. Two push
runs for the same2b4514e SHA/event/workflow are observed (`36873947386` and
`36873948341`); do not infer independent qualification or elapsed savings from
those queued/partial runs. The current scientific and hosted Qt outcomes still
require their terminal reports.

Implemented candidate: shared `WorkbenchWorkflow`, `SceneViewport` and job-worker
facade; Tk consumes the extracted layer. Qt owns one controller per window,
model/view tree and job table, docks, forms, selection policies, shortcuts,
menus, command palette and snapshot scripting. ANY3dView owns the shared retained
viewer engine and explicit Qt GPU/QPainter hosts. Qt resolves GL functions from
each widget's context, uses its current framebuffer for scene/HUD, and restores
retained resources after context replacement. Runtime fallback preserves scene
state and reports its cause. Neither the Qt frontend nor base/headless imports
load Tk.

The first engineering slice creates a beam cantilever, assigns its section,
constraints and load, generates a mesh from the Qt form, solves asynchronously,
displays results and saves/reopens its mesh/result sidecars. Additional checks
cover command edits, construction, workplanes, face sketches, playback, scripting,
read-only locks/Save As, autosave recovery, dirty Open cancellation, pending writer
ownership, viewport/tree selection, undo shortcut, docking, solve cancellation,
stale completion and replacement during a solve. Stale results remain retained
without replacing the active model; their artifacts identify the submitted mesh.

All logs are in ignored `reports/qt/`; early failures are retained. Evidence as of
2026-09-30:

| Check | Result | Evidence |
| --- | --- | --- |
| Windows real Qt GPU workflow/actions/recovery/ownership | 15 passed | `windows-ownership-recheck.log` |
| Linux WSLg real Qt GPU workflow/actions/recovery/ownership | 15 passed | `linux-ownership-final.log` |
| Windows viewer + contract checks | 32 passed | `ANY3dView/reports-qt-reviewed-final.log` |
| Linux real GL viewer, missing-GPU/fallback, picking and 150% scaling | 6 passed | `linux-viewer-dpi.log` |
| ANY3dView complete local suite | 175 passed, 14 skipped | `ANY3dView/reports-qt-suite-final.log` |
| Headless verification, frontend boundary and mesh snapshots | 35 passed | `headless-final.log` |
| Tk lifecycle/navigation in shared layer | 10 passed | `tk-services-final.log` (background tests skipped in this combined process) |
| Tk background mesh/log tests in separate process | 10 passed | `tk-background-isolated.log` |
| Shared persistence/artifacts and Tk background checks after stale-result fix | 27 passed | `shared-persistence-recheck.log` |
| Candidate wheel/sdist builds | Both repositories pass | `build-final-artifact.log`, `ANY3dView/build-final-notices.log` |
| Installed Windows and Linux candidate GPU mesh/solve/save/reopen | Passed; imports verified from environment site-packages, no Tk | `installed-final-artifact.log`, `linux-installed-final-artifact.log` |
| Source licensing inventory | Passed: MPL-2.0, 18 direct dependencies; optional PySide6 LGPL route recorded | `licenses-final.log` |

The broad ANYfem run had 926 passed, 127 skipped and seven failures
(`reports-qt-regression.log`). Two frontend extraction failures were repaired and
rechecked: old mock injection paths and a legacy ANYtk3D fallback in the shared
presenter. Five remaining failures concern four intersection/quad-first scientific
mesh cases and the launcher test's exact ANYmesher 0.5.0 expectation against the
local 0.5.1 checkout. They are outside the edited frontend code and remain open;
no clean baseline run or scientific acceptance is claimed. Dirty solver changes
and compatibility candidates are preserved.

Independent bounded source review found and closed dirty Open handling, Qt HUD
framebuffer/alpha composition, lock release during pending writes, software
visibility/clipping/HUD, context-recreation failure, animation restoration and
late-result ownership issues. It is review of this candidate slice, not acceptance
of every supported workflow. No reviewer edited implementation or ran tests.

`qt-tests.yml` adds manually dispatched Windows/Linux candidate coverage with an
explicit ANY3dView candidate ref and retained JUnit evidence. It has not been
dispatched or qualified remotely. Linux WSLg checks use real Wayland windows and
OpenGL contexts; they do not constitute every supported Linux desktop/driver.
Installed Windows checks used an NVIDIA GeForce RTX 3090 Ti; installed Linux
checks used Mesa llvmpipe (software OpenGL), not a physical Linux GPU. Workbench
images are `workbench-installed-win32.png` and `workbench-installed-linux.png`.
The disposable Windows installation has an immediately usable launcher at
`reports/qt/install-env/Scripts/anyfem-qt.exe`.

## Next bounded slice and switch gates

### Full-parity continuation (2026-09-30)

User explicitly required full parity. This continues the adopted conversion;
it does not waive any acceptance gate or authorize publication. The principal
question is now whether supported Tk interactions have equivalent usable Qt
paths. Competing explanations for apparent coverage: (a) a command exists but
its record/configuration cannot be entered correctly; (b) the command is usable
but selection, identity, result provenance or lifecycle integration is missing.
The bounded experiment pairs each added interaction with real-window tests,
then repeats the changed slice on Linux. Failures determine implementation
changes; command presence alone never closes a checklist entry.

The Tk task inventory is retained in `reports/qt/tk-task-surface.txt`. This
behavior-level ledger supplements the high-level acceptance checklist. “Tested”
here means the stated representative cases; it does not imply unrestricted
scientific qualification of every geometry, driver or numerical owner.

| Workflow | Qt implementation and measured evidence | Remaining acceptance |
| --- | --- | --- |
| Geometry and generators | Typed primitive/topology forms; all eight plate/bulkhead/frame/girder/stiffener/panel/cylinder/cone generators; eleven copy/pattern/sweep/split/orientation actions and five triangle/hole/join/overlap actions exercised with undo | Broader dependent-topology editing cases |
| Sections, materials, supports and loads | Stable-ID tree edits, undo, DNV presets, project units and explicit suffixes, active cases/combinations, imported source-group references | Broader combinations of record editing and topology replacement |
| Feature/sketch work | Exposed feature topology, bounded search, suppression/rename undo, distance/coincidence constraints and extrusion editing | Remaining sketch constraint combinations and dependency failure presentation |
| Definitions and workplanes | Coordinate systems including ndarray origins, selection/boolean mesh regions, output requests, custom units, snapping/construction; native scalar reduction, selected-frame identity, batch/global/guarded-patch and committed nonlinear stress views | Scoped views have bounded installed save/reopen/export evidence. Material/named-coordinate transformations remain unsupported intent in both frontends; broader dependent-region editing and final owner integration remain open |
| Mesh generation | Four mesh routes; typed native/structured/quad/quality/automation controls; pins/refinements; preview commit/discard/undo; inspection-only refusal, automatic budget exhaustion and strict owner refusal | Recovery-policy outcomes have candidate window evidence; clean installed geometry-to-mesh acceptance awaits published owner integration |
| Mesh lifecycle | Real-window cancellation, stale completion, replacement and injected failure on installed Windows/Linux | Independent final integration review |
| Analyses | All nine public analyses submitted through Qt controls; load case/combination routing and detached imported snapshots | Owner scientific gates remain unchanged and unresolved failures remain visible |
| Solve lifecycle | Cancel/edit/replace during held solve; stale retained mesh identity; project locks/recovery/pending writers | Partial outcome limits must remain explicitly distinguished from verified capacity |
| Engineering interpretation | Nonlinear last/peak/target/failed trial, stop reason, prescribed path; saved-frame details; submitted-input provenance; material capability versus retained plastic-state evidence; imported constitutive behavior unavailable | Reviewed slice, not numerical qualification |
| Retained results | Saved/live playback, fields/tables/histories/point/line/probe, color limits, PNG/GIF/CSV/Markdown/HTML; sidecar mesh identity independent of current mesh; committed stress histories and repeated-coordinate provenance | Additional imported-format/legacy-artifact interactions and broader nonlinear/capacity model coverage |
| Imports/exports | SESAM model/group solve/save/reopen and stress-only import/save/reopen; CalculiX deck/result retention; async neutral FEM/SIF/INP/DAT/FRD inspector; missing-file/retry, owner malformed diagnostics, replacement/close during reads and bounded-preview/full-canonical export | Broader malformed/legacy/full-size file coverage remains open; the 5001-record check establishes preview/export correctness, not scalability |
| Scripting and navigation | Output/recording/atomic commit/cancel/replacement, diagnostics, recent files, command palette, docks/shortcuts/selection synchronization | Final visual/usability inspection |
| Headless compatibility | No Tk/Qt in headless imports; verification/frontend boundary/result provenance checks | Recheck only for further changed shared inputs |

Latest measured evidence (all logs under ignored `reports/qt/`):

- `parity-source-final-fixed.log`: 132 passed across real Qt workflows, scene,
  interop, imported persistence, layout commit and frontend boundaries.
- `parity-outcomes-gpu.log`: 64 passed after outcome/provenance presentation.
- `parity-review-fixed.log`: 13 passed after independent-review corrections.
- `parity-geometry-tk-boundary-fixed.log`: 16 passed for modeling undo, mesh/load
  invalidation, Tk native-backend mapping, disposable recovery and line sampling.
- `parity-headless-current.log`: 66 passed including verification, neutral import
  boundaries, nonlinear interpretation and authenticated result parsing.
- `installed-parity-current-win.log` and `linux-installed-parity-current.log`:
  77 passed each from installed packages; real Windows NVIDIA and WSLg Wayland
  Mesa OpenGL windows. Pytest `pythonpath` disabled, source paths not injected.
- `installed-mesh-lifecycle-win.log` and `linux-installed-mesh-lifecycle.log`:
  four additional installed cases passed each: cancellation, stale completion,
  replacement and generation failure. Early fixture failures are retained.
- Candidate wheel rebuilt successfully; no publication or remote CI dispatch.

Independent bounded review found and closed load-only mesh invalidation and
unsupported constitutive claims. Loading hashes use persisted load inputs,
excluding runtime callbacks; load edits clear solutions while preserving a valid
mesh. Imported result hashes identify the exact streamed bytes parsed, even if a
producer replaces its source during parsing. Imported solves use thawed project
and embedded source snapshots, avoiding immutable-geometry deepcopy failures.
Active mesh restoration uses explicit metadata rather than UUID registry order.

The last display cleanup clears obsolete result/solver presentation on project
replacement and labels retained datasets current/stale after live invalidation.
Previews track job/result identity, so automatic activation cannot leave another
job's table/history in the new result. Independent review closed that finding.
Neutral joined-sheet topology now renders authoritative planar boundaries;
concave quad triangulation is boundary constrained and retains exact XYZ vertices.
Review closed the concavity finding; `parity-neutral-concave-scene.log` has 40
passed, including reflex vertices at positions 1 and 3 and area/coverage checks.
Returning to Mesh preserves the inspection-only candidate display; Solve stays
blocked and gives explicit admission diagnostics.

Final rebuilt installed candidate: `installed-parity-display-final-win.log` and
`linux-installed-parity-display-final.log` each have **129 passed, one failure**.
The failure is `test_coplanar_diagonal_beam_is_connected_and_drawn_continuously`:
structural preparation rejects a generated face with an unbounded topology-backed
Coons surface, before display/Qt is reached. `source-diagonal-isolated.log` and
`installed-diagonal-isolated.log` reproduce the same failure without Qt. The
previous candidate had 128 passing checks before the last two interaction cases;
those earlier passes do not supersede the final failure. Current geometry/mesh
owners contain separate dirty work; it was read, not modified. Do not infer its
cause or waive scientific acceptance from frontend passing counts.

`installed-boolean-output-win.log` and `parity-inspection-only-fixed.log` each pass
the new definition/admission interaction; they are included in the final batch.
`installed-inspector-formats-win.log` and `linux-installed-inspector-formats.log`
each add four passed cases for INP/DAT/FRD/SIF, checking actual deck counts,
buckling factors, displacement counts and stress record counts. FEM inspection
and unknown-preserving canonical export were already covered.
License inventory remains MPL-2.0 with 18 direct dependencies; diff checks pass.
The resulting local wheel is `dist/anyfem-0.4.1-py3-none-any.whl`.

The full conversion is **not accepted as complete**. Tk remains the default;
`anyfem-qt` and `python run_gui.py --qt` select the candidate. `anyfem-tk` remains
explicit. The `gui` extra includes PySide6 with transition Tk dependencies.

Next bounded experiment: scoped output filtering, remaining recovery-policy
outcomes and final visual/lifecycle acceptance through real Qt controls. Coordinate
owner artifact acceptance for the reproduced structural preparation failure;
do not modify the separate numerical-owner work under frontend migration.
Failure outcomes determine missing UI/service repairs;
passing cases close only their corresponding behavior entry. Then obtain final
independent lifecycle/persistence/viewer review against the consolidated ledger.

Default switching and Tk retirement still require full supported-feature
acceptance, clean coordinated owner artifacts, remote Qt CI and applicable
scientific/migration gates. The preserved broad run still has four scientific
intersection/quad-first failures and one exact owner-version expectation; they
are not waived or silently weakened. Local Linux acceptance is WSLg/Mesa, not a
physical Linux GPU. Package publishing remains a separate delivery step.

### Main-branch candidate update, 2026-09-30

The user authorized publishing only the Qt conversion and its required shared
viewer to both main branches. This delivery does not accept full parity, switch
the default, remove Tk or release packages. Stage explicit owned paths and
validate clean candidate trees against recorded committed dependency snapshots.
The bounded question is whether the candidate works without unrelated dirty
solver/structural-preparation/compatibility changes. Conversion failures require
repair; upstream scientific failures remain recorded with their original gates.
Publish the viewer first, then pin its exact commit in candidate CI and guidance.
Preserve excluded files by before/after content hashes and recheck remote tips
before each ordinary push. Evidence is retained under `reports/qt/publish/`.

Viewer main is published at `64b39d5f45ca01c4dfd972af5a4acf592ffccad7`.
Its clean staged tree passes 156 viewer tests (10 skipped), including real Windows
Qt GPU host checks; wheel/sdist build, metadata and headless import checks pass.
The old candidate dependency pins fail collection because ANYmesher 0.5.0 lacks
`MeshAutomationOptions`. This is retained in `fem-tests.log` and the follow-up
`fem-pinned.log`; it is not hidden by weakening tests. Candidate CI now selects
public committed ANYsolver `3adf2241a8f0427e0a3270761010d48dae16b91e` (0.4.7),
ANYgeometry `7e797791727752aec21ddd98d08daf8f0916e280` (0.4.4), and ANYmesher
`2f1543bd3e71e19fdc9d6425ca3a677b815b2af1` (0.5.1), while material/file-I/O pins
remain unchanged. These clean snapshots exclude all dirty owner changes. They
are candidate validation inputs, not a requalification of the release graph.

With those clean owner snapshots, installed candidate checks pass on Windows
(212 tests, `fem-qt-public.log`/XML) and Linux WSLg/Mesa (175 tests,
`linux-fem-clean.log`/XML). They cover the Qt workflow/lifecycle, scene, result
summary/imports, recovery and headless boundaries; the Windows batch additionally
covers shared workflow, job manager, mesh snapshots and imported persistence.
Linux installed viewer contracts pass 32 checks (`linux-viewer.log`/XML).
Wheel/sdist builds and metadata checks pass for both repositories. Installed
license checking passes (MPL-2.0, 18 direct dependencies). Module/version evidence
is in `installed-proof.json`; excluded-file hashes remain unchanged. The clean
package's solver/build and structural-preparation files are compared with the
pre-conversion committed baseline, excluding unrelated numerical edits.
The broad clean Windows regression records 1003 passed, 127 skipped and 11 failed
(`fem-public.log`/XML). Four failures arose from the disposable source layout:
missing sibling Tk sources and the migration subprocess choosing the older mesh
snapshot. After installing a complete clean sibling layout, all four pass
(`fixture-recheck.log`/XML). The remaining seven failures reproduce on
pre-conversion ANYfem `951fe61f2ac2e757fa915ba857431933fc7be761` with identical
messages (`baseline-science.log`/XML, `baseline-comparison.json`): two qualified
S3 authority/preparation cases, four intersection/quad-first admission cases,
and the connected sketch-extrusion shell junction. These numerical acceptance
failures are not waived or repaired by this candidate delivery. No test assertion
was weakened. Source-equality evidence confirms all 118 installed ANYfem and 32
installed viewer Python files match the clean candidate sources, normalizing
Windows line endings (`installed-source-equality.json`).

### Acceptance and main cleanup continuation, 2026-09-30

The user authorized completing remaining parity/scientific/hardware/review gates
and housekeeping on main. Recent main commits have changed S3 preparation
admission and nonlinear quad-first routing; recheck their affected failures
against clean committed owners before interpreting acceptance. Keep numerical
criteria, retained failures and unrelated compatibility work intact.

First bounded question: why candidate CI passes Windows but fails Linux.
Run 36735205661 passes Windows 3.11/3.14; Linux 3.14 fails building glcontext
because X11 headers are absent before installation. Linux 3.11 aborts in
QApplication initialization under Xvfb; inspect the XCB plugin requirements.
Move Linux build/runtime libraries before pip, explicitly select XCB and run a
visible platform startup check before pytest so startup diagnostics survive.
Then rerun the affected CI with the exact pinned candidate inputs.

Next slices close scoped-output and automatic-recovery real-window outcomes,
assess current scientific failures against their existing gates, and resolve
independent review findings. Only mark supported features accepted when their
evidence is complete. A physical Linux GPU host has been requested; WSLg/Mesa
cannot close that requirement. Do not switch defaults or retire Tk until all
acceptance prerequisites hold. Housekeeping keeps the living ledger current,
fixes reproducibility and removes obsolete owned code only after coverage;
it does not stage unrelated work or delete historical failure evidence.

The user explicitly leaves ANYgeometry publication to its owner. Current
ANYfem main `e0f7ac70a9b34966f7bf411e9ae3739988abf55f` requires batch
intersection exports absent from public geometry `7e797791727752aec21ddd98d08daf8f0916e280`.
Lazy capability imports restore headless loading with that public artifact;
preparation fails explicitly before mutation instead of substituting different
numerical semantics. Published-owner headless/boundary checks pass 15 tests
(`reports/qt/acceptance/public-headless.log`). Clean mesh acceptance remains open.

Qt Submit now carries selected output-request IDs into its fresh analysis and
immutable submitted-input report. Validation rejects missing or incompatible
requests before queuing. ID-backed selection survives request renaming; a real
solve and save/reopen retain the requests. This repairs submission/provenance,
not actual scoped result filtering. Candidate window checks pass 81 tests on
Windows and 81 on Linux XCB/WSLg, plus two new recovery-policy tests on each:
automatic budget exhaustion retains an incomplete diagnostic, and strict policy
retains an owner refusal without activating a mesh. Evidence is under
`reports/qt/acceptance/` (`qt-current`, `linux-qt-current`,
`recovery-policy-ui`, `linux-recovery-policy-ui` logs/XML). These functional
rehearsals use local unpublished geometry; its observed source hashes are in
`local-geometry-inputs.json`. They are not clean installed scientific acceptance.

The affected intersection/sketch rehearsal records 8 passed and 9 failed
(`current-science-rehearsal.log`/XML). Failures include geometry/mesher attachment
integration errors. This mixed owner candidate does not establish a scientific
regression attribution or close the earlier failures; retain both evidence sets.
No owner fixes or publications are included in this slice.

Independent review (`reports/qt/acceptance/independent-review.md`) confirms the
request-submission repair, finds no additional defect in the bounded lifecycle
and viewer inspection, and keeps public owner integration open. Linux CI setup
now installs build headers before pip and checks explicit XCB startup; local XCB
startup passes, but final remote CI is pending. The physical Linux GPU host is
still unspecified. Next: actual scoped-output filtering, coordinated published
owner integration with scientific checks, physical hardware evidence, then final
review bound to clean installed commits. Keep Tk default and all acceptance
gates until those prerequisites pass.

Scoped-output slice: determine whether submitted region intent can be resolved
once against the submitted mesh and persisted as named quantity views without
consulting a subsequently edited project. Retain native solver fields for the
existing workbench; requested views must contain only matching entities and
components, carry their own frame/association metadata, and be inspectable and
exportable after reopening. Test an actual Qt solve/export/reopen and a changed
region after submission. Missing quantities or unsupported recovery/basis must
produce explicit request diagnostics, never full-field substitution or zeros.
This slice does not change numerical kernels or owner connectivity contracts.

Named native output views now freeze canonical region membership before queuing
and persist only matching entities/components under request-specific field keys.
Native full fields remain available to the workbench. The selected quantity can
be inspected with exact entity IDs and exported independently after reopening.
First/last/all frames retain actual frame values; signed maximum-absolute
envelopes disclose their synthetic coordinate and source frames. Missing members
produce partial status and explicit IDs, never zero-filled rows. Unsupported
basis/recovery/reduction or selected frames without recorded indices are explicit
unavailable outcomes; those advanced capabilities are not closed by this slice.

The real Qt save/reopen check exposed synchronous Save omitting submitted-input
provenance. Save and background persistence now use the same provenance builder.
An in-flight edit test confirms the saved view retains its submitted tip scope
instead of following the edited request. Independent scrutiny also found a mesh
query mixing node and element ID namespaces; candidate filtering and Boolean
query collision tests repair that expansion. Review follow-up confirms the scope,
missing-member and local-frame presentation findings are repaired.

Affected source checks pass 126 tests on Windows and 111 on Linux XCB/WSLg
(`scoped-output-windows-final`, `scoped-output-linux-final` logs/XML under
`reports/qt/acceptance/`). Both use unpublished local geometry and are functional
candidate evidence, not scientific or physical Linux GPU acceptance. Fifteen
published-owner headless checks, wheel/sdist build, metadata and 18-dependency
license checks pass (`scoped-output-headless`, `scoped-build`, `scoped-metadata`,
`scoped-license`). Earlier failed UI runs remain retained; the Linux selector
test now selects the displayed field text instead of relying on platform-specific
Qt tuple lookup. Scientific and owner integration failures remain open.

Installed-wheel checks additionally pass 40 contracts with published owners;
`scoped-installed-origins.json` confirms site-packages origins and neither Qt nor
Tk loaded. A subsequent focused check passes 16 view contracts after adding
external request-ID escaping for HDF5 keys; canonical UUID keys are unchanged.
Do not infer full request parity from these native-view checks: selected-frame
indices and qualified recovery/basis/reduction transformations remain unresolved.

The clean staged package also passes 41 installed contracts and all 86 real
Windows Qt workflow cases (`scoped-clean-installed`, `scoped-clean-qt-installed`).
The latter retains the explicit unpublished geometry input. Request views are
presented by their labels in the quantity selector; storage keys remain internal.

The final label checks pass four cases on Windows. On Linux, the two isolated
named-view cases pass, while both beam workflow cases fail in newly changing
ANYgeometry source: `features.py` calls undefined
`_discard_equivalent_unpublished_owners` (`scoped-label-linux.log`/XML).
Retain this owner integration failure; it does not supersede the earlier 111
passing Linux source checks or establish acceptance at the current owner identity.
The final label change does not alter filtering/persistence semantics. The
concurrent committed viewport batching parent passes 17 installed selection
contracts (`scoped-parent-selection`). Uncommitted intersection-test owner work
is excluded from this slice.

Final scrutiny found that duplicate output labels could change the selected
scope during a choice-list refresh. Restoration now uses the stable `(kind,
field-key)` item data, normalizes Qt tuple/list representations, and clears an
unavailable selection. A two-label/two-scope widget test forces a rebuild, then
checks inspection and exported node IDs. The three affected view/selection cases
pass on Windows and Linux with published owner packages
(`scoped-selection-identity`, `scoped-selection-identity-linux`).

Selected-frame slice: add optional explicit zero-based frame indices to the
typed request without changing positional arguments or default serialization.
Resolve indices against each stored quantity's actual frame metadata, preserving
declared order and refusing unavailable indices. Prove request/hash/save/reopen
round trips and a Qt form-to-retained-view-to-CSV path with nontrivial frame
values. Legacy selected requests remain readable but cannot queue without an
explicit selection. No numerical owner, tolerance or execution gate changes.

The selected-frame implementation passes 46 Windows contracts and the real Qt
modal request/solve/save/reopen/CSV workflow against published owner packages
(`selected-frames-contract-final`, `selected-frames-ui-final`). Linux XCB/WSLg
passes the initial 44 contracts plus the same Qt workflow (`selected-frames-linux`);
this is functional evidence, not physical Linux GPU acceptance. Requests retain
the exact declared order and actual modal frequencies. Out-of-range indices
refuse the whole view. Legacy projects with missing indices load but validation
blocks their analysis until resolved. The temporary Tk form also exposes indices.
Independent read-only review found no actionable defect in validation,
serialization, frame ordering or the Qt persistence/export path. The initial Qt
test failures (JSON input syntax and an assumed metres unit for normalized modal
shapes) remain in the evidence directory. Qualified recovery, basis and reduction
semantics, owner integration/scientific acceptance, physical Linux GPU evidence
and final full-parity review remain open; Tk remains the default.

Integration-point reduction slice: the principal uncertainty is whether retained
stress samples have sufficient axis metadata to apply the existing per-element
postprocessing reductions without mixing components, entities or frames. Preserve
native arrays and add explicit scalar-sample layout provenance at production.
Support mean/average, min, max and signed maximum-absolute over only those sample
axes, with native recovery/basis retained. Validate against existing post.fields
reduction behavior, persistence/CSV and real Qt inspection. Ambiguous legacy or
unsupported fields must remain explicitly unavailable rather than guessed.

Explicit stress requests now trigger the existing cached owner-backed recovery
inside the numerical worker, using the submitted snapshot. Cancellation is checked
before and after recovery; the owner call itself remains synchronous. This fixes
the real Qt failure where retained linear results depended on prior display
interaction to populate stress. Native arrays retain their established shapes;
scalar sample axes are additive metadata. Reduced views preserve entity scope,
frame order, units, basis and recovery and remove the native layout marker.
Windows affected contracts, job cancellation and import boundaries pass 62 tests
(`sample-reduction-contract-final`); the real imported-shell pressure/solve/save/
reopen/inspection/CSV path passes one (`sample-reduction-ui`). Linux XCB/WSLg
passes 50 contracts plus that workflow (51 total, `sample-reduction-linux`).
Independent read-only review found no confirmed defect in axes, signed/tie
semantics, metadata or worker lifecycle. Initial missing recovery and descriptor
stub failures remain retained. This establishes application behavior with public
owners, not additional solver qualification, physical Linux GPU acceptance or
full parity. Basis transformations and unsupported recovery/reductions stay open.

Final review found a legacy cancellation-token signature mismatch: the fallback
did not accept the recovery stage argument. It now accepts the optional stage;
both current and fallback recovery-cancellation tests pass
(`sample-reduction-legacy-token`). The preceding 62/51/64 results describe the
pre-repair tree, not the final package. Viewer owner local main meanwhile advances
to `d05093a2920b4329379f7efceeff570f9251c4e9` while public main remains
`9d27c1a6ca1b52691bd36791de1131cc8b1d9c0b`; preserve that separate owner work.

Batch-stress slice: explicit stress requests are admitted for batch linear
analysis, but the worker recovers only a single solution and the artifact adapter
retains only batch displacement. Recover each existing LinearSolution on the
worker, preserving cancellation boundaries and case order. Persist only actual
cached recovered fields with case/frame provenance, then exercise selected-case
scope/reduction/save/reopen/export through a real Qt batch solve. Incomplete
fields must retain diagnostics rather than invent values. Existing numerical
methods, factorization, owner contracts and acceptance tolerances are unchanged.

The batch worker now recovers requested stresses through each existing
LinearSolution cache. Per-case stress fields retain case provenance and explicit
sample axes; partial caches disclose missing cases instead of emitting fabricated
frames. Scoped views carry local frame labels, Qt inspection names the case, and
CSV exports include `frame_label` for named cases. Synthetic envelopes carry no
single-case label. Windows source contracts and real batch workflow pass 52
tests (`batch-stress-source`); lifecycle/import boundaries pass 15 including
current/fallback cancellation during both single and batch recovery
(`batch-stress-lifecycle`). Linux XCB/WSLg passes those 67 cases
(`batch-stress-linux`). Three additional first/last/envelope case-label contracts
pass on Windows (`batch-case-labels`). Independent read-only review found no
confirmed defect in case ordering, missing-cache diagnostics or worker lifecycle.
These results remain functional application evidence; scientific owner gates and
physical Linux GPU acceptance remain open, and the Tk default stays unchanged.

Global-stress slice: published ANYsolver exposes authoritative surface-tensor
recovery via `return_global=True`. Explicit global-basis stress requests currently
cannot select those values. Retain a separate owner recovery on the worker,
preserving the local default cache; persist only the explicit `global_` components
with global basis, sampling and source provenance. Exercise actual scalar scope,
reduction/save/reopen/CSV on a real Qt solve and compare to owner-returned arrays.
This introduces no application tensor rotation or new numerical method. Other
coordinate systems, material basis and patch/nodal recovery remain separate gaps.

Global requests now retain a separate owner-backed recovery, leaving native local
caches unchanged. Single and batch fields expose only the twelve explicit physical
surface-stress components; global section resultants/tensors are not mislabeled
as stresses in Pa. These fields retain global basis, native sampling and owner
provenance. A plate rotated 45 degrees tests exact agreement with owner-returned
global arrays, and real Qt single/batch paths exercise reduction, save/reopen and
CSV. Windows passes 74 affected contracts (`global-stress-contract`) and four Qt
cases (`global-stress-ui`); Linux XCB/WSLg passes all 78 (`global-stress-linux`).
Cancellation tests cover current/fallback tokens in single/batch local/global
recovery. Independent source review found no confirmed defect. This is application
integration evidence using the published owner API, not a new numerical
qualification or physical Linux GPU acceptance. Remaining recovery/material-basis
and owner scientific gates stay open; default switching remains gated.

Patch-contract inspection found a retained global-surface gap: the published
owner names lower components `bot`, while the filter used `bottom`. Correct the
filter to owner spelling and prove all twelve fields plus real lower-surface
single/batch save/reopen/export. Patch recovery itself returns continuous `nodal`
values, separate `nodal_regions`, and qualified/fallback/discontinuous diagnostics.
Next integrate the existing guarded owner method with exact node/frame/status
provenance; never cross-average regions or advertise fallback values as qualified.

Guarded patch recovery now runs on the existing analysis worker through published
ANYsolver `PatchRecoveryConfig`, retaining its continuous nodal values without
averaging separate regions. Artifact tables retain region diagnostics; fields,
requested views, Qt inspection and CSV preserve per-node/per-frame owner status.
Fallback/unclassified requested nodes produce a partial outcome. Patch requests
require node location, global basis and stress quantities before submission;
legacy intent remains readable. Single/batch recovery preserves the local cache
and cancellation checks before/after owner calls, including fallback tokens.
The lower-surface filter now uses the actual owner `bot` names; all twelve
physical global components are tested, alongside real lower-surface workflows.
Windows contracts and real Qt workflows passed 113 cases across
`patch-recovery-combined`, `patch-request-admission` and
`patch-request-ui-validation-final`; Linux XCB/WSLg passed all 113 together
(`patch-recovery-linux`). Earlier UI failures remain recorded and were repaired.
These are functional owner-integration checks using published dependencies, not
scientific qualification or physical Linux GPU acceptance. Clean installed
artifact validation and exact-tree independent review are the next checks.
ANYgeometry publication remains with its owner; Tk remains the default.
The clean staged-tree wheel also passes all 113 selected Windows tests without
source-path injection (`patch-recovery-installed`). Wheel/sdist build, Twine
metadata checks and MPL-2.0 inventory checks for 18 direct dependencies pass.
Independent source review found no actionable defect, including legacy request
admission; final exact-tree review is being performed before main publication.

Inspector failure/lifecycle slice: current Qt load clears the result object but
keeps the prior file's tables visible. A failed worker read leaves the inspector
status at Reading while only the main-window log reports the error. Distinguish
stale presentation from a reader/owner failure with bounded real-window tests:
valid-to-missing/malformed replacement must clear old data and disclose failure;
a held old read must not overwrite a newer file; close must suppress late widget
updates. A 5001-record disposable FEM checks the existing 5000-row preview bound
and full owner document/report retention, not a scalability claim. Preserve
owner parsing/canonical semantics and use the current artifact worker/UI scheduler.
Inspector source checks now pass ten real Qt cases on Windows
(`inspector-formats-final`) and Linux XCB/WSLg (`inspector-linux`). The original
stale-row failure is retained (`inspector-stale-before`); two initial test
assumptions were corrected to preserve actual owner diagnostics and the canonical
terminator rather than altering owner behavior (`inspector-lifecycle`). Failure
and retry clear stale tables, exports are disabled without a loaded result, and
running owner reads may finish without late presentation after replacement/close.
The preview reports 5000 of 5001 records while canonical export retains all owner
records. FRD inspector regression uses a real imported SESAM mesh/solve, keeping
format inspection independent of unpublished geometry-to-mesh preparation.
Independent source review found no actionable defect. Clean installed validation
and exact-tree review follow; broad malformed/legacy/full-size input acceptance,
scientific gates, published owner integration and physical Linux GPU remain open.

Nonlinear retained-frame inspection: requested stress recovery currently acts on
the final solution, not each saved committed increment. Full committed-history
recovery remains required. Its frame-identity prerequisite is now concrete:
output views select data by index but remap labels and node qualification through
`frames.index(value)`, which confuses repeated coordinates (possible on traced
nonlinear paths). Before extending recovery, replace value-based remapping with
actual selected frame indices; test all/first/last/reordered-selected/envelope
views with repeated coordinates and different node qualification/case labels.
Retain exact values and signed-envelope semantics, verify persisted CSV, then
continue owner-backed committed-increment recovery without failed-trial frames.
Repeated-frame source checks pass 70 affected contracts and real Qt inspection/
patch workflows on Windows (`repeated-frame-windows`) and Linux XCB/WSLg
(`repeated-frame-linux`). Five original failures remain in
`repeated-frame-before`; positional data selections were unchanged. Requested
views now retain source indices; CSV keeps its local frame index and adds the
source index when available, leaving legacy exports unchanged when absent. Qt
inspection displays both coordinate and source identity. Envelopes retain all
contributor indices and conservative qualification with no single-case label.
Independent source review found no actionable defect. Duplicate-coordinate tests
use a signed global tensor component, not negative equivalent stress. Clean
installed/exact-tree checks follow; nonlinear increment recovery and the wider
scientific/owner/physical-GPU/full-parity gates remain open.

Committed nonlinear recovery slice: the worker currently recovers the final
wrapper only, and increment views expose retained states without a stress method.
Use one shared history-aware ANYsolver recovery adapter for final/increment views;
recover actual saved snapshots on the worker, preserving their states and the
parent recovery kinematics (snapshot objects do not necessarily carry info).
Persist full native/global/patch frame histories with actual load factors, step
indices and per-frame owner provenance. Missing caches remain explicit, never
scaled final values; absent snapshots remain final-only. Bound the experiment to
public-owner imported shell workflows with two stored increments, exact comparisons
to public recovery, selected/reordered views, save/reopen/CSV/Qt inspection and
cancellation between increments. Failed trials are excluded because only owner
committed snapshots enter the adapter. Existing scientific gates stay unchanged.
Committed recovery now uses each actual saved increment on the worker, with a
shared history-aware adapter preserving parent kinematics and separate native/
global/patch caches. Artifact histories retain step indices and per-frame owner
provenance; selected views remap that metadata positionally, and envelopes retain
source provenance separately. Native requests no longer consider unrelated patch
fields as missing native output. Initial contract evidence retained one such
false-partial failure (`committed-recovery-contract`); the source was repaired.
Subsequent Windows contracts pass 88 cases (`committed-recovery-contract-final`),
and six real imported Qt workflows pass for native/global/patch recovery under
both von-Karman and corotational kinematics (`committed-recovery-ui`). Exact owner
arrays persist through reordered scope/save/reopen/CSV. Added final-only and
capacity-wrapper contracts preserve absent/missing snapshot semantics, and
current/fallback cancellation tests include stopping before the next increment.
Final affected Linux and clean installed checks are next. Independent early source
review found no confirmed defect. This is adapter fidelity evidence, not new
nonlinear/capacity scientific qualification or physical Linux GPU acceptance.

Full-parity audit distinguishes supported Tk interactions from unimplemented
request intent: Tk exposes `local` and `element` basis spellings, but scoped views
compare only owner descriptor `element_local`, causing false refusal for native
local stresses. Accept those two spellings as aliases at the view boundary while
preserving request serialization and actual owner basis. No tensor rotation,
material/named-coordinate transformation or numerical fallback is introduced.
Bounded evidence: persisted local/element request scope, exact owner values,
legacy request roundtrip, real Qt local stress solve/save/reopen/export and
continued refusal of unsupported material basis. The existing broader scientific,
owner publication and physical Linux GPU gates remain prerequisites for switching.
Local-basis Windows source checks pass 97 affected contracts and real-window
workflows (`local-basis-windows`), including exact retained/exported values and
saved alias intent. Both original false refusals remain in `local-basis-before`.
The boundary compares only equivalent spellings; material basis and local requests
for global vectors remain explicit refusals. Independent source review found no
actionable defect. The workflow ledger now reflects completed request/recovery and
inspector slices while retaining clean owner, broad model/input, scientific and
physical Linux GPU acceptance gates. Final installed and Linux checks follow.
Evidence audit: `current-science.xml` records four module collection errors from
the missing published batch API, not four numerical tolerance failures. The
separate 17-case dirty-owner rehearsal records eight passes and nine topology/
attachment integration failures (`current-science-rehearsal.xml`). Neither file
adjudicates the earlier preserved broad scientific failures or attributes them
to a numerical owner. Re-run the applicable clean scientific/migration gates only
with coordinated published inputs; retain all prior failures and tolerances.

Sketch validation slice (2026-10-01): a failed Qt constraint preview appends its
constraint before owner validation, leaving hidden working-copy state behind
while the editor and project remain unchanged. Three real-window reproductions
(malformed extrusion, conflicting distances and distance/coincidence conflict)
fail the working-copy preservation check (`sketch-constraint-before`). Parse
preview inputs before mutation and discard only the attempted constraint when
owner validation fails. Keep the valid preview, editor and project unchanged;
verify subsequent apply/undo remains usable. This addresses dependency-failure
presentation without changing owner sketch mathematics or scientific tolerances.
Windows GPU and Linux XCB/WSLg checks each pass four real-window cases
(`sketch-constraint-windows` and `sketch-constraint-linux`); the original three
failures remain retained. Clean installed checks pass eleven cases, build/twine
and independent exact-candidate review pass. Published commit `b2a4459`.
WSLg remains software GL evidence, not physical Linux GPU acceptance.

Sketch apply continuation (2026-10-01): inspect whether rejected Apply changes
the working preview before the command succeeds. Bound the experiment to
create/edit sketches, malformed extrusion, inconsistent owner constraints and
injected dependency refusal, followed by corrected retry/save/reopen. Preserve
preview/project identity on failure and the entered correction text; keep owner
mathematics, persistence formats and acceptance gates unchanged.
The six original Apply failures remain in `sketch-apply-before`. Apply now solves
a deep copy and submits its definition before ending the displayed task.
Windows source, clean installed Windows and Linux checks each pass seventeen
cases, including prior constraint rollback and frontend boundaries; build/twine
and independent exact-candidate review pass. Published commit `50e0042`.
Command refusal is injected at the UI boundary; this does not qualify every
owner dependency rollback. Retry/save/reopen covers both create and edit.

Full workbench audit (2026-10-01): distinguish remaining Qt interaction defects
from the missing published ANYgeometry batch API using the complete existing
real-window workbench and frontend-boundary suite on the clean installed
candidate. Retain every failure and dependency version. Classify failures by
actual exceptions/job diagnostics; repair consumer defects without weakening
tests, and defer owner integration failures to coordinated published inputs.
This audit does not confer scientific or physical Linux GPU acceptance.
The installed Windows audit completes with 101 passes, 28 failures and one Qt
test-API deprecation warning in 720.04 s (`full-workbench-public-windows` log/XML;
installed commit `50e0042`, tree `06e460f6270b5967cd2eeab83908829597d24a94`,
dependency freeze retained). Independent adjudication identifies six direct
missing-API assertions, eighteen initial meshing-prerequisite timeouts, and four
inspection/budget/strict-refusal/held-edit outcome checks. Timeout-only traces
do not independently establish cause, and no solver/scientific failure follows
from these UI counts. Capture terminal mesh diagnostics and injected-hook entry
for the four outcome checks before attributing them. The original
geometry-to-mesh cases remain required and unchanged.
Bounded diagnostic follow-up retains all four failures
(`mesh-outcome-diagnostic` log/XML, `mesh-outcome-diagnostics.jsonl`). Each final
mesh record is failed with `ProjectError` explicitly naming the missing
ANYgeometry batch API. The inspection and held-mesh hooks entered but failed in
their original generation call; the injected strict-refusal hook never ran.
The budget case installs no hook and failed at structural preparation before
its intended budget outcome. These four scenarios did not reach their intended
outcomes. Their assertions remain unchanged; do not claim stale/inspection/
recovery-policy acceptance from this run. Broader timeout-only cases remain
prerequisite-blocked evidence requiring coordinated integration rather than
numerical adjudication.
Imported-result continuation (2026-10-01): the full public-owner audit stops stress-only SESAM retention and CalculiX export/import tests at geometry meshing, so it does not measure those downstream paths. Add separate real imported-mesh workflows (retaining the original geometry end-to-end tests) for stress-only SIF and translations-only FRD, exact retained arrays, absent displacement/rotation semantics, deck export, source-file removal, save/reopen, field CSV and viewport capture. These are adapter/persistence checks against published owners, not new numerical qualification or a replacement for geometry acceptance.
The two imported-mesh paths expose an attachment/report defect: built() returns ImportedModel, whose project attribute is a method, so Qt report refresh crashes after accepting results. Return its existing BuiltModel adapter with the current project context; do not rebuild geometry, recover stresses or invent absent components. Original AttributeError evidence is retained in imported-external-first. A separate test correction saves the new project before inspecting persisted artifacts (imported-external-second retained), and the Tk fixture uses its declared six-DOF layout instead of an unavailable FEMesh.total_dofs attribute. Windows affected checks pass 33 cases and real Tk imported-result checks pass two. Exact imported arrays, component absence, portable reopen without source files, CSV values/IDs, deck and PNG paths are exercised. Final installed/Linux evidence and independent candidate review follow.
Retained playback continuation (2026-10-01): original animation/report end-to-end test stops at unpublished geometry preparation. Keep that test required; additionally exercise an actual published-owner imported-shell transient through Qt, compare every saved displacement/time to owner shapes, plot live history, play live and reopened frames, use the retained mesh with current mesh absent, export Markdown/HTML and asynchronously encode a readable GIF. This measures UI/artifact fidelity and not new transient scientific or physical Linux GPU acceptance.
The actual reopen failure (imported-playback-third) reveals that importing an unsaved mesh defers its record until save, so a submitted job has empty mesh_hash and its retained artifact refers to orphan active-mesh. Register the imported record with the existing public-codec hash at import, cache it, and reuse its identity on save. The test now asserts pre-save record/job identity and persisted mesh ID/hash, compares all six stored DOFs, and renders without the active mesh. Initial test syntax/widget-property/component-layout corrections and a missing required constructor field remain retained separately; they are not numerical owner failures. Windows and Linux affected persistence/import/history checks each pass 59 cases; the final added identity assertions and installed verification follow. GIF decoding is output smoke evidence, not visual or frame-by-frame acceptance.
Imported job lifecycle continuation (2026-10-01): geometry-based held-solve cases remain blocked before worker entry. Preserve them; add published-owner imported-shell cases for token cancellation, load edit during immutable snapshot solve, project replacement and window shutdown. Require no late result application after cancel/replace/close, retained stale answer on the submitted mesh after edit, and stable imported identity. This is lifecycle/adapter coverage, not a substitute for geometry integration, numerical qualification or physical Linux GPU acceptance.
Imported lifecycle Windows checks pass eleven cases (four real-window scenarios plus frontend boundaries). Initial one-case failure was a test expectation: an imported mesh remains in mesh view after a load edit, whereas modeled geometry remains in geometry view; production needed no change. The edited job retains an independent submitted project/load snapshot, exact saved displacement data and stale status across reopen on its submitted mesh. Original three geometry cases remain intact and required; added close case confirms owned cancellation and no result application. Source/API/dependency/license/packaged contents are unchanged, so reuse the previously verified 2b1cfe1 wheel and run the new cases from a clean exported test tree instead of rebuilding unchanged production.

Main CI continuation (2026-10-01): run 36788727691 stops all eight platform/Python test jobs at installed licensing because the Tk/headless install step omits the newly declared optional PySide6 dependency. The build job passes; no numerical test result can be inferred from skipped jobs. Add the existing declared PySide6 range to that install step, preserve the licensing gate and owner pins, verify source/installed license checks locally, and use the next remote matrix to establish platform-wide installation and expose downstream outcomes. Complete failed logs are retained as main-ci-2b1cfe1-failed.log; full parity and scientific/hardware acceptance stay open.

Remote run 36790025225 passes build and advances Linux beyond installed licensing. Its retained Linux 3.12 job log stops collection in two modules because the main matrix still pins ANYmesh 2ccef378, lacking MeshAutomationOptions and MeshRecoveryIncomplete. Published owner main 2f1543bd3e71e19fdc9d6425ca3a677b815b2af1 exports both and is the existing Qt candidate/installed mesher dependency. Update only the main test/build mesher pins to that exact commit, preserve frozen solver and other owner pins, verify affected collection, and adjudicate the next remote result. No scientific acceptance follows from resolving imports.

Main matrix evidence (2026-10-01): completed Linux 3.11 job 110141387424 from run 36790308045 records 783 passed, 218 failed, 130 skipped and 39 errors after licensing/import corrections. Of 257 summary failure/error lines, 237 explicitly name the missing batch API; the other twenty require individual inspection. The headless migration traceback also explicitly fails geometry preparation, not a measured Tk/Qt import leak. The independent launcher failure hard-codes mesher 0.5.0 while the correct active source reports 0.5.1; reproduce it (launcher-ci-before), then bind the test to disposable source pyproject metadata for 0.5.0/0.5.1/1.0.0 while installed metadata remains stale 0.3.2. All eleven launcher checks pass on Windows and Linux. Production and scientific assertions are unchanged; remaining failures are not waived or collectively attributed without traces. Complete CI log and bounded other-summary list remain in reports/qt/acceptance.

Indirect failure diagnostic (2026-10-01): latest Linux 3.11 CI job 110142915685 verifies the launcher repair (786 passed, 217 failed, 130 skipped, 39 errors; no launcher failure). For the quad-first and mapped worker tests, competing explanations are missing owner preparation versus a separate publication defect; their existing assertions show only zero completed events. Capture poll terminal messages in two unchanged tests using an ignored read-only pytest plugin. Explicit preparation refusal defers integration to the geometry owner; a different worker/publication error calls for an ANYfem fix. No tolerance, production path or execution authority changes.
Both unchanged worker tests fail with terminal events explicitly naming missing ANYgeometry batch preparation (worker-failure-diagnostics.jsonl/log/XML; two failed, twenty deselected). They never publish a mesh, so this run cannot establish route or publication acceptance. Preserve the failures and rerun those required paths only after coordinated owner integration; nine other indirect entries remain unadjudicated. No production repair is justified by these two outcomes.

Remaining asynchronous diagnostics (2026-10-01): apply the same unchanged-assertion, terminal-error experiment to four native component publication/cancellation tests, desktop native cancellation and background snapshot mapping. Capture native generator exceptions and drain existing terminal mesh events on owned shutdown, without changing computation or injecting owner success. A missing preparation API defers these acceptance paths; other terminal errors require separate repair. This bounded batch is not a numerical qualification rerun.
All six remaining asynchronous diagnostic cases stop with explicit batch-API terminal errors before native triangulation/publication; no cancellation success is claimed (native-worker-failure-diagnostics.log/XML and worker-failure-diagnostics.jsonl). The final three indirect entries are aggregate reporting: the migration headless criterion consumes headless_model_report().ok, and two verification presentation assertions share the report fixture whose case diagnostics explicitly record missing preparation. Latest run 36790783343 is terminal: all eight test jobs fail and build succeeds. Representative Linux/Windows 3.11 jobs both record 786 passed, 217 failed, 130 skipped, 39 errors, with identical 256 failure/error identifiers and no launcher failures; full logs retained. Of those entries, 237 explicitly name the API in summaries, eight more in traces, eight asynchronous paths are confirmed by the bounded diagnostic reruns, and three aggregate failures depend on blocked reports. This is blocker adjudication, not scientific acceptance. No production repair, skipped test or weakened assertion is justified by these results; owner integration and physical Linux GPU evidence remain necessary.
Independent bounded CI-adjudication review confirms the exact latest commit/run, matching Windows/Linux identifier sets, all four cause categories and terminal counts, with no actionable overclaim. Diagnostic poll forwarding and exception rethrowing preserve assertions; the shutdown drain observes queued terminal events and is not arbitrary shutdown/cancellation acceptance. Existing geometry scientific failures remain unadjudicated. No final full-parity acceptance, default switch, Tk retirement or release is authorized by this review.

Owner-candidate integration continuation (2026-10-01): published ANYgeometry remains 7e797791, but its owner has committed local 6557b2cc01d970438ca4a699887bf8c6a9c27d6b with batch/material arrangements and records 1062 kernel tests; curved shared-boundary mesh repair remains owner work. Export only that committed source into an isolated ANYfem report directory and run the original bounded basic Qt geometry-to-solve/save-reopen workflow against public mesher/solver dependencies. This tests whether the new owner commit unblocks the first path; it does not authorize owner publication, accept curved/scientific parity or replace the clean published-artifact gate. Preserve all owner dirty files and prior failed evidence.
The exact exported owner commit passes the original test_real_qt_mesh_solve_save_reopen on Windows (one passed, 4.82s) and Linux XCB/WSLg (one passed, 10.32s), including actual geometry meshing, solving, save/reopen, retained display and PNG capture. Effective anygeometry import resolves to the isolated committed export. The initial selector mistakenly deselected all tests (geometry-6557-first); preserve it as non-evidence. No installed-owner, curved-mesh, scientific or physical Linux GPU acceptance is established. Next bounded action is the original geometry lifecycle/mesh-route checks against this exact candidate, followed by clean exported scientific integration under existing authorities; owner publication remains solely with ANYgeometry.

Candidate lifecycle/mesh outcomes (2026-10-01): with the original first workflow now passing, use the same exact owner export to run the three original geometry held-solve cases, four plate mesh routes, inspection refusal, automatic budget exhaustion, strict owner refusal and structured preview commit/undo. These eleven unchanged cases discriminate resolved preparation prerequisites from downstream ANYfem lifecycle/policy defects. Keep GPU Qt windows and original assertions; classify failures before broadening. This does not consume frozen scientific owner authority or publish the candidate.
All eleven unchanged geometry lifecycle/routes/policy cases pass on Windows (8.14s) and Linux XCB/WSLg (14.14s). With the formerly blocked prerequisites now resolving against this changed exact owner input, broaden to the complete Qt workbench suite on Windows; classify any remaining failures before repeating or expanding. This is source-candidate integration evidence and preserves published-owner, numerical qualification and physical Linux acceptance boundaries.
Complete Windows Qt suite against owner 6557b2c records 128 passed, one failed and one deprecated QApplication test-API warning (99.44s). The sole failed feature suppression test is unchanged. Bounded status capture reports cannot preserve feature lineage: regenerated structural owner lacks one exact replacement role. A minimal ANYgeometry-only script (geometry_6557_suppression_repro.py) creates and successfully regenerates generator.plate, then set_suppressed(True) and regenerate_features() returns success=False with the identical diagnostic (one face/Sheet remains). This isolates an owner suppression/regeneration defect, independent of Qt and ANYfem commands; no consumer workaround or assertion change is justified. At this checkpoint Linux suite execution was pending; its completed result follows below. Owner correction/publication, Linux acceptance, clean installed integration and scientific/hardware gates remain open.
Full Linux XCB/WSLg Qt suite against the same committed owner candidate also records 128 passed, the identical feature suppression failure and one deprecated test-API warning (112.42s). Every selected test assertion remains intact. Windows/Linux parity for this suite is measured, but not accepted while suppression is unresolved; this is neither clean published-artifact nor physical Linux GPU evidence. Independent bounded source-candidate integration review follows; scientific acceptance remains gated on coordinated published inputs as previously recorded.
Independent source-candidate review verifies both platforms original workflow/lifecycle counts and complete 129-case suites, reproducer attribution and the ANYfem command clone/refusal-before-publication boundary. Two record wording corrections distinguish completed Linux execution from open acceptance and call the owner behavior a defect without asserting an unmeasured introduction baseline. No consumer workaround is justified. This review does not close the final complete-workbench, scientific, installed/published-owner or physical Linux GPU acceptance gates.

WSL setup (2026-10-01): user requests Linux execution while geometry publication is pending. Reuse the installed Ubuntu-24.04 Python3.12/PySide6 environment, add isolated launch and startup/capture/shutdown commands, record effective module origins and graphics/platform diagnostics, and verify OpenGL/software paths. The current renderer is llvmpipe; no physical Linux GPU or scientific acceptance follows. Preserve concurrent structural-preparation/scene/import tests and all owner artifacts.
WSL installed-package startup checks pass for explicit OpenGL and software renderers, both with XCB, nonempty captured viewports and shutdown_complete=true. Evidence directories20261001T072818883224Z and20261001T072839299502Z record effective module origins and llvmpipe graphics. tools/run_qt_wsl.sh offers launch/smoke with isolated Python imports; docs/WSL_QT.md gives repeatable commands and boundaries. The live launch is intentionally left for the user; no scientific/physical GPU acceptance is claimed and no candidate owner packages were installed or published.

Development-test efficiency transition (2026-10-01): user authorizes a more efficient future development regime. Adopt quick/focused/full/Qt execution profiles with explicit measured scope, upfront required-owner capability checks for full/Qt runs, fresh retained reports, and reuse the same full verification fixture's JSON/Markdown in CI rather than repeat its numerical cases. Conversion-record-only commits do not require another unchanged matrix. Preserve the eight-lane full code-change matrix, all original numerical predicates, UI/native GC cleanup, Qt/GPU/installed/scientific/release gates, frozen authorities and dirty owner files. Proportionate runner tests and representative timings will verify the implementation; do not claim unmeasured savings or treat quick success as acceptance.

Development-test efficiency implementation (2026-10-01): added `tools/test_anyfem.py` quick/focused/Qt/full profiles with retained logs, JUnit counts, nonzero blocked/failure/interruption records, fresh directories, complete scientific inventory checks, and bounded cleanup of owned process trees. Inherited pytest filters cannot narrow a profile. CI reuses the original verification fixture report and preserves all eight lanes plus licensing/build/parity; only conversion-record/report-only changes skip this workflow. Initial quick checks: Windows 76 passed in 12.10s; WSL Linux 76 passed in 14.09s. Final intended files in a clean main snapshot: 87 quick tests passed (`reports/tests/efficiency-clean-final-quick/`). Runner regressions: 28 passed on each OS, plus the final stricter real parent/descendant cleanup case passed on each OS. Public-owner full preflight stopped as blocked in 0.24s, naming the missing ANYgeometry batch API; no full/scientific pass is claimed. Independent OpenAI source review findings were repaired; Mistral review failed with a Windows encoding error (retained log), so no Mistral review is claimed. Retain failed Windows sandbox process-tree evidence and native rerun pass. Existing full parity, owner-publication, scientific, installed, physical Linux GPU and final acceptance-review gates remain open.
