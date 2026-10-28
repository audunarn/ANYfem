# Inertia relief in ANYfem — 2026-10-27

## Floating-wind implementation transition — 2026-10-27

User approved the complete floating-wind plan. Detailed accepted specification:
C:/ANYworkspaceWork/floating-wind-plan-20261027-02/implementation-plan.txt.
New evidence: C:/ANYworkspaceWork/floating-wind-implementation-20261027-01.
Allowance: 2400 numerical process-wall seconds, 480 final reserve, 180/process,
two numerical threads or stricter existing owner limits. Actual consumption is
recorded in the new evidence root's checks/*/result.json receipts.
This does not renew the base floating-pressure task's authority.

Dependency status: Investigate irregular wave run remains active/unaccepted;
its inclined-floater test exposed an adjacent-plate source-mesh defect under
heel/trim. That owner is repairing it. No shared floating-workflow edits until
that delivery is accepted. Independent headless new modules may proceed.
One Sonnet5.5 subscription implementation owner is assigned the connected new
ANYloads wind/external histories, ANYsolver floating transient and ANYhydro field
kernel modules/tests only. Parent owns tests, cross-repository reconciliation,
application integration after dependency acceptance, Opus5.5 medium review and
final acceptance. Existing dirty work/qualification gates remain protected.

Dependency checkpoint: base owner reports source-mesh unit repairs and real Qt
Apply passing, but inclined native load verification still timed out. Its
1200-second allowance is effectively exhausted (about 13 seconds remaining).
It is handing off with that gate unmet. Do not spend this task's new allowance
to renew or silently accept that frozen base gate. Shared application edits
remain held. Independent new headless work continues as a candidate only.

Initial headless source: eight new files authored by the assigned Sonnet owner;
ownership audit shows no writes beyond them and checkpoint/handoff. Parent
checks: 65 passed and 14 failed across initial unit/independent suites; exact
receipts under the new evidence root. The failures include admission, immutable
result identity and close multiple-root discrimination. Same-owner continuation
is repairing these and owns two additional new transient-history files plus
the native infinity adapter. No acceptance or capability claim yet.

Prospective check clarification: the approved new plan includes native limit
checks. One isolated, small omega=inf vendor/helper unit may run under THIS
task's allowance (180-second cap, native n_threads=1), on its own explicit box
input without ANYfem Prepare/equilibrium or the unresolved inclined case.
It establishes only native API/normalization/closure behavior for the new helper,
not base-task acceptance, pressure-distribution qualification or a floating-wind
capability. No old verification authority is renewed. Shared workflow/native
qualification gates stay held. The helper's unqualified status is preserved.

Canonical mean-equilibrium boundary: the current public ANYstability adapter
solves draft/heel/trim at a fixed horizontal/yaw datum and exposes no external
wind/mooring wrench hook. The new coupled operating equilibrium needs a canonical
owner extension after the base dependency is accepted; it must not be emulated
with invented mass/ballast or an application-owned hydrostatics implementation.
New headless response tests do not establish that operating-equilibrium path.

## Historical planning scope: floating wind foundation response and relief histories

Planning only, 2026-10-27. User explicitly chose: calculate floating response and
loads as well as structural relief. This section scopes the follow-up; it does
not authorize implementation, native hydrodynamic/stability runs or publication.
Existing moving-floater work, source owners, held hooks and qualification gates
remain protected. The completed static-relief evidence below is unchanged.

User clarification: (1) inertia relief compatible with external load histories,
(2) a turbine load applicator based on a thrust curve, and (3) user-generated
turbulent wind with relative wind recalculated from platform motion. These are
the three public deliverables. Reuse the current floating implementation rather
than create a second platform solver. The connected result calculates six rigid
platform motions, velocities and accelerations, matching motion-dependent loads,
and time-indexed elastic foundation results. Supplied snapshots retain ordinary
static inertia relief; a history of static snapshots alone is not platform dynamics.

Current source findings:
- ANYsolver already recomputes rigid acceleration/inertial load for every load
  column; it should not be described as using one acceleration for a whole batch.
- ANYfem static batch shapes currently use value=float(index). Physical case
  times must survive build, solving, transport, plots, envelopes and saved results.
- FloatingPrepared/history, retained pressure families, six-DOF frequency response
  and residual-relief helpers already exist in separate in-progress work. Audit
  and complete these rather than creating a parallel implementation. Its notes
  declare implemented-unverified state; no new numerical claim is made here.
- ApplyFloatingLoad generated analysis settings and floating dispatch are under
  concurrent development. Audit the final job route and its explicit floating/
  relief selection; do not overwrite the other owner's candidate implementation.
- build._accumulate_floating assembles pressure + weight + primary inertia and
  tags _floating_compound. The current run.py now has _require_floating_relief and
  _floating_relief_metadata consumers. Verify this connected route and preserved
  primary/correction/total semantics; earlier observations that these were absent
  have been superseded by concurrent edits.
- floating_batch.iter_residual_relief already separates primary, correction and
  total accelerations. Generic IR metadata for these compound inputs would report
  only the correction as acceleration unless the special dispatch is connected.

Public feature contracts:
1. External loads: timestamped nodal forces/moments, distributed loads and declared
   interface histories, with explicit units, frame, attachment, interpolation,
   coverage and provenance. Distinguish prescribed histories from load models
   depending on motion. For the floating route, include every contribution in the
   response and structural-load accounting; do not bypass the compound-load safety
   checks by simply permitting an extra load after response has been computed.
