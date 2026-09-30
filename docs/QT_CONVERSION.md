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
Windows GPU and Linux XCB/WSLg checks each pass four real-window cases (sketch-constraint-windows and sketch-constraint-linux); the original three failures remain retained. Clean installed-artifact checks and independent review follow. WSLg remains software GL evidence, not physical Linux GPU acceptance.
Sketch apply continuation (2026-10-01): inspect whether rejected Apply changes the working preview before the command succeeds. Bound the experiment to create/edit sketches, malformed extrusion, inconsistent owner constraints and injected dependency refusal, followed by corrected retry/save/reopen. Candidate fix must preserve preview/project identity on failure and the entered correction text; keep owner mathematics, persistence formats and acceptance gates unchanged.
The six original Apply failures are retained in sketch-apply-before. Candidate Apply solves a deep copy and submits its definition before ending the displayed task. Windows source checks pass 17 cases, including prior constraint rollback and frontend boundaries. Command refusal is injected at the UI boundary; this does not qualify every owner dependency rollback. Retry/save/reopen is exercised for both create and edit. Installed/Linux checks and independent exact-candidate review follow.
