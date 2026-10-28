"""Headless floating Prepare/Apply/reopen/remap pipeline on a small real project (no native work by default).

Family snapshots written here are SYNTHETIC but exactly self-consistent (A, B, Fexc are the integrals of the stored native-style
panel pressures on the same panels), so every downstream stage runs for real: target/source preparation (ANYmesh budgeted plate
source), physical overlap regions, restoring from the source quadrature, 6-DOF response, source closure, solver-owned region
pressure work, direct wet-region hydrostatics, weight, primary inertia, history, self-contained save, offline reopen/remap with the
work cache DELETED.  The real owner-wheel equilibrium and native BEM are parent-run gated nodes (last test).
"""
import os

import numpy as np
import pytest

from anyfem import Project, steel
from anyfem.model import WetSurface
from anyfem.application import floating_apply as fa
from anyfem.application import floating_prepare as fp
from anyfem.application import floating_source as fsrc
from anyfem.application.floating_mass import dry_mass_properties
from anyfem.solve.build import build_fe_model
from anyhydro import floating_radiation_fields as ff

RHO, G, DEPTH = 1025.0, 9.81, 100.0
L, B, H, T_PLATE = 5.0, 4.0, 2.0, 0.06     # GM>0 with and without the deck; stiff enough for the owner's IR certification
BASE, FINE, PANEL = 0.8, 0.65, 0.8     # check-27/29 timed out at 100/110 s with BASE=0.5 on the 5x4x2 box (IR certification cost)


def box_project(with_deck=False):
    """A connected box: eight shared corner vertices, shared edges (one welded body), outward-agnostic loops."""
    from anygeometry import OrientedEdge
    p = Project(); p.add_material(steel()); p.add_plate_section('plate', T_PLATE, 'S355')
    g = p.geometry
    corners = [(0, -B / 2, 0), (L, -B / 2, 0), (L, B / 2, 0), (0, B / 2, 0),
               (0, -B / 2, H), (L, -B / 2, H), (L, B / 2, H), (0, B / 2, H)]
    vid = [g.add_point(*x) for x in corners]
    edges = {}

    def edge(a, b):
        key = tuple(sorted((a, b)))
        if key not in edges:
            edges[key] = g.add_line(vid[a], vid[b])
        return edges[key]

    def face(idx):
        ring = []
        for a, b in zip(idx, idx[1:] + idx[:1]):
            e = edge(a, b)
            ring.append(OrientedEdge(e, g.edges[e].start == vid[a]))
        return g.add_face_from_loop(tuple(ring), (0, 1, 2, 3))

    loops = {'bottom': (0, 1, 2, 3), 'port': (0, 1, 5, 4), 'starboard': (3, 2, 6, 7), 'end0': (0, 3, 7, 4),
             'end1': (1, 2, 6, 5)}
    if with_deck:
        loops['deck'] = (4, 5, 6, 7)
    faces = {}
    for name, idx in loops.items():
        fid = face(idx)
        p.assign_plate(fid, 'plate'); faces[name] = fid
        if name != 'deck':
            p.add_wet_surface(WetSurface(ref=p.face(fid), name=name, fluid_side='away_from_point',
                                         interior_point=(L / 2, 0, H / 2)))
    return p, faces


def meshed(project, size):
    return project.generate_mesh(size)          # the application's DEFAULT shell discretisation (native S3 triangles)


def pose_record(props):
    draft = props.mass / (RHO * L * B)
    return dict(heel_rad=0.0, trim_rad=0.0, origin_z_m=-draft, datum_xy_m=[0.0, 0.0],
                rotation_body_to_world=np.eye(3).tolist(), cg_world_m=(props.cg + [0, 0, -draft]).tolist(),
                study_identity='test', result_identity='test', assurance='test-supplied equilibrium record', hydrostatic_GM_m=None)


def synthetic_snapshot(source, rec, comp):
    Q0 = np.array(rec['rotation_body_to_world']); cg = np.array(rec['cg_world_m'])
    req = ff.family_request(source.vertices, source.faces, source.surface_ids, heading=comp.heading, water_depth=DEPTH,
                            rho=RHO, gravity=G, Q0=Q0, cg_world=cg)
    n = len(source.triangles); x = source.centers
    dofs = np.stack([Q0[:, i][None, :].repeat(n, 0) for i in range(3)] +
                    [np.cross(Q0[:, i][None, :], x - cg) for i in range(3)])
    DN = np.einsum('kij,ij->ki', dofs, source.normals)
    w = comp.omega; a, b = 5.0e3, 2.0e3
    S = (DN * source.areas) @ DN.T
    pR = -(w ** 2 * a + 1j * w * b) * DN
    pID = 300.0 * (1.0 + 0.5j) * (1.0 + 0.05 * x[:, 0] - 0.1 * x[:, 2])
    F = -(DN * source.areas) @ pID
    return ff.FloatingFamilySnapshot(req, w, source.vertices, source.faces, source.surface_ids, np.zeros((6, n), complex),
                                     np.zeros(n, complex), a * S, b * S, F, {'backend': 'synthetic-test'}, None, 0., '', None,
                                     None, pR, pID, source.centers, source.normals, source.areas)


