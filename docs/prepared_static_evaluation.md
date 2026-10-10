# Prepared static evaluation

**Experimental and currently unaccepted for the governing accuracy gates.**
Implementation tests do not establish scientific acceptance. The prior milestone's
integrated 97-test check passed, including the preserved canonical principal comparison.
Full-load/solve roundoff and envelope-contender qualification remain unresolved;
see the solver's `docs/PREPARED_STATIC_EVALUATOR.md` and preserved
`tests/prepared_static_qualification_cases.py`. Load accumulation/recovery roundoff
can affect stress values and governing envelope identities. Tolerances have not
been relaxed. Existing/default APIs remain unchanged. This document makes no
performance-GO or release claim. The final free-free holdout was slower
(9.1743 seconds prepared versus 4.7274 seconds exact stream), so promotion is
NO_GO. The implementation remains isolated and uncommitted.

`anyfem.solve.prepare_linear_static_evaluator` prepares the fixed structural
model once and constructs independent static loads through the canonical ANYfem
load builders. Existing live static APIs retain their behavior.

```python
from anyfem.solve import prepare_linear_static_evaluator

with prepare_linear_static_evaluator(
    project, mesh=mesh, output_nodes=[node_id], output_connections=["end"],
) as evaluator:
    for block in evaluator.iter_batches(case_names, batch_size=32):
        selected = block.node_displacement(node_id)
        motion = block.connection_motion("end")
        stresses = block.stresses()
        reactions = block.reactions()
```

Requests are case names, external `LoadCase` records, or `StaticLoadRequest`
records with identifiers, combinations and independent time overrides. An empty
iterable yields no blocks. Time is an input to load evaluation, including arbitrary
nonlinear time histories; this route performs independent static solves.

Preparation captures the project load catalog, geometry, coordinate systems,
mesh data and resolved connection mappings. Named cases and combinations resolve
against those captured definitions. New external `LoadCase` objects are copied
once when consumed. Source edits and deletion after preparation cannot change
captured inputs. Neither a source FEModel nor source BuiltModel is retained by
the adapter. A preparation-only before/after witness checks application geometry,
mesh, named definitions and combinations alongside the solver owner's comprehensive
mechanics/constraint/class witness. Mixed source capture raises `ProjectError` and
closes the incomplete solver owner. Source changes after successful preparation
remain allowed. Per-case construction does not run `_freeze` or `_structure`.

Point forces and moments, pressures, wet expressions/mappings/tables/trusted
Python code, connection loads, line loads, surface tractions, gravity/acceleration,
combinations and their case/per-load envelopes use the existing canonical
builders. Provider evaluation occurs while numeric loads enter the solver;
no provider runs inside numerical solve spans. Provider coordinates and parameter
mappings are detached from retained input storage. Geometry revision mutation
checks remain fail-closed. The solver certifies the actual assembled numeric load
and supplies its full digest and diagnostics unchanged through each block.

`PreparedStaticBlock` stores selected numeric results and copied application
metadata, without a LinearSolution. Motion accessors and stress/reaction helpers
return copies. Mutating block connections, node mappings, provenance or the
snapshot diagnostics cannot mutate the evaluator's captured inputs.

The optional `envelope_recovery_config` selects a separate solver-owned envelope.
It can select all elements while `recovery_config` continues to select only the
per-case history needed by the caller. `evaluator.envelope_result()` returns a
detached `PreparedStaticEnvelope` with `complete`, `cases_processed`, `extrema`,
`snapshot_identity`, solver diagnostics and copied application `provenance`.
Each extrema entry identifies its quantity/entity/component and retains signed
minimum, maximum and absolute-maximum values, governing case IDs, times and input
indices, with associated component values from the same governing case. Repeated
case IDs remain distinguishable by input index. The adapter does not construct
whole-model `LinearSolution` objects or reduce full per-case result dictionaries.
Omitting `envelope_recovery_config` retains the existing behavior and returns
`None` from the envelope accessor.

One envelope run may be active at a time. Starting another run resets its summary
when iteration begins; detached earlier summaries remain unchanged. Only fully
certified blocks contribute. Normal exhaustion, including an empty input, marks
the summary complete. Cancellation, callback errors and early close preserve an
incomplete summary of previously certified blocks. The accessor remains available
after closing the evaluator. Caller-retained summary copies consume caller memory;
the solver accounts its retained envelope storage in the remaining resource budget.

