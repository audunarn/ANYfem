# Experimental shell-pressure consumer, 2 October 2026

Typed `ExperimentalGEPointPanel(..., pressure_load=request)` owns and persists
`GEShellPressureLoad` with explicit element IDs, signed pressure and native
reference-dead/current-surface choice. Default Project solve is reference-linear;
explicit targets dispatch finite stepping. Legacy schemas and defaults remain.
Input model identity, strict schema, mutual load exclusions and unsupported
footprints are checked; exceptions, failed status and accepted prefixes pass
through without becoming successful results. Experimental status remains explicit.

Run `examples/stiffened_shell_ge_pressure_consumer.py` for a two-Q4/two-native-GE
Project save/reopen, linear solve, cancellation after0.25, disk checkpoint restore,
and identical direct/resumed0.5/1 continuation. Pressure acts on the shell
interpolation surface. Input project and accepted checkpoint are distinct files.
Current-area pressure has no endpoint potential.

## Verification and exact artifacts

- Source adapter/regressions119passed; source full workflow/cleanup2passed.
- Installed adapter/regressions119passed in4.07s pytest (6.3815s route).
- Final installed workflow/cleanup2passed (8.2229s route,183058432B sampledpeak).
- ANYfem wheel `a27398219e649e742bc703a2260d0b86b4ec745d85cc9172e026664f759a0e09`:98modules.
- ANYsolver wheel `ac2ea592e6316af7fa2d2ea5a4c1d5d7e6a5acc041c2b290abff815ea5199772`:396modules.
- 281actual solver/consumer imports inside the isolated site;
  effective1numericalthread, JIT disabled. All source module hashes match wheels
  and installed files. Package versions unchanged. No source-root fallback.
- Raw archive `3877adba10520f2da17f7c6224e26894b9504f6fce1cd3959faa9579d8ac479f`, 81members. Contains commands,
  runtime/dependency identity, fixtures, disk projects/checkpoints, source hashes
  and reports. Wheels/site remain under Temp/r8-anyfem-ge-pressure-installed-v1
  and Temp/r8-ge-pressure-installed-v1; no publication.

Integration owner independently inspected the worker's two adapter changes and
all relevant tests, then executed source and installed checks. Worker reviewed
root-authored example/workflow: no consequential finding; copied label
'pressure beams' corrected to'shell pressure'. Final installed affected tests
were rerun;119unchanged checks reused by impact. Original example fixture is
retained under fixture-history-v1 and hash-checked against its consumed manifest.
This is OpenAI review, not different-family qualification.

## Boundaries

Real consumer route here is Q4; status/schema/dispatch tests also use mocks.
Mock failures do not mechanically qualify S3 or mixed pressure. Solver all-S3
pressure lifecycle has separate source evidence. Mixed Q4/S3 follower still
FAILS its unchanged component acceptance; finite S3 enhanced reference-dead work
is explicitly unsupported. No full G4A/G6/platform/material/attachment acceptance
or release claim. All these mandatory rows remain open; no tolerance changed.