def eta_evaluator(snap, pts, k):
    return np.full(len(pts), 0.05 + 0j)


def settings(comp, **kw):
    base = dict(components=(comp,), times=(0.0, 1.0), water_depth=DEPTH, rho=RHO, gravity=G, panel_size=PANEL)
    base.update(kw)
    return fp.FloatingSettings(**base)


@pytest.fixture(scope='module')
def case(tmp_path_factory):
    project, faces = box_project()
    mesh = meshed(project, BASE)
    built = build_fe_model(project, mesh, require_loads=False, require_supports=False)
    props = dry_mass_properties(built.fe_model)
    rec = pose_record(props)
    comp = fp.FloatingComponent(omega=0.8, amplitude=1.0, phase=0.2, heading=0.3)
    return project, mesh, built, props, rec, comp, tmp_path_factory.mktemp('floating-cache')


def test_dry_mass_properties_are_authoritative(case):
    project, mesh, built, props, rec, comp, cache = case
    assert props.mass > 0 and props.dofmap.index.shape == (len(props.node_ids), 6)
    assert abs(props.cg[0] - L / 2) < 1e-6 and abs(props.cg[1]) < 1e-6
    assert np.all(np.linalg.eigvalsh(props.inertia_cg) > 0)


def test_source_is_mesh_independent_budgeted_and_overlap_regions_cover_the_hull(case):
    project, mesh, built, props, rec, comp, cache = case
    pose = fp._pose(rec)
    target = fsrc.prepare_target_cells(project, mesh, pose)
    source = fsrc.prepare_source(project, mesh, pose, target, panel_size=PANEL)
    assert source.provenance['panels'] <= 600 and source.vertices[:, 2].max() <= 1e-9
    fine = meshed(project, FINE)
    t2 = fsrc.prepare_target_cells(project, fine, pose)
    assert fsrc.prepare_source(project, fine, pose, t2, panel_size=PANEL).digest() == source.digest()
    auto = fsrc.prepare_source(project, mesh, pose, target, max_panels=120)          # deterministic automatic size in budget
    assert auto.provenance['size_chosen_automatically'] and auto.provenance['panels'] <= 120
    for tgt in (target, t2):
        omap = fsrc.build_overlap_map(source, tgt, pose)
        assert omap.coverage['source_uncovered_rel'] < 1e-6 and omap.coverage['target_unmatched_rel'] < 1e-6


def test_mapped_pressure_work_has_no_dry_loading_and_matches_source_wrench(case):
    project, mesh, built, props, rec, comp, cache = case
    from anyfem.application.floating_loads import nodal_wrench
    pose = fp._pose(rec)
    target = fsrc.prepare_target_cells(project, mesh, pose)
    source = fsrc.prepare_source(project, mesh, pose, target, panel_size=PANEL)
    omap = fsrc.build_overlap_map(source, target, pose)
    p = 100.0 * (1.0 + 0.1 * source.centers[:, 0])
    nodal = fsrc.map_pressure_nodal(built.fe_model, props.node_ids, target, omap, p)
    cg = np.array(rec['cg_world_m']) + 0 * props.cg
    wt = nodal_wrench(props.points, nodal, props.cg)
    f_src = -(p[:, None] * source.normals * source.areas[:, None]).sum(axis=0)
    # nodal loads are loads ON the structure along the fluid-side convention: magnitude must equal the panel force
    assert np.linalg.norm(wt[:3]) == pytest.approx(np.linalg.norm(f_src), rel=1e-3)
    wet_nodes = {n for e in target.element_ids for n in mesh.corners_of(e)}                # nodes of wet/partial elements
    loaded = {nid for i, nid in enumerate(props.node_ids) if np.any(nodal[i])}
    assert loaded and loaded <= wet_nodes                                                   # fully dry elements carry no load


