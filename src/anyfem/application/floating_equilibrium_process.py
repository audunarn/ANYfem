"""Mean equilibrium through the PUBLIC ANYstability workflow in an ISOLATED configured runtime (neutral JSON in/out).

The runtime must hold the pinned installed ANYgeometry owner (the in-process editable checkout cannot authenticate it), so
the product spawns ``settings.equilibrium_python`` running ``anystability.floating_equilibrium_worker``.  Nothing is
hardcoded: the interpreter, optional ANYstability source path and owner wheel are explicit settings; a missing/invalid
runtime is an actionable refusal and is never bypassed.  The closed displacement envelope is validated here first.
The child is bounded by a wall-time limit, polled for cancellation and killed (with its tree) on cancel/timeout; the
result file is written atomically by the worker.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import numpy as np


class FloatingEquilibriumError(ValueError):
    pass


def envelope_face_ids(project, settings_or_extra):
    """Explicit hull reference faces: wet-surface plates + the user-selected closing faces (never inferred)."""
    extra = getattr(settings_or_extra, 'envelope_extra_face_ids', settings_or_extra)
    ids = [int(s.ref.id) for s in project.wet_surfaces] + [int(i) for i in extra]
    if len(set(ids)) != len(ids):
        raise FloatingEquilibriumError('envelope faces must be unique')
    return ids


def envelope_document(project, settings_or_extra=()):
    """Geometry-owner validated closed displacement envelope (indexed by geometry vertex identity, oriented, identity-bound)."""
    from anygeometry.floating_envelope import build_envelope
    from anygeometry.errors import GeometryError
    try:
        return build_envelope(project.geometry, envelope_face_ids(project, settings_or_extra))
    except GeometryError as error:
        raise FloatingEquilibriumError(f'displacement envelope refused by the geometry owner: {error}') from error


def _kill(process):
    try:
        if os.name == 'nt':
            subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'], capture_output=True, timeout=20)
        else:
            import signal
            os.killpg(process.pid, signal.SIGKILL)
    except Exception:
        process.kill()


def solve_equilibrium_isolated(project, settings, props, *, cancel=None):
    """Returns the accepted ``MeanFloatingPose.to_dict()``; raises FloatingEquilibriumError with the worker's refusal."""
    if not settings.equilibrium_python or not Path(settings.equilibrium_python).is_file():
        raise FloatingEquilibriumError('floating equilibrium needs an explicitly configured isolated Python runtime '
                                       '(equilibrium_python) that holds the pinned installed ANYgeometry; none/invalid supplied')
    if not settings.owner_wheel:
        raise FloatingEquilibriumError('floating equilibrium needs the pinned ANYgeometry owner wheel path (owner_wheel)')
    if settings.allow_development is not True:
        raise FloatingEquilibriumError('engineering/development equilibrium requires explicit allow_development opt-in')
    return _run_worker(settings, _base_spec(project, settings, props), 'floating_equilibrium_worker', cancel)['pose']


def solve_operating_equilibrium_isolated(project, settings, props, operating, *, cancel=None):
    """Canonical OPERATING mean (owner hydrostatic mean, then the full mean wrench with thrust at the trial orientation,
    mooring pretension and external means) in the same isolated runtime.  ``operating`` is the neutral dict documented in
    ``anystability.floating_operating_equilibrium_worker``.  Returns the worker's ``{hydrostatic_pose, operating,
    mean_equilibrium, components_body}``; refusals raise FloatingEquilibriumError."""
    spec = prepare_operating_spec(project, settings, props, operating)
    return run_operating_spec(settings, spec, cancel=cancel)


def prepare_operating_spec(project, settings, props, operating) -> dict:
    """GUI/caller-thread part: validate the runtime and freeze the complete neutral worker spec (envelope from the
    geometry owner, dry mass / CG, operating loads, finite timeout).  The returned dict is all a worker needs."""
    if not isinstance(operating, dict) or 'length_scale' not in operating:
        raise FloatingEquilibriumError('operating mean needs a neutral operating dict with length_scale')
    timeout = float(settings.equilibrium_timeout_s)
    if not (np.isfinite(timeout) and 0.0 < timeout <= 3600.0):
        raise FloatingEquilibriumError('the operating-mean runtime needs a finite timeout in (0, 3600] s')
    return json.loads(json.dumps(dict(_base_spec(project, settings, props), operating=operating,
                                      owner_deadline_s=timeout), allow_nan=False))


