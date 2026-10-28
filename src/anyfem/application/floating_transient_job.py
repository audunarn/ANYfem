"""Headless job entry points for the floating TRANSIENT workflow (run / reopen).

* ``run_floating_transient_job`` loads the immutable job input and the digest-bound field kernel from disk (hash
  verified, header-first admission), runs the connected transient against the selected FE model and publishes the
  immutable result atomically.  A cancelled run publishes NOTHING (an existing result is left untouched); other
  non-success outcomes are published as explicit non-success records so their diagnostics survive.
* ``reopen_floating_transient_result`` only loads and verifies a saved result: it never runs BEM, the transient
  solver or the residual relief.

Both return JSON-safe summaries; no callbacks or executable objects are persisted.
"""
from __future__ import annotations

from typing import Any, Callable, Optional

from .floating_transient import load_floating_transient_input, run_floating_transient
from .floating_transient_record import FloatingTransientRecordError, load_floating_transient_result


def _summary(result: Any, digest: str, path: str) -> dict:
    return dict(status=result.status, message=result.message, accepted=result.accepted_structural_output,
                hydrodynamic_verification=result.hydrodynamic_verification,
                unpassed_gates=list(result.unpassed_gates), n_samples=int(result.times.size),
                n_frames=int(result.frame_indices.size), result_hash=digest, result_path=str(path),
                qualification_status="unqualified")


def run_floating_transient_job(input_path: str, field_kernel_path: str, result_path: str, *, fe_model: Any,
                               expected_input_hash: Optional[str] = None, relief: Any = None, batch_size: int = 8,
                               output_dofs: Any = None, cancellation_token: Any = None,
                               progress_callback: Optional[Callable[[str, int, int], None]] = None,
                               max_work_bytes: Optional[int] = None, resource_config: Any = None,
                               screening: Any = None, rigid_limits: Any = None) -> dict:
    from anyhydro.radiation_field_kernel import load_radiation_field_kernel
    inp = load_floating_transient_input(input_path, expected_hash=expected_input_hash)
    fk = load_radiation_field_kernel(field_kernel_path)
    if fk.content_hash() != inp.field_kernel_hash:
        raise FloatingTransientRecordError("the field-kernel artifact is stale for this job input (hash mismatch)")
    extra = {} if max_work_bytes is None else {"max_work_bytes": int(max_work_bytes)}
    result = run_floating_transient(inp, fe_model=fe_model, field_kernel=fk, relief=relief, batch_size=batch_size,
                                    output_dofs=output_dofs, cancellation_token=cancellation_token,
                                    progress_callback=progress_callback, resource_config=resource_config,
                                    screening=screening, rigid_limits=rigid_limits, **extra)
    def token_cancelled():
        return cancellation_token is not None and bool(getattr(cancellation_token, "is_cancelled", False))

    if result.status == "cancelled" or (result.status != "complete" and "cancel" in result.status) \
            or token_cancelled():                          # token checked again AFTER relief
        return dict(status="cancelled", message=result.message if result.status == "cancelled" else
                    "cancelled after the run; nothing published", published=False, result_path=None)
    from .floating_transient_record import PublicationCancelled
    try:
        digest = result.save(result_path, cancel=token_cancelled)   # polled immediately before the atomic replace
    except PublicationCancelled as exc:
        return dict(status="cancelled", message=str(exc), published=False, result_path=None)
    out = _summary(result, digest, result_path)
    out["published"] = True
    return out


def hydro_fields_from_development_source(source: Any) -> dict:
    """Input fields of an ANYhydro ``DevelopmentCoarseSource`` (always unverified, its unpassed gates verbatim)."""
    from .floating_transient import hydro_input_fields
    if getattr(source, "hydrodynamic_verification", None) != "unverified" or not getattr(source, "unpassed_gates", ()):
        raise FloatingTransientRecordError("a development coarse source must be unverified with its unpassed gates")
    return hydro_input_fields(radiation_kernel=source.kernel.kernel, added_mass_infinite=source.added_mass_infinite,
                              dt=float(source.kernel.settings.dt), field_kernel=source.field_kernel,
                              verification="unverified", unpassed_gates=tuple(source.unpassed_gates),
                              admission_omegas=source.admission_omegas)