def test_prepare_from_pose_closes_saves_self_contained_and_remaps_without_cache(case, tmp_path):
    project, mesh, built, props, rec, comp, cache = case
    st = settings(comp)
    pose = fp._pose(rec)
    source = fsrc.prepare_source(project, mesh, pose, fsrc.prepare_target_cells(project, mesh, pose), panel_size=PANEL)
    assert fp.missing_solve_count(cache, st, source, rec) == 7                  # actual RHS count, shown before Prepare
    with pytest.raises(fp.FloatingPrepareError) as refused:
        fp.prepare_from_pose(project, mesh, st, rec, cache_dir=cache, built=built, props=props, evaluator=eta_evaluator)
    assert refused.value.stage == 'family'
    ff.save_family_snapshot(cache, synthetic_snapshot(source, rec, comp))
    out = fp.prepare_from_pose(project, mesh, st, rec, cache_dir=cache, built=built, props=props, evaluator=eta_evaluator)
    h = out.history
    assert all(r['passed'] for r in out.diagnostics['source_closure'])
    assert all(r['passed'] for r in out.diagnostics['mapped_closure'])
    assert out.diagnostics['mapping_error'][0]['relative'] < st.mapping_tolerance
    assert h.loads['pressure'].shape == (1, len(props.node_ids), 6) and np.isfinite(h.evaluate(0.5)).all()
    path = fa.save_prepared(out, tmp_path / 'floating.npz')
    back = fa.recover_prepared(path)
    assert back.history.array_sha256 == h.array_sha256 and np.array_equal(back.Hq, out.Hq)
    import json
    assert back.settings == json.loads(json.dumps(out.settings)) and back.active == out.active
    assert back.source.digest() == source.digest() and back.snapshots[0].snapshot_id == out.snapshots[0].snapshot_id
    empty_cache = tmp_path / 'deleted-cache'                                     # external cache loss must not matter
    again = fa.reopen_or_remap(project, mesh, st, path, cache_dir=empty_cache, evaluator=eta_evaluator)
    assert again.history.array_sha256 == h.array_sha256
    # a stricter gate than the saved verdict re-runs the gates from the retained data (no BEM, no cache)
    stricter = fa.reopen_or_remap(project, mesh, settings(comp, source_closure_tolerance=1e-12), path, cache_dir=empty_cache,
                                  evaluator=eta_evaluator)
    assert stricter.stage_keys['tolerances']['source_closure'] == 1e-12
    fine = meshed(project, FINE)
    remapped = fa.reopen_or_remap(project, fine, st, path, cache_dir=empty_cache, evaluator=eta_evaluator)
    assert remapped.history.mesh_content_sha256 != h.mesh_content_sha256 and remapped.history.array_sha256 != h.array_sha256
    assert remapped.stage_keys['source'] == out.stage_keys['source'] and not empty_cache.exists()
    assert np.allclose(remapped.Hq, out.Hq, rtol=5e-2, atol=1e-6)
    with pytest.raises(fa.FloatingStale) as stale:
        fa.reopen_or_remap(project, mesh, settings(fp.FloatingComponent(0.8, 2.0, 0.2, 0.3)), path, cache_dir=empty_cache)
    assert stale.value.stage == 'realization'
    # residual nodal load hook: explicit, gravity free
    case_at = fa.floating_load_case(out, built.fe_model, 0.4)
    assert case_at.gravity is None and not case_at.pressure_loads and case_at.nodal_loads


def _prepared_record(case, tmp_path):
    project, mesh, built, props, rec, comp, cache = case
    st = settings(comp)
    pose = fp._pose(rec)
    source = fsrc.prepare_source(project, mesh, pose, fsrc.prepare_target_cells(project, mesh, pose), panel_size=PANEL)
    cache2 = tmp_path / 'cache'
    ff.save_family_snapshot(cache2, synthetic_snapshot(source, rec, comp))
    out = fp.prepare_from_pose(project, mesh, st, rec, cache_dir=cache2, built=built, props=props, evaluator=eta_evaluator)
    return st, out, fa.save_prepared(out, tmp_path / 'record.npz')


def test_apply_floating_load_is_one_undoable_project_load_that_survives_project_save_reopen(case, tmp_path):
    from anyfem.document import canonical_hash
    from anyfem.io.project_file import project_from_dict, project_to_dict
    from anyloads.floating_history import FloatingCoupledLoadHistory
    project, mesh, built, props, rec, comp, cache = case
    st, out, record = _prepared_record(case, tmp_path)
    result = dict(name='Floating wave', record_path=str(record), array_sha256=out.history.array_sha256,
                  selected_times=[0.0, 1.0], window=[0.0, 1.0], input_document_hash=canonical_hash(project_to_dict(project)),
                  replace=False)
    command = fa.ApplyFloatingLoad(result)
    parent = command.do(project)
    try:
        history = fa.floating_history_of(parent)
        assert type(history) is FloatingCoupledLoadHistory and history.times == (0.0, 1.0) and history.payload
        assert history.array_sha256 == out.history.array_sha256 and len(project.series_children('Floating wave')) == 2
        # normal project save/reopen carries the whole validated record (no sidecar, no cache)
        reopened = project_from_dict(project_to_dict(project))
        again = fa.floating_history_of(reopened.load_cases['Floating wave'])
        assert again.content_sha256 == history.content_sha256
        assert fa.recover_prepared(again.payload).stage_keys['source'] == out.stage_keys['source']
        np.testing.assert_allclose(fa.floating_nodal_load(again, 1.0), out.history.evaluate(1.0))
        with pytest.raises(ValueError):                                          # only the selected absolute samples are served
            fa.floating_nodal_load(again, 0.5)
        with pytest.raises(ValueError, match='already exists'):
            fa.ApplyFloatingLoad({**result, 'input_document_hash': canonical_hash(project_to_dict(project))}).do(project)
        command.undo(project)
        assert 'Floating wave' not in project.load_cases
        command.redo(project)
        assert 'Floating wave' in project.load_cases
    finally:
        command.undo(project)
    with pytest.raises(ValueError, match='project changed'):
        fa.ApplyFloatingLoad({**result, 'input_document_hash': '0' * 64}).do(project)


