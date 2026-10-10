"""Submodels on cylinders (workflow contract section 12, slices S3b and S4c-1)."""

from __future__ import annotations

import numpy as np
import pytest

from anyfem import Project, solve_linear_static, steel
from anyfem.commands import AddCylinder, CommandStack
from anyfem.model import support
from anyfem.submodel import SubmodelError, SubmodelLineage, SubmodelSettings, bind_source, solve_submodel
from anygeometry.surfaces import Cylinder, Plane

R, L, T, P = 1.0, 4.0, 0.01, 1.0e5
E = 2.1e11
W_EXACT = P * R * R / (E * T)  # open tube, axially free: w = pR^2/(Et)
P2 = SubmodelSettings(policy="drive_all")
P1 = SubmodelSettings(policy="release_normal_rotation")


def _materials(project):
    project.add_material(steel("S355", T))
    project.add_plate_section("plate", thickness=T, material="S355")


def tube(size, *, fin=False, revolve=False):
    """Pressurised open tube; bottom ring axially supported, rigid modes removed at three vertices."""

    project = Project("tube")
    g = project.geometry
    if revolve:
        start, end = g.add_point(R, 0.0, 0.0), g.add_point(R, 0.0, L)
        g.revolve([g.add_line(start, end)], (0, 0, 0), (0, 0, 1), 2 * np.pi)
    else:
        CommandStack(project).run(AddCylinder(kind="generator.cylinder", name="Cylinder", parameters={
            "radius": R, "height": L, "circumferential_segments": 4, "origin": (0.0, 0.0, 0.0),
            "axis": (0.0, 0.0, 1.0), "radial_direction": (1.0, 0.0, 0.0)}, label="add cylinder"))
    _materials(project)
    shell = list(g.faces)
    if fin:
        axial = next(e for e, edge in g.edges.items()
                     if all(np.allclose(np.asarray(g.vertex_position(v))[:2], (R, 0.0)) for v in (edge.start, edge.end)))
        a, b = g.edges[axial].start, g.edges[axial].end
        za, zb = (float(np.asarray(g.vertex_position(v))[2]) for v in (a, b))
        c, d = g.add_point(R + 0.3, 0.0, zb), g.add_point(R + 0.3, 0.0, za)
        g.add_face([axial, g.add_line(b, c), g.add_line(c, d), g.add_line(d, a)],
                   surface=Plane(origin=(R, 0.0, min(za, zb)), u_vector=(0.3, 0.0, 0.0),
                                 v_vector=(0.0, 0.0, abs(zb - za))))
    for face in g.faces:
        project.assign_plate(face, "plate")
    for face in shell:
        project.load_case().add_pressure(project.face(face), P)
    for e, edge in g.edges.items():
        ends = [np.asarray(g.vertex_position(v)) for v in (edge.start, edge.end)]
        if all(abs(x[2]) < 1e-9 and abs(np.linalg.norm(x[:2]) - R) < 1e-9 for x in ends):
            project.add_support(support(project.edge(e), uz=0.0))
    for v in g.vertices:
        x = np.asarray(g.vertex_position(v))
        if abs(x[2]) < 1e-9 and abs(np.linalg.norm(x[:2]) - R) < 1e-9:
            angle = np.degrees(np.arctan2(x[1], x[0])) % 360
            if min(abs(angle - 0), abs(angle - 360), abs(angle - 180)) < 1e-6:
                project.add_support(support(project.point(v), uy=0.0))
            if abs(angle - 90) < 1e-6:
                project.add_support(support(project.point(v), ux=0.0))
    mesh = project.generate_mesh(size)
    solution = solve_linear_static(project, mesh=mesh)
    return project, mesh, solution, bind_source(solution)