2. Turbine applicator: hub/attachment geometry, declared rotor yaw/tilt, and a
   user-supplied axial thrust-versus-wind-speed curve. Validate strictly increasing
   speed samples and finite thrust; thrust itself need not be monotonic. Declare
   interpolation, operating/parked state, cut-in/out, reverse-flow and out-of-range
   behavior; no silent extrapolation or absolute-value conversion. Apply the hub
   force and its lever-arm moment through a force/moment-conserving attachment.
   Mass/inertia must be explicitly represented in the FE body or interface model,
   never silently added or counted twice. Rotor torque, gyroscopic terms and
   controller dynamics are not implied by a thrust curve.
3. Wind: create or import a reproducible vector time series with mean speed,
   direction, declared turbulence spectrum/intensity, seed, timestep and duration.
   First delivery uses a hub-point/uniform-field approximation; a spatial field,
   shear, veer and rotor averaging require an explicit extension. Store the actual
   realization and sampling rules; iterations must never draw fresh turbulence.
   The hub offset r_h and rotor axis n are transformed into the global frame:

       v_h = v_reference + omega x r_h
       V_axial = (U_wind(x_h,t) - v_h) dot n
       F_h = T_curve(V_axial) n;  moment_reference = r_h x F_h

   For the initial uniform-field model U_wind is independent of x_h. This uses
   physical platform motion, never gauge-fixed static elastic displacement. The
   turbine force must be reevaluated within each timestep's converged motion/load
   coupling. A precomputed thrust history evaluated once at stationary wind does
   not meet this contract. Elastic tower velocity enters only with a separately
   admitted flexible dynamic model.

Bounded implementation slices (dependencies are sequential):
1. Freeze the input/body contract: physical participating mass/CG/inertia, ballast,
   tower/RNA representation or consistent interface loads; mean draft/heel/trim;
   hydro geometry; mooring attachments and declared restoring/damping; wave and
   wind/turbine excitation definitions; time axis and initial state. Compute mean
   equilibrium including mean wind/mooring loads. Missing terms cannot silently
   become zero or invented ballast. Acceptance: independent equilibrium and mass
   resultants with consistent axes, origins and provenance.
2. Complete six-DOF platform response and the wind/turbine feedback loop. Reuse the
   existing frequency-domain solver for linear prescribed-load references;
   controlled frequency synthesis can cover linear records with adequate padding
   and tail control. The motion-dependent thrust curve requires time integration
   with a time-domain radiation-memory formulation (convolution or
   validated causal/passive state space), with explicit initial conditions and
   initialization/warm-up. Include dry mass, hydrodynamic added mass/radiation,
   hydrostatic and declared mooring restoring, and physical damping. No artificial
   restraints, damping or stiffness. Frequency-dependent A/B cannot simply be
   treated as constant instantaneous coefficients for arbitrary load histories.
   Establish A(infinity), kernel truncation/fit, causality/passivity and matching
   pressure-memory operators; the integrated panel forces must reproduce the
   global radiation model. Wind-only forcing must still drive response/radiation:
   the current zero-wave shortcut is admissible only when all forcing is absent.
   Cover wind as well as wave frequencies. Mean thrust uses the equilibrium path,
   not omega=0 in the harmonic solver; declare mean/initial-state treatment for
   nonlinear thrust, drift and unrestrained directions. Require a valid equilibrium
   or explicitly bounded drift event; do not invent mooring stiffness.
   Acceptance: stationary-wind thrust, surge-relative-speed sign, pitch/roll hub
   velocity and lever-arm moments, wind-only gust, seed-repeatable forcing,
   coupled iteration convergence/refusal, analytic heave/pitch/free decay,
   coupled six-DOF reference cases,
   time/frequency steady-state agreement, radiation-fit checks and time-step
   convergence against a prospectively fixed temporal error budget.
3. Calculate phase-consistent pressure and nodal forces at each actual time:
   incident/diffracted/radiated pressure, hydrostatic perturbation, weight,
   mooring/fairlead forces and wind/turbine forcing. The same hydrodynamic model
   must supply response forces and motion-dependent structural pressure. Wind
   excitation uses the declared thrust-curve applicator or imported histories;
   a full aeroelastic turbine/controller is a separate scope. Structural weight,
   hydrostatic perturbation and restoring must share the same frame/linearization:
   their integrated perturbation loads must reproduce the response restoring law.
   Acceptance: source and mapped force/moment closure, phase/frame/reference-point
   covariance and static/zero-amplitude limits. Retain existing mapping gates.
4. Connect primary inertia and bounded residual relief to the existing multi-RHS
   nullspace engine. Per frame, form Fphysical - M_FE*a_primary, then compute only
   the permitted residual correction -M_FE*R*delta_a. Structural M_FE contains
   physical participating mass; hydrodynamic added mass is not added a second
   time. Gravity/inertia must each enter once. Keep primary, correction and total
   acceleration separate. Require R^T M_FE R to match the participating six-DOF
   dry mass, with explicit accounting for interface-represented masses. Enforce
   source/mapping closure and an adopted residual force/moment limit before relief;
   freeze its normalization and threshold before implementation. Reject a correction
   that masks missing or inconsistent physics. Do not feed correction
   back into the independently computed primary response/pressure. Acceptance:
   manufactured zero residual, perturbed residual within adopted gates, double-
   gravity/inertia refusal and exact per-frame balance/accuracy diagnostics.
   Algebraically, subtracting a primary rigid inertia load and then applying full
   relief produces the same elastic solution as relieving the physical loads
   directly. Physical platform response affects stress through motion-dependent
   pressure, wind, gravity and interface loads; primary inertia alone is not a new
   stress mechanism. Its residual is a consistency diagnostic, not validation of
   the response model.
