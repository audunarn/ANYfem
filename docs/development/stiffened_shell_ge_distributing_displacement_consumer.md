# Experimental finite distributing displacement consumer

The existing `ExperimentalGEFiniteDistributingPanel` and
`ExperimentalGEFiniteDistributingMaterialPanel` accept optional keyword-only
`displacement_control` and `displacement_seed_factors`. Their existing
`from_definition` constructors, `AnalysisDefinition.experimental_point_panel`,
Project persistence, standalone `save_case`/`load_case`, and public owner factory
remain the entry points. This development route is explicitly
`EXPERIMENTAL_NOT_QUALIFIED`. It does not qualify the reduced physical connection,
yield reversal, actual-zero material unloading, G3, or programme G0–G6 acceptance.

```python
panel = ExperimentalGEFiniteDistributingPanel.from_definition(
    definition, actions,
    displacement_control=ss.DisplacementControl("beam", 103, 2),
    displacement_seed_factors=(.25,))
analysis = AnalysisDefinition.experimental_point_panel("physical pad", panel)
result = solve_point_panel(analysis, targets=(target_m, next_target_m),
                           max_iterations=20, max_backtracks=12)
```

Components 0–2 select physical translations in the shell or beam domain. Unknown,
restrained, rotational, event/trial, malformed and untyped controls fail closed.
The public solver remains authoritative for assembled mechanical admission.
The consumer seed is an owned, nonempty, finite, strictly increasing positive
factor tuple. Selected cyclic material requests retain the existing immutable
model-bound reference actions; nonunit positive seeds preserve their actual seed
factor in the solver's accepted state and checkpoint metadata.
These limits apply to the fresh combined consumer helper; they do not change the
solver's lower-level public contract. No adaptive, arc, staged or chart controls
are introduced.

`solve_point_panel` constructs a fresh owner, accepts the persisted ordinary-force
seed using the same actions, then dispatches the signed physical displacement
targets through the existing `owner.solve(..., displacement_control=...)` method.
Targets are a nonempty finite immutable tuple of absolute translations in metres
from the model reference. Adjacent targets must differ; the solver checks the
first target against the authentic accepted seed. A callable observer or `None`,
integer iteration limit1–24 and integer backtrack limit0–12 are validated before
owner construction, including before the force seed. Cancellation reaches both
phases. `on_accepted` observes displacement publications only. Failures close the
fresh owner, and an unsuccessful seed never launches displacement stepping.
The returned result carries the actual solver material histories and evaluated
physical footprint wrenches without consumer reconstruction. Its `amplitude`
is an affine increment: `accepted_seed_load + amplitude * actions_direction`.
It is not a force factor, and no virgin zero state is inferred as a seed.

For a durable accepted prefix, checkpoint recovery and continuation, use
`analysis.create_point_panel_analysis()`. This factory returns a fresh admitted
public owner and performs no implicit solve. Seed a new owner explicitly, or
restore an existing solver checkpoint; after restoration continue directly with
displacement controls. Do not call the combined helper to continue a restored
history, because every helper invocation creates a separate fresh run. The
consumer never reseeds restored owners or switches their history back to force.

```python
owner = analysis.create_point_panel_analysis()
try:
    owner.restore(checkpoint_bytes)
    result = owner.solve(panel.actions, (next_target_m,),
                         displacement_control=panel.displacement_control)
    checkpoint_bytes = owner.checkpoint()
    wrenches = owner.recover(selection=panel.recovery_selection)
finally:
    owner.close()
```

Force-only records retain their exact v1 input schemas and admissions. Selecting
displacement writes `anyfem.experimental-ge-finite-distributing-displacement-panel-v2`
or `anyfem.experimental-ge-finite-distributing-material-displacement-panel-v2`,
with the exact policy `B3_GE_DISTRIBUTING_DISPLACEMENT_V1_DEV`, the distinct
`fixed_signed_physical_translation_from_ordinary_force_seed_only` admission,
the old force admission retained in `force_seed_admission`, and explicit control
and seed fields. Targets remain runtime arguments. Reopening saves input only;
it does not upgrade native persistence or recreate accepted state. Material
policy, native beam bytes and cyclic reference pattern retain their identities.
Legacy force-only capabilities can admit unselected force input, while selected
displacement requires the exact modern capability and checkpoint pair. Partial
or mismatched capability upgrades fail closed and close the owner.

`examples/stiffened_shell_ge_distributing_displacement_consumer.py` covers
standalone and Project persistence, the fresh combined helper, an explicit seed,
public physical target reading, checkpoint/restore/continuation and physical
wrench recovery. `tests/test_stiffened_shell_ge_distributing_displacement_consumer.py`
adds source controls for ownership, strict schemas/admissions, material/native/
cyclic policy retention, refusal before owner construction, ordered dispatch,
cancellation, observer errors, closure and a factory that never reseeds.

Source implementation is complete but unverified. No Python import, numerical
test, wheel build, installation or example execution was run during this
source-only assignment. The living handoff in
`.development/distributing-displacement-consumer-20261007/handoff.md` proposes
focused source/installed checks and one public example under a fresh root-owned
allowance. Existing solver installed development evidence is reusable for its
exact wheel identity; it does not establish consumer verification or qualification.