def run_operating_spec(settings, spec, *, cancel=None) -> dict:
    """Worker-thread part: run the frozen spec in the isolated runtime (process tree killed on cancel / timeout)."""
    out = _run_worker(settings, dict(spec), 'floating_operating_equilibrium_worker', cancel)
    return {k: out[k] for k in ('hydrostatic_pose', 'operating', 'mean_equilibrium', 'components_body')}


def _check_runtime(settings):
    if not settings.equilibrium_python or not Path(settings.equilibrium_python).is_file():
        raise FloatingEquilibriumError('floating equilibrium needs an explicitly configured isolated Python runtime '
                                       '(equilibrium_python) that holds the pinned installed ANYgeometry; none/invalid supplied')
    if not settings.owner_wheel:
        raise FloatingEquilibriumError('floating equilibrium needs the pinned ANYgeometry owner wheel path (owner_wheel)')
    if settings.allow_development is not True:
        raise FloatingEquilibriumError('engineering/development equilibrium requires explicit allow_development opt-in')


def _base_spec(project, settings, props):
    _check_runtime(settings)
    envelope = envelope_document(project, settings)
    return dict(envelope=dict(vertices=envelope['vertices'], faces=envelope['faces']), mass_kg=float(props.mass),
                cg_body_m=[float(x) for x in props.cg], rho=settings.rho, gravity=settings.gravity,
                datum_xy_m=list(settings.datum_xy), owner_wheel=str(settings.owner_wheel),
                controls=settings.equilibrium_controls, allow_development=True)


def _run_worker(settings, spec, module, cancel):
    bootstrap = ('import sys; src = sys.argv[3]; sys.path.insert(0, src) if src else None; '
                 f'from anystability.{module} import main; sys.exit(main(sys.argv[1:3]))')
    with tempfile.TemporaryDirectory(prefix='anyfem-floating-eq-') as work:
        source, target = Path(work) / 'input.json', Path(work) / 'output.json'
        source.write_text(json.dumps(spec, allow_nan=False), encoding='utf-8')
        # Owned output goes to FILES (never un-drained pipes): a chatty owner/Capytaine cannot fill a pipe buffer and block the child.
        out_log = open(Path(work) / 'stdout.log', 'wb')
        err_log = open(Path(work) / 'stderr.log', 'wb')
        kwargs = dict(stdout=out_log, stderr=err_log)
        if os.name == 'nt':
            kwargs['creationflags'] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            kwargs['start_new_session'] = True
        process = subprocess.Popen([str(settings.equilibrium_python), '-c', bootstrap, str(source), str(target),
                                    str(settings.stability_source or '')], **kwargs)
        deadline = time.monotonic() + float(settings.equilibrium_timeout_s)
        try:
            while process.poll() is None:
                if cancel is not None and cancel():
                    _kill(process)
                    raise FloatingEquilibriumError('equilibrium cancelled')
                if time.monotonic() > deadline:
                    _kill(process)
                    raise FloatingEquilibriumError(f'equilibrium exceeded {settings.equilibrium_timeout_s} s and was stopped')
                time.sleep(0.05)
        finally:
            if process.poll() is None:
                _kill(process)
            for stream in (out_log, err_log):
                stream.close()
        try:
            err = (Path(work) / 'stderr.log').read_bytes()[-2000:].decode('utf-8', 'replace')   # bounded diagnostics
        except OSError:
            err = ''
        if not target.is_file():
            raise FloatingEquilibriumError(f'equilibrium worker produced no result (exit {process.returncode}): {err}')
        out = json.loads(target.read_text(encoding='utf-8'))
    if out.get('status') != 'ok':
        raise FloatingEquilibriumError(f"equilibrium refused ({out.get('type')}, {out.get('code')}): {out.get('message')}")
    return out
