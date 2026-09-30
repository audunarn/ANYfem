# Experimental GE point-panel consumer

This Level A headless slice constructs a fresh stiffened-shell analysis through
`anysolver.stiffened_shell.create_analysis`. It accepts the exact
`B3_GE_E4_PL_STIFFENED_SHELL_V1` definition: a connected flat Q4/S3 shell panel,
native B3-GE beam definitions, explicit point connections, physical fixed
coordinates, and nodal actions. The native beam definitions come from
`anysolver.b3_ge.define_beam`. The consumer stores inputs in a separate
`anyfem.experimental-ge-point-panel-v1` JSON record; save/reopen checks the
profile and each native beam's hash before creating a fresh analysis. The file
must use the `.anyfem-ge.json` suffix so it cannot be confused with an ordinary
`.anyfem` project.

```python
from anyfem.solve.stiffened_shell import ExperimentalGEPointPanel, save_case, load_case

# shell, beams, points, fixed, and actions use the public anysolver
# stiffened_shell and b3_ge definition types.
case = ExperimentalGEPointPanel(shell, beams, points, fixed, actions)
save_case(case, "panel.anyfem-ge.json")
reopened = load_case("panel.anyfem-ge.json")
owner = reopened.create_analysis()
result = owner.solve_linear(reopened.actions)
owner.close()
```

The same reopened definition can start a separate experimental elastic finite
run. Its accepted solver state belongs in a solver checkpoint, not in the input
file:

```python
owner = reopened.create_analysis()
accepted = owner.solve(reopened.actions, (.5, 1.))
checkpoint = owner.checkpoint()
owner.close()

resumed = reopened.create_analysis()
resumed.restore(checkpoint)
continued = resumed.solve(reopened.actions, (2.,))
resumed.close()
```

The result remains **experimental and unqualified**. This file is an input
record, not a solver checkpoint or an ordinary `.anyfem` project. It stores no
mesh, accepted state, results, jobs, or UI selections. Reopening constructs a
fresh owner; it does not resume an analysis. The consumer currently exposes
construction, input persistence, preflight, reference-linear solving and bounded
elastic finite stepping through the returned owner. Line/footprint attachment,
plastic finite response, modal
and buckling workflows, and GUI project commands are outside this slice. The
solver's capability report is authoritative for its current experimental
operations. Neither this adapter nor a successful local solve changes its
qualification status or the legacy ANYfem defaults.

Task record: the main ANYfem checkout had unrelated edits, so this slice lives
on `codex/experimental-ge-panel-consumer`. The principal uncertainty was
whether the solver's public GE facade and native beam record could support a
separate round trip without changing the established project schema. The
bounded check is a saved/reopened two-Q4, two-B3 point-panel solve, finite
accepted-state continuation, and fail-closed profile and beam-integrity checks.
Scientific acceptance,
release gates, ownership, resource limits, and any frozen execution authority
remain unchanged. The earlier reference-linear smoke test used local ANYsolver
`6b036b5c` and 38 nearby ANYfem tests passed. A later Revision 7 installed-wheel
run passed all three consumer tests, including finite restore and continuation;
the wheel identity and imports are recorded in the solver's living note. The
runs used `NUMBA_DISABLE_JIT=1` after an ordinary import stalled; they do not
establish qualified or accelerated-runtime behavior.

## Separate B2 inputs and reference modes

`anyfem.solve.stiffened_shell_b2.ExperimentalB2PointPanel` persists physical
B2/Q4 point-panel inputs in `.anyfem-b2.json`. Its default
`anyfem.experimental-b2-q4-point-panel-v1` remains static and rejects density,
beam mass and prestress metadata. It preserves ordered load cases, affine
supports and selected recovery; `solve_linear_many()` delegates bounded batches
and complete-case cancellation to the public solver owner.

Select `schema=SCHEMA_MASS` explicitly in `from_definition(...)` for
`anyfem.experimental-b2-q4-point-panel-mass-v2`. Each shell then records its
nullable `density_kg_m3`; each beam records nullable physical mass metadata:
source ID, local origin in metres and the complete 6-by-6 per-length matrix.
The velocity order is `[vx, vy, vz, wx, wy, wz]` at that declared origin.
ANYsolver validates physical section mass and applies the reference-axis
transformation. The consumer infers no density or inertia from stiffness.
Missing mass stays missing; it is never replaced by zero or discarded on reopen.

```python
from anyfem.solve.stiffened_shell_b2 import (
    ExperimentalB2PointPanel, SCHEMA_MASS, save_case, load_case,
)
from anysolver import stiffened_shell

case = ExperimentalB2PointPanel.from_definition(
    definition, cases, selected_dofs=selected_dofs, schema=SCHEMA_MASS,
)
save_case(case, "panel.anyfem-b2.json")
reopened = load_case("panel.anyfem-b2.json")
batch = reopened.solve_linear_many()
modes = reopened.modes(stiffened_shell.B2ReferenceModesRequest(count=3))
```

Complete explicit shell and beam mass is required for reference modes. The
current solver admits coordinate supports, including rescaled selectors; it
rejects general MPC modal requests. Partial-mass and affine models remain valid
static inputs. Physical model admission happens through `create_analysis()`;
construction validates the bounded input format, not every solve prerequisite.
Modal cancellation and numerical failure retain the solver's status and details;
delegated operations close their owner even when an exception propagates.
`create_analysis()` returns an owner the caller must close.

Both formats are **experimental and not qualified**, input-only, separate from
ordinary native projects and solver checkpoints. They admit no new prestress,
buckling, finite B2 response or GE spectral operation. Save/reopen starts a fresh
owner and does not resume an accepted state. Unsupported versions or fields fail
explicitly, including an attempted mass-v2 downgrade to static v1.