def prepare_floating_transient_job(input_path: str, *, solve_mean: Callable[[], dict],
                                   prepare_at_pose: Callable[[Any], Any], hydro_fields: dict, load_fields: dict,
                                   times: Any, frame_indices: Any, dry: Any, gravity: float, length_scale: float,
                                   identities: Optional[dict] = None, cancellation_token: Any = None) -> dict:
    """Mean preparation + coarse operators + immutable job input (no BEM, no transient).

    1. ``solve_mean()`` MUST run the canonical operating mean (e.g. ``floating_equilibrium_process.
       solve_operating_equilibrium_isolated``) and return a dict with its ``mean_equilibrium`` record
       (``rotation_body_to_world``, ``translation_world``, ``mean_thrust``, ``identity``, ``status``);
    2. ``prepare_at_pose(MeanPose)`` prepares the coarse operators AT that solved pose
       (``floating_transient_prepare.prepare_coarse_transient``); operators bound to another pose refuse (stale);
    3. the input binds the mean record, the operator identities and the mean thrust (turbine runs) exactly.
    A cancelled preparation publishes nothing."""
    from .floating_source import MeanPose
    from .floating_transient import FloatingTransientInput, mass_identity
    from .floating_transient_prepare import require_bound_to_mean
    import numpy as np

    def cancelled():
        return cancellation_token is not None and bool(getattr(cancellation_token, "is_cancelled", False))

    mean = solve_mean()
    if cancelled():
        return dict(status="cancelled", published=False, input_path=None)
    if not isinstance(mean, dict) or "mean_equilibrium" not in mean:
        raise FloatingTransientRecordError("the mean preparation did not return an operating-mean record")
    record = dict(mean["mean_equilibrium"])
    Q = np.asarray(record["rotation_body_to_world"], dtype=float)
    t = np.asarray(record["translation_world"], dtype=float)
    ops = prepare_at_pose(MeanPose(Q, t))
    if cancelled():
        return dict(status="cancelled", published=False, input_path=None)
    require_bound_to_mean(ops, Q, t)
    loads = dict(load_fields)
    if loads.get("wind_velocity") is not None:
        loads["mean_thrust"] = float(record["mean_thrust"])
        # the declared physical reference mean wind of the operating mean (the realization is used as is; its sample
        # mean is never subtracted); a different caller value is refused by the input binding
        ref = (record.get("binding") or {}).get("wind_reference_mean_world")
        if loads.get("wind_reference_mean_world") is None and ref is not None:
            loads["wind_reference_mean_world"] = np.asarray(ref, dtype=float)
    ident = dict(identities or {})
    ident.update(coarse_prepare=dict(ops.identities), mean_equilibrium_identity=str(record.get("identity", "")))
    inp = FloatingTransientInput(times=times, frame_indices=frame_indices, gravity=gravity, length_scale=length_scale,
                                 mass_digest=mass_identity(dry), mean_equilibrium=record, identities=ident,
                                 **ops.input_fields(), **hydro_fields, **loads)
    digest = inp.save(input_path)
    return dict(status="prepared", published=True, input_path=str(input_path), input_hash=digest,
                hydrodynamic_verification=inp.hydrodynamic_verification,
                unpassed_gates=list(inp.unpassed_hydro_gates), mean_equilibrium=record,
                qualification_status="unqualified")


from ..commands import Command  # noqa: E402


def result_reference(name: str, result_path: str, *, expected_hash: str, development_attached: bool = False):
    """Build the persistent ``FloatingTransientResultRef`` from the hash-VERIFIED artifact (never from UI state)."""
    from ..model.project import FLOATING_TRANSIENT_DEVELOPMENT_MARKER, FloatingTransientResultRef
    res = load_floating_transient_result(result_path, expected_hash=expected_hash)
    unverified = res.hydrodynamic_verification == "unverified"
    idx = tuple(int(i) for i in res.frame_indices)
    prov = dict(identities=dict(res.identities), mean_equilibrium=res.diagnostics.get("mean_equilibrium"),
                static_mean_balance=res.diagnostics.get("static_mean_balance"),
                stress_layout=res.diagnostics.get("stress_layout"), message=res.message)
    return FloatingTransientResultRef(
        name=str(name), result_path=str(result_path), result_hash=expected_hash, status=res.status,
        hydrodynamic_verification=res.hydrodynamic_verification, unpassed_gates=tuple(res.unpassed_gates),
        frame_indices=idx, frame_times=tuple(float(res.times[i]) for i in idx),
        input_hash=str(res.identities.get("input_hash", "")), mass_digest=str(res.identities.get("mass_digest", "")),
        structural_digest=_require_structural(res), provenance=json_safe(prov), development_attached=bool(development_attached and unverified),
        marker=FLOATING_TRANSIENT_DEVELOPMENT_MARKER if unverified else "")


def _require_structural(res) -> str:
    s = str(res.identities.get("structural_digest", ""))
    if len(s) != 64:
        raise ValueError("the transient result does not bind its structural (stiffness) identity; it cannot be applied")
    return s


def json_safe(value):
    import json
    from .floating_transient_record import _canonical
    return json.loads(_canonical(value))