def patch(size, *, centre=0.0, half=np.pi / 6, z0=1.5, z1=2.5, radius=R, split=False, fin=False, loaded=True,
          coons=False):
    """A cylindrical patch child; ``split`` divides it at ``centre`` (two faces sharing an axial edge)."""

    project = Project("patch")
    _materials(project)
    g = project.geometry

    def point(angle, z, r=radius):
        return g.add_point(r * np.cos(angle), r * np.sin(angle), z)

    angles = [centre - half, centre, centre + half] if split else [centre - half, centre + half]
    bottom = [point(a, z0) for a in angles]
    top = [point(a, z1) for a in angles]
    sides = [g.add_line(b, t) for b, t in zip(bottom, top)]
    faces, cut = [], {}
    for k in range(len(angles) - 1):
        mid = 0.5 * (angles[k] + angles[k + 1])
        arc_bottom = g.add_arc(bottom[k], point(mid, z0), bottom[k + 1])
        arc_top = g.add_arc(top[k], point(mid, z1), top[k + 1])
        surface = None if coons else Cylinder(origin=(0, 0, 0), axis=(0, 0, 1), radial_direction=(1, 0, 0),
                                              radius=radius, height=L, start_angle=angles[k],
                                              sweep_angle=angles[k + 1] - angles[k])
        face = g.add_face([arc_bottom, sides[k + 1], arc_top, sides[k]], surface=surface)
        faces.append(face)
        cut[f"bottom{k}"], cut[f"top{k}"] = (arc_bottom,), (arc_top,)
    cut["left"], cut["right"] = (sides[0],), (sides[-1],)
    if fin:
        c, d = g.add_point(R + 0.3, 0.0, z1), g.add_point(R + 0.3, 0.0, z0)
        fin_bottom, fin_top = g.add_line(d, bottom[1]), g.add_line(top[1], c)
        fin_face = g.add_face([sides[1], fin_top, g.add_line(c, d), fin_bottom],
                              surface=Plane(origin=(R, 0.0, z0), u_vector=(0.3, 0.0, 0.0), v_vector=(0.0, 0.0, z1 - z0)))
        faces.append(fin_face)
        cut["fin_bottom"], cut["fin_top"] = (fin_bottom,), (fin_top,)
    for face in faces:
        project.assign_plate(face, "plate")
        if loaded and face in faces[: len(angles) - 1]:
            project.load_case().add_pressure(project.face(face), P)
    return project, faces, cut, project.generate_mesh(size)


def shell_faces(project):
    from anygeometry.surfaces import Cylinder as _Cylinder

    return tuple(f for f, face in project.geometry.faces.items() if isinstance(getattr(face, "surface", None), _Cylinder))


def lineage_for(source_project, faces, cut, *, fin_source=None):
    shell = shell_faces(source_project) or tuple(source_project.geometry.faces)
    mapping = {face: shell for face in faces}
    if fin_source is not None:
        mapping[faces[-1]] = (fin_source,)
    return SubmodelLineage(mapping, cut)


def radial_at(result, mesh, angle, z):
    target = np.array([R * np.cos(angle), R * np.sin(angle), z])
    node = min(mesh.nodes, key=lambda n: np.linalg.norm(np.asarray(mesh.nodes[n]) - target))
    d = np.asarray(result.solution.node_displacement(node))
    return float(d[:3] @ np.array([np.cos(angle), np.sin(angle), 0.0]))


@pytest.fixture(scope="module")
def sources():
    return {size: tube(size) for size in (0.25, 0.125)}


# ----------------------------------------------------------------------
# rigid motion through S3b and the S1 transport, with facet offsets
# ----------------------------------------------------------------------
@pytest.mark.parametrize("policy", ["drive_all", "release_normal_rotation"])
def test_rigid_motion_is_reproduced_on_a_cylinder(sources, policy):
    from anymesher.submodel_correspondence import CylinderFrame, locate_for_transfer
    from anysolver import solve_linear
    from anysolver.submodel_transfer import TransferPoint, apply_driven_trace, build_driven_trace, interpolate_nodal_field
    from anyfem.solve.build import build_fe_model

    gproject, gmesh, gsolution, _binding = sources[0.25]
    model = gsolution.built.fe_model
    a, w = np.array([1e-3, -2e-3, 3e-3]), np.array([2e-4, -1e-4, 3e-4])
    u = np.zeros(model.mesh.dof_manager.total_dofs)
    for node in model.mesh.nodes:
        dofs = model.mesh.dof_manager.get_node_dofs(node)
        x = np.asarray(model.mesh.get_node(node).coords())
        u[dofs[:3]] = a + np.cross(w, x)
        u[dofs[3:]] = w
    cproject, faces, cut, cmesh = patch(0.05, loaded=False)
    driven = sorted({n for edges in cut.values() for e in edges for n in cmesh.nodes_on(cproject.edge(e))})
    points = {n: np.asarray(cmesh.nodes[n]) for n in driven}
    shell = set(e for f in shell_faces(gproject) for e in gmesh.elements_of_face[f])
    hits = locate_for_transfer(gmesh, points, candidate_elements={n: shell for n in points},
                               cylinder=CylinderFrame((0, 0, 0), (0, 0, 1), R, 1e-9 * L))
    assert max(h.distance for h in hits.values()) > 1e-3  # facet offsets are present
    values = interpolate_nodal_field(model, u, [
        TransferPoint(n, h.source_element_id, h.natural, tuple(points[n]), h.weights, h.inverse_residual, h.alternatives)
        for n, h in hits.items()])
    for n, value in values.items():
        expected = np.concatenate([a + np.cross(w, points[n]), w])
        assert np.max(np.abs(np.asarray(value.values) - expected)) <= 1e-15
    built = build_fe_model(cproject, cmesh, "default", require_loads=False, require_supports=False,
                           apply_imperfections=False)
    normals = None
    if policy == "release_normal_rotation":
        normals = {n: [np.array([points[n][0], points[n][1], 0.0]) / np.linalg.norm(points[n][:2])] for n in points}
    trace = build_driven_trace(built.fe_model, {n: v.values for n, v in values.items()}, policy=policy, normals=normals)
    apply_driven_trace(built.fe_model, trace)
    child_u, _info = solve_linear(built.fe_model, built.load_case)
    for node in built.fe_model.mesh.nodes:
        dofs = built.fe_model.mesh.dof_manager.get_node_dofs(node)
        x = np.asarray(built.fe_model.mesh.get_node(node).coords())
        assert np.max(np.abs(child_u[dofs[:3]] - (a + np.cross(w, x)))) <= 1e-12
        assert np.max(np.abs(child_u[dofs[3:]] - w)) <= 1e-12


