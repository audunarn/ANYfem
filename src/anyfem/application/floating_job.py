"""Worker-process entry points for the floating linear-response mode (Prepare / Reopen-validate / Estimate).

All take a serialized project + mesh like the existing wave jobs, return JSON-safe summaries and persist the immutable record
next to the project.  Only ``prepare_floating_job`` can ever run BEM, and only when ``allow_native_solve`` is explicitly true;
reopen/validate/remap never solves.  Cancellation uses the public ``CancellationToken.is_cancelled()``.
"""
from __future__ import annotations

import contextlib
import math
import shutil
import sys
import tempfile
from pathlib import Path

import numpy as np

from .floating_prepare import FloatingComponent, FloatingSettings, FloatingPrepareError


def floating_settings_from_wave(wave: dict, **floating) -> FloatingSettings:
    """Translate the dialog's regular/irregular wave settings into a floating realization (same components/times)."""
    from .hydro_wave_input import RegularWaveInput
    from .irregular_wave_input import IrregularWaveInput
    if wave.get('follow_surface') is True:
        raise FloatingPrepareError('Floating linear response cannot be combined with Follow surface', 'settings')
    if wave.get('wave_type') == 'irregular':
        w = IrregularWaveInput(**{k: v for k, v in wave.items() if k in IrregularWaveInput.__dataclass_fields__})
        r = w.realization()
        comps = tuple(FloatingComponent(float(c.omega), float(c.amplitude), float(c.phase),
                                        math.radians(float(getattr(c, 'direction_degrees', w.heading))))
                      for c in r.components)
    else:
        w = RegularWaveInput(**{k: v for k, v in wave.items() if k in RegularWaveInput.__dataclass_fields__})
        comps = (FloatingComponent(math.tau / w.period, w.height / 2.0, math.radians(w.phase), math.radians(w.heading)),)
    from dataclasses import asdict
    return FloatingSettings(components=comps, times=tuple(w.times), water_depth=float(w.depth),
                            wave_input=asdict(w), **floating)


def _settings(settings):
    """``FloatingSettings`` or ``{'wave': <dialog wave settings>, 'floating': <FloatingSettings keywords>}`` (strict keys)."""
    if isinstance(settings, FloatingSettings):
        return settings
    unknown = set(settings) - {'wave', 'floating'}
    if unknown:
        raise FloatingPrepareError(f'unknown floating setting groups {sorted(unknown)}', 'settings')
    floating = dict(settings.get('floating', {}))
    bad = set(floating) - set(FloatingSettings.__dataclass_fields__)
    if bad:
        raise FloatingPrepareError(f'unknown floating settings {sorted(bad)}', 'settings')
    if 'envelope_extra_face_ids' in floating:
        floating['envelope_extra_face_ids'] = tuple(int(i) for i in floating['envelope_extra_face_ids'])
    return floating_settings_from_wave(settings['wave'], **floating)


def _load(document, mesh_data, project):
    from ..io.project_file import project_from_dict
    from ..model import Project
    from anymesher.serialize import mesh_from_dict
    # The worker wrapper passes its (possibly empty) project override as ``project``; only a real Project is used as one.
    return (project if isinstance(project, Project) else project_from_dict(document)), mesh_from_dict(mesh_data)


@contextlib.contextmanager
def owned_publication_workspace(record_path):
    """Parent-owned PER-JOB publication workspace: a fresh directory inside the target's own directory (same filesystem, so the final
    commit stays an atomic ``os.replace``).  The worker stages its temp ONLY in here; when the job ends - normally, by error, or after
    a HARD kill of the worker tree - the parent removes exactly this directory and nothing else (no globbing, no other job's files)."""
    target = Path(record_path)
    if not target.is_absolute():
        raise FloatingPrepareError('the record path must be absolute for an owned publication workspace', 'publication')
    target.parent.mkdir(parents=True, exist_ok=True)
    workspace = Path(tempfile.mkdtemp(prefix='.anyfem-publication-', dir=target.parent))
    failures = []
    try:
        yield workspace
    except BaseException as primary:
        # The primary worker failure/cancel stays THE error; a cleanup failure is attached to it, never substituted for it.
        _remove_workspace(workspace, failures)
        if failures:
            primary.add_note(_cleanup_message(workspace, failures, committed=False))
        raise
    else:
        _remove_workspace(workspace, failures)
        if failures:
            # The job itself finished (an atomic commit, if any, is NOT undone by this): only the leftover staging is reported.
            raise FloatingCleanupError(_cleanup_message(workspace, failures, committed=True), 'publication')


class FloatingCleanupError(FloatingPrepareError):
    """The parent-owned staging directory could not be removed; its path is named so the user can delete it."""


def _remove_workspace(workspace, failures):
    def onerror(function, path, exc_info):
        failures.append(f'{path}: {exc_info[1]!r}')
    try:
        if sys.version_info >= (3, 12):
            shutil.rmtree(workspace, onexc=lambda f, p, e: failures.append(f'{p}: {e!r}'))
        else:
            shutil.rmtree(workspace, onerror=onerror)
    except OSError as error:
        failures.append(f'{workspace}: {error!r}')


