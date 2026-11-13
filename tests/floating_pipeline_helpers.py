"""Shared non-test floating fixtures; importing performs no mesh or solve.

Extracted unchanged from the retained floating pipeline campaign so ordinary
manager consumers do not import or collect campaign tests.
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


def _prepared_record(case, tmp_path):
    project, mesh, built, props, rec, comp, cache = case
    st = settings(comp)
    pose = fp._pose(rec)
    source = fsrc.prepare_source(project, mesh, pose, fsrc.prepare_target_cells(project, mesh, pose), panel_size=PANEL)
    cache2 = tmp_path / 'cache'
    ff.save_family_snapshot(cache2, synthetic_snapshot(source, rec, comp))
    out = fp.prepare_from_pose(project, mesh, st, rec, cache_dir=cache2, built=built, props=props, evaluator=eta_evaluator)
    return st, out, fa.save_prepared(out, tmp_path / 'record.npz')
