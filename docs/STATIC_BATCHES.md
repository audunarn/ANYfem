# Streamed independent static cases

`iter_linear_static_batches` builds one structural model and yields selected
results in bounded blocks. Each case is assembled and solved independently.
Time identifies a static load state and evaluates its expressions and envelopes;
loads may vary arbitrarily with time. The iterator performs no interpolation,
load compression, dynamics or history integration. The existing
`solve_linear_static_many` collecting API is unchanged.

## Runnable examples

From the repository root, with the coordinated ANYfem and ANYsolver packages
installed or their `src` directories on `PYTHONPATH`, run:

```shell
python examples/static_batch_supported.py
python examples/static_batch_free_free.py
```

[The supported example](../examples/static_batch_supported.py) constructs a
clamped plate, material, connection, mesh and three load records entirely through
public APIs. It selects 12 displacement DOFs, one physical von Mises stress field
and reactions, consumes each block, then finishes an `EnvelopeReducer`. Its
point load varies as `t*t`, connection load as `1+t*t*t`, pressure activates at
the second series step, and Python wet pressure changes by time and position.
These states are not scalar multiples of one load shape.

[The free-free example](../examples/static_batch_free_free.py) constructs an
unsupported beam with balanced opposite axial forces. It selects one node and
one element's stresses, reduces three independent cases, and demonstrates the
explicit refusal of a subsequent unbalanced load. Displacements use the
solver's free-free gauge; the example adds no artificial supports.

Both scripts close their iterators in `finally`, retain only the envelope, and
assert successful completion. Retaining every yielded block instead retains
all those blocks' result data.

## Load compatibility and evidence

The adapter invokes the same public load build dispatch as the collecting
workflow; it caches only fixed geometric mappings. It adds no physical load
definition or solver capability. Here, **dispatch oracle** means the adapter's
load case is compared with `build_fe_model` using a solver spy; it does not
prove a production numerical solve. **Production** means the actual public
solve path was exercised.

| Approved input | Dispatch retained | Focused coverage |
| --- | --- | --- |
| Point forces and moments, distribution and envelopes | `_accumulate_case` point loop → `_add_nodal` | Eight-family dispatch oracle; production mixed history, rotational recovery test and balanced free-free forces. |
| Ordinary pressure, dead/follower policy flags | `_accumulate_case` pressure loop → element `pressure_loads` | Dispatch oracle; production mixed plate history; existing public pressure scaling, combinations and follower-policy/refusal checks. Linear static retains existing follower capability limits. |
| Wet constant and expression pressure | `_accumulate_wet_pressures` → `wet_element_pressures` | Dispatch/factor oracles; production spatial/time-dependent mixed history; existing wet resultant, dry ownership and series checks. |
| Wet Python code, tables and face/element mapping | `wet_element_pressures` → existing field/table/mapping evaluation | Three-variant dispatch oracles; existing public code/table evaluation and table-series solves. The supported runnable example exercises trusted Python code with a production solver. |
| Connection forces and moments, independent envelopes | `_accumulate_connection_loads` → prepared connection mapping | Dispatch/factor oracles; production connection-force mixed history; existing rigid, distributing, plane-section and spring public solves/refusals, including applied moments. |
| Line force per length and envelopes | `_accumulate_case` → `_line_target_to_nodes` → `_add_nodal` | Dispatch oracle; production mixed history and existing public beam/plate line-load equivalence checks. |
| Surface traction and envelopes | `_accumulate_case` → `_traction_target_to_nodes` → `_add_nodal` | Dispatch oracle; production mixed history; existing public build checks for sloped and triangular shells. |
| Gravity and uniform inertial acceleration | `_accumulate_case` sums the case acceleration field; original solver mass loading | Both dispatch oracles; production acceleration mixed history and existing public self-weight, acceleration-direction and added-mass checks. |
| Explicit factored combinations | `_build_combination` → `_accumulate_case`, without generated model registration | Adapter dispatch/factor/time oracles and invalid-combination refusal; existing public exact factored-sum, pressure and acceleration combination checks. |
| Series windows and per-load envelopes | Original `resolve_case`, `_envelope_factor` and wet/connection envelope dispatch | Zero/non-unit/negative factor oracles, nonlinear-time and step-window tests, production mixed history. Time override preserves the original step number. |
| Fixed supports, prescribed displacement, project masses and imperfections | Existing structural build, shared for the run | Existing public prescribed-displacement and mass impact checks. These inputs stay fixed across records; this API does not turn them into variable time-history state. |

Adapter checks are in `tests/test_static_batch_adapter.py`; physical selected
result oracles are in `tests/test_static_batch_integration.py`; runnable scripts
are exercised by `tests/test_static_batch_examples.py`. The factor repair is
covered by `tests/test_static_batch_envelope_factors.py`. Existing affected
static/load/connection/wet/postprocessing/control checks are recorded separately
in task evidence. This table is not a qualification claim for every element,
custom callback or coordinate-system variant.