```python
from anysolver import RecoveryConfig

with prepare_linear_static_evaluator(
    project, mesh=mesh, output_nodes=[node_id],
    recovery_config=RecoveryConfig(element_ids=[element_id],
        components=["axial_stress"], include_stresses=True),
    envelope_recovery_config=RecoveryConfig(components=["axial_stress"],
        include_stresses=True, include_displacements=False, include_reactions=False),
) as evaluator:
    stream = evaluator.iter_batches(requests, batch_size=32)
    try:
        for block in stream:
            consume_selected_history(block)
    finally:
        stream.close()
    summary = evaluator.envelope_result()
    assert summary.complete
```

Preparation forwards `cancellation_token` and structured `progress_callback` to
the solver factory. The existing `_step_reporter` translates events for an optional
text `progress` callback without duplicating structured events. Cancellation at
solver preparation checkpoints releases the incomplete owner. The joint capture
witness also surrounds preparation callbacks, so mutations of application load
definitions during solver progress are rejected.

Close an iterator after stopping early, and close the evaluator deterministically
(or use its context manager). Closing the evaluator closes active solver/request
streams. Cancellation, provider failures and cleanup failures remain visible;
secondary cleanup failures are attached to the primary error. A closed evaluator
refuses new work. Missing `anysolver.prepare_static_evaluator` raises
`PreparedStaticUnavailableError`; solver refusals propagate without a legacy
fallback or fabricated successful zero results.

## Resource accounting and limits

The application retains a copied catalog and geometry. Its storage therefore
scales with the captured input catalog, including existing histories; it is not
independent of the number of authored input cases. Diagnostics report case and
history sample counts and `application_retained_input_bytes`, a one-time,
alias-deduplicated Python container/object/array storage estimate. That estimate
is reserved from `ResourceConfig.memory_limit_bytes` before preparing the solver.
A snapshot exceeding the available limit fails before solver preparation. A
separate `application_case_cache_limit_bytes` reserve bounds target, node, element,
wet-surface and centroid caches. Each cache entry is conservatively charged for
its keys, values and dictionary capacity before retention; entries exceeding the
ceiling are returned without caching. All caches clear after each case, including
failed cases. The canonical centroid helper's mesh-attached cache is removed so it
cannot bypass this ceiling. The solver receives the remaining memory budget after
both application reservations.

The estimate is not process RSS or native allocator usage. Native owner overhead,
temporary load-construction values and caller-retained result facade copies are
not measured by this retained-storage estimate. Do not interpret it as a demonstrated peak-memory bound. Numerical
resource accounting and admissible recovery/load capabilities belong to ANYsolver.
Performance and release qualification require their separate evidence.


## Supported and free-free public example

`examples/prepared_static_time_history.py` constructs a supported beam by default;
`--free-free` removes the support while preserving two independently balanced
force/moment patterns. Each pattern has a different nonlinear time law at irregular
samples, so this is a nonproportional static history. Its two-element mesh selects
one node, one element and two physical stress fields for history, and requests whole-model
solver-owned signed stress envelopes with
case/input-index/time identities, and supplies explicit resource and cancellation
controls through the public adapter. The node list and element/component lists
are output selections, not a modification of the mechanical model.

```text
python examples/prepared_static_time_history.py
python examples/prepared_static_time_history.py --free-free
python examples/prepared_static_time_history.py --stop-after 1
python examples/prepared_static_time_history.py --cancel-after 1
```

Normal exhaustion alone marks the solver summary complete. Early stopping or
cancellation leaves `complete=False` and exits with status1; solver/provider errors
propagate and never mark partial results complete. Iterators and owners close in
all paths. Block size2 is a request; the resource policy may reduce it. The earlier
selected-output reducer version passed supported and free-free smoke execution in
`fem-example-supported-01` and `fem-example-free-free-01`; those records do not
verify this new whole-model envelope version. Current milestone evidence is
recorded separately under `m2-fem-*`, and does not establish global accuracy or
benchmark acceptance.

The current two-element example passed supported and free-free smoke execution
(`m2-fem-example-supported-02`, `m2-fem-example-free-02`): each completed five
samples and reported extrema for both elements. The early-stop and cancellation
variants (`m2-fem-example-stop-01`, `m2-fem-example-cancel-01`) each preserved one
certified sample with `complete=False` and the expected exit status1. Resource
policy reduced the requested block size2 to1 under the explicit memory cap.
The focused final envelope check `m2-fem-envelope-04` passed11 tests, covering the
10 new envelope cases plus the changed public example's free-free parity case.
Earlier `m2-fem-envelope-02` passed59 tests and failed one test's expected wrapper
error text; the corrected test checks both `PreparedStaticError` and the original
provider exception cause. The remaining original adapter/integration cases passed
in that run. Failed check records remain preserved.