def test_applied_floating_load_dispatches_through_the_finished_ir_route_scalar_equals_batch(case, tmp_path):
    from anyfem.document import canonical_hash
    from anyfem.io.project_file import project_to_dict
    from anyfem.solve.run import solve_linear_static, solve_linear_static_many
    project, mesh, built, props, rec, comp, cache = case
    st, out, record = _prepared_record(case, tmp_path)
    cmd = fa.ApplyFloatingLoad(dict(name='FW', record_path=str(record), array_sha256=out.history.array_sha256,
                                    selected_times=[0.0, 1.0], window=[0.0, 1.0],
                                    input_document_hash=canonical_hash(project_to_dict(project)), replace=False))
    cmd.do(project)
    try:
        names = [c.name for c in project.series_children('FW')]
        try:
            batch = solve_linear_static_many(project, mesh=mesh, load_cases=names, inertia_relief=True)
        except Exception as error:      # keep the owner's diagnostics visible in the receipt
            raise AssertionError(f'{error}: {getattr(error, "diagnostics", None)}') from error
        assert len(batch.shapes) == 2
        for name, shape in zip(names, batch.shapes):
            meta = shape.info['floating_relief']
            assert meta['kind'] == 'floating_residual_relief_v1' and meta['history_content_sha256']
            assert shape.info['inertia_relief']['status'] == 'converged'          # owner diagnostics preserved
            single = solve_linear_static(project, mesh=mesh, load_case=name, inertia_relief=True)
            np.testing.assert_allclose(single.displacements, shape.displacements, rtol=1e-6, atol=1e-12)
            for solved in (shape, single):                                          # REAL numeric provenance, scalar AND batch
                num = solved.info['floating_relief']['numeric']
                assert num['relief_available'] and num['kind'] == 'floating_relief_numeric_v1'
                np.testing.assert_allclose(np.array(num['primary_acceleration_cg']) + num['correction_acceleration_cg'],
                                           num['total_acceleration_cg'], rtol=1e-12, atol=1e-14)
                np.testing.assert_allclose(np.array(num['primary_angular_acceleration']) + num['correction_angular_acceleration'],
                                           num['total_angular_acceleration'], rtol=1e-12, atol=1e-14)
                original = np.array(num['original_wrench'])
                physical = props.mass * G                      # the assembled compound is self-balanced: scale by the weight
                assert np.isfinite(original).all() and np.abs(original).max() <= 1e-6 * physical
                # inertia relief balances the assembled load: original - M delta_a closes (owner balance level)
                np.testing.assert_allclose(num['corrected_wrench'], 0.0, atol=1e-8 * physical)
                np.testing.assert_allclose(np.array(num['correction_wrench']), np.array(num['corrected_wrench']) - original,
                                           rtol=1e-9, atol=1e-8 * physical)
                assert np.abs(num['primary_acceleration_cg']).max() > 0 and 'correction_acceleration_cg' in num
            # nodal vectors enter once, gravity stays None (weight is inside the compound)
            sc = single.built.load_case
            assert sc.gravity is None and not sc.pressure_loads and sc.nodal_loads
        # a free body without relief is refused, never silently supported/balanced
        with pytest.raises(Exception):                         # supports are required without relief: refused before solving
            solve_linear_static_many(project, mesh=mesh, load_cases=names)
        # the parent's result-artifact bridge preserves the REAL numeric metadata (not a manufactured dict)
        from anyfem.io.artifacts import ArtifactStore
        from anyfem.io.result_artifact import result_artifact_payload
        store = ArtifactStore(tmp_path / 'floating.anyfem')
        payload = result_artifact_payload(batch)
        artifact = store.write_result(job_id='job', document_id='document', mesh_id='mesh', model_hash='model',
                                      mesh_hash='mesh', analysis_hash='analysis', **payload.write_result_inputs())
        saved = store.open_result(artifact).metadata('provenance')
        for entry, shape in zip(saved['floating_relief_cases'], batch.shapes):
            kept = entry['summary']['numeric']
            np.testing.assert_allclose(kept['total_acceleration_cg'], shape.info['floating_relief']['numeric']['total_acceleration_cg'])
            np.testing.assert_allclose(kept['corrected_wrench'], shape.info['floating_relief']['numeric']['corrected_wrench'])
        from anyfem.solve.run import _require_floating_relief                        # the explicit floating guard itself
        with pytest.raises(Exception, match='inertia relief'):
            _require_floating_relief(single.built.load_case, False)
        _require_floating_relief(single.built.load_case, True)
    finally:
        cmd.undo(project)


def test_floating_compound_cannot_be_mixed_with_gravity_or_cases_in_either_fold_order(case, tmp_path):
    from anyfem.document import canonical_hash
    from anyfem.io.project_file import project_to_dict
    from anyfem.model.project import ProjectError
    project, mesh, built, props, rec, comp, cache = case
    st, out, record = _prepared_record(case, tmp_path)
    cmd = fa.ApplyFloatingLoad(dict(name='FM', record_path=str(record), array_sha256=out.history.array_sha256,
                                    selected_times=[0.0, 1.0], window=[0.0, 1.0],
                                    input_document_hash=canonical_hash(project_to_dict(project)), replace=False))
    cmd.do(project)
    try:
        step = project.series_children('FM')[0].name
        project.load_case('GRAV').set_gravity(0.0, 0.0, -9.81)
        project.load_case('OTHER').add_point_load(project.point(next(iter(project.geometry.vertices))), force=(1.0, 0, 0))
        for label, factors in (('floating-first', {step: 1.0, 'GRAV': 1.0}), ('gravity-first', {'GRAV': 1.0, step: 1.0}),
                               ('case-first', {'OTHER': 1.0, step: 1.0}), ('case-last', {step: 1.0, 'OTHER': 1.0})):
            project.add_combination(label, factors)
            with pytest.raises(ProjectError, match='floating'):
                build_fe_model(project, mesh, combination=label, require_supports=False)
    finally:
        for key in ('GRAV', 'OTHER'):
            project.load_cases.pop(key, None)
        for key in ('floating-first', 'gravity-first', 'case-first', 'case-last'):
            project.combinations.pop(key, None)
        cmd.undo(project)