def _cleanup_message(workspace, failures, *, committed):
    tail = ' The completed job result was not changed by this failure.' if committed else ''
    return (f'could not remove the owned publication staging directory {workspace} ({len(failures)} failure(s), first: {failures[0]}); '
            f'it may be deleted manually.{tail}')


def _cancel(token):
    """Public ``CancellationToken.is_cancelled`` is a bool PROPERTY; never call it."""
    return (lambda: bool(token.is_cancelled)) if token is not None else None


def _summary(prepared, record_path):
    d = prepared.diagnostics
    h = prepared.history
    return dict(kind='floating_linear_response_v1', record_path=str(record_path), array_sha256=h.array_sha256,
                resultants=d['resultants'], components=int(h.omega.size), active_components=len(prepared.active),
                nodes=len(h.node_ids),
                mass=prepared.mass, pose=prepared.pose, preview_times=list(prepared.settings['times']),
                rotation_envelope_deg=math.degrees(d['rotation_envelope_rad']), waterline_envelope_m=d['waterline_envelope_m'],
                clearance_m=d['clearance_m'], source_panels=d['source_panels'], target_cells=d['target_cells'],
                source_closure_max=max((r['relative'] for r in d['source_closure']), default=0.0),
                mapping_wrench_rel_max=max((e['relative'] for e in d['mapping_error']), default=0.0),
                mapped_closure_passed=all(r['passed'] for r in d['mapped_closure']),
                restoring_null_dimension=d['restoring_null_dimension'], static=bool(h.is_static))


def prepare_floating_job(*, project=None, document, mesh_data, settings, cache_directory, record_path, progress=None,
                         cancellation_token=None, publication_directory=None):
    from .floating_prepare import prepare_floating
    from .floating_apply import save_prepared
    p, mesh = _load(document, mesh_data, project)
    st = _settings(settings)
    cancel = _cancel(cancellation_token)
    prepared = prepare_floating(p, mesh, st, cache_directory, cancel=cancel,
                                progress=None if progress is None else (lambda w: progress(f'family omega={w:.4g} ready')))
    if cancel is not None and cancel():                       # stage/publish boundary: nothing is published after cancel
        raise FloatingPrepareError('cancelled before publication; the existing record is untouched', 'publish')
    return _summary(prepared, save_prepared(prepared, record_path, cancel=cancel, workspace=publication_directory))


def _canonical(value):
    import json
    return json.dumps(value, sort_keys=True, default=str)


def reopen_floating_job(*, project=None, document, mesh_data, settings, cache_directory=None, record_path, progress=None,
                        cancellation_token=None, publication_directory=None):
    """Offline reopen/validate/remap of a saved record (never BEM); rewrites the record only when it was re-assembled."""
    from .floating_apply import recover_prepared, reopen_or_remap, save_prepared
    p, mesh = _load(document, mesh_data, project)
    st = _settings(settings)
    old = recover_prepared(record_path)
    before = (old.history.array_sha256, _canonical(old.stage_keys), _canonical(old.settings))
    cancel = _cancel(cancellation_token)
    prepared = reopen_or_remap(p, mesh, st, record_path, cancel=cancel)
    # Re-gated proof metadata (keys/settings/marker) is published even when every array is identical.
    if (prepared.history.array_sha256, _canonical(prepared.stage_keys), _canonical(prepared.settings)) != before:
        if cancel is not None and cancel():
            raise FloatingPrepareError('cancelled before publication; the existing record is untouched', 'publish')
        save_prepared(prepared, record_path, cancel=cancel, workspace=publication_directory)
    return _summary(prepared, record_path)


def estimate_floating_job(*, project=None, document, mesh_data, settings, cache_directory, progress=None,
                          cancellation_token=None):
    """Mass + equilibrium + source, then the number of native solves Prepare would need (no BEM; zero amplitude needs none)."""
    from ..solve.build import build_fe_model
    from .floating_mass import dry_mass_properties
    from . import floating_source as fsrc
    from .floating_prepare import solve_equilibrium, missing_solve_count, _active
    p, mesh = _load(document, mesh_data, project)
    st = _settings(settings)
    built = build_fe_model(p, mesh, require_loads=False, require_supports=False)
    props = dry_mass_properties(built.fe_model)
    pose = solve_equilibrium(p, st, props, cancel=_cancel(cancellation_token))      # returns the accepted pose dict
    mp = fsrc.MeanPose(np.array(pose['rotation_body_to_world']),
                       np.array([pose['datum_xy_m'][0], pose['datum_xy_m'][1], pose['origin_z_m']]))
    target = fsrc.prepare_target_cells(p, mesh, mp)
    source = fsrc.prepare_source(p, mesh, mp, target, panel_size=st.panel_size, max_panels=st.max_panels)
    return dict(missing_families=missing_solve_count(cache_directory, st, source, pose), components=len(st.components),
                active_components=len(_active(st)), source_panels=len(source.triangles), pose=pose, mass=props.mass)