5. Finish actual-time dispatch and storage. Generated floating analyses select
   the proper route; generic static/compression routes refuse unsupported compound
   inputs. Evaluate all contributions on the same finite ordered physical time
   axis; preserve original case/frame identities and nonuniform output times.
   Cache only unchanged operators; recompute frame loads and accelerations.
   Stream bounded blocks and retain first/last/peak timestamps and histories.
   Acceptance: scalar/batch/block-size parity, selected-window and ordering tests,
   per-frame diagnostics, cancellation/failure, restart/reopen and cache invalidation.
6. Expose a distinct Floating response + structural relief workflow in ANYfem,
   with External load history, Turbine thrust curve and Turbulent wind controls,
   environment/mooring/mass/time inputs and mean-equilibrium checks. Display
   surge/sway/heave/roll/pitch/yaw, velocity, primary/correction/total acceleration,
   elastic displacement/stress and balance history. Keep elastic gauge-fixed
   displacement distinct from physical platform motion; result animation must
   label them. Acceptance: actual Qt -> immutable job -> worker -> saved/reopened
   results using one regular-wave case, one irregular/multi-heading case and one
   admitted coupled transient wind event, with independent review. Also preserve
   wind, relative axial speed and thrust histories in saved/reopened results.

Initial physical boundary: linear/small-motion floating response about a validated
mean equilibrium and the existing connected beam/shell structural profile.
Quasistatic elastic foundation stresses require an adequacy check, with a fixed
criterion against loading bandwidth and elastic modes adopted before implementation.
Tower/RNA flexible dynamic stress claims are excluded. Elastic resonance requires
a separately scoped transient
elastic route; static relief must not be represented as full structural dynamics.
Large rotations/wet-dry changes, nonlinear mooring dynamics, slamming, full turbine
aero-servo-elasticity and hydroelastic feedback need explicit additional scope.
First-order wave/hydrodynamic and declared linearized mooring models must be
labelled. Second-order drift, nonlinear line dynamics, viscous drag, current and
rotor gyroscopic effects are excluded unless explicitly supplied by an admitted
model; absence of these terms precludes a full offshore design capability claim.
Imported equilibrium mooring forces and tangent stiffness must be consistent;
nonlinear mooring equilibrium/linearization is separate if not already available.
Preserve the existing rotation/waterline envelope, residual/mapping limits and
the 1e-10 IR gates / 1e-12 spectral margin / 1e-13 eigenpair residual.

Owner boundaries: ANYstability mean equilibrium; ANYhydro hydrodynamic operators
and pressures/radiation representation; ANYsolver six-DOF response and structural
numerics; ANYloads coherent histories; ANYgeometry/ANYmesh authoritative mapping;
ANYfem turbine configuration, orchestration, UI, jobs and persistence; ANYloads wind
realization, thrust-curve evaluation and attachment/interface histories; mooring
equilibrium/linearization supplied by an explicitly owned provider or imported
consistent data. One integration owner should close
the connected outcome while respecting existing file ownership and held runs.

Primary theory references: OpenFAST HydroDyn modeling considerations and input
guide explain displacement-dependent restoring, velocity/acceleration-dependent
loads, added mass and radiation memory, and convolution/state-space time-domain
representations. Existing GF/Prony regeneration state is not, by itself, evidence
of a validated temporal radiation-memory model.

Independent scope advice: Claude Opus 5.5 medium, subscription Read, completed
with four successful reads in 76.328 process-wall seconds and no remaining owned
processes. Evidence: C:/ANYworkspaceWork/anyfem-inertia-relief-20261027-01/
floating-scope-opus-review/review.txt. This assessed the broader draft before the
latest turbine/wind clarification. Its mass, restoring, residual, radiation and
quasistatic-scope findings are incorporated above. A prescribed linear FFT route
remains a reference, but cannot replace the nonlinear relative-wind feedback loop.
No follow-up implementation or numerical tests have run as part of this scope.

User request: expose inertia relief in ANYfem in the same application paths as
nullspace. Existing nullspace, wave, transport and presentation edits are protected.
This authorizes application integration and focused checks; no sibling edits,
publication, full-vessel qualification or new solver mathematics.

Plan and acceptance:
1. Add typed inertia_relief/reference_point settings to single/batch static,
   shared admission, immutable job build and preflight. Accept analytical free
   beam acceleration, actual solver diagnostics, incompatible-option refusal and
   unchanged supported/nullspace defaults through direct and job paths.
2. Surface mass-based inertia relief beside nullspace in Solve controls, with
   explicit mutual exclusion from generalized balancing and persistence/restoration.
   Accept Qt controls, saved/reopened settings, actual submit settings and no
   leakage into nonlinear/modal analyses.
3. Retain per-case accelerations, mass, balance and accuracy diagnostics through
   batch results, progress, recovery and worker transport. Exact checked cases
   bypass synthetic load compression. Accept scalar/batch parity, failure and
   cancellation before publication, transport round-trip and affected regressions.

