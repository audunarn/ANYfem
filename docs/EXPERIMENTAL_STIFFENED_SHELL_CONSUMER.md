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
elastic finite stepping through the returned owner. Line/footprint attachment in
the point record,
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

## Separate BL5 inputs and elastic finite continuation

`ExperimentalGEBL5Panel` is a separate Level A input record for the current
elastic generalized-B3/Q4 BL5 owner. Its schema is
`anyfem.experimental-ge-bl5-panel-v1`; it uses the same `.anyfem-ge.json`
suffix and atomic bounded save/load functions. The point-panel v1, J2 v2 and
Q4-pullback v3 records keep their original fields and meaning. A point record
cannot acquire lines by adding a field or changing its schema label.

The BL5 record requires pure Q4 elastic shell inputs, native generalized B3
beam definitions, one or more public `ExperimentalBL5Line` definitions and
fixed coordinates. It stores the native beam bytes and their SHA-256 bindings,
the complete declared line controls, and nodal actions. Physical-fibre beams,
shell J2, S3, point attachments and the algorithmic Q4 point pullback are outside
this record. Those combinations fail explicitly rather than selecting a different
solver route. Construction checks the input family without assembling an
analysis. The solver owns trace geometry, supports, constraint admission and
elastic-response checks.

Every line explicitly preserves `shell_nodes`, ordered `beam_halfcells`,
`multiplier_span`, `expected_beam_arm` in metres and `basis`. All valid public
controls remain available: `p0` with span 1 or 2, `continuous_p1`, and
`continuous_p1_coarse` with span 1. Public line validation remains authoritative;
in particular, native continuous-P1 requires the shell trace to be at least as
fine as the beam half-cells. No basis is scientifically selected by this adapter.
The current owner handles its admitted straight, oriented material trace,
including nonmatching partitions. It bonds position and the top-surface
director while releasing normal spin. Curved or reversed mappings remain
excluded. Finite actions currently admit translation forces only, and the owner
rejects a finite trial entering generalized beam plastic response.

```python
import anyfem
import anysolver
from anysolver import stiffened_shell
from anyfem.solve.stiffened_shell import ExperimentalGEBL5Panel, save_case, load_case

# shell, beams and fixed are explicit public solver definitions in SI units.
# These node/half-cell IDs describe the nonmatching public solver example;
# use the declared topology of the actual model.
line = stiffened_shell.ExperimentalBL5Line(
    (1, 2, 3, 4), ((0, 0), (0, 1), (1, 0), (1, 1)), basis="p0")
actions = (stiffened_shell.NodalAction("beam", 105, (0., 0., 100., 0., 0., 0.)),)
case = ExperimentalGEBL5Panel(shell, beams, (line,), fixed, actions)
save_case(case, "bl5.anyfem-ge.json")
reopened = load_case("bl5.anyfem-ge.json")
owner = reopened.create_analysis()
resumed = reopened.create_analysis()
try:
    accepted = owner.solve(reopened.actions, targets=(.5, 1., .75))
    checkpoint = owner.checkpoint()
    resumed.restore(checkpoint)
    assert resumed.checkpoint() == checkpoint
    direct = owner.solve(reopened.actions, targets=(.9,))
    continued = resumed.solve(reopened.actions, targets=(.9,))
    assert owner.checkpoint() == resumed.checkpoint()
    print({"consumer_source": anyfem.__file__, "solver_source": anysolver.__file__,
           "status": continued.status, "qualification": owner.capabilities["status"],
           "accepted_factors": continued.accepted_factors})
finally:
    owner.close()
    resumed.close()
```

Reopening restores inputs and creates a fresh owner. Continued accepted state
requires the solver's separately obtained checkpoint; the consumer never decodes
or converts checkpoint bytes. The current finite BL5 identity is the solver's
V11 checkpoint route, distinct from historical point and line identities.
These files are neither ordinary native `.anyfem` projects nor result or job
artifacts. They do not establish BL5 qualification, production release, native
project support or a selected multiplier law. Actual runtime provenance comes
from the imported consumer and solver paths and the installed-artifact evidence,
not from the input schema label. Installed identities, verification outcomes and
remaining programme gates are recorded by the solver's living R8 task record.

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

## GE staged translation dead loads in input records and native Projects

`ExperimentalGEPointPanel(..., actions=(), load_programme=programme)` opts into
`anyfem.experimental-ge-point-panel-staged-v4`. Its envelope nests the complete
unchanged v1/v2/v3 point input and the public solver's immutable
`stiffened_shell.StagedDeadLoadProgramme` descriptor, including all future
absolute endpoints and the physical model identity. Legacy actions must be
empty in this mode. Point inputs without a programme retain their original
schemas, defaults and reference-linear dispatch. BL5/B2 do not acquire this
staged policy.

Construct the programme using a public point owner's `preflight()["identity"]`
and translation-force endpoint tuples. Positions describe stage progress,
not force amplitudes. The solver owns policy/bounds/interpolation/admission;
ANYfem copies none of those mechanics. `panel.load_input` explicitly selects
the programme or the unchanged legacy action tuple.

```python
from anyfem.model.records import AnalysisDefinition
from anyfem.solve.stiffened_shell_project import (
    point_panel_from_analysis, solve_point_panel,
)

# staged_panel is an ExperimentalGEPointPanel with empty actions and the
# public model-bound programme. Store the record in project.analyses and use
# the ordinary save_project/load_project .anyfem APIs.
record = AnalysisDefinition.experimental_point_panel("staged GE", staged_panel)
result = solve_point_panel(record, targets=(.5, 1., 1.5, 2.))

reopened_panel = point_panel_from_analysis(record)
owner = reopened_panel.create_analysis()
try:
    owner.solve(reopened_panel.load_input, (.5, 1.))
    accepted_checkpoint = owner.checkpoint()
finally:
    owner.close()
```

The Project adapter requires explicit staged targets and forwards existing
finite controls, cancellation and `on_accepted`; it propagates the public
owner's original status/errors and closes its temporary owner. Finite controls
on legacy reference-linear records fail explicitly. To restore and continue,
create a fresh owner, pass the separately retained opaque checkpoint directly
to `restore`, then use the reopened panel's `load_input`. Save/reopen stores
inputs only and never converts, decodes or embeds accepted-state checkpoints.

Save/reopen validates and deep-owns the public descriptor without assembling a
mechanical owner. Actual panel/model identity is checked when `create_analysis`
constructs and preflights the public owner. The owner checks every endpoint's
node/scope admission before trials when it solves. Thus structural persistence
is not a promise a particular model or requested path is mechanically admitted.
Mutated nested Project payloads are revalidated on save; corrupt future identity,
unknown envelope versions, moments and ambiguous legacy/staged combinations
fail closed. Native `.anyfem` format/version/default selectors remain unchanged.
The distinct standalone `.anyfem-ge.json` input file keeps its existing meaning.

This is experimental consumer persistence/dispatch. It does not qualify F06,
yielding/staged history transport, complete unloading, line loading or the
broader programme. A zero-force endpoint retains the solver's existing strict
equilibrium behavior. Runtime provenance is the actual imported consumer/solver
source and installed-artifact evidence, not the schema or input filename.