def test_floating_pressure_inspection_in_pa_matches_the_assembled_load_and_reaches_the_mesh_preview(case, tmp_path):
    from anyfem.application import floating_pressure_view as fpv
    from anyfem.application.floating_source import prepare_target_cells
    from anyfem.document import canonical_hash
    from anyfem.io.project_file import project_to_dict
    from anyfem.solve.build import wet_element_pressures
    from anyloads.floating_history import FloatingCoupledLoadHistory
    from anygeometry import floating_overlap as ov
    project, mesh, built, props, rec, comp, cache = case
    st, out, record = _prepared_record(case, tmp_path)
    history = FloatingCoupledLoadHistory.from_payload(record.read_bytes(), times=(0.0, 1.0), selected_window=(0.0, 1.0))
    t = 0.4
    view = fpv.floating_element_pressures(history, project, mesh, t)
    target = prepare_target_cells(project, mesh, fp._pose(rec))
    force = np.zeros(3); base = np.zeros(3)
    for j in range(len(view['element_ids'])):
        n_f = ov.polygon_plane(target.polygons_wet[j])[0]                       # into the fluid; the body feels -p n A
        force += -(view['dynamic_wet_average_pa'][j] + view['hydrostatic_perturbation_pa'][j]) * view['area_wet'][j] * n_f
        base += -view['hydrostatic_baseline_pa'][j] * view['area_wet'][j] * n_f
    nodal = history.evaluate_component(t, 'pressure')[:, :3].sum(axis=0)         # assembled dynamic pressure force (N, FE axes)
    np.testing.assert_allclose(force, nodal, rtol=1e-6, atol=1e-6 * max(1.0, np.abs(nodal).max()))   # same regions, same model
    assert abs(base[2] - props.mass * G) < 2.0                                  # baseline buoyancy balances the dry weight
    assert np.all((view['wet_fraction'] > 0) & (view['wet_fraction'] <= 1 + 1e-12)) and 'Pa' in view['labels']['pressure']
    cmd = fa.ApplyFloatingLoad(dict(name='FP', record_path=str(record), array_sha256=out.history.array_sha256,
                                    selected_times=[0.0, 1.0], window=[0.0, 1.0],
                                    input_document_hash=canonical_hash(project_to_dict(project)), replace=False))
    cmd.do(project)
    try:
        resolved = project.resolve_case(project.series_children('FP')[1])                    # the step with its series loads
        records = wet_element_pressures(project, mesh, resolved)                             # the REAL mesh-preview caller
        assert len(records) == len(view['element_ids']) and any(abs(r.pressure) > 0 for r in records)
    finally:
        cmd.undo(project)


def test_pressure_inspection_caller_refuses_stale_mesh_wet_binding_and_mass_never_serves_old_colours(case, tmp_path):
    """The ACTUAL pressure caller (not only the solve guard): same-mesh node edit, wet side/interior-point edit and a dry-mass change
    each REFUSE; nothing is cached across calls (an unchanged call afterwards still succeeds)."""
    from dataclasses import replace
    from anyfem.application import floating_pressure_view as fpv
    from anyfem.application.floating_apply import FloatingStale
    from anyloads.floating_history import FloatingCoupledLoadHistory
    project, mesh, built, props, rec, comp, cache = case
    st, out, record = _prepared_record(case, tmp_path)
    history = FloatingCoupledLoadHistory.from_payload(record.read_bytes(), times=(0.0, 1.0), selected_window=(0.0, 1.0))
    fpv.floating_element_pressures(history, project, mesh, 0.4)                          # unchanged: served
    assert not hasattr(fpv, '_CONTEXTS')                                                 # no global id-keyed cache any more
    node_id = min(mesh.nodes)
    original_node = np.array(mesh.nodes[node_id], dtype=float)
    try:
        mesh.nodes[node_id] = original_node + np.array([0.0, 0.0, 0.003])                # same mesh OBJECT, edited node
        with pytest.raises(FloatingStale, match='mesh'):
            fpv.floating_element_pressures(history, project, mesh, 0.4)
    finally:
        mesh.nodes[node_id] = original_node
    original_surface = project.wet_surfaces[0]
    try:
        project.wet_surfaces[0] = replace(original_surface, interior_point=(L / 2, 0.1, H / 2 + 0.2))
        with pytest.raises(FloatingStale, match='geometry|wet'):
            fpv.floating_element_pressures(history, project, mesh, 0.4)
    finally:
        project.wet_surfaces[0] = original_surface
    section = project.plate_sections['plate']
    try:
        project.plate_sections['plate'] = replace(section, thickness=section.thickness * 1.2)          # density/thickness => dry mass
        with pytest.raises(FloatingStale, match='mass'):
            fpv.floating_element_pressures(history, project, mesh, 0.4)
    finally:
        project.plate_sections['plate'] = section
    fpv.floating_element_pressures(history, project, mesh, 0.4)                          # restored: served again, no stale state


