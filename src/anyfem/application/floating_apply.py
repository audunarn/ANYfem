"""Apply / reopen / remap and load-builder hooks for prepared floating histories (no BEM anywhere in this module).

The saved artifact is ONE atomic ``.npz`` that is self-contained: history arrays, response (Hq), equilibrium record, settings,
stage keys, the mesh-independent BEM source manifest and EVERY retained family snapshot.  ``ApplyFloatingLoad`` embeds the same
bytes in the project's ONE coupled load (``FloatingCoupledLoadHistory`` inside a ``WetPressure``), so the normal project save/reopen
carries the entire validated record.  Reopen/remap of the same hull needs no external cache and cannot start BEM; stale stages are
named; tolerance tightening re-runs the gates from the retained data (no BEM).

Hooks callable by a later run/build dispatch (those files are held):
  ``floating_history_of(case)``                      the case's floating history (or None)
  ``floating_nodal_load(history, time)``             real nodal (n,6) pressure+weight+primary inertia at an absolute time
  ``floating_load_case(prepared_or_history, fe_model, time)`` explicit nodal LoadCase (gravity=None)
  ``run_floating_relief(prepared, fe_model, times)`` residual inertia-relief stream (solve/floating_batch)
"""
from __future__ import annotations

import io
import json
from dataclasses import replace
from pathlib import Path
from typing import Any, Iterable, Optional

import numpy as np

from ..commands import Command
from ..document import DocumentSession, canonical_hash
from ..io.project_file import project_to_dict
from .floating_prepare import FloatingPrepared, FloatingPrepareError, assemble_prepared, _mesh_hash
from .floating_mass import dry_mass_properties
from .floating_loads import nodal_wrench

VERSION = 'floating_prepared_v3'
STAGE_ORDER = ('mass', 'geometry', 'equilibrium', 'realization', 'mesh', 'gates')
TOLERANCE_KEYS = ('asymmetry', 'source_closure', 'coverage', 'mapping', 'static_force_N', 'static_moment_Nm')


class FloatingStale(FloatingPrepareError):
    """A saved record no longer matches the current inputs; ``stage`` is the first stage that must be redone."""


def _json_safe(value):
    from collections.abc import Mapping
    if isinstance(value, Mapping):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    if isinstance(value, np.ndarray):
        return _json_safe(value.tolist())
    if isinstance(value, (np.floating, np.integer)):
        return value.item()
    if isinstance(value, np.bool_):
        return bool(value)
    if isinstance(value, complex):
        return [value.real, value.imag]
    return value


def prepared_to_bytes(prepared: FloatingPrepared) -> bytes:
    from anyhydro import floating_radiation_fields as ff
    h = prepared.history; src = prepared.source
    if src is None or prepared.settings is None:
        raise FloatingPrepareError('a saved floating artifact must embed its source manifest and settings', 'record')
    arrays, snap_meta = {}, []
    for i, s in enumerate(prepared.snapshots):
        meta, arr = ff.snapshot_to_payload(s)
        snap_meta.append(meta)
        arrays.update({f'snap{i}_{k}': v for k, v in arr.items()})
    meta = dict(version=VERSION, binding=h.binding, stage_keys=prepared.stage_keys, pose=prepared.pose,
                diagnostics=_json_safe(prepared.diagnostics), mass=prepared.mass, cg_body=list(prepared.cg_body),
                node_ids=list(h.node_ids), mesh_content_sha256=h.mesh_content_sha256, array_sha256=h.array_sha256,
                snapshots=snap_meta, active=list(prepared.active), settings=_json_safe(prepared.settings),
                source_labels=list(src.surface_ids), source_provenance=_json_safe(src.provenance))
    buffer = io.BytesIO()
    np.savez(buffer, meta=json.dumps(_json_safe(meta), sort_keys=True), omega=h.omega, amplitude=h.amplitude, phase=h.phase,
             pressure=h.loads['pressure'], weight=h.loads['weight'], inertia=h.loads['inertia'], static=h.static_load,
             Hq=prepared.Hq, src_vertices=src.vertices, src_faces=src.faces, src_areas=src.areas, src_centers=src.centers,
             src_normals=src.normals, src_triangles=np.array(src.triangles), **arrays)
    return buffer.getvalue()