## Public load-coverage evidence map

The following test names are present in the focused application suite. Adapter
checks use a solver spy to compare canonical numeric load construction; they do
not establish numerical solver acceptance. Integration checks use the actual
prepared solver. Tests live in `tests/test_prepared_static_adapter.py` and
`tests/test_prepared_static_integration.py` respectively.

| Public behavior | Existing focused test |
|---|---|
| Point forces/moments, pressure, wet expression, connection force/moment, line load, surface traction, uniform acceleration, gravity | Adapter `test_every_load_family_matches_the_canonical_build`, parameters `point`, `pressure`, `wet`, `connection`, `line`, `traction`, `acceleration`, `gravity` |
| Independent case/per-load envelopes and combination factors | Adapter `test_case_and_per_load_envelope_factors_stay_independent` |
| Wet Python scalar/vectorized code, mutable vectorized samples, table and face mapping | Adapter `test_wet_sources_match_canonical_and_keep_owned_samples`, parameters `code`, `code_vectorized`, `table`, `mapping` |
| Wet provider evaluation count and numeric ingress | Adapter `test_wet_provider_evaluates_once_per_sample_outside_solver_spans`; its spy verifies ingress construction, not native solver span instrumentation |
| Provider errors and geometry mutation refusal | Adapter `test_provider_error_fails_the_case_without_bogus_output`, `test_provider_mutating_observed_geometry_fails_closed` |
| Named-case snapshot, late case refusal, external record ingress | Adapter `test_source_mutation_after_preparation_cannot_change_results`, `test_late_source_cases_are_not_visible`, `test_external_records_are_captured_once_at_ingress` |
| Labels, times, lazy requests and combinations | Adapter `test_lazy_requests_labels_times_and_progress`; integration `test_combinations_and_time_overrides_match_the_live_stream` |
| Actual supported shell nonzero selected motion, connection motion, nonempty selected membrane/bending stresses and support/MPC reactions | Integration `test_prepared_results_match_the_exact_live_stream` |
| Balanced free-free nonproportional public history with nonempty axial/torsional stresses and enabled reaction diagnostics | Integration `test_balanced_free_free_nonproportional_public_history_recovery_parity`; its support reaction map is physically empty |
| Actual source-mutation stability and full assembled-load diagnostics | Integration `test_prepared_results_survive_source_mutation_that_breaks_the_live_stream`, `test_solver_certifies_actual_assembled_loads` |
| Mixed snapshot refusal across app/FE capture | Adapter `test_joint_capture_refuses_mixed_source_snapshot`, parameters `mesh`, `definitions`, `mechanics`, `constraints` |
| Actual preparation cancellation, resource release and progress mutation refusal | Integration `test_preparation_cancellation_releases_actual_solver_owner`, `test_preparation_progress_mutation_of_app_definition_refused` |
| Independent envelope selection and detached application provenance | Adapter `test_envelope_configuration_and_detached_facade` |
| Whole-model envelopes alongside selected histories; supported/free-free and connected/disconnected nonproportional beam cases; uneven blocks and repeated IDs | `test_prepared_static_envelopes.py::test_whole_model_envelope_matches_live_nonproportional_history`; signed extrema, case/time/input identity and complete associated component values compare against actual live recovery |
| Whole-shell envelope with point, pressure, wet-pressure and connection loads | `test_prepared_static_envelopes.py::test_whole_shell_envelope_with_mixed_public_loads_and_selected_history` |
| Empty/reset/early-close semantics, one active iterator, source and returned-summary mutation isolation | `test_prepared_static_envelopes.py::test_envelope_lifecycle_empty_early_close_reset_and_source_mutation` |
| Cancellation, callback error and owner-close transaction boundaries | `test_prepared_static_envelopes.py::test_envelope_does_not_publish_uncertified_block` |
| Error during input construction leaves the next partial block unpublished | `test_prepared_static_envelopes.py::test_ingress_error_keeps_only_previously_certified_blocks` |

## Capability and evidence gaps