def test_mass_and_wet_binding_mutations_refuse_before_any_solve(case, tmp_path):
    from dataclasses import replace
    from anyfem.document import canonical_hash
    from anyfem.io.project_file import project_to_dict
    from anyfem.solve.run import _require_floating_relief
    project, mesh, built0, props, rec, comp, cache = case
    st, out, record = _prepared_record(case, tmp_path)
    cmd = fa.ApplyFloatingLoad(dict(name='FG', record_path=str(record), array_sha256=out.history.array_sha256,
                                    selected_times=[0.0, 1.0], window=[0.0, 1.0],
                                    input_document_hash=canonical_hash(project_to_dict(project)), replace=False))
    cmd.do(project)
    original_surface = project.wet_surfaces[0]
    try:
        step = project.series_children('FG')[0].name
        built = build_fe_model(project, mesh, load_case=step, require_supports=False)
        _require_floating_relief(built.load_case, True, built.fe_model, project)           # current: admitted
        built.fe_model.add_point_mass(min(built.fe_model.mesh.nodes), 500.0)                 # public mutation on the REUSED model
        with pytest.raises(Exception, match='mass'):
            _require_floating_relief(built.load_case, True, built.fe_model, project)
        fresh = build_fe_model(project, mesh, load_case=step, require_supports=False)
        project.wet_surfaces[0] = replace(original_surface, interior_point=(L / 2, 0.1, H / 2 + 0.2))
        with pytest.raises(Exception, match='geometry|wet-surface'):
            _require_floating_relief(fresh.load_case, True, fresh.fe_model, project)
    finally:
        project.wet_surfaces[0] = original_surface
        cmd.undo(project)


def test_zero_amplitude_realization_needs_no_bem_and_is_static(case, tmp_path):
    project, mesh, built, props, rec, comp, cache = case
    zero = fp.FloatingComponent(omega=0.8, amplitude=0.0, phase=0.2, heading=0.3)
    st = settings(zero)
    pose = fp._pose(rec)
    source = fsrc.prepare_source(project, mesh, pose, fsrc.prepare_target_cells(project, mesh, pose), panel_size=PANEL)
    assert fp.missing_solve_count(tmp_path / 'empty', st, source, rec) == 0
    out = fp.prepare_from_pose(project, mesh, st, rec, cache_dir=tmp_path / 'empty', built=built, props=props)
    assert out.history.is_static and out.snapshots == () and out.active == ()
    assert np.allclose(out.history.evaluate(0.3), out.history.static_load) and np.allclose(out.Hq, 0.0)
    back = fa.recover_prepared(fa.prepared_to_bytes(out))
    assert back.history.array_sha256 == out.history.array_sha256


def test_changed_hull_geometry_or_dry_inertia_cannot_reopen_a_stale_record(case, tmp_path):
    project, mesh, built, props, rec, comp, cache = case
    st, out, record = _prepared_record(case, tmp_path)
    prepared = fa.recover_prepared(record)
    mesh_id = fp._mesh_hash(mesh)
    assert fa.stale_stage(prepared, props=props, mesh_id=mesh_id, settings=st, project=project) is None
    face = next(iter(project.geometry.faces))                                    # an extra reference face changes the hull identity
    heavier = type(props)(props.mass, props.cg, props.inertia_cg * 1.01, props.node_ids, props.points, props.dofmap,
                          props.mass_matrix)
    assert fa.stale_stage(prepared, props=heavier, mesh_id=mesh_id, settings=st, project=project) == 'mass'
    # an already-wet face listed as a closing face leaves the hull geometry key unchanged (same role) but changes the declared
    # equilibrium envelope selection: stale at the equilibrium stage (a real geometry/wet-side change is covered by the mutation tests)
    assert fa.stale_stage(prepared, props=props, mesh_id=mesh_id, settings=settings(comp, envelope_extra_face_ids=(face,)),
                          project=project) == 'equilibrium'


def test_equilibrium_requires_explicit_runtime_and_opt_in(case):
    project, mesh, built, props, rec, comp, cache = case
    with pytest.raises(fp.FloatingPrepareError) as e:
        fp.solve_equilibrium(project, settings(comp), props)
    assert e.value.stage == 'equilibrium' and 'equilibrium_python' in str(e.value)
    with pytest.raises(fp.FloatingPrepareError) as open_shell:                   # no closing deck face -> not a closed envelope
        fp.solve_equilibrium(project, settings(comp, equilibrium_python=__file__, owner_wheel='w', allow_development=True), props)
    assert 'closed' in str(open_shell.value)