def save_prepared(prepared: FloatingPrepared, path, cancel=None, workspace=None) -> Path:
    """Atomically publish a record.  ``cancel`` (optional bool callback) is polled before encoding, after encoding, after the temp is
    written and IMMEDIATELY before the atomic replace; a cooperative cancel refuses with stage 'publication', leaves an existing
    destination untouched and removes only the owned temp.  (Hard process kills are not covered by this callback.)"""
    import os, tempfile

    def check(where):
        if cancel is not None and cancel():
            raise FloatingPrepareError(f'cancelled {where}; nothing was published and the existing record is untouched', 'publication')

    check('before encoding')
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    stage_dir = path.parent
    if workspace is not None:
        # Parent-owned per-job workspace (hard-kill cleanup): an absolute target, a workspace that is a directory directly INSIDE the
        # target's directory and on the same filesystem, so the final os.replace stays atomic.  Nothing else is ever touched.
        ws = Path(workspace)
        if not path.is_absolute() or not ws.is_dir() or ws.resolve().parent != path.parent.resolve() \
                or os.stat(ws).st_dev != os.stat(path.parent).st_dev:
            raise FloatingPrepareError('the publication workspace must be a directory inside the absolute target directory '
                                       '(same filesystem)', 'publication')
        stage_dir = ws
    data = prepared_to_bytes(prepared)                       # everything validated BEFORE a temp file exists
    check('after encoding')
    fd, name = tempfile.mkstemp(dir=stage_dir, prefix=path.name + '.', suffix='.tmp')   # unique, owned temp
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data); stream.flush(); os.fsync(stream.fileno())
        check('before the atomic replace')
        os.replace(name, path)                               # atomic publish; an existing destination is untouched on failure
    finally:
        if os.path.exists(name):
            os.unlink(name)                                  # only our own temp
    return path


def recover_prepared(source) -> FloatingPrepared:
    """Fully validated recovery from a path or from bytes: arrays, Hq, source, families, settings, keys, identities."""
    from anyloads.floating_history import FloatingCoupledLoadHistory
    from anyhydro import floating_radiation_fields as ff
    from .floating_source import SourceManifest
    payload = bytes(source) if isinstance(source, (bytes, bytearray)) else Path(source).read_bytes()
    history = FloatingCoupledLoadHistory.from_payload(payload)          # arrays + binding identity checked
    with np.load(io.BytesIO(payload), allow_pickle=False) as d:
        meta = json.loads(str(d['meta']))
        if meta.get('version') != VERSION:
            raise FloatingPrepareError('unsupported floating record version', 'record')
        Hq = np.array(d['Hq'])
        if Hq.shape != (history.omega.size, 6) or not np.isfinite(Hq).all():
            raise FloatingPrepareError('embedded response does not match the realization', 'record')
        source_m = SourceManifest(np.array(d['src_vertices']), np.array(d['src_faces']), tuple(meta['source_labels']),
                                  tuple(np.array(t) for t in d['src_triangles']), np.array(d['src_areas']),
                                  np.array(d['src_centers']), np.array(d['src_normals']), meta['source_provenance'])
        if source_m.digest() != meta['stage_keys'].get('source'):
            raise FloatingPrepareError('embedded source manifest does not match its recorded identity', 'record')
        snaps = []
        for i, text in enumerate(meta['snapshots']):
            prefix = f'snap{i}_'
            snaps.append(ff.snapshot_from_payload(text, {k[len(prefix):]: np.array(d[k]) for k in d.files if k.startswith(prefix)}))
        if [s.snapshot_id for s in snaps] != meta['stage_keys'].get('families'):
            raise FloatingPrepareError('embedded families do not match their recorded identities', 'record')
    return FloatingPrepared(history, Hq, meta['pose'], meta['stage_keys'], meta['diagnostics'], meta['mass'],
                            tuple(meta['cg_body']), source_m, tuple(snaps), meta['settings'], tuple(meta['active']))