Canonical builder reuse covers existing application load families; it does not
broaden solver admission. Prepared solver capability refusals propagate. Foreign
solver load objects, unreviewed/custom element or material providers and unsupported
requested stress measures are not silently routed through the live solver.
Free-free inputs must satisfy the solver's balance policy; the example leaves
`allow_unbalanced_free_free=False`. Cylindrical acceleration and follower/generalized
section restrictions remain those of the canonical owners.

The focused application matrix is not exhaustive across every coordinate frame,
distribution policy, wet element mapping, structural/per-case mass combination,
material, element formulation or free-free recovery selection. Its wet mapping
parameter exercises face mapping. Real integration now explicitly requests and
compares nonempty selected stresses/reactions on a supported shell, and selected
axial/torsional stresses on a balanced free-free beam. The latter enables reaction
recovery but correctly has no support/MPC reactions. The supported and free-free
examples both passed smoke execution. These fixtures do not cover every recovered
component or formulation. Separate
solver tests and qualification evidence govern those capabilities. Resource storage
estimates, successful implementation tests and an executable example do not close
the unresolved full-load/solve and envelope accuracy gates or establish peak RSS,
installed-artifact acceptance, performance-GO or release readiness.

## Experimental source responses (M8)

`anyfem.solve.prepare_linear_static_response_evaluator` is an opt-in companion
to the unchanged direct factory. Pass `patterns={"name": app_case_or_request}`
and optionally `baseline_load_case=app_case_or_request`. Consume
`PatternLoadRequest(case_id, {"name": coefficient}, time=...)` alongside normal
application requests or native solver `PatternCase`/`StaticCase` records. A
`PatternLoadRequest.canonical_request` supplies an independent original-time
application case when source reconstruction is only a candidate. Dense coefficient
vectors follow declared pattern insertion order, as defined by ANYsolver.

`PeriodicLoadSource("series", period=6., time_step=.1)` declares a periodic
wet-pressure source. The adapter uses ANYloads' whitelisted expression parser and
evaluator, conservatively admitting time only through sin/cos of affine arguments
whose frequency matches the declared period. Spatial expressions and max/min wet
clipping are kept intact. It creates all 60 phase loads directly with unit envelope,
including zero-envelope phases, plus independent fixed connection-load patterns.
There is no division by a sampled envelope and no interpolation. Coefficients
apply the original case/per-load envelopes at original time and original sample
number. A sparse/shuffled `times=` selection does not renumber the original series
or shrink its phase library. Exact decimal-grid membership is required; adjacent
floating values are not snapped to a phase.

This declared-harmonic classification is **not a recurrence or source-equivalence proof**. Every periodic
sample sends its original-time canonical load to the solver for full-load
certification and canonical envelope replay. Reconstruction may be refused in
favor of that direct load; all unresolved governing samples require canonical
replay. Its preparation and recurring cost remain part of the workflow. No cheap
contender certificate, output bit identity or speed improvement is asserted.

Use `PeriodicSample(case_id, original_time)` for explicit/repeated labels; omit
requests to stream the declared selection. Sources with unsupported families,
nonperiodic expressions or noncanonical grids take the original-time direct
route, with `direct_reason` in diagnostics. Samples absent from the source's
original times are explicitly refused: use a full `StaticLoadRequest` with the
intended case identity, especially for number-based envelopes. Periodic v1 admits
wet expressions plus fixed connection vectors; other arbitrary application load
families remain available through direct requests. A separate baseline argument
with a periodic source is refused because that source already declares its full
canonical load.

The input catalog, phase-native packets, source definitions and owned geometry
are included in the application's preparation storage estimate. Phase/sample
catalog preflights and incremental pattern capture respect a supplied memory
limit; solver response columns have their own remaining resource budget. Estimates
are not process-RSS measurements. Callbacks stay outside numerical spans;
cancelled, failed or early-closed streams retain only certified blocks and report
incomplete envelopes. New runs reset envelopes; results/provenance are detached.

Executable example: `python examples/prepared_static_response.py`, optionally
`--stop-after 1` or `--cancel-after 1`. Focused source tests live in
`tests/test_prepared_static_response.py`: clipped-harmonic admission and refusal,
nextafter rejection, full phase preparation, sparse/shuffled number envelopes,
nonempty supported/free recovery, direct mixed requests, source mutation,
empty/restart/early-close behavior, and cancellation/resource refusal. Actual run
results and limitations are recorded in `.prepared-static-evidence/m8-fem-handoff.md`.
M8 remains experimental and unaccepted; default APIs and scientific tolerances
are unchanged, with no measured GO or release claim.
