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
| Definitions and workplanes | Coordinate systems including ndarray origins, selection/boolean mesh regions, output requests, custom units, snapping/construction | Boolean-region creation and typed output-request attachment/undo tested; scoped result filtering still needs real-window acceptance |
| Mesh generation | Four mesh routes; typed native/structured/quad/quality/automation controls; pins/refinements; preview commit/discard/undo | Inspection-only candidate display and solve refusal tested; remaining automatic recovery-policy outcomes |
| Mesh lifecycle | Real-window cancellation, stale completion, replacement and injected failure on installed Windows/Linux | Independent final integration review |
| Analyses | All nine public analyses submitted through Qt controls; load case/combination routing and detached imported snapshots | Owner scientific gates remain unchanged and unresolved failures remain visible |
| Solve lifecycle | Cancel/edit/replace during held solve; stale retained mesh identity; project locks/recovery/pending writers | Partial outcome limits must remain explicitly distinguished from verified capacity |
| Engineering interpretation | Nonlinear last/peak/target/failed trial, stop reason, prescribed path; saved-frame details; submitted-input provenance; material capability versus retained plastic-state evidence; imported constitutive behavior unavailable | Reviewed slice, not numerical qualification |
| Retained results | Saved/live playback, fields/tables/histories/point/line/probe, color limits, PNG/GIF/CSV/Markdown/HTML; sidecar mesh identity independent of current mesh | Additional imported-format and legacy-artifact interactions |
| Imports/exports | SESAM model/group solve/save/reopen; CalculiX deck and result retention/reopen; async neutral FRD/DAT/INP/FEM/SIF inspector and canonical FEM export | SESAM stress-only import/save/reopen tested; All FEM/SIF/INP/DAT/FRD inspector paths now tested; malformed/large-file coverage remains bounded |
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