def stale_stage(prepared: FloatingPrepared, *, props, mesh_id: str, settings, project=None) -> Optional[str]:
    """First stage whose identity differs from the saved record (None = fully current)."""
    from .floating_source import hull_geometry_key
    k = prepared.stage_keys
    saved = k.get('mass_numeric', {})
    same_mass = (np.isclose(props.mass, saved.get('mass', np.nan), rtol=1e-9, atol=0.0)
                 and np.allclose(props.cg, saved.get('cg', [np.nan] * 3), rtol=1e-9, atol=1e-9))
    same_mesh = k.get('mesh') == mesh_id
    same_inertia = np.allclose(props.inertia_cg, saved.get('inertia', np.full((3, 3), np.nan)), rtol=1e-9,
                               atol=1e-9 * max(1.0, float(np.abs(props.inertia_cg).max())))
    saved_tol = k.get('tolerances', {})
    # A missing saved verdict tolerance is unknown: the gates are re-evaluated from the retained data (no BEM).
    stricter = any(float(getattr(settings, name_map)) < float(saved_tol.get(key, np.inf))
                   for key, name_map in (('asymmetry', 'asymmetry_tolerance'), ('source_closure', 'source_closure_tolerance'),
                                         ('coverage', 'coverage_tolerance'), ('mapping', 'mapping_tolerance'),
                                         ('static_force_N', 'static_force_N'), ('static_moment_Nm', 'static_moment_Nm')))
    if not same_mass or (same_mesh and not same_inertia):
        return 'mass'
    if project is not None and k.get('geometry') != hull_geometry_key(project, settings.envelope_extra_face_ids):
        return 'geometry'
    sv = prepared.settings or {}
    # Explicit physical comparison (not only a hash): the world datum/phase origin and fluid constants define the equilibrium.
    if (list(sv.get('datum_xy', ())) != [float(x) for x in settings.datum_xy]
            or float(sv.get('rho', np.nan)) != float(settings.rho) or float(sv.get('gravity', np.nan)) != float(settings.gravity)
            or list(sv.get('envelope_extra_face_ids', ())) != [int(i) for i in settings.envelope_extra_face_ids]):
        return 'equilibrium'
    controls = json.dumps(settings.equilibrium_controls, sort_keys=True, default=str)
    if json.dumps(sv.get('equilibrium_controls'), sort_keys=True, default=str) != controls:
        return 'equilibrium'                          # the workflow controls define the accepted equilibrium
    if k.get('realization') != settings.realization_digest():
        return 'realization'
    # A different BEM source request is a different source: it can never masquerade as the stored one.
    if float(sv.get('panel_size', np.nan)) != float(settings.panel_size) or int(sv.get('max_panels', -1)) != int(settings.max_panels):
        return 'source'
    if not same_mesh:
        return 'mesh'
    from .floating_prepare import RESTORING_GATE_MARKER
    # Records written before the restoring audit / response-sensitivity gates carry no marker: they are re-gated offline.
    unmarked = k.get('restoring_gate') != RESTORING_GATE_MARKER
    return 'gates' if (stricter or unmarked) else None


