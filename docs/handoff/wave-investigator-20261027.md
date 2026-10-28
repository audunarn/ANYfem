# Wave time-series investigator navigation — 2026-10-27

State: implemented and verified against the assigned Qt/navigation criteria. Independent parent review clean after repair; source-only delivery, no publication.
Scope: six force/moment preview graphs in WaveResultantPlot. Preserve existing dirty wave dialog, numeric interval controls, original-sample snapping, SI-to-kilo display conversion and bounded rendering.

## Implemented (src/anyfem/ui/qt/wave_preview_plot.py)
- Shared viewport `view_interval` (absolute time, separate from `interval`); `_set_view` clamps to the record and keeps >=2 samples. Reset on `set_data`, validated against `times` identity so direct `times=None` clears are tolerated.
- `visible_extrema()` -> per graph `(min,t_min,max,t_max)` from raw visible samples (argmin/argmax, earliest tie); `y_bounds()` per-graph auto y range from the same visible samples. Markers: red ▼ max, blue ▲ min, purple ◆ "min = max" when flat; labels with value + absolute time, placed to avoid overlap.
- Gestures: left drag in body right = zoom in, left = zoom out (span / dragged fraction about gesture centre, clamped); <5 px or outside = no-op; handle within 8 px (visible endpoints only) wins and keeps emitting selectionChanged. Zoom never emits it.
- Buttons `WavePlotFitAll` ("Reset / fit all"), `WavePlotFitSelected` ("Fit selected interval") with tooltips + hint text; disabled with no data (`update()` override syncs state).
- Layout: plot top margin 80->106, minimum height 420->440 to fit button row. `_x(time,rect)` signature unchanged.
- Drawing bounded: data slice clipped to each plot, pixel_extrema over visible slice (+1 sample margin).

## Tests: tests/test_qt_wave_preview_navigation.py (ANYFEM_RUN_QT_TESTS=1)
Suggested (parent): `$env:ANYFEM_RUN_QT_TESTS=1; $env:QT_QPA_PLATFORM='offscreen'; python -m pytest tests/test_qt_wave_preview_navigation.py tests/test_qt_wave_window.py tests/test_qt_wave_load.py tests/test_qt_irregular_wave_load.py -q`

## Repair 1 (after check-01: 19 passed / 2 failed, both test-fixture mistakes) — implemented, unverified
- Flat/tied test: column 1 now scaled by 1000 (newtons), so 3 kN / earliest-tie assertions are unchanged.
- Zoom test: isolated 50 kN spike at t=3 (outside zoom 5–10) added before plot creation so visible-fit y ranges must shrink; exact bound/extrema/unchanged-data assertions kept.
- Product fix: long-record envelope branch now feeds pixel_extrema ONLY viewport samples (no outside neighbour that could draw a false peak); the ±1 neighbours remain for the short non-decimated line path.
- New regressions: spy on pixel_extrema inputs — spike at 2.9995 just outside [3,7] absent from all six long envelopes; spike inside at 5.0 retained.

## Worker handoff risks (superseded by parent verification below)
- Unrun: pixel-tolerance assertions (zoom ±0.2 s, zoom-out ratio) may need tuning.
- Handle drag no longer jumps the handle on press (only on move); old test still moves.
- Visual check at 620x440 and ~700 px width needed (label overlap, hint elision).
- Dialog layout effect of taller minimum height (680 px dialog) unverified.
Evidence: C:/ANYworkspaceWork/evidence/wave-investigator-20261027 (nothing added by this worker).

## Final parent acceptance

- All six graphs show raw visible-sample min/max occurrence markers with value and absolute time. Equal extrema select the earliest sample; flat signals use a combined marker. One synchronized time viewport supports left-to-right drag zoom-in, right-to-left drag zoom-out, Reset / fit all and Fit selected interval. Fits/zoom do not change the analysis interval or source samples. Visible blue endpoint handles retain original-sample snapping; hidden handles cannot steal zoom. Empty/cleared data disables both buttons.
- Independent repair excludes out-of-view samples from long-record decimation so an unseen spike cannot create a false visible peak. Short traces retain neighbours for clipped line continuity; both inside/outside spike regressions pass.
- Exact command: `C:/Python/Python314/python.exe C:/ANYworkspaceWork/evidence/wave-investigator-20261027/check.py tests`. Child environment: `ANYFEM_RUN_QT_TESTS=1`, `QT_QPA_PLATFORM=offscreen`; source-bootstrap then `pytest -q --basetemp <attempt>/pytest-temp tests/test_qt_wave_preview_navigation.py tests/test_qt_wave_window.py tests/test_qt_wave_load.py tests/test_qt_irregular_wave_load.py`. Final result23passed in53.70s, process wall58.9856194s, exit0. All owned processes retired; no source changes during the check. Exact argv/hashes/full logs: check-03-tests under the evidence root.
- Capture command: same runner with `capture`; check-04-capture passed (3.7289286s process wall). Full/selected/reset and minimum620x440 captures retained, with normal1000x690. Labels, buttons, clipping and synchronized axes visually inspected. Selection116.1-128.4 remains unchanged through fit/reset captures.
- Failed evidence retained: check-01 had19passes/2failures from an N-vs-kN fixture and a periodic interval retaining every original peak. Correct units and an isolated out-of-view spike repair those fixtures without changing assertions/tolerances. Original plot backup: wave_preview_plot.before.py in the evidence root.
- Author: scoped Claude subscription Sonnet5.5/medium. Independent review/acceptance: OpenAI parent, clean after repair. Write audit limited to the plot, its new test file and this note. Dirty wave_load.py/existing tests and prior compact-results work preserved.
- Limits: offscreen Qt/widget verification, not installed-package or desktop/GPU qualification. No wave/model/sample/solver/load changes, BEM, structural solve, Git integration or publication. Restart the source app to load changes. Full evidence: C:/ANYworkspaceWork/evidence/wave-investigator-20261027.
