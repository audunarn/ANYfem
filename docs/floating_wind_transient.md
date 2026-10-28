# Floating wind transient workflow

This development workflow combines a prescribed external load history, one
thrust-curve turbine and wind relative to the moving hub. Foundation stresses
are **quasistatic**. The stationary structural mesh is retained. Source
preparation, hydrodynamic acceptance and structural numerical acceptance are
separate: an unverified coarse hydrodynamic source remains explicitly
unverified even when integration and structural recovery finish successfully.

## Prepared inputs

Use the selected FE mass formulation, including explicitly modelled tower,
RNA, ballast and point masses. The turbine applicator adds no mass. The
operating mean uses the authenticated ANYgeometry owner through ANYstability;
its installed runtime and pinned wheel are configured separately from the
application's geometry dependency.

The headless preparation entry points are:

* `anystability.floating_operating_equilibrium`: canonical operating mean with
  trial-pose thrust, external attachment loads and supplied fairlead laws.
* `anyhydro.floating_transient_prepare.build_development_coarse_source`:
  retained coarse family and native coefficients to explicitly unverified
  generalized and field kernels, preserving failed diagnostics.
* `anyfem.application.floating_transient_prepare.prepare_coarse_transient`:
  conservative panel mapping and hydrostatic nodal integration on the supplied
  source and structural mesh.
* `anyfem.application.floating_transient_job.prepare_floating_transient_job`:
  bind the solved mean, mapping, physical load definitions and saved job input.

The source geometry, pose, query points, kernel, timestamps, wave provenance,
mass and structural content must match. A changed operating mean requires a
matching prepared family; old coefficients are refused. Remapping, Apply and
reopening do not launch BEM. The exact-zero instantaneous waterline policy
retains its raw native failure and requires the matching pointwise error bound
in every clearance check.

## Application controls

Open **Floating wind transient…** from the wave-load dialog and select the
prepared job input, field-kernel artifact and output path. Configure imported
three-component wind or Kaimal turbulence, thrust curve, absolute body hub
point, yaw/tilt, external histories and mooring attachments. Mooring stiffness
and damping are supplied in world axes and transformed into the solved mean
body axes. External load channels declare their frame and attachment point.

The Kaimal spectral realization and omitted variance are retained. Refining
the integration grid reuses that realization; regeneration is explicit.
Changing the structural output window does not restart the physical history.
The turbine uses rotor-axial relative wind and refuses speeds outside the
supplied curve, including negative speeds.

Mean preparation and integration run from frozen inputs in the cancellable
worker. Development hydrodynamics require the explicit development option.
Reopen and **Inspect applied frame** read saved data without solving. Apply
registers an artifact reference with provenance and supports undo/redo; it
does not solve already-relieved frame loads a second time. A changed mass,
constraint, stiffness or recovery section invalidates the saved result.

## Headless execution example

For an existing ANYfem `project`, `mesh`, prepared input and field kernel:

```python
from anyfem.solve.build import build_fe_model
from anyfem.application.floating_transient_job import run_floating_transient_job
from anyfem.application.floating_transient_record import load_floating_transient_result

fe = build_fe_model(
    project, mesh, require_loads=False, require_supports=False
).fe_model

summary = run_floating_transient_job(
    "prepared-input.npz", "field-kernel.npz", "floating-result.npz",
    fe_model=fe,
    # screening=modal_record,  # independently verified, model-bound modal data
)
if summary.get("published"):
    result = load_floating_transient_result(
        summary["result_path"], expected_hash=summary["result_hash"]
    )
    print(result.status, result.times, result.frame_indices)
```

Publication means a diagnostic/result artifact was saved, not that its stresses
were accepted. Inspect `status`, `accepted_structural_output`, hydrodynamic
verification and unpassed gates. Missing modal data are reported as not
assessed. The modal/loading-band comparison includes self-equilibrated physical
loads; it is a heuristic indicator, not proof of elastic dynamic accuracy.
An attention result is not accepted as quasistatic structural output.

Each selected frame assembles physical mean and perturbation loads, subtracts
the primary rigid inertial load, then uses the existing batch inertia-relief
path for the residual correction. Primary, correction and total accelerations
remain separate. Stress recovery uses effective loads. Static balance is
checked separately from mapped dynamic closure before residual relief.

Default additional working-memory and field-artifact limits are 256 MiB and
2 GiB. `max_work_bytes`, `rigid_limits` and the structural `resource_config`
can impose tighter limits. The mesh is not refined to satisfy these limits.

Verification commands, source identities, failed evidence, independent Opus
reviews and current limitations are recorded in
[`handoff/inertia-relief-20261027.md`](handoff/inertia-relief-20261027.md).
The development implementation does not constitute hydrodynamic qualification
or publication approval.
