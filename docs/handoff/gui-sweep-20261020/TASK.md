# ANYfem GUI usability sweep — 2026-10-20

Outcome: a clearer, easier-to-use Qt workbench. The changes affect presentation only: commands, parsing and saved results do not change.
Risk: Level A UI. The other session's uncommitted sketcher/construction-plane work is the owner-approved baseline. It is preserved, and no other file differs from it (checked against a reconstructed pre-sweep tree).
Owner decisions (2026-10-20): build on the dirty tree. Slices: shortcuts and toolbar, task-panel layout, folding of advanced options, theme polish. This supersedes the 2026-10-03 shortcut choice in QT_CONVERSION.md: Save is now Ctrl+S and Show selected is Ctrl+Shift+H.

Implemented (src/anyfem/ui/qt/):
- app.py: icon-only file and undo actions, with Generate mesh and a primary Solve button. Workspaces are a text-labelled toolbar in four groups, with Ctrl+1..9 and a Workspace menu. The task dock shows a title and a one-line summary instead of the WORKSPACE combo. Selection controls are labelled (Pick/Tool/Depth/Mode) and show readable values. Fit/Iso/Top/Front/Side are in the toolbar; the renderer selector is in the status bar. Shortcuts appear in tooltips. The Jobs view has an empty-state text. The title is "ANYfem". Geometry operations are reordered into six labelled groups (same 33 operations), and their palette sits below Apply.
- editors.py: a natural-height form host replaces the inner scroll area. A compact unit hint follows the unit profile. distribution_policy and *coordinate_system_id are drop-downs whose text() returns the stored value; coordinate systems refresh. "Ref" is shown as "Target". The generic JSON placeholder is no longer the constraints example.
- tasks.py: Mesh shows the essential controls, Generate/Cancel and folded sections (Seeding, Layout preview, Advanced, Diagnostics). Solve has primary Solve/Cancel, a no-mesh hint, folded Output requests (count in title), Resources and Advanced options, plus Run log/Convergence headings. Results and Visualization buttons are grouped in grids and rows.
- navigation.py: grouped option icons with headings, keeping combo indices. widgets.py (new): FormHost, Collapsible, ValueCombo, ChoiceEdit. theme.py, tree.py: polish; tree labels no longer truncate.

Tests: new tests/test_qt_gui_sweep.py (11). Contract updates in test_qt_workbench.py, test_qt_visibility.py and test_qt_geometry_schema6_consumer.py (value()/set_value(), workspace heading, new shortcuts).
Evidence (C:\ANYworkspaceWork, ANYsolver .venv, sibling ANYgeometry/ANYmesh sources, offscreen):
- Same Qt/UI selection before and after: 555 passed / 62 failed before, 566 passed / 62 failed after, with an identical set of failures. Those 62 failures already existed (mostly "cannot pickle 'mappingproxy'"). Logs: anyfem-gui-sweep-baseline-tests-20261020.log and anyfem-gui-sweep-final-tests-20261020.log, failure lists *-failed-20261020.txt.
- Both review-driven regression tests fail on the earlier code and pass after the fix.
- Screenshots: anyfem-gui-sweep-{baseline,final}-20261020\.
Review: Codex gpt-6.1-sol, high effort, read-only, session 01a15acc-0c25-7ce3-9305-69ad7b01635a. Round 1: Geometry fields were below the fold, fixed. Round 2: the unit hint went stale after a profile change, fixed. Round 3: clean. Logs: anyfem-gui-sweep-codex-review-r{1,2,3}-20261020.*.
Not done: no commit or publication. construction.py (the other session's sketcher panel) is left unstyled. Real-display and GPU-renderer visual checks were not run; only offscreen software rendering was used.