def reopen_or_remap(project, mesh, settings, path_or_bytes, *, cache_dir=None, cancel=None, progress=None,
                    evaluator=None) -> FloatingPrepared:
    """Offline reopen.  Same everything: the validated record.  Same hull, other mesh (or a stricter gate): the embedded
    source/families are re-assembled on the current target (no cache, never BEM).  Changed mass, hull geometry or
    realization: ``FloatingStale`` naming the stage.  ``cache_dir`` is accepted for API symmetry and never read."""
    from ..solve.build import build_fe_model
    if cancel is not None and cancel():
        raise FloatingPrepareError('cancelled', 'reopen')
    prepared = recover_prepared(path_or_bytes)
    built = build_fe_model(project, mesh, require_loads=False, require_supports=False)
    props = dry_mass_properties(built.fe_model)
    stage = stale_stage(prepared, props=props, mesh_id=_mesh_hash(mesh), settings=settings, project=project)
    if stage is None:
        return prepared
    if stage in ('mass', 'geometry', 'equilibrium', 'realization', 'source'):
        raise FloatingStale(f'saved floating record is stale at stage {stage!r}; run Prepare again', stage)
    if cancel is not None and cancel():
        raise FloatingPrepareError('cancelled', 'reopen')
    return assemble_prepared(project, mesh, settings, prepared.pose, prepared.source, prepared.snapshots, built=built,
                             props=props, evaluator=evaluator)


# ---------------------------------------------------------------- the ONE project load
class ApplyFloatingLoad(Command):
    """Apply a prepared floating record as ONE project load series (undo/redo, replacement safeguards, selected absolute times).

    ``result`` carries: name, record_path, prepared content identity, selected_times, window, input_document_hash, replace.
    """
    label = 'Floating linear-response load'

    def __init__(self, result):
        self.result = dict(result)

    def do(self, project):
        from anyloads.floating_history import FloatingCoupledLoadHistory
        from ..model import WetPressure
        from ..model.records import AnalysisDefinition
        r = self.result
        if canonical_hash(project_to_dict(project)) != r['input_document_hash']:
            raise ValueError('project changed while preparing the floating response; nothing applied')
        payload = Path(r['record_path']).read_bytes()
        with np.load(io.BytesIO(payload), allow_pickle=False) as data:
            _require_gate_marker(json.loads(str(data['meta'])))
        history = FloatingCoupledLoadHistory.from_payload(payload, times=r['selected_times'], selected_window=r['window'])
        if history.array_sha256 != r['array_sha256']:
            raise ValueError('floating record changed after preparation; nothing applied')
        name = r['name']
        existing = project.load_cases.get(name)
        owned = (existing is not None and existing.times is not None
                 and any(isinstance(load.history, FloatingCoupledLoadHistory) for load in existing.wet_pressures))
        if existing is not None and not (owned and r.get('replace')):
            raise ValueError('a load series with this name already exists; choose a new name or replace the floating series')
        if owned and len(existing.wet_pressures) != 1 or (owned and (existing.point_loads or existing.pressures
                or existing.line_loads or existing.surface_tractions or existing.connection_loads or existing.parameters
                or existing.gravity is not None or existing.follower_pressure)):
            raise ValueError('floating replacement would discard additional loads or parameters; choose a new series name')
        removed = set()
        if owned:
            removed.update(c.name for c in project.series_children(name)); removed.add(name)
        children = set(project.series_step_names(name, len(history.times)))
        if children & (set(project.load_cases) - removed):
            raise ValueError('floating series step names collide with existing load cases')
        self.before_cases = dict(project.load_cases); self.before_analyses = dict(project.analyses)
        structural_hash = DocumentSession(project).revision.model_hash
        try:
            for key in removed:
                del project.load_cases[key]
            if owned:
                for aid, old in tuple(project.analyses.items()):
                    if old.target_id == name and old.name == name + ' — Floating batch' and old.type == 'batch_linear_static':
                        del project.analyses[aid]
            parent = project.add_load_series(name, history.times)
            parent.wet_pressures.append(WetPressure(history=history, mapping_key='element',
                                                    name='Floating linear response (pressure + weight + rigid inertia)'))
            project.add_analysis(AnalysisDefinition(name + ' — Floating batch', type='batch_linear_static',
                                                    target_kind='load_cases', target_id=name,
                                                    settings={'load_cases': [c.name for c in project.series_children(name)],
                                                              'inertia_relief': True, 'constraint_mode': 'nullspace',
                                                              'allow_unbalanced_free_free': False}))
            if DocumentSession(project).revision.model_hash != structural_hash:
                raise ValueError('floating load unexpectedly changed structural mesh inputs')
            self.after_cases = dict(project.load_cases); self.after_analyses = dict(project.analyses)
            return parent
        except BaseException:
            self.undo(project)
            raise

    def undo(self, project):
        project.load_cases = dict(self.before_cases); project.analyses = dict(self.before_analyses)

    def redo(self, project):
        project.load_cases = dict(self.after_cases); project.analyses = dict(self.after_analyses)
        return project.load_cases[self.result['name']]