Owner: Astra retains this tightly coupled cross-repository interface integration:
the relevant ANYfem files already contain uncommitted nullspace/wave changes, and
the prior Sonnet Edit launch was rejected by automatic approval review. No alternate
Claude billing route or repeated probe. Independent general review uses the user's
requested Claude Opus5.5 medium subscription Read route. ANYsolver owns all
physical equations, numerical gates, unsupported-body checks and effective loads.

Evidence root: C:/ANYworkspaceWork/anyfem-inertia-relief-20261027-01.
Checks are supervised, exact selectors only, 2 numerical threads, offscreen Qt,
180 seconds per process and 1200 cumulative process-wall seconds for this new
application milestone. This does not renew any prior campaign authority. Retain
failures; no whole-repository, BEM, wave-vessel or performance campaign.

Open risks: relaxing supports without explicit relief; dropping flags in immutable
jobs or batch/compression; confusing inertial/gauge forces with reactions; advanced
JSON overriding restored controls; stale/missing diagnostics in worker results.

## Implemented outcome and use

State: implemented and verified against the scoped application criteria.
Restart ANYfem, open Solve -> Linear static or Batch linear static, and select
**Mass-based inertia relief**. It selects nullspace, clears/disables generalized
load balancing and enables an optional reference point in global metres. Empty
uses the centre of mass. Unticking restores the previous constraint mode; it
does not automatically re-enable generalized balancing. Physical supports are
never removed. Solver admission remains authoritative.

Headless: solve_linear_static(..., inertia_relief=True) and
solve_linear_static_many(..., inertia_relief=True). Explicit transformation or
generalized balancing with relief is refused. The old constraint_mode literal
values are unchanged; inertia relief is a separate physical setting. Default
supported-static and explicit nullspace behavior remains unchanged.

Settings, reference point, actual mapped-case production preflight and the
owner call are connected through the immutable workflow. Scalar numerical
non-success fails before a LinearSolution/recovery/publication. Batches use
exact original cases with input-index/case-id checks and bypass compression
even at the default positive load_tolerance.

LinearSolution.inertia_relief and per-case info retain owner acceleration,
mass, centre/reference point, inertia, resultants, provenance and numerical
diagnostics. Summary/transcript report physical acceleration and balance.
Single results include the richer owner result-case metadata; batch summaries
are compact and share the documented physical fields. No gauge multiplier is
reported as acceleration or support reaction. reactions={} for free-free
relief; scalar internal MPC forces remain separate owner data.

The additive saved-artifact bridge stores inertia_relief (single) or named
inertia_relief_cases (batch) in result provenance. Reloaded provenance retains
accelerations/reference point and balance diagnostics, with no DOF-sized load
vectors. Existing artifact behavior without relief is unchanged.

Advanced JSON keeps its existing precedence over typed controls and is
validated for incompatible options. Restoring a saved analysis surfaces the
relief/reference fields rather than retaining hidden JSON overrides.


## Verification and exact commands

### acceptance-01

**163 passed, 0 failed, 0 skipped**; 95.281 supervised process-wall seconds.

```text
C:/Python/Python314/python.exe -u C:\ANYworkspaceWork\anyfem-inertia-relief-20261027-01\pytest_entry.py -q --junitxml=C:\ANYworkspaceWork\anyfem-inertia-relief-20261027-01\checks\acceptance-01\junit.xml --tb=short tests/test_inertia_relief_settings.py tests/test_qt_inertia_relief.py tests/test_free_floating.py tests/test_qt_free_floating.py tests/test_solve.py tests/test_solver_controls.py tests/test_solver_outcome_jobs.py tests/test_result_transport.py tests/test_qt_workbench.py::test_qt_file_inspector_formats[frd]
```

### final-smoke-01

**3 passed, 0 failed, 0 skipped**; 10.250 supervised process-wall seconds.

```text
C:/Python/Python314/python.exe -u C:\ANYworkspaceWork\anyfem-inertia-relief-20261027-01\pytest_entry.py -q --junitxml=C:\ANYworkspaceWork\anyfem-inertia-relief-20261027-01\checks\final-smoke-01\junit.xml --tb=short tests/test_inertia_relief_settings.py::test_process_worker_preserves_physical_support_refusal tests/test_qt_inertia_relief.py::test_actual_static_controls_capture
```

### capture-final

**1 passed, 0 failed, 0 skipped**; 5.141 supervised process-wall seconds.

```text
C:/Python/Python314/python.exe -u C:\ANYworkspaceWork\anyfem-inertia-relief-20261027-01\pytest_entry.py -q --junitxml=C:\ANYworkspaceWork\anyfem-inertia-relief-20261027-01\checks\capture-final\junit.xml --tb=short tests/test_qt_inertia_relief.py::test_actual_static_controls_capture
```

### artifact-03

**29 passed, 0 failed, 0 skipped**; 7.844 supervised process-wall seconds.

```text
C:/Python/Python314/python.exe -u C:\ANYworkspaceWork\anyfem-inertia-relief-20261027-01\pytest_entry.py -q --junitxml=C:\ANYworkspaceWork\anyfem-inertia-relief-20261027-01\checks\artifact-03\junit.xml --tb=short tests/test_inertia_relief_settings.py::test_acceleration_and_balance_survive_saved_result_reopen tests/test_result_artifact.py
```

### final-controller-01

**2 passed, 0 failed, 0 skipped**; 11.140 supervised process-wall seconds.