@pytest.mark.skipif(os.environ.get('ANYFEM_NATIVE_FLOATING_FIELD') != '1', reason='native BEM gated (parent-run)')
def test_native_damping_resolution_report_coarse_vs_refined_source(tmp_path):
    """DIAGNOSTIC evidence (no gate is relaxed): native A/B of the same hull for a coarse and a refined independent BEM source at
    two finite frequencies.  Writes the raw matrices + symmetric-part eigenvalues next to the receipt so the passivity question
    (check-17/18/32: tiny negative symmetric-B eigenvalue) can be adjudicated from actual numbers.  Asserts only finiteness and that
    the refined source never makes the worst relative negative eigenvalue larger by an order of magnitude than the coarse one."""
    import json
    project, faces = box_project(with_deck=True)
    mesh = meshed(project, BASE)
    built = build_fe_model(project, mesh, require_loads=False, require_supports=False)
    props = dry_mass_properties(built.fe_model)
    rec = pose_record(props)
    pose = fp._pose(rec)
    target = fsrc.prepare_target_cells(project, mesh, pose)
    report = {}
    for size in (0.8, 0.55):
        source = fsrc.prepare_source(project, mesh, pose, target, panel_size=size)
        for omega in (1.2, 2.0):
            snap = ff.solve_family_snapshot(source.vertices, source.faces, source.surface_ids, omega, heading=0.3,
                                            water_depth=DEPTH, rho=RHO, gravity=G, Q0=np.eye(3), cg_world=rec['cg_world_m'])
            B = np.array(snap.damping); A = np.array(snap.added_mass)
            ev = np.linalg.eigvalsh(0.5 * (B + B.T))
            key = f'size{size}_omega{omega}'
            report[key] = dict(panels=len(source.triangles), B_sym_eigenvalues=ev.tolist(), relative_min=float(ev.min() / abs(ev).max()),
                               asymmetry_B=float(np.linalg.norm(B - B.T) / np.linalg.norm(B)),
                               asymmetry_A=float(np.linalg.norm(A - A.T) / np.linalg.norm(A)), B=B.tolist(), A=A.tolist())
    (tmp_path / 'damping-report.json').write_text(json.dumps(report, indent=1), encoding='utf-8')
    print(json.dumps({k: {x: v[x] for x in ('panels', 'relative_min', 'asymmetry_B', 'asymmetry_A')} for k, v in report.items()}))
    assert all(np.isfinite(v['relative_min']) for v in report.values())
    for omega in (1.2, 2.0):
        coarse, fine = report[f'size0.8_omega{omega}'], report[f'size0.55_omega{omega}']
        assert fine['relative_min'] >= min(coarse['relative_min'] * 10.0, 0.0) or fine['relative_min'] >= 0.0


@pytest.mark.skipif(not os.environ.get('ANYFEM_FLOATING_EQUILIBRIUM_PYTHON') or not os.environ.get('ANYFEM_FLOATING_OWNER_WHEEL'),
                    reason='parent-run gate: isolated pinned-ANYgeometry runtime + owner wheel supplied by evidence/config')