_MASS_RECORDS: dict = {}


def _payload_mass_record(history) -> dict:
    """The dry mass/CG/inertia the saved compound was built with (read once per embedded payload)."""
    key = history.content_sha256
    if key not in _MASS_RECORDS:
        with np.load(io.BytesIO(history.payload), allow_pickle=False) as d:
            meta = json.loads(str(d['meta']))
        _MASS_RECORDS[key] = dict(meta['stage_keys']['mass_numeric'])
        if len(_MASS_RECORDS) > 16:
            _MASS_RECORDS.pop(next(iter(_MASS_RECORDS)))
    return _MASS_RECORDS[key]


def _require_gate_marker(meta) -> None:
    from .floating_prepare import RESTORING_GATE_MARKER
    keys = meta.get('stage_keys') if isinstance(meta, dict) else None
    if not isinstance(keys, dict) or keys.get('restoring_gate') != RESTORING_GATE_MARKER:
        raise FloatingStale('the floating load predates the current restoring/response gates; reopen and revalidate '
                            'the floating record before Apply or Solve (no BEM is needed)', 'gates')


def require_current_binding(history, fe_model, project=None) -> None:
    """Scalar AND batch pre-solve guard: current dry mass/CG/inertia plus the physical hull binding (wet faces vs closing faces,
    fluid side, interior point, exact vertex positions) must equal what the compound was prepared with."""
    with np.load(io.BytesIO(history.payload), allow_pickle=False) as data:
        meta = json.loads(str(data['meta']))
    _require_gate_marker(meta)
    require_current_mass(history, fe_model)
    if project is not None:
        from .floating_source import hull_geometry_key
        saved = meta['stage_keys'].get('geometry')
        extra = tuple(int(i) for i in meta.get('settings', {}).get('envelope_extra_face_ids', ()))
        if saved != hull_geometry_key(project, extra):
            raise FloatingStale('the hull geometry / wet-surface binding (faces, closing faces, fluid side, interior point) differs '
                                'from the one the floating load was prepared with; prepare the floating load again', 'geometry')


def _current_dry_properties(fe_model):
    """Dry properties of the CURRENT model, cached per snapshot but keyed by the public revision signature."""
    try:
        signature = repr(fe_model.revision_signature())
    except Exception:                                  # no revision contract: never cache
        signature = None
    cached = getattr(fe_model, '_floating_dry_properties', None)
    if signature is not None and cached is not None and cached[0] == signature:
        props = cached[1]
    else:
        props = dry_mass_properties(fe_model)
        if signature is not None:
            try:
                fe_model._floating_dry_properties = (signature, props)
            except AttributeError:
                pass
    return props


def require_current_mass(history, fe_model) -> None:
    """Refuse a floating compound whose primary inertia/weight no longer match the CURRENT assembled dry mass.

    The compound contains -M_old a and M_old g; inertia relief would silently hide a changed M (density, thickness, point mass,
    cargo) behind a larger correction."""
    props = _current_dry_properties(fe_model)
    saved = _payload_mass_record(history)
    ok = (np.isclose(props.mass, saved['mass'], rtol=1e-9, atol=0.0) and np.allclose(props.cg, saved['cg'], rtol=1e-9, atol=1e-9)
          and np.allclose(props.inertia_cg, saved['inertia'], rtol=1e-9, atol=1e-9 * max(1.0, float(np.abs(props.inertia_cg).max()))))
    if not ok:
        raise FloatingStale('the current dry mass/CG/inertia differs from the mass the floating load was prepared with '
                            '(material, thickness, point mass or cargo changed); prepare the floating load again', 'mass')