```text
C:/Python/Python314/python.exe -u C:\ANYworkspaceWork\anyfem-inertia-relief-20261027-01\pytest_entry.py -q --junitxml=C:\ANYworkspaceWork\anyfem-inertia-relief-20261027-01\checks\final-controller-01\junit.xml --tb=short tests/test_qt_inertia_relief.py::test_real_solve_button_controller_worker_and_display
```

The163-test integration run includes real Qt Solve-button -> controller ->
snapshot/mesh clone -> process worker -> decoded result for single and batch,
saved settings, beam and shell-pressure mass/acceleration, reference changes,
at least10 compressible cases (compression kernel forbidden), case-order
mismatch refusal, cancellation, numerical non-success/no recovery/publication,
old nullspace and supported-static tests, solver controls/outcomes, transport
regressions and the FRD workflow. The3 final smoke tests add actual worker
physical-support refusal and a rendered control capture. The29 artifact tests
include2 actual saved/reopened physical-relief results and27 existing artifact
regressions. The final controller rerun covers the additive artifact delta.

Runtime: C:/Python/Python314/python.exe, PySide6 6.12.0, source ANYfem and
ANYsolver (origins printed per run), offscreen Qt and explicit Qt opt-in.
BLAS/Numba thread environment requested2; thread pools were not separately
instrumented here and no performance claim is made. TEMP/TMP are child-local
writable evidence directories. Every completed owned process tree is verified
retired. Some runs print the pre-existing pyreadline3 Console.__del__ invalid
handle diagnostic during interpreter teardown; passing exit/counts are retained.


## Independent review

Claude Opus5.5 medium subscription Read review completed: 11 successful tool calls, 110.860 seconds, no tool/API errors, **no blocking findings**.

Evidence: C:/ANYworkspaceWork/anyfem-inertia-relief-20261027-01/opus-review.
Reconciled advice: compression test now exceeds8-case threshold; actual Qt
controller/process route tested; physical supports refused headless and in
real workers; prior constraint mode restored after untick; owner case identity
checked with adversarial reorder coverage. Method-specific summary extras and
advanced-JSON precedence are documented. Parent inspected the small additive
artifact-provenance delta and its29 passing tests after this source review.


## Preserved failures and limits

focused-01:57 passed/2 failed. Test transport fixtures passed a raw generated
mesh containing a cancellation closure. Inspection confirmed the real GUI
already uses clone_mesh_for_job; fixtures now exercise that same public
boundary. No transport/mesher implementation was changed.
focused-02:60 passed/1 failed. The new worker test assumed a default case
ordering after snapshot serialization; it now submits an explicit ordered
case list and verifies it. Parent diagnostic printing also hit CP1252, while
full child receipt/JUnit were recovered unchanged. focused-03:61 passed.

integration-01:180.078-second timeout, incomplete,3 observed failures; not
a passing suite. Scoped collection only identified334 selectors; it ran no
tests. Exact current and preserved pre-change module-overlay reruns both
reproduced3 failures: missing VisualizationTask.option_icons, FRD sandbox TEMP
permission, and saved-result playback frame timeout. Child-local workspace
TEMP fixed the environment error; FRD passes in acceptance-01. The icon and
playback failures predate this change and remain untouched; no full Qt or
repository-green claim. Baseline files, import overlay and diagnostics retained.

artifact-01:2 passed/27 failed (missing adapter type reference and incorrect
new test getter). Adapter now follows its existing class-name dispatch.
artifact-02:27 passed/2 failed (test read a nonexistent top-level metadata
namespace). Tests now read the existing public metadata("provenance") boundary;
artifact-03:29 passed, with no schema or numerical tolerance changes.

The initial offscreen capture lacked system-font glyphs. The verification
capture loads a local Segoe UI font only in that test process, optionally on
Windows; no product theme/font changes. Final controls were visually inspected:
C:/ANYworkspaceWork/anyfem-inertia-relief-20261027-01/checks/capture-final/solve-controls.png.

Cumulative supervised check process-wall use: 490.595 of1200 seconds.
No installed ANYfem artifact, full vessel/BEM, full repository, performance or
release campaign was run. Source changes are uncommitted; publication remains
outside this application task. All preceding failure evidence remains retained.

## Floating-wind dependency update (later checkpoint)
Predecessor now reports all three integrated native cases passing (inclined loads, irregular worker/reopening, zero-wave hydrostatics) under its separately increased 1800-second allowance, 1746 seconds consumed. Its final Fable review and acceptance are still pending. The earlier exhausted-budget hold describes historical state, not current numerical outcome. Shared edits still await actual acceptance. This task's own native finite-depth waterline gate remains failed; an independent infinite-depth exact-image control passed with raw coefficients identically zero for all six DOFs. Exact evidence remains under floating-wind-implementation-20261027-01/checks. A bounded provider-attribution/replay probe is running under this task's own remaining allowance; no analytic boundary policy has been adopted.


