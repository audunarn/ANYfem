"""Pressure / weight / acceleration / residual-correction inspection of a floating coupled load on the STATIONARY model.

Pure data API (no Qt): per-node vectors for an ABSOLUTE selected time plus resultant graph series with TRUTHFUL labels - every
generalized force is the resultant about the MEAN CENTRE OF GRAVITY (reported in the load's own FE/body axes), never about the
world origin.  The primary acceleration is the rigid field -w^2 R(r) Hq; the correction is the owner's inertia-relief
acceleration of the residual (kept separate); nothing here simulates or displays body motion.
"""
from __future__ import annotations

import numpy as np

from .floating_apply import floating_nodal_load, primary_acceleration_at, recover_prepared
from .floating_loads import nodal_wrench

COMPONENTS = ('Fx', 'Fy', 'Fz', 'Mx', 'My', 'Mz')


def _origin_label(cg):
    return (f'moments about the mean centre of gravity ({cg[0]:.6g}, {cg[1]:.6g}, {cg[2]:.6g}) m, '
            'in the mean body (project) axes; forces in the same axes')


def inspect_floating_load(history, fe_model, time: float, *, relief_result=None) -> dict:
    """Per-node pressure/weight/inertia loads, primary acceleration and (optionally) the IR correction at ``time``.

    ``relief_result``: the ``floating_relief`` metadata of a solved case (or an IR info dict); its correction acceleration
    is expanded to a rigid nodal field about the reported centre of mass."""
    prepared = recover_prepared(history.payload)
    pts = np.array([fe_model.mesh.nodes[i].coords() for i in history.node_ids])
    cg = np.array(prepared.cg_body)
    out = {'time': float(time), 'node_ids': tuple(history.node_ids), 'component_labels': COMPONENTS,
           'origin_label': _origin_label(cg), 'origin': cg.tolist(),
           'pressure': history.evaluate_component(time, 'pressure'), 'weight': history.evaluate_component(time, 'weight'),
           'inertia': history.evaluate_component(time, 'inertia'), 'static': np.array(history.static_load),
           'total': floating_nodal_load(history, time),
           'primary_acceleration': primary_acceleration_at(prepared, fe_model, time)}
    for name in ('pressure', 'weight', 'inertia', 'total'):
        out[f'{name}_resultant'] = nodal_wrench(pts, out[name], cg)
    out['static_resultant'] = nodal_wrench(pts, out['static'], cg)
    if relief_result is not None:
        a = np.asarray(relief_result['correction_acceleration_at_center_of_mass'], dtype=float)
        al = np.asarray(relief_result['correction_angular_acceleration'], dtype=float)
        com = np.asarray(relief_result['correction_center_of_mass'], dtype=float)
        lin = a[None, :] + np.cross(al[None, :], pts - com[None, :])
        out['correction_acceleration'] = np.concatenate([lin, np.broadcast_to(al, pts.shape)], axis=1)
        out['total_acceleration'] = out['primary_acceleration'] + out['correction_acceleration']
    return out


def inspect_record(record_path, project, mesh, time: float, *, relief_result=None) -> dict:
    """Caller used by the dialog: load the saved record, assemble the current model once, inspect one absolute time."""
    from pathlib import Path
    from anyloads.floating_history import FloatingCoupledLoadHistory
    from ..solve.build import build_fe_model
    history = FloatingCoupledLoadHistory.from_payload(Path(record_path).read_bytes())
    built = build_fe_model(project, mesh, require_loads=False, require_supports=False)
    return inspect_floating_load(history, built.fe_model, time, relief_result=relief_result)


def format_inspection(view: dict) -> str:
    """Honest text report: resultants (about the CG, body axes), peak nodal pressure-load and acceleration magnitudes."""
    lines = [f't = {view["time"]:.9g} s   ·   {view["origin_label"]}']
    for name in ('pressure', 'weight', 'inertia', 'static', 'total'):
        w = view[f'{name}_resultant']
        lines.append(f'{name:>9}: ' + '  '.join(f'{c}={v:.4g}' for c, v in zip(COMPONENTS, w)))
    pa = np.linalg.norm(view['primary_acceleration'][:, :3], axis=1)
    lines.append(f'primary acceleration: max |a| = {pa.max():.4g} m/s^2 at node {view["node_ids"][int(pa.argmax())]}; '
                 f'max |alpha| = {np.linalg.norm(view["primary_acceleration"][:, 3:], axis=1).max():.4g} rad/s^2')
    pn = np.linalg.norm(view['pressure'][:, :3], axis=1)
    lines.append(f'pressure nodal load: max |F| = {pn.max():.4g} N at node {view["node_ids"][int(pn.argmax())]}')
    if 'correction_acceleration' not in view:
        lines.append('residual correction (owner inertia relief): not available - no relief solve has run for this record yet '
                     '(solve the applied series with inertia relief to obtain it)')
    if 'correction_acceleration' in view:
        ca = np.linalg.norm(view['correction_acceleration'][:, :3], axis=1)
        lines.append(f'residual correction (owner inertia relief): max |a| = {ca.max():.4g} m/s^2 (kept separate from the primary)')
    return '\n'.join(lines)


def resultant_series(history, fe_model, times=None) -> dict:
    """Graph data: resultant wrench (6 components) of each contribution versus the selected ABSOLUTE times."""
    prepared = recover_prepared(history.payload)
    pts = np.array([fe_model.mesh.nodes[i].coords() for i in history.node_ids])
    cg = np.array(prepared.cg_body)
    ts = tuple(history.times if times is None else times)
    series = {name: np.array([nodal_wrench(pts, history.evaluate_component(t, name), cg) for t in ts])
              for name in ('pressure', 'weight', 'inertia')}
    series['total'] = np.array([nodal_wrench(pts, floating_nodal_load(history, t), cg) for t in ts])
    # The contributions above are DYNAMIC; the saved baseline (hydrostatic pressure p0, dry weight) completes the applied ones so
    # that pressure_full + weight_full + inertia == total (exact sum retained).
    base = recover_prepared(history.payload).diagnostics.get('baseline')
    if base is not None:
        series['pressure_dynamic'] = series['pressure']
        series['pressure'] = series['pressure_dynamic'] + np.array(base['pressure'])[None, :]
        series['weight_dynamic'] = series['weight']
        series['weight'] = series['weight_dynamic'] + np.array(base['weight'])[None, :]
    return {'times': ts, 'series': series, 'component_labels': COMPONENTS, 'origin_label': _origin_label(cg),
            'units': {'force': 'N', 'moment': 'N m', 'time': 's'}}