_RESPONSES: dict = {}


def _payload_response(history):
    key = history.content_sha256
    if key not in _RESPONSES:
        with np.load(io.BytesIO(history.payload), allow_pickle=False) as d:
            meta = json.loads(str(d['meta']))
            _RESPONSES[key] = (np.array(d['Hq']), np.array(meta['cg_body'], dtype=float))
        if len(_RESPONSES) > 16:
            _RESPONSES.pop(next(iter(_RESPONSES)))
    return _RESPONSES[key]


def floating_relief_record(history, fe_model, nodal_load, time: float, relief: Optional[dict]) -> dict:
    """NUMERIC primary / correction / total acceleration and original / corrected wrench of one solved floating frame.

    Compact generalized form, all in the FE (mean body) axes about the mean centre of gravity: translation of the CG [m/s^2]
    and angular acceleration [rad/s^2].  The primary comes from the saved response (-w^2 Hq, phase/amplitude/absolute time),
    the correction is the owner's inertia-relief acceleration of the residual, total = primary + correction.  Wrenches: the
    ORIGINAL assembled load (pressure + weight + primary inertia) and the CORRECTED one (original - M delta_a, dry M only)."""
    Hq, cg = _payload_response(history)
    primary = np.zeros(6)
    for k in range(history.omega.size):
        z = history.amplitude[k] * np.exp(1j * history.phase[k]) * np.exp(-1j * history.omega[k] * float(time))
        primary += np.real(z * (-history.omega[k] ** 2) * Hq[k])
    pts = np.array([fe_model.mesh.nodes[i].coords() for i in history.node_ids])
    original = nodal_wrench(pts, np.asarray(nodal_load, dtype=float), cg)
    record = dict(kind='floating_relief_numeric_v1', time=float(time), axes='mean body (FE) axes',
                  reference_point=cg.tolist(), reference='mean centre of gravity',
                  primary_acceleration_cg=primary[:3].tolist(), primary_angular_acceleration=primary[3:].tolist(),
                  original_wrench=original.tolist(), units=dict(acceleration='m/s^2', angular='rad/s^2', force='N', moment='N m'),
                  wrench_note='force/moment about the mean CG in FE axes; corrected = original - M_dry delta_a (no gyroscopic term)')
    # The caller may pass the raw OWNER inertia-relief summary (acceleration_at_center_of_mass / angular_acceleration) or the
    # floating-prefixed form; both are read, a missing/None correction is reported (never silently invented).
    def pick(*keys):
        for key in keys:
            if relief is not None and relief.get(key) is not None:
                return relief[key]
        return None
    a_raw = pick('acceleration_at_center_of_mass', 'correction_acceleration_at_center_of_mass')
    al_raw = pick('angular_acceleration', 'correction_angular_acceleration')
    record['relief_available'] = a_raw is not None and al_raw is not None
    if a_raw is not None and al_raw is not None:
        a = np.asarray(a_raw, dtype=float)
        al = np.asarray(al_raw, dtype=float)
        props = _current_dry_properties(fe_model)
        corr_wrench = np.concatenate([-props.mass * a, -props.inertia_cg @ al])
        record.update(correction_acceleration_cg=a.tolist(), correction_angular_acceleration=al.tolist(),
                      total_acceleration_cg=(primary[:3] + a).tolist(), total_angular_acceleration=(primary[3:] + al).tolist(),
                      correction_wrench=corr_wrench.tolist(), corrected_wrench=(original + corr_wrench).tolist(),
                      correction_center_of_mass=list(relief.get('center_of_mass') or cg))
    return record