class ApplyFloatingTransientResult(Command):
    """Undoable, persistent registration of a published transient result (``project.floating_transient_results``,
    saved by the project file as an artifact reference + provenance).  The artifact hash is re-verified at do/redo; no
    solve and no BEM.  Unverified results need an explicit development attachment (``FloatingTransientResultRef``)."""
    label = 'Floating transient result'

    def __init__(self, reference, *, replace: bool = False):
        from ..model.project import FloatingTransientResultRef
        if not isinstance(reference, FloatingTransientResultRef):
            raise ValueError('ApplyFloatingTransientResult needs a FloatingTransientResultRef')
        self.reference, self.replace = reference, bool(replace)

    def _verify(self):
        # the FULL reference metadata must be what the verified artifact yields (not only the status)
        ref = self.reference
        rebuilt = result_reference(ref.name, ref.result_path, expected_hash=ref.result_hash,
                                   development_attached=ref.development_attached)
        if rebuilt.to_dict() != ref.to_dict():
            raise ValueError('the transient artifact no longer matches its reference metadata; nothing applied')

    def do(self, project):
        self._verify()
        self.before = dict(project.floating_transient_results)
        name = self.reference.name
        if name in self.before and not self.replace:
            raise ValueError('a floating transient result with this name already exists; choose a new name')
        after = dict(self.before)
        after[name] = self.reference
        project.floating_transient_results = after
        self.after = after
        return self.reference

    def undo(self, project):
        project.floating_transient_results = dict(self.before)

    def redo(self, project):
        self._verify()
        project.floating_transient_results = dict(self.after)
        return self.reference


def applied_floating_transient_frames(project, fe_model, name: str, *, allow_development: bool = False) -> dict:
    """Production consumer of an applied result: hash-verified artifact, CURRENT dry-mass identity, then the TOTAL
    quasistatic frame loads (mean + perturbation - M a), relieved displacements and labelled quasistatic stresses.
    Development results are returned only with ``allow_development=True`` and stay labelled unverified."""
    from .floating_mass import dry_mass_properties
    from .floating_transient import mass_identity
    ref = project.floating_transient_results.get(name)
    if ref is None:
        raise ValueError(f'no applied floating transient result {name!r}')
    if not ref.accepted and not allow_development:
        raise ValueError(f'{name!r} is an UNVERIFIED development result ({", ".join(ref.unpassed_gates)}); it is not '
                         'an accepted load - pass allow_development explicitly to inspect it')
    from .floating_transient import structural_identity
    res = load_floating_transient_result(ref.result_path, expected_hash=ref.result_hash)
    if mass_identity(dry_mass_properties(fe_model)) != ref.mass_digest:
        raise ValueError(f'{name!r} was computed for another dry mass / mesh (stale); run the transient again')
    if structural_identity(fe_model) != ref.structural_digest:
        raise ValueError(f'{name!r} was computed for another structural model (stiffness / material / section '
                         'changed; stale displacements and stresses are never reused); run the transient again')
    return dict(name=name, accepted=ref.accepted, marker=ref.marker, status=ref.status, frame_times=ref.frame_times,
                frame_indices=ref.frame_indices, displacement_layout=res.diagnostics.get("displacement_layout"),
                total_loads=res.frame_loads["total"], displacements=res.frame_displacements,
                stresses=res.frame_stresses, stress_layout=res.diagnostics.get("stress_layout"),
                stress_label=ref.stress_label, unpassed_gates=ref.unpassed_gates)


def applied_frame_at(frames: dict, time: float) -> dict:
    """Inspection of ONE applied frame at an exact saved frame time (no interpolation, no solve, no BEM)."""
    times = list(frames["frame_times"])
    matches = [i for i, t in enumerate(times) if abs(t - float(time)) <= 1e-12 * max(1.0, abs(t))]
    if not matches:
        raise ValueError(f'{time!r} is not a saved structural frame time; available: {times}')
    i = matches[0]
    layout = frames.get("stress_layout") or []
    stresses, col = {}, 0
    for eid, key, size in layout:
        stresses.setdefault(str(eid), {})[str(key)] = frames["stresses"][i, col:col + int(size)].tolist()
        col += int(size)
    return dict(time=times[i], frame_index=frames["frame_indices"][i], status=frames["status"],
                accepted=frames["accepted"], marker=frames["marker"], displacements=frames["displacements"][i],
                total_load=frames["total_loads"][i], stresses=stresses, stress_label=frames["stress_label"])


def reopen_floating_transient_result(result_path: str, *, expected_hash: Optional[str] = None) -> dict:
    """Load + verify only (no BEM, no transient, no relief)."""
    result = load_floating_transient_result(result_path, expected_hash=expected_hash)
    return _summary(result, result.content_hash(), result_path)