## Explicit waterline policy and model transition (latest)
Direct human approval received: "Approve the explicit boundary policy, subject to verification".
This amends native-only E_inf at exactly z=0: unconditional continuum E_inf=0 may be used while preserving raw native values/failed raw gate, pointwise absolute-S/source consistency bounds and all-sample guard inflation. Pressure/A_inf and reconstruction/closure tolerances stay unchanged. No enabling/capability claim before verification.
User then directed: "Stop using Fable. Only Opus 5.5 at medium effort." All FUTURE Claude implementation/review runs in this task use Opus 5.5 medium; no owned Fable process exists. Existing Sonnet-authored work is preserved rather than restarted. Same connected CLI owner/session continues with the user-selected Opus model.
Current implementation run: C:/ANYworkspaceWork/floating-wind-owner-explicit-policy-20261027-02 (45 turns, 900-second coding deadline, no numerical execution), owned12 files (adds new ANYhydro floating_transient_fields.py and test). Early checkpoint read and actual16 source edits inspected at180seconds; meaningful progress, no scope expansion beyond assigned policy/adapter. Initial launcher attempt -01 failed on brief encoding BEFORE inference and is retained with explicit terminal receipt; -02 corrected UTF8.
Parent native controls: exact-image control passes with raw0 sixDOF; finite50/100/200m bound/depth/replay/projection probe passes; Prony sensitivity passes (raw heave1.646e-6 ->9.902e-8 under tighterMSE fit; relative scaled A/P changes4.772e-8/1.682e-5). All runs are diagnostics, not native-family qualification. Fresh-process saved-source replay, mesh refinement, unchanged finite-frequency reconstruction and all-sample guard wiring remain required. Corrected independent Opus policy review actually read4 evidence files, no tool errors,128.829sec; held enabling pending those gates. User contract approval now resolves the earlier native-only scope question.
Predecessor still active with passing integrated native cases and final review pending as last observed. No shared ANYfem/ANYstability workflow edits authorized by acceptance yet. Continue from source artifacts; do not misreport historical exhausted-budget state as current.


### Continuation checkpoint, 2026-10-28 00:45 UTC

Checkpoint 2026-10-28 00:45 UTC: partial implementation, NOT accepted.
User model policy: only Claude Opus 5.5 at medium effort for all future Claude implementation/review. No Fable/Sonnet/API substitution. Prior Opus session quota failure preserved; reset advertised 02:50 Europe/Oslo. No retry performed in this continuation.

Executed:
python C:\ANYworkspaceWork\floating-wind-implementation-20261027-01\check.py bounded-native-transient-unit ANYsolver tests/test_floating_transient_native_admission.py tests/test_floating_transient.py tests/test_marine_linear.py
80 passed, 0 failed; process-wall 9.03200000000652 seconds. Receipt/JUnit/source identities under checks/bounded-native-transient-unit. Existing pyreadline shutdown warning retained. Process-tree cleanup verified zero.

Pending native accuracy check inspected: checks/explicit-native-profile-accuracy, 1 failed, 30.296000000002095 process-wall seconds. Native finite-frequency passivity at omega=1 remains -8.353480502735592e-6 relative to norm, below -1e-8 gate; tightening native Prony MSE to 1e-10/max120 terms did not cure it. At omega=3.3 ratio is +0.0009214015038185517. Source profile 192 physical hull +49 lid unknowns. Native pressure projection error 7.275957614183426e-12; raw boundary remains failed, separately approved exact-z0 policy admitted. These do not override passivity failure.

Three failed profile cases now preserved (n4, n8, n8 higher-accuracy fit). Stop further same-problem backend trials under retry rule. No source factory accepted; no passivity clipping, tolerance relaxation, or capability claim. Next decision requires source-level adjudication/independent Opus review rather than more tuning. New primary profile and bounded-native changes are not independently reviewed.

Remaining: unchanged causal field reconstruction, native source/passivity acceptance, canonical mean equilibrium, ANYfem full-trajectory/batch/controls/persistence integration, independent review, affected regressions and current isolated artifact check. Earlier artifacts are stale for primary changes. No commit/push/publication. Other dirty edits preserved.

Numerical allowance used: 805.3949999999604 seconds of 2400; final reserve remains 480 seconds. Full evidence: C:\ANYworkspaceWork\floating-wind-implementation-20261027-01\checkpoint-20261028-0045.txt


### User-directed continuation, 2026-10-28
User directs keeping coarse mesh, implementing connected method now, and deferring hydro debugging. No further native mesh refinement or Prony/backend tuning. Implement coarse-model workflow as a development result with explicit unverified-hydrodynamics status and retained failed diagnostics; scientific/production acceptance gates remain unchanged and unpassed. Numerical allowance is not renewed (805.395/2400 seconds used; reserve480). Model remains only Claude Opus5.5/medium. Next slice: canonical mean/physical-load/trajectory/residual-batch integration using fixed prepared source/operator contracts; no new BEM campaign. Connected owner may resume after advertised subscription reset. Preserve accepted predecessor and concurrent edits.

Parent acceptance sequence for coarse integration (no new hydro solve/refinement):
1. Inspect connected owner files and complete-vs-unverified status propagation; original strict/default API regressions.
2. Independent simple rigid force/torque and Newmark references with supplied operators; mass/reference/attachment identities and invalid/stale inputs.
3. Real small beam/shell FE batch residual relief, primary/correction/total bookkeeping and effective-load recovery; complete full history then selected-window identity.
4. Safe artifact save/reopen/job cancellation, no BEM imports/calls; additive UI path and undo/redo where delivered.
5. Current isolated built artifacts and Opus independent review of integrated implementation, reserving480seconds. Remaining numerical total1594.605seconds prior new checks. Existing native failed diagnostics remain unverified; no new tuning or native BEM campaigns.
Mean-equilibrium boundary: detailed approved spec requires solving owner mean force/moment including thrust/moorings and rebuilding/rebinding source at solved pose. A linear offset around old mean is explicitly a development approximation only, not fulfillment of accepted mean-equilibrium gate. Parent will inspect/report it separately, require honest metadata, and return concrete gaps to same owner rather than silently adopt changed physics.