def restore_dialog_settings(history) -> dict:
    """The original dialog inputs saved with the record: ``{'wave': <wave dialog settings>, 'floating': <floating options>}``.

    Strict: a record without its original inputs refuses (the dialog never guesses a realization)."""
    prepared = recover_prepared(history.payload)
    st = prepared.settings or {}
    wave = st.get('wave_input')
    if not isinstance(wave, dict) or not wave:
        raise ValueError('the saved floating record has no original wave inputs')
    floating = {k: st[k] for k in ('equilibrium_python', 'owner_wheel', 'stability_source', 'envelope_extra_face_ids',
                                   'panel_size', 'allow_development') if k in st}
    return {'wave': dict(wave), 'floating': floating}


# ---------------------------------------------------------------- load builder hooks
def floating_history_of(case):
    """The exact-class floating coupled history of a load case (None when the case is not a floating load)."""
    from anyloads.floating_history import FloatingCoupledLoadHistory
    found = [load.history for load in getattr(case, 'wet_pressures', ()) if type(load.history) is FloatingCoupledLoadHistory]
    if len(found) > 1:
        raise ValueError('a load case may carry only one floating coupled load')
    return found[0] if found else None


def floating_nodal_load(history, time: float, contributions=None) -> np.ndarray:
    """Real nodal (n,6) load (pressure + weight + primary inertia + static) at an ABSOLUTE time; time must be selected."""
    if history.times and not any(abs(float(time) - t) <= 1e-12 * max(1.0, abs(t)) for t in history.times):
        raise ValueError('floating load requested at a time outside the selected absolute samples')
    return history.evaluate(time) if contributions is None else history.evaluate(time, contributions)


def floating_load_case(source, fe_model, time: float, name: Optional[str] = None):
    """Explicit nodal LoadCase for the total load at ``time``; gravity=None (weight is already a nodal load)."""
    from ..solve.floating_batch import build_residual_load_case
    history = getattr(source, 'history', source)
    return build_residual_load_case(fe_model, history.node_ids, floating_nodal_load(history, time),
                                    name or f'floating@{float(time):.9g}')


def primary_acceleration_at(prepared_or_payload, fe_model, time: float) -> np.ndarray:
    """(n,6) real PRIMARY nodal acceleration field at an absolute time (separate from any IR correction)."""
    from .floating_loads import primary_acceleration_nodal
    prepared = prepared_or_payload if isinstance(prepared_or_payload, FloatingPrepared) else recover_prepared(prepared_or_payload)
    h = prepared.history
    pts = np.array([fe_model.mesh.nodes[i].coords() for i in h.node_ids])
    cg = np.array(prepared.cg_body)
    acc = np.zeros((len(h.node_ids), 6))
    for k in range(h.omega.size):
        if h.amplitude[k] == 0.0:
            continue
        z = h.amplitude[k] * np.exp(1j * h.phase[k]) * np.exp(-1j * h.omega[k] * float(time))
        acc += np.real(z * primary_acceleration_nodal(pts, cg, np.eye(3), prepared.Hq[k], h.omega[k]))
    return acc


def run_floating_relief(prepared: FloatingPrepared, fe_model, times: Iterable[float], *, batch_size: int = 8,
                        output_dofs=None, session=None, cancellation_token=None, progress_callback=None):
    """Stream residual-IR results for the requested actual times (primary acceleration kept separate from the correction)."""
    from ..solve.floating_batch import FloatingFrameInput, iter_residual_relief
    h = prepared.history

    def frames():
        for t in times:
            yield FloatingFrameInput(f'floating@{float(t):.9g}', float(t), h.evaluate(t),
                                     primary_acceleration_at(prepared, fe_model, t))

    yield from iter_residual_relief(fe_model, h.node_ids, frames(), batch_size=batch_size, output_dofs=output_dofs,
                                    session=session, cancellation_token=cancellation_token,
                                    progress_callback=progress_callback)