## Python wet pressure trust

`LoadCase.add_wet_pressure(code=source)` treats code authored by its caller as
trusted on that machine; the supported example uses this public route. Code
received inside a project file remains untrusted until the existing explicit
trust workflow is completed. After reviewing that exact source, the existing
`anyloads.trust_code(source)` API grants trust. The adapter does not grant trust
or bypass an untrusted-code refusal while iterating. Trust is stored outside the
project, so sharing a project does not transfer execution authority.

Wet Python code and other user load evaluation run during each record's load
construction, outside solver authority spans. They can change pressure
arbitrarily by time and position. Structural mutation is checked immediately
after wet evaluation and again before the solver receives the load record;
mutating geometry, mesh, materials, supports or mappings aborts the stream.

## Requests, selections and identity

The iterator accepts a lazy iterable of case names, `LoadCase` objects, or
`StaticLoadRequest` records. `StaticLoadRequest("combination-result",
combination="ULS")` selects a factored combination without confusing its name
with a case name. A request must name exactly one case or combination. `time`
overrides evaluation time for every load in that request, including each
component of a combination. Series-step windows keep the original step numbers.
Existing invalid-combination and series-definition refusals are retained.

With `load_cases=None`, all non-template project cases stream in project order.
An explicitly empty iterable stays empty. `case_ids` identify results; `labels`
retain source names; `times` retain evaluation times; `input_indices` distinguish
repeated IDs or labels and determine envelope ties.

Selections use node IDs, geometry references in `output_refs`, connection names
or IDs in `output_connections`, or explicit global `output_dofs`. Entity and node
requests select all six DOFs. Spring connections also select their section node.
With no output selection, the displacement block contains all DOFs. A supplied
`RecoveryConfig.node_ids` selects nodal output; conflicting explicit node or
connection selections raise an error. Stress components and elements follow
`RecoveryConfig` unchanged. The facade offers `node_displacement`,
`connection_motion`, `stresses` and `reactions` with a block-local `case_index`;
unselected displacement queries raise `KeyError`.

Explicit `output_dofs` controls exactly the displacement matrix rows.
`RecoveryConfig.node_ids` independently controls nodal recovery and reactions;
it does not expand explicit matrix DOFs. An explicit node, geometry reference
or connection selection requires all its node DOFs in supplied `output_dofs`;
otherwise the iterator raises a selection conflict. One rotational matrix DOF
and recovery of that node produce one row alongside the requested six-component
nodal recovery.

When `built` is supplied, it provides the project and mesh without rebuilding.
A different explicit `project` or `mesh` raises a conflict. Build-only options
such as target size and overrides have no effect on an existing built model.

## Failure, cancellation and resource behavior

Supported and free-free cases use the existing static contracts. An unbalanced
free-free load is refused unless the caller explicitly requests the existing
gauged diagnostic solution through `allow_unbalanced_free_free=True`. The model,
geometry, materials, supports, regions and mappings stay fixed throughout a run.
User evaluation, input iteration, callbacks and consumer code execute outside
solver leases; structural changes at those boundaries terminate the stream.
Evaluated loads, full solution facades and generated model registrations are not
retained across the whole history.

| Boundary | Behavior |
| --- | --- |
| Input iterator, load evaluation, progress callback or consumer mutates the fixed project/mesh | Adapter `ProjectError`; solver-side fixed-model changes use `StaticBatchError`. |
| Incompatible free-free RHS, non-certified solution or invalid combination | Existing static/combination refusal; no invalid block is yielded. Earlier completed blocks remain usable. |
| `CancellationToken.cancel(reason)` | Cooperative `SolveCancelled`; an already cancelled token is checked before model construction or case consumption. |
| `resource_config=ResourceConfig(memory_limit_bytes=...)` refuses projected memory | `ResourcePolicyError`; preflight may inspect one first record to distinguish empty input, but does not consume the whole history. Batch size and selected recovery determine working storage. |
| Primary input/solver/cancellation failure plus cleanup mutation/error | Primary type, failing case and diagnostic survive; additional errors appear in notes and `static_batch_secondary_errors`. |
| Normal exhaustion or explicit close with cleanup mutation | Cleanup/unchanged failure still raises. Internally owned sessions and input generators close; supplied sessions stay caller-owned. |

Pass `cancellation_token`, `resource_config`, `recovery_config` and
`progress_callback` directly to `iter_linear_static_batches`. Catch failures at
the iteration boundary and close in `finally`, as the examples do. Closing early
releases owned resources; completed blocks remain available after later failure.

Call `EnvelopeReducer.finish()` only after successful exhaustion; failure or
cancellation leaves its result incomplete. Scalar extrema retain signed value,
governing case ID, time, input position and associated physical components;
equal extrema use the earliest input position. Derived stresses such as von
Mises are recovered for each physical case before reduction.
