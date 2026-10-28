"""PUBLIC headless floating-body Prepare pipeline (stationary mesh, no motion simulation).

``prepare_floating(project, mesh, settings, cache_dir, ...)`` runs, in order:
  1 dry FE mass/CG/inertia (build_fe_model -> assembled M, explicit point masses included)
  2 coupled mean equilibrium through the PUBLIC ANYstability workflow in an isolated configured runtime (no fake acceptance)
  3 mean-wet BEM source (ANYmesh budgeted plate panelisation, geometry-owner z=0 clipping) + independent structural target
    cells + physical overlap REGIONS
  4 native 6-radiation + diffraction family per (omega, heading): recovered from the work cache when present, otherwise
    solved only with ``allow_native_solve``; every retained family is then EMBEDDED in the saved artifact
  5 6-DOF coupled response (mean body axes), source closure against native A/B/Fexc, restoring from the same source
    quadrature, rotation and waterline admissions
  6 pressure work over the overlap regions (solver-owned shape/native-S3 integration), direct mean-wet hydrostatics, weight and
    primary inertia -> ONE immutable ``FloatingCoupledLoadHistory``
``assemble_prepared`` is stages 3(target)+5+6 given a stored source/families/equilibrium: that is the offline remap (no cache, no BEM).
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
import hashlib
import json
import math
from typing import Any, Optional

import numpy as np

from .floating_loads import SOURCE_CLOSURE_REL, nodal_wrench, static_weight_nodal
from .floating_mass import dry_mass_properties
from . import floating_source as fsrc
from .floating_transfer import compose_floating_components, source_dynamic_closure


class FloatingPrepareError(ValueError):
    """A Prepare stage refused; ``stage`` names the stage (mass/equilibrium/source/family/response/closure/...)."""

    def __init__(self, message, stage='prepare'):
        super().__init__(message)
        self.stage = stage


@dataclass(frozen=True)
class FloatingComponent:
    omega: float
    amplitude: float
    phase: float          # rad, fixed WORLD phase origin, exp(-i w t)
    heading: float        # rad


@dataclass(frozen=True)
class FloatingSettings:
    components: tuple
    times: tuple
    water_depth: float
    rho: float = 1025.0
    gravity: float = 9.81                 # same convention as the existing wave/application inputs
    owner_wheel: str = ''
    allow_development: bool = False
    equilibrium_python: str = ''          # explicit isolated runtime holding the pinned ANYgeometry
    stability_source: str = ''            # optional ANYstability source path injected into that runtime
    equilibrium_timeout_s: float = 600.0
    datum_xy: tuple = (0.0, 0.0)
    envelope_extra_face_ids: tuple = ()
    equilibrium_controls: Optional[dict] = None
    panel_size: float = 0.0               # 0: deterministic automatic size within max_panels (recorded in provenance)
    max_panels: int = 600
    asymmetry_tolerance: float = 0.05
    source_closure_tolerance: float = SOURCE_CLOSURE_REL
    coverage_tolerance: float = 1e-6
    mapping_tolerance: float = 0.01
    static_force_N: float = 1.0           # owner workflow residual criteria (force_N / heel_Nm / trim_Nm defaults)
    static_moment_Nm: float = 1.0
    allow_native_solve: bool = False
    wave_input: Optional[dict] = None     # the ORIGINAL dialog inputs (type, seed, spectrum, heading ...) saved with the record

    def digest(self):
        return hashlib.sha256(json.dumps(asdict(self), sort_keys=True, default=str).encode()).hexdigest()

    def realization_digest(self):
        return hashlib.sha256(json.dumps([[asdict(c) for c in self.components], list(self.times), self.water_depth,
                                          self.rho, self.gravity], sort_keys=True).encode()).hexdigest()


@dataclass(frozen=True)
class FloatingPrepared:
    history: Any
    Hq: np.ndarray
    pose: dict
    stage_keys: dict
    diagnostics: dict
    mass: float
    cg_body: tuple
    source: Any = None                    # SourceManifest (embedded, mesh independent)
    snapshots: tuple = ()                 # retained FloatingFamilySnapshot per NONZERO-amplitude component (embedded)
    settings: Any = None                  # FloatingSettings as a dict (embedded, validated on recover)
    active: tuple = ()                    # indices of the components that carry a hydrodynamic solution


def _hash(*parts):
    h = hashlib.sha256()
    for p in parts:
        h.update(p if isinstance(p, bytes) else json.dumps(p, sort_keys=True, default=str).encode())
    return h.hexdigest()


def _mesh_hash(mesh):
    from anymesher.serialize import mesh_to_dict
    from ..document import canonical_hash
    return canonical_hash(mesh_to_dict(mesh))


NATIVE_PANEL_LIMIT = 600      # the native body preparation limit: a larger public max_panels could only fail late


def _check_components(settings):
    if not 4 <= int(settings.max_panels) <= NATIVE_PANEL_LIMIT:
        raise FloatingPrepareError(f'max_panels={settings.max_panels} is outside 4..{NATIVE_PANEL_LIMIT} (the native BEM body limit); '
                                   'the source budget is never raised or silently coarsened', 'settings')
    if not settings.components or not settings.times:
        raise FloatingPrepareError('floating realization needs components and times', 'realization')
    for c in settings.components:
        if not (math.isfinite(c.omega) and c.omega > 0 and math.isfinite(c.amplitude) and c.amplitude >= 0):
            raise FloatingPrepareError('components need positive finite frequency and non-negative amplitude', 'realization')


def solve_equilibrium(project, settings, props, *, cancel=None):
    from .floating_equilibrium_process import solve_equilibrium_isolated, FloatingEquilibriumError
    try:
        return solve_equilibrium_isolated(project, settings, props, cancel=cancel)
    except FloatingEquilibriumError as error:
        raise FloatingPrepareError(str(error), 'equilibrium') from error


def _pose(record):
    return fsrc.MeanPose(np.array(record['rotation_body_to_world'], dtype=float),
                         np.array([record['datum_xy_m'][0], record['datum_xy_m'][1], record['origin_z_m']], dtype=float))


# ---------------------------------------------------------------- stage 4: families
def _active(settings):
    """Components that need a hydrodynamic solution: zero amplitude carries no wave load (no BEM, zero primary acceleration)."""
    return [k for k, c in enumerate(settings.components) if c.amplitude > 0.0]


def family_requests(settings, source, pose):
    from anyhydro import floating_radiation_fields as ff
    Q0 = np.array(pose['rotation_body_to_world']); cg = pose['cg_world_m']
    return [ff.family_request(source.vertices, source.faces, source.surface_ids, heading=settings.components[k].heading,
                              water_depth=settings.water_depth, rho=settings.rho, gravity=settings.gravity, Q0=Q0,
                              cg_world=cg) for k in _active(settings)]


def missing_solve_count(cache_dir, settings, source, pose):
    """Native solve families Prepare would need (shown before running; recovery never solves; zero-amplitude needs none)."""
    from anyhydro import floating_radiation_fields as ff
    return sum(ff.missing_radiation_rhs(cache_dir, r, settings.components[k].omega)
               for r, k in zip(family_requests(settings, source, pose), _active(settings)))


def collect_snapshots(cache_dir, settings, source, pose, *, cancel=None, progress=None):
    from anyhydro import floating_radiation_fields as ff
    Q0 = np.array(pose['rotation_body_to_world']); cg = pose['cg_world_m']
    snaps = []
    for req, c in zip(family_requests(settings, source, pose), (settings.components[k] for k in _active(settings))):
        if cancel is not None and cancel():
            raise FloatingPrepareError('cancelled', 'family')
        try:
            snaps.append(ff.recover_family_snapshot(cache_dir, req, c.omega))
        except ff.WaveFieldMissing:
            if not settings.allow_native_solve:
                raise FloatingPrepareError(f'{missing_solve_count(cache_dir, settings, source, pose)} native right-hand sides '
                                           'are missing; run explicit Prepare with native solves enabled', 'family') from None
            snap = ff.solve_family_snapshot(source.vertices, source.faces, source.surface_ids, c.omega, heading=c.heading,
                                            water_depth=settings.water_depth, rho=settings.rho, gravity=settings.gravity,
                                            Q0=Q0, cg_world=cg, donor=ff.recover_radiation_donor(cache_dir, req, c.omega))
            ff.save_family_snapshot(cache_dir, snap)
            snaps.append(snap)
        if progress is not None:
            progress(c.omega)
    return snaps


def _panel_wrench_body(snap, p, Q0, cg):
    """Force of the fluid on the body from native panel pressures, as a mean-body-axes generalized force (6x6 basis)."""
    from anysolver import floating_response as fr
    f = -(p[:, None] * snap.normals * snap.areas[:, None])
    w = np.concatenate([f.sum(axis=0), np.cross(snap.centers - np.asarray(cg), f).sum(axis=0)])
    return fr.world_to_body_generalized_force(w, Q0)


def prepare_from_pose(project, mesh, settings, pose_record, *, cache_dir, built=None, props=None, cancel=None, progress=None,
                      evaluator=None):
    """Stages 3-6 for a given equilibrium record; the source is generated (budgeted) and families come from the work cache."""
    from ..solve.build import build_fe_model
    _check_components(settings)
    if built is None:
        built = build_fe_model(project, mesh, require_loads=False, require_supports=False)
    props = props or dry_mass_properties(built.fe_model)
    pose = _pose(pose_record)
    target = fsrc.prepare_target_cells(project, mesh, pose)
    source = fsrc.prepare_source(project, mesh, pose, target, panel_size=settings.panel_size, max_panels=settings.max_panels)
    snaps = collect_snapshots(cache_dir, settings, source, pose_record, cancel=cancel, progress=progress)
    return assemble_prepared(project, mesh, settings, pose_record, source, snaps, built=built, props=props, target=target,
                             evaluator=evaluator)


def assemble_prepared(project, mesh, settings, pose_record, source, snaps, *, built, props, target=None, evaluator=None):
    """Response + pressure work + history from a stored source and families (no cache access, no BEM)."""
    from anysolver import floating_response as fr
    from anyloads.floating_history import FloatingCoupledLoadHistory
    _check_components(settings)
    fe_model = built.fe_model
    dofmap = props.dofmap
    pose = _pose(pose_record)
    Q0, cg_w = pose.Q0, np.array(pose_record['cg_world_m'], dtype=float)
    if not np.allclose(pose.world(props.cg[None, :])[0], cg_w, rtol=1e-9, atol=1e-6):
        raise FloatingPrepareError('equilibrium record CG disagrees with the assembled dry mass CG', 'mass')
    target = target or fsrc.prepare_target_cells(project, mesh, pose)
    omap = fsrc.build_overlap_map(source, target, pose, coverage_tolerance=settings.coverage_tolerance)

    hv, hf = fsrc.source_triangles_closed(source)
    rest = fr.hydrostatic_restoring_world(hv, hf, rho=settings.rho, gravity=settings.gravity, mass=props.mass, cg_world=cg_w)
    weight = props.mass * settings.gravity
    try:
        fr.require_static_balance(rest, force_tolerance=settings.static_force_N / weight,
                                  moment_arm_tolerance=settings.static_moment_Nm / max(rest['buoyancy_force'], 1.0))
    except fr.FloatingResponseError as error:
        raise FloatingPrepareError(f'source static equilibrium: {error}', 'source') from error
    # C from the SAME source-panel pressure/gravity quadrature the FE loads are derived from (mean body axes about the CG).  The closed-form
    # world waterplane operator (Newman) is kept as an audit: it is identical at an exact equilibrium and differs by theta x (small
    # tolerated residual force) otherwise.
    C_body = fr.restoring_body_from_source(hv, hf, Q0=Q0, cg_world=cg_w, rho=settings.rho, gravity=settings.gravity, mass=props.mass)
    C_closed_form = fr.world_to_body_operator(rest['C_world'], Q0)
    hull_length = physical_hull_length(source, pose)
    residual_op = fr.residual_restoring_operator(Q0, buoyancy=rest['buoyancy_force'], weight=weight,
                                                 buoyancy_centre_relative_cg=rest['buoyancy_centre_relative_cg'])
    try:
        audit = fr.restoring_audit(C_body, C_closed_form, residual_op, length=hull_length)
    except fr.FloatingResponseError as error:
        raise FloatingPrepareError(f'restoring audit: {error}', 'source') from error
    M_body = fr.dry_mass_matrix_body(props.mass, Q0 @ props.inertia_cg @ Q0.T, Q0)

    g0 = fr.gravity_body(Q0, settings.gravity)
    # The baseline is kept as TWO parts (hydrostatic pressure p0 on the mean wet region, dry weight); the history stores their exact sum.
    kernel_proof = {}          # job-owned: owner element-kernel equivalence proofs (identity = element object + node coordinates)
    static_pressure = fsrc.wet_region_nodal(fe_model, props.node_ids, target, pose,
                                            lambda xw: settings.rho * settings.gravity * (-xw[:, 2]), kernel_proof)
    static_weight = static_weight_nodal(props.apply, dofmap, settings.gravity, g0)
    static = static_pressure + static_weight
    baseline = dict(pressure=nodal_wrench(props.points, static_pressure, props.cg).tolist(),
                    weight=nodal_wrench(props.points, static_weight, props.cg).tolist())
    sw = nodal_wrench(props.points, static, props.cg)
    if np.linalg.norm(sw[:3]) > settings.static_force_N or np.linalg.norm(sw[3:]) > settings.static_moment_Nm:
        raise FloatingPrepareError(f'target (structural) static equilibrium residual force {np.linalg.norm(sw[:3]):.3g} N, '
                                   f'moment {np.linalg.norm(sw[3:]):.3g} N m exceeds the owner criteria '
                                   f'({settings.static_force_N} N, {settings.static_moment_Nm} N m)', 'target')
    static_report = [float(np.linalg.norm(sw[:3])), float(np.linalg.norm(sw[3:]))]
    static_parts = baseline
    act = _active(settings)
    nk = len(settings.components)
    if len(snaps) != len(act):
        raise FloatingPrepareError('retained families must correspond exactly to the nonzero-amplitude components', 'family')
    if not act:
        # Zero amplitude/Hs: direct hydrostatics + weight, zero primary acceleration, NO native BEM and no response solve.
        zeros = np.zeros((nk, len(props.node_ids), 6), dtype=np.complex128)
        diag0 = dict(source_closure=[], mapped_closure=[], mapping_error=[], overlap_coverage=omap.coverage,
                     static_balance=dict(force_rel=rest['force_residual_rel'], arm=rest['moment_arm_horizontal']),
                     target_static=static_report, active_components=[], source_provenance=source.provenance,
                     source_panels=len(source.triangles), target_cells=len(target.element_ids),
                     restoring_body=C_body.tolist(), rotation_envelope_rad=0.0, waterline_envelope_m=0.0,
                     clearance_m=_vertical_clearance(project, pose), restoring_null_dimension=0,
                     asymmetry={}, condition=[], baseline=baseline, restoring_audit=audit, response_sensitivity=None)
        return _finish_prepared(project, mesh, settings, pose_record, source, [], props, target, static,
                                np.zeros((nk, 6), dtype=np.complex128),
                                dict(pressure=zeros, weight=zeros, inertia=zeros), diag0, [])
    w = np.array([settings.components[k].omega for k in act]); amp = np.array([settings.components[k].amplitude for k in act])
    A = np.stack([s.added_mass for s in snaps]); B = np.stack([s.damping for s in snaps])
    Fx = np.stack([s.excitation for s in snaps])
    try:
        resp = fr.solve_floating_response(w, M_body, A, B, C_body, Fx, asymmetry_tolerance=settings.asymmetry_tolerance)
        env = fr.rotation_envelope(resp.Hq, amp)
    except fr.FloatingResponseError as error:
        raise FloatingPrepareError(f'response admission: {error}', 'response') from error
    Hq = np.asarray(resp.Hq)
    # Diagnostic response with the transformed geometric C and the SAME M/A/B/F; the actual response keeps the direct C.
    try:
        C_reference = C_closed_form
        ref = fr.solve_floating_response(w, M_body, A, B, C_reference, Fx, asymmetry_tolerance=settings.asymmetry_tolerance)
        sensitivity = fr.response_sensitivity(w, Hq, np.asarray(ref.Hq), length=hull_length)
    except fr.FloatingResponseError as error:
        raise FloatingPrepareError(f'response sensitivity to the admitted static residual: {error}', 'response') from error

    p_src, closure, src_wrench = [], [], []
    for k, s in enumerate(snaps):
        if s.centers is None or len(s.centers) != len(source.triangles):
            raise FloatingPrepareError('retained family lacks native panel pressures for this source', 'family')
        p = s.panel_pressure_incident_diffraction + np.tensordot(Hq[k], s.panel_pressure_radiation, axes=(0, 0))
        wb = _panel_wrench_body(s, p, Q0, cg_w)
        rep = source_dynamic_closure(w[k], wb, Hq[k], M_body, C_body, tolerance=settings.source_closure_tolerance)
        if not rep['passed']:
            raise FloatingPrepareError(f"source dynamic closure {rep['relative']:.3e} exceeds {rep['tolerance']:.1e} "
                                       f"at omega={w[k]:.6g}", 'closure')
        p_src.append(p); closure.append(rep); src_wrench.append(wb)

    clearance = _vertical_clearance(project, pose)
    eta = _waterline_eta(snaps, source, Hq, evaluator)
    try:
        wl = fr.relative_waterline_envelope(eta['eta_total'], eta['points'], cg_w, Q0, Hq, amp, clearance=clearance)
    except fr.FloatingResponseError as error:
        raise FloatingPrepareError(f'waterline excursion admission: {error}', 'response') from error

    length = hull_length
    errors = []

    def hydro(k, h):
        nodal = fsrc.map_pressure_nodal(fe_model, props.node_ids, target, omap, p_src[k], kernel_proof)
        # independent spatial mapping error: mapped nodal wrench (about the CG, body axes) vs the source panel wrench
        wt = nodal_wrench(props.points, nodal, props.cg)
        ws = src_wrench[k]
        # The SAME per-component rule as the dynamic closure: |r_i| <= tol * max(d_i, 0.1 S), moments / L, d_i the source component,
        # S = max d_i.  The summed LOCAL load magnitudes replace S only when every generalized component vanishes numerically.
        s_k = snaps[k]
        scale_vec = np.array([1.0, 1.0, 1.0, 1.0 / length, 1.0 / length, 1.0 / length])
        d = np.abs(np.asarray(ws)) * scale_vec
        S = float(d.max())
        local_f = np.abs(p_src[k]) * s_k.areas
        local = max(float(local_f.sum()), float((local_f * np.linalg.norm(s_k.centers - cg_w, axis=1)).sum()) / length)
        if S <= 1e-12 * local:                      # generalized loads vanish numerically: physical scale from local loads
            S = local
        resid = np.abs(np.asarray(wt) - np.asarray(ws)) * scale_vec
        denominator = np.maximum(d, 0.1 * S)
        err = float(np.max(resid / np.maximum(denominator, 1e-300)))
        errors.append(dict(omega=float(w[k]), relative=err, per_component=(resid / np.maximum(denominator, 1e-300)).tolist()))
        if err > settings.mapping_tolerance:
            raise FloatingPrepareError(f'mapped pressure wrench error {err:.3e} exceeds {settings.mapping_tolerance} '
                                       f'at omega={w[k]:.6g}', 'mapping')
        return nodal

    def hydrostatic(k, h):
        return fsrc.wet_region_nodal(fe_model, props.node_ids, target, pose,
                                     lambda xw: fr.hydrostatic_perturbation_pressure(xw, cg_w, Q0, h, rho=settings.rho,
                                                                                     gravity=settings.gravity), kernel_proof)

    comp = compose_floating_components(points=props.points, mass_apply=props.apply, dofmap=dofmap, cg_world=props.cg, Q0=Q0,
                                       gravity=settings.gravity, rho=settings.rho, omegas=w, Hq=Hq,
                                       source_hydrodynamic=hydro, direct_hydrostatic_nodal=hydrostatic, length=length,
                                       fe_frame='body')
    nk = len(settings.components)
    pad = lambda a: _expand(a, act, nk)
    diag = dict(source_closure=closure, mapped_closure=[{k: (v.tolist() if hasattr(v, 'tolist') else v) for k, v in r.items()}
                for r in comp['closure']], mapping_error=errors, overlap_coverage=omap.coverage,
                rotation_envelope_rad=env, waterline_envelope_m=wl, clearance_m=clearance, condition=resp.condition.tolist(),
                restoring_null_dimension=resp.restoring_null_dimension, asymmetry=resp.asymmetry,
                static_balance=dict(force_rel=rest['force_residual_rel'], arm=rest['moment_arm_horizontal']),
                target_static=static_report, active_components=act, baseline=static_parts,
                source_provenance=source.provenance, source_panels=len(source.triangles),
                target_cells=len(target.element_ids), restoring_body=C_body.tolist(),
                restoring_audit=audit, response_sensitivity=sensitivity,
                restoring_closed_form_max_abs_difference=float(np.abs(C_body - C_closed_form).max()))   # legacy mixed-unit diagnostic
    return _finish_prepared(project, mesh, settings, pose_record, source, snaps, props, target, static, pad(Hq),
                            dict(pressure=pad(comp['pressure']), weight=pad(comp['weight']), inertia=pad(comp['inertia'])),
                            diag, act)


def physical_hull_length(source, pose) -> float:
    """Largest extent of the PHYSICAL source hull expressed in body coordinates (attached cargo / outlier FE nodes excluded)."""
    v = np.asarray(pose.body(np.asarray(source.vertices, dtype=float)), dtype=float)
    length = float(np.ptp(v, axis=0).max()) if v.size else 0.0
    if not np.isfinite(length) or length <= 0.0:
        raise FloatingPrepareError('the physical source hull has no positive finite length', 'source')
    return length


RESTORING_GATE_MARKER ='restoring-audit-1e-8+response-sensitivity-1pct-v1'   # records without it are re-gated offline (no BEM)


def _expand(a, act, nk):
    a = np.asarray(a)
    out = np.zeros((nk,) + a.shape[1:], dtype=a.dtype)
    out[act] = a
    return out


def _finish_prepared(project, mesh, settings, pose_record, source, snaps, props, target, static, Hq_all, loads, diag, act):
    from anyloads.floating_history import FloatingCoupledLoadHistory
    cg_w = np.array(pose_record['cg_world_m'], dtype=float)
    w_all = np.array([c.omega for c in settings.components]); amp_all = np.array([c.amplitude for c in settings.components])
    ph_all = np.array([c.phase for c in settings.components])
    mesh_id = _mesh_hash(mesh)
    keys = dict(mass=_hash(props.mass, props.cg.tolist(), props.inertia_cg.tolist()),
                mass_numeric=dict(mass=float(props.mass), cg=props.cg.tolist(), inertia=props.inertia_cg.tolist()),
                geometry=fsrc.hull_geometry_key(project, settings.envelope_extra_face_ids),
                equilibrium=_hash(pose_record), source=source.digest(), target=target.digest(),
                families=[s.snapshot_id for s in snaps], realization=settings.realization_digest(), mesh=mesh_id,
                restoring_gate=RESTORING_GATE_MARKER,
                tolerances=dict(asymmetry=settings.asymmetry_tolerance, source_closure=settings.source_closure_tolerance,
                                coverage=settings.coverage_tolerance, mapping=settings.mapping_tolerance,
                                static_force_N=settings.static_force_N, static_moment_Nm=settings.static_moment_Nm))
    binding = dict(source=keys['source'], equilibrium=keys['equilibrium'], geometry=keys['geometry'], mass=keys['mass'],
                   backend=_hash(keys['families']) if snaps else 'no-hydrodynamic-solution-zero-amplitude',
                   dofs='mean_body_6dof_translation_then_rotation',
                   axes='mean body axes through mean CG; Q0 body->world', units='SI N, N m, m, rad, s',
                   reference=cg_w.tolist(), phase='fixed world origin; exp(-i w t); loads per unit amplitude',
                   realization=keys['realization'])
    history = FloatingCoupledLoadHistory(w_all, amp_all, ph_all, loads, static, props.node_ids, mesh_id, binding)
    # Resultants about the mean CG in the FE (mean body) axes over the whole preview record: the dynamic pressure and the
    # assembled compound (which balances to the closure level before any residual relief).
    ts = list(settings.times)
    dyn_p = np.array([nodal_wrench(props.points, history.evaluate_component(t, 'pressure'), props.cg) for t in ts])
    dyn_w = np.array([nodal_wrench(props.points, history.evaluate_component(t, 'weight'), props.cg) for t in ts])
    base = diag['baseline']
    diag['resultants'] = dict(
        times=ts, origin=props.cg.tolist(),
        pressure_dynamic=dyn_p.tolist(),                       # dynamic pressure only (pI + pD + radiation + dp_hs)
        pressure_baseline=base['pressure'], weight_baseline=base['weight'],
        pressure=(dyn_p + np.array(base['pressure'])).tolist(),    # FULL applied pressure resultant incl. hydrostatic baseline
        weight=(dyn_w + np.array(base['weight'])).tolist(),        # FULL weight incl. static dry weight
        total=[nodal_wrench(props.points, history.evaluate(t), props.cg).tolist() for t in ts])   # exact sum of every contribution
    return FloatingPrepared(history, np.asarray(Hq_all), pose_record, keys, diag, props.mass, tuple(props.cg), source,
                            tuple(snaps), asdict(settings), tuple(act))


def _vertical_clearance(project, pose):
    """CONSERVATIVE vertical clearance from the actual plate geometry (never from arbitrary mesh nodes).

    ADMITTED SCOPE: a flat-keel barge.  The keel faces are identified from the geometry-owner body positions: the wet faces whose plane
    is horizontal in the body (project) axes and which lie at the LOWEST body elevation.  Every posed corner of every keel face must be
    strictly below z = 0 (mean flat-keel emergence or zero draft at any keel point is REFUSED, never treated as upper plating); the keel
    bound is then the shallowest posed keel corner, min(-z) (under combined heel and trim the shallowest keel corner touches faces whose
    other endpoints are deeper, so per-face maxima would overstate it).  top: the LOWEST above-water corner of every NON-keel wet plate
    that crosses the mean waterplane (low-side freeboard, not the global highest node).  The smaller of the two is returned; it can only
    understate the available clearance.  If no keel face or no crossing plate can be established the hull is unsupported and refused."""
    geom = project.geometry
    plates = []
    for surface in project.wet_surfaces:
        body = np.array([geom.vertex_position(v) for v in geom.face_corner_vertices(surface.ref.id)], dtype=float)
        normal = 0.5 * np.cross(body, np.roll(body, -1, axis=0)).sum(axis=0)
        length = float(np.linalg.norm(normal))
        horizontal = length > 0.0 and abs(normal[2]) >= (1.0 - 1e-9) * length
        plates.append((body, pose.world(body)[:, 2], horizontal))
    flat = [(body[:, 2].mean(), z) for body, z, horizontal in plates if horizontal]
    if not flat:
        raise FloatingPrepareError('unsupported hull: no horizontal flat-keel face among the wet surfaces, no keel bound can be '
                                   'established', 'response')
    lowest = min(level for level, _ in flat)
    scale = max(1.0, max(abs(level) for level, _ in flat))
    keel_faces = [z for level, z in flat if level <= lowest + 1e-9 * scale]
    keel_z = np.concatenate(keel_faces)
    if not np.all(keel_z < 0.0):
        raise FloatingPrepareError('the flat keel emerges (or touches z = 0) at the mean pose; zero or negative keel draft is outside '
                                   'the admitted barge contract', 'response')
    keel = -float(keel_z.max())
    keel_ids = {id(z) for z in keel_faces}
    top = np.inf
    for body, z, horizontal in plates:
        if any(id(z) == k for k in keel_ids):
            continue
        if z.min() < 0.0 < z.max():
            top = min(top, float(z[z > 0.0].min()))
    if not np.isfinite(top) or keel <= 0.0 or top <= 0.0:
        raise FloatingPrepareError('hull has no established positive freeboard bound (no non-keel wet plate crosses the waterplane)',
                                   'response')
    return float(min(keel, top))


def _waterline_eta(snaps, source, Hq, evaluator=None):
    """eta_total (incident+diffraction+radiation) at waterline vertices from the retained fields (native recovery, no solve)."""
    from anyhydro import floating_radiation_fields as ff
    on = np.nonzero(np.abs(source.vertices[:, 2]) < 1e-12)[0]
    if len(on) == 0:
        raise FloatingPrepareError('source has no waterline vertices', 'source')
    normals = np.zeros((len(on), 3)); pos = {int(v): i for i, v in enumerate(on)}
    for f, n in zip(source.faces, source.normals):
        for v in set(int(x) for x in f):
            if v in pos:
                normals[pos[v]] += n
    normals[:, 2] = 0.0
    ok = np.linalg.norm(normals, axis=1) > 1e-12
    pts, nrm = source.vertices[on][ok], normals[ok]
    eta = []
    for k, s in enumerate(snaps):
        if evaluator is None:
            # builtin native path: elevation-only recovery (no unused hull-pressure potentials), bit-identical eta by construction
            f = ff.evaluate_floating_elevations(s, pts, nrm)
        else:
            f = ff.evaluate_floating_fields(s, pts, nrm, evaluator=evaluator)       # arbitrary callbacks keep their exact call semantics
        eta.append(f.eta_incident_diffraction + np.tensordot(Hq[k], f.eta_radiation, axes=(0, 0)))
    return dict(eta_total=np.array(eta), points=pts)


def prepare_floating(project, mesh, settings, cache_dir, *, cancel=None, progress=None, evaluator=None):
    """Production entry: dry mass -> isolated coupled equilibrium -> source/target/regions -> family -> response -> history."""
    from ..solve.build import build_fe_model
    _check_components(settings)
    built = build_fe_model(project, mesh, require_loads=False, require_supports=False)
    props = dry_mass_properties(built.fe_model)
    pose = solve_equilibrium(project, settings, props, cancel=cancel)
    return prepare_from_pose(project, mesh, settings, pose, cache_dir=cache_dir, built=built, props=props, cancel=cancel,
                             progress=progress, evaluator=evaluator)