Parallel coarse diagnostic complete: Opus5.5medium Read review141.5seconds,8successfulReads,0tool/APIerrors, cleanup0. Evidence C:/ANYworkspaceWork/floating-wind-opus-coarse-diagnostic-20261028-01/review.txt. No implementation acceptance inferred. Parent independently verified saved NPZ SHA/data and symmetric damping eigensystem and2x2 determinant identities in checks/saved-coarse-damping-diagnostic:1pass,0.8120000000053551process-wall seconds. No BEM, sourcefit or newmesh executed. Coarse n4 omega1 negative modes occupy surge/pitch and sway/roll; det(raw2x2)>0 but det(sym2x2)<0 due paired crosscoefficients differing. Finer historical n8 minimum is yaw diagonal. Sign/order/extraction inspection found no demonstrated defect; near-field collocation reciprocity error is supported explanation, not proven universal rootcause. A raw nonsymmetric matrix's positive eigenvalues do not prove passive work; its symmetric quadratic form is relevant. Review's raw-norm normalizer is dimensionally mixed; independent report also includes congruence scaling using4m reference length without changing any gate. Do not tune based on this diagnosis. Continue fixed-coarse development method with unverifiedhydro label, retaineddiagnostics.

Completion-owner run01 interrupted by parent after scoped Bash read denial exposed output-reader AttributeError on scalar event.message. This was a runner defect, not evidence of Claudequota or numericalfailure. Actual34pass repaircheck retained; no newsource writes aftercheckpoint4. Verified ownedClaudePID53948 stopped; terminalreceipt process_error424.39sec, cleanup0verified. Old handoff_updated=true was existence-only and does NOT mean a freshhandoff. Task-local worker_supervisor.py fixes typeguard without alteringpermissions; stringevent regression4events/1tool/0readererrors in0.11sec. Sharedlauncher unchanged. Resume sameOpusmediumowner with Read/Edit/Writeonly; parentchecks.


Coarse connected continuation checkpoint, 2026-10-28:
- Independent Opus5.5medium CORE review completed191.016seconds,12Reads,0errors,cleanup0; review at C:/ANYworkspaceWork/floating-wind-opus-coarse-core-review-20261028-01/review.txt. Parent source-verified mean/perturbation identity gaps, mistaken1%static gate,45degree mean limit, body-z vs world-z waterline displacement, missing mean-world-load tangent and undercounted memory. Returned adopted findings to SAME Opusowner under coarse-correctness-brief.txt; no clean review/acceptance.
- Latest connected UI/mean run coarse-completion-first:50passed,3failed,12.75process-wall seconds. Allthree UI failures stop beforeRun because required mean service isn't wired. Headless50passing doesn't establish usableUI. Previous hydropreparation run4failed75passed1skip from600frequency fixture vs retained513cap; owner correctingfixture, notcap.
- Parent cached48panel/120frequency native field smoke failed at nonexistent RadiationKernel.save after building field and checkingFFT. Failure retained: checks/fixed-coarse-cached-method-01,74.546seconds. Reused SAVED field artifact, rebuilt only cheap6x6timekernel from cachedA/B, saved/reopened via public save_radiation_kernel/load_radiation_kernel, matchedcontenthash/digests, pressure/elevation directFFTrelativeerrors5.021645209004767e-16/6.254755567821453e-16. Recovery1passed,4.031seconds: checks/recover-coarse-artifact-01. Zero BEM/refit calls; no remesh, no native field replay. This is method/persistence evidence only. No-lid cachedfamily retains99passivityviolations, source asymmetry, elevationreconstruction10.744% vs1%gate; pressuremaxrelative0.9957%passes. These failedrawdiagnostics remainunverified, distinct from separate48hull+9lid dampingdiagnostic.
- Sameowner now completes correctness/persistence/UI on existing coarsemesh. Prospective additional paths ANYfem/io/project_file.py and model/project.py, additiveonly; dirtybaseline captured coarse-correctness-before.json. NoGitpublication, no numericalworker authority. Parent runbudget consumed944.751999999920/2400seconds;480reserve unchanged.


## Fixed-coarse delivery checkpoint, 2026-10-28



External histories, deterministic Kaimal/imported wind, one rotor-axial thrust-curve turbine, trial-pose operating mean, motion-relative thrust and exact segment-root coupling; Newmark/radiation history; conservative pressure and attachment recovery; primary inertia plus separate residual batch relief; immutable saved results, cancellation, stale-input checks, controls and Apply/undo/redo. Foundation recovery is quasistatic. Mean/frame identity, actual waterline error bounds, wave provenance, actual section recovery identity and resource ceilings are enforced. Failed coarse source data never become verified because a time integration succeeded.

Usage: C:/Github/ANYfem/docs/floating_wind_transient.md.

### Current verification

