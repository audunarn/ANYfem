"""Pressure (Pa) inspection of an applied floating compound on the STATIONARY mean wet mesh.

Recovers the retained SOURCE pressures (pI + pD + sum Hq_j pR from the saved native panel pressures), synthesises them in time with the
saved amplitudes/phases, averages them over the SAME physical overlap regions that the assembled load integrates, and adds the
directly evaluated hydrostatic baseline p0 and its perturbation on each mean wet element.  Values are SIGNED pressure magnitudes pressing
into the plate [Pa]; wet-area averages and the mean wet fraction are returned separately (no time-varying wet/dry or stretching).
Nodal force arrays (N, N m) are NOT pressures and are never reported here.
"""
from __future__ import annotations

import numpy as np

from . import floating_source as fsrc
from .floating_apply import FloatingStale, recover_prepared, require_current_binding


def _validate_current_inputs(history, project, mesh) -> None:
    """Refuse (never remap, never BEM) unless the CURRENT mesh content, physical hull/wet-side/closing/interior binding and dry
    mass/CG/inertia are exactly those the applied history was built with.  Evaluated on every call: no cache can serve stale colours."""
    from anymesher.serialize import mesh_to_dict
    from ..document import canonical_hash
    from ..solve.build import build_fe_model
    current = canonical_hash(mesh_to_dict(mesh)).removeprefix('sha256:')
    if current != history.mesh_content_sha256.removeprefix('sha256:'):
        raise FloatingStale('the current mesh content differs from the mesh the applied floating history was built on; the field is not '
                            'remapped here - reopen/remap the floating load first', 'mesh')
    built = build_fe_model(project, mesh, require_loads=False, require_supports=False)
    require_current_binding(history, built.fe_model, project)          # dry mass/CG/inertia + wet/closing faces, side, interior point


def _context(history, project, mesh):
    """Uncached inspection context (defensive, small scope): validated inputs, recovered record, mean wet regions."""
    _validate_current_inputs(history, project, mesh)
    from anysolver import floating_response as fr
    prepared = recover_prepared(history.payload)
    st = prepared.settings
    rec = prepared.pose
    pose = fsrc.MeanPose(np.array(rec['rotation_body_to_world'], dtype=float),
                         np.array([rec['datum_xy_m'][0], rec['datum_xy_m'][1], rec['origin_z_m']], dtype=float))
    target = fsrc.prepare_target_cells(project, mesh, pose)
    omap = fsrc.build_overlap_map(prepared.source, target, pose, coverage_tolerance=float(st['coverage_tolerance']))
    # wet-area average of each active component's source pressure on every wet element (same regions as the assembly)
    areas = {j: 0.5 * np.linalg.norm(np.cross(t[:, 1] - t[:, 0], t[:, 2] - t[:, 0]), axis=1) for j, (t, _) in omap.regions.items()}
    dynamic = []
    for s, k in zip(prepared.snapshots, prepared.active):
        p = s.panel_pressure_incident_diffraction + np.tensordot(prepared.Hq[k], s.panel_pressure_radiation, axes=(0, 0))
        avg = np.zeros(len(target.element_ids), dtype=np.complex128)
        for j, (tris, src) in omap.regions.items():
            avg[j] = np.sum(areas[j] * p[src]) / np.sum(areas[j])
        dynamic.append(avg)
    cg_w = np.array(rec['cg_world_m'], dtype=float)
    perturb = [fr.hydrostatic_perturbation_pressure(target.centroid_wet, cg_w, pose.Q0, prepared.Hq[k], rho=float(st['rho']),
                                                    gravity=float(st['gravity'])) for k in range(prepared.Hq.shape[0])]
    return dict(prepared=prepared, target=target, dynamic=dynamic, perturb=perturb,      # job-local, never stored globally
                baseline=float(st['rho']) * float(st['gravity']) * (-target.centroid_wet[:, 2]))


def floating_element_pressures(history, project, mesh, time: float) -> dict:
    """Signed element pressures [Pa] of the compound at an ABSOLUTE time on the mean wet mesh (no BEM)."""
    ctx = _context(history, project, mesh)
    target, prepared = ctx['target'], ctx['prepared']
    z = history.amplitude * np.exp(1j * history.phase) * np.exp(-1j * history.omega * float(time))
    dyn = np.zeros(len(target.element_ids))
    for avg, k in zip(ctx['dynamic'], prepared.active):
        dyn += np.real(z[k] * avg)
    dp = np.zeros(len(target.element_ids))
    for k in range(len(z)):
        if history.amplitude[k] != 0.0:
            dp += np.real(z[k] * ctx['perturb'][k])
    wet_avg = ctx['baseline'] + dyn + dp
    fraction = target.area_wet / target.area_element
    return dict(kind='floating_element_pressure_v1', time=float(time), element_ids=tuple(target.element_ids), sign=target.sign.copy(),
                pressure_wet_average_pa=wet_avg, pressure_full_area_average_pa=wet_avg * fraction,
                dynamic_wet_average_pa=dyn, hydrostatic_baseline_pa=ctx['baseline'].copy(), hydrostatic_perturbation_pa=dp,
                wet_fraction=fraction, area_wet=target.area_wet.copy(), area_element=target.area_element.copy(),
                labels=dict(pressure='signed pressure pressing into the plate [Pa], mean over the MEAN wet area',
                            full_area='the same force spread over the whole element [Pa] (dry part exactly zero)',
                            note='mean waterplane regions only; no time-varying wetting, drying or stretching',
                            legend=('Floating load: signed FULL-AREA element-average pressure [Pa] (wet-area average x mean wet fraction); '
                                    'mean wet fraction shown per element; mean waterplane only, no time-varying clipping; '
                                    'nodal forces (N, N m) are not pressures'),
                            mean_wet_fraction_range=(float((target.area_wet / target.area_element).min()),
                                                     float((target.area_wet / target.area_element).max()))))
