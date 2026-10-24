# Sketch grid coordinates and responsiveness — 2026-10-24

Level A presentation change, authorised by the user's sketch-mode request. Preserve the existing dirty ANYfem tree. No solver, persisted geometry or sibling changes.

Plan and acceptance:
1. Add sparse grid-edge world X/Y/Z labels, derived from the resolved workplane and current display units. Check translated XY/XZ/YZ and oblique planes, extent changes, and cancellation. Keep local u/v axes.
2. Exercise Qt sketch hover, drawing, selection/dragging, camera navigation, tool switching and cancellation. Measure event/refresh durations, verify bounded overlay lifetime and retained-scene reuse. Repair demonstrated in-scope responsiveness defects; distinguish offscreen software evidence from native/GPU behaviour.
3. Review the exact delta and run relevant sketch regressions. Record counts, failures, measurements and limitations here.

Implementation ownership: direct OpenAI fallback. Configured Sonnet 5.5 was attempted with scoped permissions but Anthropic rejected inference for insufficient credit; no worker source edits occurred. Mistral exhaustion remains cached. Worker evidence: `C:/ANYworkspaceWork/anyfem-sketch-20261024/`.

Investigation question: does adding bounded coordinate text materially affect preview refresh, and does the existing sketch event path retain scene/annotations under repeated input? Compare the same bounded software Qt sweep before/after, then inspect any slow stage before choosing a repair. Timings are diagnostic, not a platform-wide latency guarantee.

Implemented and verified against the assigned criteria:
- `src/anyfem/ui/qt/sketch_coordinates.py`: four sparse world-coordinate ticks on the visible bottom/left viewport edges, plus a world-coordinate/unit heading. Each tick uses the viewer's public ray/plane intersection and the resolved workplane, including its translation, orientation and normal offset. Display conversion uses the active unit profile. The local u/v axes remain unchanged. Overlapping or oversized labels are omitted; parallel/behind-camera rays produce no invented coordinates.
- `src/anyfem/ui/qt/viewport.py`: attaches the mouse-transparent sibling overlay to viewer paints and renderer changes, clears it on cancellation, restores it after a failed renderer switch. Cached label content prevents repaint feedback; no polling timer, model geometry or retained-scene annotation is added.
- `tests/test_qt_sketch_coordinates.py`: nine cases covering translated/offset XY/XZ/YZ and oblique planes, independent projection of displayed numbers back to their ticks, camera/resize/unit updates, very fine grids, bounded preview lifetime, same-plane and different-plane restart, renderer replacement/failure, cancellation, invalid rays and idle painting.

Design refinement: the baseline screenshot showed that the finite grid's outer boundary is often outside the viewport. Labels therefore follow the **visible viewport edges**, with their coordinates measured at the corresponding edge ticks on the sketch plane. This keeps spatial information visible during pan/zoom instead of attaching labels to off-screen grid endpoints.

Verification (Python `C:/Python/Python314/python.exe`; source paths include ANYfem, ANYgeometry, ANYmesh and ANYloads):

```text
ANYFEM_RUN_QT_TESTS=1 QT_QPA_PLATFORM=offscreen
C:/Python/Python314/python.exe -m pytest -q tests/test_qt_sketch_coordinates.py tests/test_qt_sketch_overlay_lifetime.py tests/test_qt_sketcher_controller.py tests/test_qt_sketcher_workflow.py tests/test_sketcher_controller.py --tb=short
50 passed, 6 existing Qt deprecation warnings; exit 0; process wall 38.3916875 s.

ANYFEM_RUN_QT_TESTS=1 QT_QPA_PLATFORM=offscreen QT_SCALE_FACTOR=2
C:/Python/Python314/python.exe -m pytest -q tests/test_qt_sketch_coordinates.py --tb=short
9 passed; exit 0; process wall 12.4724871 s.

git diff --check
Passed (existing CRLF conversion notices in unrelated dirty files).
```

Earlier failures are retained: the first diagnostic lacked ANYloads on its source path; the first test run had one test-helper restart setup failure (14 passed); the next broader run had one child-process import failure because ANYloads was absent from PYTHONPATH (47 passed). The harness/source path and test setup were corrected; final regression is green. Some Python 3.14 runs print a `pyreadline3` invalid-handle destructor message after successful pytest completion; all final check processes exited 0. No application exception was observed in either GUI sweep.

Responsiveness sweep: 20-point profile; 60 hover events, 15 explicit refreshes, 10 drag moves, 5 tool changes, 6 camera orientations, 5 wheel zooms, 5 pan moves and cancellation. Software/offscreen and native Windows/GPU were exercised with exact settings recorded in the run JSON. Screenshots were visually inspected in light/software and dark/native GPU modes.

| Measured stage | Software median / p95 ms | Native GPU median / p95 ms |
| --- | --- | --- |
| Hover event plus event processing | 0.35 / 0.73 | 0.56 / 14.06 |
| Drawing click plus event processing | 6.40 / 7.98 | 16.71 / 28.41 |
| Drag move plus event processing | 1.00 / 5.59 | 1.05 / 7.15 |
| Orbit update plus event processing | 5.31 / 6.11 | 16.66 / 18.02 |
| Zoom update plus event processing | 4.89 / 6.61 | 16.21 / 17.73 |
| Pan move plus event processing | 5.58 / 6.54 | 16.62 / 17.91 |

Longest sampled native action: 34.80 ms (tool change). The tracked overlay refresh function itself measured software median/p95 1.35/1.85 ms and native GPU 11.16/25.54 ms (103 calls each). Native timings include renderer scheduling; they are not directly comparable with offscreen timings. The software baseline tracked refresh median/p95 was 1.10/1.45 ms: labels add a small measured cost, with no claimed speedup.

Both sweeps retained stable scene/text counts (6 entries, 22 world-text annotations), a single undo entry per drag, and no pending preview after cancellation or geometry revision change. Native GPU had zero full scene clears before cancellation. Software had one during camera interaction; the hover-specific lifetime tests independently confirmed no retained-scene rebuild. The nine-case suite additionally verifies that the ruler does not keep an idle canvas repainting.

Evidence: `C:/ANYworkspaceWork/anyfem-sketch-20261024/` contains command/environment/process-wall JSON, complete logs, diagnostic script, baseline/candidate/native-GPU timing JSON and PNG previews. `final-sketch-regression.log`, `hidpi-coordinate-tests.log`, `candidate.json`, and `native-gpu.json` are the final relevant checks. Failed worker and diagnostic evidence is preserved.

Review: source and integration self-reviewed, with independent numerical camera round-trip checks and real widget/GPU evidence. No independent model review was required for this Level A presentation change. Existing dirty work was retained; only the viewport integration and the three new task files were changed by this task. No commit, publication, full repository suite, installed-artifact release check or large-model latency guarantee is claimed.

Prospective worker-policy transition: the user replaced worker instructions during final verification, disabling Claude API routing and preferring the subscription-authenticated Claude CLI for future suitable work. No further API inference was attempted. Completed implementation was retained as directed; no new worker assignment was needed to finish this local verification/handoff.