- Installed ANYfem final: 106 passed, 0 failures/errors. Includes new workflow and synthetic saved floating Apply through the production worker, selected interval, undo/redo, ordinary persistence and residual-relief regression. Command: `python C:\ANYworkspaceWork\floating-wind-implementation-20261027-01/artifact_app_final_check_10.py`. Exact nested command, runtime import paths and JUnit: artifacts-10/steps.json, runtime-paths.json, junit.xml. Process wall 41.047 seconds.
- Independent parent checks against installed artifacts: 56 passed, 1 explicitly gated native-control skip. Current pinned operating-mean check: 1 passed. Command: `python C:\ANYworkspaceWork\floating-wind-implementation-20261027-01/artifact_remaining_check_09.py`; exact successful subprocess commands in artifacts-09/steps.json and native-owner-clean/steps.json. The enclosing check later failed in a separate broad GUI regression; successful JUnit records are preserved independently.
- Installed unchanged core: ANYloads 46 passed; ANYsolver 51 passed; ANYhydro 98 passed, 1 native-gated skip. ANYstability 12 passed plus an initial native-runtime authentication failure caused by relocated compiled bytecode. The fresh no-compile pinned runtime passed, including the current ANYstability wheel in artifacts-09.
- Latest source repairs: coarse-review-repaired-source 89 passed, 1 failed (NumPy truth-value bug in Q8 stress preflight); coarse-recovery-probe-repair 36 passed after the mechanical fix. Final installed application includes that fix. Exact commands and source hashes in checks/<label>/command.json.
- All 26 task source-module files match their tested private installations; 42 owned/source/documentation paths have recorded SHA256 identities. See final-installed-source-comparison.json and final-source-identity.json. Wheels/dependency snapshots are private evidence artifacts, not releases.

## Measured numerical evidence

The analytic aerodynamic-damping oscillator has relative history errors 3.753182905915087e-4, 9.384328275319209e-5 and 2.3461002384178586e-5 for dt 0.02, 0.01 and 0.005 seconds; refinement ratios 3.999415616977041 and 3.9999690216338437. Maximum scaled equation error 1.0754140416113678e-13. The independent four-element axial elastic-dynamic example gives force error 7.342450330337698e-5 at forcing/first-axial frequency ratio 0.01; this is not general foundation qualification. These JSON records are under artifacts-09/parent/pytest-temp.

Reused fixed 48-panel/120-frequency field artifact: direct versus FFT relative errors pressure 5.021645209004767e-16 and elevation 6.254755567821453e-16. No new BEM solve, fit or finer mesh was needed for this method check. Saved no-lid family retains 99 passivity violations and elevation reconstruction error 10.744% against the unchanged 1% gate (pressure 0.9957%). It remains unverified.

## Independent review and disposition

Only Claude Opus 5.5 / medium was used. The parallel coarse diagnostic completed with 8 successful reads; the final integrated review completed with 19 successful reads. Review: C:/ANYworkspaceWork/floating-wind-opus-coarse-final-review-20261028-01/review.txt. The connected owner repaired mooring frame transforms, failed screening acceptance, wave/bound provenance, stress-memory preflight, late cancellation and resource forwarding. Parent counterexamples also verified recovery-section invalidation. Parent made a final small mechanical NumPy/local-displacement fix after the owner finished; this did not warrant another substantial worker assignment. Relevant independent tests and the installed suite passed. There is no new blanket clean-review verdict after those repairs.

Parallel hydro diagnosis supports reciprocity/asymmetry error in the coarse collocation coefficients: paired cross coefficients produce negative symmetric damping work in nearly undamped coupled modes. No demonstrated sign/order bug was found. This is evidence, not a proven universal cause; hydro debugging/refinement remains deferred.

## Failed evidence and limits

Initial isolated app attempts found stale dependency wheels (missing sketch/floating-overlap/mesher/viewer APIs). Matching read-only dependency snapshots resolved import compatibility. Artifact07: 101 passed, 5 failed, 2 setup errors. Artifact08: bounded timeout at 180.094 seconds, partial output retained. Artifact09's broader legacy wave test exposed the isolated launcher's missing multiprocessing main guard: child processes re-entered pytest. Final launcher fixes that guard; artifact10 passes the synthetic no-BEM Apply path. Two legacy wave-Prepare tests are not rerun because they can prepare hydrodynamics; no claim that their hydro preparation is qualified. The earlier failed/timeout evidence remains on disk.

Native field qualification, spatial refinement, a new full BEM campaign and general foundation dynamic validation were not run. No acceptance tolerance was relaxed. Native-bound wave data require a provenance-capable producer; imported histories remain explicitly labelled. Missing/attention modal screening cannot yield accepted structural stresses. The current application runtime used PySide6 6.12.0, outside the declared <6.11 GUI extra; this is not a supported-version or release qualification claim. A non-fatal Pyreadline3 deallocator warning is retained.

## Accounting and identities

Total recorded numerical/build check process-wall: 1516.970999999903 / 2400 seconds; remaining 883.029000000097. The 480-second reserve was not renewed or borrowed. Maximum per-process bound 180 seconds, two numerical threads. Exact commands, receipts, counts and preserved failures: final-verification.json and checks/. Review inference time is separate from this numerical allowance. All terminal check receipts report owned-process cleanup.

Repository HEADs (dirty task files identified separately):
- ANYloads: `28a15c09eff75037dedf62c66ca09ea21221c5af`
- ANYsolver: `491026cad3c56a2628151805eb8f32ad1046175b`
- ANYhydro: `8614ddf068d9d7149e5a80f66a1a4bc24c3da1eb`
- ANYfem: `80d18140bb075e26b239f5243c8da4cd09b4ea2c`
- ANYstability: `d5dde0e492d9a21dae670c63d54a2cae66c95ef5`

Full handoff: C:\ANYworkspaceWork\floating-wind-implementation-20261027-01\final-evidence.md