# ----------------------------------------------------------------------
# A6 variant: open tube under internal pressure
# ----------------------------------------------------------------------
@pytest.mark.parametrize("settings", [P2, P1], ids=["P2", "P1"])
def test_pressurised_tube_child_meets_the_closed_form(sources, settings):
    cproject, faces, cut, cmesh = patch(0.05)
    lineage = lineage_for(sources[0.25][0], faces, cut)
    child = {}
    for size, (gproject, gmesh, gsolution, binding) in sources.items():
        result = solve_submodel(gsolution, binding, cproject, cmesh, lineage, settings)
        child[size] = radial_at(result, cmesh, 0.0, 2.0)
        coarse = float(np.mean([gsolution.node_displacement(n)[:2] @ (np.asarray(x[:2]) / np.linalg.norm(x[:2]))
                                for n, x in gmesh.nodes.items() if abs(x[2] - 2.0) < 1e-9]))
        print(f"source {size}: coarse error {coarse / W_EXACT - 1:+.3%}, child error {child[size] / W_EXACT - 1:+.3%}")
        assert abs(child[size] / W_EXACT - 1.0) <= 0.005
        body = result.diagnostics["free_body"]
        assert body["force_ratio"] <= 1e-12 and body["moment_ratio"] <= 1e-12
        cylindrical = result.diagnostics["cylindrical"]
        assert cylindrical["nodes"] == len(result.hits)
        assert 0.0 < cylindrical["max_facet_offset"] <= cylindrical["max_facet_bound"] + cylindrical["max_float_allowance"]
    assert abs(child[0.25] - child[0.125]) / W_EXACT <= 0.005


def test_a_seam_node_has_one_normal_and_releases_it(sources):
    gproject, gmesh, gsolution, binding = sources[0.25]
    # Straddles the source faces' seam at angle 0; split there so a driven node lies on it.
    cproject, faces, cut, cmesh = patch(0.05, split=True)
    result = solve_submodel(gsolution, binding, cproject, cmesh, lineage_for(gproject, faces, cut), P1)
    seam = next(n for n, x in cmesh.nodes.items() if np.allclose(x, (R, 0.0, 1.5)))
    assert len(result.hits[seam].incident_source_faces) == 2
    labels = {(r.node, r.label) for r in result.trace.rows}
    assert {(seam, "t0"), (seam, "t1")} <= labels
    assert not {(seam, "rx"), (seam, "ry"), (seam, "rz")} & labels


def test_a_patch_across_the_angle_wrap(sources):
    gproject, gmesh, gsolution, binding = sources[0.25]
    cproject, faces, cut, cmesh = patch(0.05, centre=np.pi)
    result = solve_submodel(gsolution, binding, cproject, cmesh, lineage_for(gproject, faces, cut), P2)
    assert abs(radial_at(result, cmesh, np.pi, 2.0) / W_EXACT - 1.0) <= 0.005