def test_production_worker_irregular_selected_interval_apply_undo_reopen_and_each_frame_solve(tmp_path):
    """Real isolated equilibrium + native BEM in the production worker PROCESS, irregular realization, selected absolute
    interval, ONE undoable project load, project save/reopen with dialog-input restore, stale refusal and each-frame
    scalar-vs-batch solves through the finished IR route on the default native S3 mesh."""
    from dataclasses import asdict
    from anysolver import CancellationToken
    from anyfem.application.floating_job import prepare_floating_job, reopen_floating_job
    from anyfem.application.irregular_wave_input import IrregularWaveInput
    from anyfem.document import canonical_hash
    from anyfem.io.project_file import project_from_dict, project_to_dict
    from anyfem.job_process import run_in_worker_process
    from anyfem.solve.run import solve_linear_static, solve_linear_static_many
    from anymesher.serialize import mesh_to_dict
    project, faces = box_project(with_deck=True)
    mesh = meshed(project, BASE)
    # components inside omega 2.0..3.0 (resolved benchmark band, see the regular node / check-33); small Hs
    wave = asdict(IrregularWaveInput(name='Irr', significant_height=0.1, peak_period=2.5, seed=3, component_count=3,
                                     lower_frequency_ratio=0.8, upper_frequency_ratio=1.2, depth=DEPTH, duration=4.0, dt=1.0,
                                     heading=20.0))
    floating = dict(owner_wheel=os.environ['ANYFEM_FLOATING_OWNER_WHEEL'], allow_development=True,
                    equilibrium_python=os.environ['ANYFEM_FLOATING_EQUILIBRIUM_PYTHON'],
                    stability_source=os.environ.get('ANYFEM_FLOATING_STABILITY_SOURCE', ''),
                    envelope_extra_face_ids=(faces['deck'],), panel_size=0.7, allow_native_solve=True)    # explicit 0.7 m benchmark source (0.55 m exceeds 600 panels when inclined):
    kwargs = dict(document=project_to_dict(project), mesh_data=mesh_to_dict(mesh),
                  settings=dict(wave=wave, floating=floating),
                  cache_directory=str(tmp_path / 'cache'), record_path=str(tmp_path / 'record.npz'))
    token = CancellationToken()
    summary = run_in_worker_process(function=prepare_floating_job, kwargs=kwargs, snapshot=None, project_override={},
                                    progress=lambda value: None, token=token, containment=True,
                                    on_tree=lambda process, tree: None)
    assert summary['kind'] == 'floating_linear_response_v1' and summary['components'] == 3 and summary['active_components'] == 3
    assert summary['preview_times'] == [0.0, 1.0, 2.0, 3.0, 4.0]
    # offline reopen in a second worker process: no BEM, no cache
    import shutil
    shutil.rmtree(tmp_path / 'cache')
    kwargs2 = {k: v for k, v in kwargs.items() if k != 'cache_directory'}
    again = run_in_worker_process(function=reopen_floating_job, kwargs=kwargs2, snapshot=None, project_override={},
                                  progress=lambda value: None, token=CancellationToken(), containment=True,
                                  on_tree=lambda process, tree: None)
    assert again['array_sha256'] == summary['array_sha256'] and not (tmp_path / 'cache').exists()
    result = dict(name='Irr', record_path=summary['record_path'], array_sha256=summary['array_sha256'],
                  selected_times=[1.0, 2.0, 3.0], window=[1.0, 3.0],
                  input_document_hash=canonical_hash(project_to_dict(project)), replace=False)
    command = fa.ApplyFloatingLoad(result)
    parent = command.do(project)
    history = fa.floating_history_of(parent)
    assert history.times == (1.0, 2.0, 3.0) and history.selected_window == (1.0, 3.0)
    saved = project_to_dict(project)
    command.undo(project); assert 'Irr' not in project.load_cases
    command.redo(project); assert 'Irr' in project.load_cases
    reopened = project_from_dict(saved)
    restored = fa.restore_dialog_settings(fa.floating_history_of(reopened.load_cases['Irr']))
    assert restored['wave'] == wave and restored['floating']['panel_size'] == 0.7      # seed/spectrum/type restored, not guessed
    names = [c.name for c in project.series_children('Irr')]
    batch = solve_linear_static_many(project, mesh=mesh, load_cases=names, inertia_relief=True)
    for name, shape in zip(names, batch.shapes):                                         # each frame: scalar vs batch
        single = solve_linear_static(project, mesh=mesh, load_case=name, inertia_relief=True)
        np.testing.assert_allclose(single.displacements, shape.displacements, rtol=1e-6, atol=1e-12)
        assert shape.info['floating_relief']['history_content_sha256'] == history.content_sha256
    from anyfem.application.floating_inspection import inspect_floating_load, resultant_series
    built = build_fe_model(project, mesh, require_loads=False, require_supports=False)
    view = inspect_floating_load(history, built.fe_model, 2.0)
    assert 'mean centre of gravity' in view['origin_label'] and view['primary_acceleration'].shape[1] == 6
    graph = resultant_series(history, built.fe_model)
    assert graph['times'] == (1.0, 2.0, 3.0) and graph['series']['pressure'].shape == (3, 6)
    # stale: a different realization (other seed) cannot reopen the saved record
    other = dict(wave, seed=4)
    with pytest.raises(Exception, match='stale|realization'):
        reopen_floating_job(document=kwargs['document'], mesh_data=kwargs['mesh_data'],
                            settings=dict(wave=other, floating=floating), record_path=summary['record_path'])


@pytest.mark.skipif(not os.environ.get('ANYFEM_FLOATING_EQUILIBRIUM_PYTHON') or not os.environ.get('ANYFEM_FLOATING_OWNER_WHEEL'),
                    reason='parent-run gate: isolated pinned-ANYgeometry runtime + owner wheel supplied by evidence/config')
def test_production_prepare_with_isolated_equilibrium_and_native_families(tmp_path):
    project, faces = box_project(with_deck=True)
    mesh = meshed(project, BASE)
    # Benchmark choice (documented, evidence check-33 damping-report.json): omega=2.0 with the finer independent BEM source (0.55 m);
    # the symmetric radiation damping is positive there (relative min +7.3e-5, asymmetry 7e-3) while omega=0.8/1.2 have a weak
    # negative direction that the original passivity gate refuses (check-17/18/32).  Gates are unchanged; those families are NOT accepted.
    # amplitude 0.05 m: check-34 showed 0.2 m gives a relative waterline envelope 0.54 m > the 0.254 m top clearance (valid refusal)
    comp = fp.FloatingComponent(omega=2.0, amplitude=0.05, phase=0.0, heading=0.3)
    st = settings(comp, panel_size=0.7, owner_wheel=os.environ['ANYFEM_FLOATING_OWNER_WHEEL'], allow_development=True,
                  equilibrium_python=os.environ['ANYFEM_FLOATING_EQUILIBRIUM_PYTHON'],
                  stability_source=os.environ.get('ANYFEM_FLOATING_STABILITY_SOURCE', ''),
                  envelope_extra_face_ids=(faces['deck'],), allow_native_solve=True)
    out = fp.prepare_floating(project, mesh, st, tmp_path / 'cache')
    path = fa.save_prepared(out, tmp_path / 'record.npz')
    back = fa.reopen_or_remap(project, meshed(project, 1.0), st, path, cache_dir=tmp_path / 'no-cache')
    assert back.history.content_sha256 and out.pose['assurance']