# ----------------------------------------------------------------------
# refusals
# ----------------------------------------------------------------------
def test_unsupported_and_mismatched_surfaces_refuse(sources):
    gproject, gmesh, gsolution, binding = sources[0.25]
    loose = SubmodelSettings(policy="drive_all", physical_tolerance=10.0)
    # a non-planar Coons child face
    cproject, faces, cut, cmesh = patch(0.05, coons=True)
    for settings in (P2, loose):
        with pytest.raises(SubmodelError, match="SUBMODEL_SURFACE_UNSUPPORTED"):
            solve_submodel(gsolution, binding, cproject, cmesh, lineage_for(gproject, faces, cut), settings)
    # a child on another cylinder (radius 1.001 R)
    cproject, faces, cut, cmesh = patch(0.05, radius=1.001 * R)
    for settings in (P2, loose):
        with pytest.raises(SubmodelError, match="SUBMODEL_SURFACE_MISMATCH"):
            solve_submodel(gsolution, binding, cproject, cmesh, lineage_for(gproject, faces, cut), settings)


def test_a_non_planar_coons_source_refuses():
    gproject, gmesh, gsolution, binding = tube(0.25, revolve=True)
    cproject, faces, cut, cmesh = patch(0.05)
    with pytest.raises(SubmodelError, match="SUBMODEL_SURFACE_UNSUPPORTED"):
        solve_submodel(gsolution, binding, cproject, cmesh, lineage_for(gproject, faces, cut), P2)


def test_a_cylindrical_child_over_planar_sources_refuses():
    from test_submodel_workflow import global_solution

    gproject, gfaces, _gmesh, gsolution, binding = global_solution()
    cproject, faces, cut, cmesh = patch(0.05)
    lineage = SubmodelLineage({face: (gfaces[1, 1],) for face in faces}, cut)
    with pytest.raises(SubmodelError, match="SUBMODEL_SURFACE_MISMATCH"):
        solve_submodel(gsolution, binding, cproject, cmesh, lineage, SubmodelSettings("drive_all", physical_tolerance=10.0))


def test_a_plane_cylinder_junction_node_refuses():
    gproject, gmesh, gsolution, binding = tube(0.25, fin=True)
    fin_source = next(f for f, face in gproject.geometry.faces.items() if isinstance(getattr(face, "surface", None), Plane))
    cproject, faces, cut, cmesh = patch(0.05, split=True, fin=True)
    lineage = lineage_for(gproject, faces, cut, fin_source=fin_source)
    junction = next(n for n, x in cmesh.nodes.items() if np.allclose(x, (R, 0.0, 1.5)))
    assert len({f for f in cmesh.elements_of_face if junction in cmesh.nodes_on(cproject.face(f))}) == 3
    with pytest.raises(SubmodelError, match="SUBMODEL_SURFACE_JUNCTION"):
        solve_submodel(gsolution, binding, cproject, cmesh, lineage, P2)


def test_cylinder_identity_is_symmetric():
    from anyfem.submodel import _Surface, _same_cylinder

    base = _Surface("cylinder", (0.0, 0.0, 0.0), (0.0, 0.0, 1.0), R)
    tilted_axis = np.array([1e-6, 0.0, 1.0]) / np.linalg.norm([1e-6, 0.0, 1.0])
    tilted = _Surface("cylinder", (0.0, 0.0, 1000.0), tuple(tilted_axis), R)  # parallel within 1e-6 only
    shifted = _Surface("cylinder", (0.0, 0.0, 1000.0), (0.0, 0.0, -1.0), R)  # opposite sign, shifted along the axis
    off = _Surface("cylinder", (1e-3, 0.0, 5.0), (0.0, 0.0, 1.0), R)
    g = 1e-9 * 1000.0
    for a, b, same in ((base, tilted, False), (base, shifted, True), (base, off, False)):
        assert _same_cylinder(a, b, g) is same and _same_cylinder(b, a, g) is same


def test_a_cut_node_without_a_child_shell_refuses(sources):
    from anyfem.model import BeamSection

    gproject, gmesh, gsolution, binding = sources[0.25]
    cproject, faces, cut, _ = patch(0.05)
    g = cproject.geometry
    corner = next(v for v in g.vertices if np.allclose(g.vertex_position(v), (R * np.cos(np.pi / 6), R * np.sin(np.pi / 6), 1.5)))
    tip = g.add_point(1.3 * R * np.cos(np.pi / 6), 1.3 * R * np.sin(np.pi / 6), 1.5)
    beam = g.add_line(corner, tip)
    cproject.add_beam_section(BeamSection(name="bar", profile="Flatbar", material="S355", flange_width=0.05,
                                          flange_thickness=0.01, web_direction=(0.0, 0.0, 1.0)))
    cproject.assign_beam(beam, "bar")
    cmesh = cproject.generate_mesh(0.05)
    lineage = lineage_for(gproject, faces, {**cut, "beam": (beam,)})
    with pytest.raises(SubmodelError, match="SUBMODEL_NO_SHELL"):
        solve_submodel(gsolution, binding, cproject, cmesh, lineage, P2)
