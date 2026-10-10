"""Submodel workflow, cut-independent part (docs/SUBMODEL_WORKFLOW_CONTRACT.md, slice S4a-1).

Oracles: the element-aligned restriction (a submodel whose mesh equals the global
elements inside the cut reproduces the global solution there) and an exact affine
membrane field transferred onto a non-matching submodel mesh.
"""

from __future__ import annotations

import copy

import numpy as np
import pytest

pytest.importorskip("anysolver.submodel_transfer")
pytest.importorskip("anymesher.submodel_correspondence")

from anyfem import Project, solve_linear_static, solve_modal, steel  # noqa: E402
from anyfem.model.materials import Material  # noqa: E402
from anygeometry.surfaces import Plane  # noqa: E402
from anyfem.model import pinned, plate_mode, support  # noqa: E402
from anyfem.submodel import (  # noqa: E402
    SubmodelError, SubmodelLineage, SubmodelSettings, admit_source, bind_source, free_body_ratios,
    refinement_change, solve_submodel,
)
from anyfem.submodel import _face_normal, _operator_fingerprint, _solver_fingerprint  # noqa: E402

LINES = [0.0, 0.3, 0.7, 1.0]
SIZE = 0.1
PRESSURE = 10_000.0
DRIVE_ALL = SubmodelSettings(policy="drive_all")
RELEASE = SubmodelSettings(policy="release_normal_rotation")


def grid_project(name, xs, ys, *, skip=()):
    project = Project(name=name)
    project.add_material(steel("S355", 0.01))
    project.add_plate_section("plate", thickness=0.01, material="S355")
    g = project.geometry
    vid = {(i, j): g.add_point(float(x), float(y), 0.0) for j, y in enumerate(ys) for i, x in enumerate(xs)}
    h = {(i, j): g.add_line(vid[i, j], vid[i + 1, j]) for j in range(len(ys)) for i in range(len(xs) - 1)}
    v = {(i, j): g.add_line(vid[i, j], vid[i, j + 1]) for j in range(len(ys) - 1) for i in range(len(xs))}
    faces = {}
    for j in range(len(ys) - 1):
        for i in range(len(xs) - 1):
            if (i, j) in skip:
                continue
            face = g.add_face([h[i, j], v[i + 1, j], h[i, j + 1], v[i, j]])
            project.assign_plate(face, "plate")
            faces[i, j] = face
    return project, faces


def boundary_edges(project, faces):
    count = {}
    for face in faces:
        for oriented in project.geometry.faces[face].loop:
            count[oriented.edge] = count.get(oriented.edge, 0) + 1
    return tuple(sorted(e for e, n in count.items() if n == 1))


def edge_mid(project, edge):
    g = project.geometry
    return 0.5 * (np.asarray(g.vertex_position(g.edges[edge].start)) + np.asarray(g.vertex_position(g.edges[edge].end)))


def sides(project, faces, box):
    """Cut faces of an axis-aligned box: one group per side, all its edges."""

    (x0, x1), (y0, y1) = box
    groups = {"x0": [], "x1": [], "y0": [], "y1": []}
    for edge in boundary_edges(project, faces):
        mid = edge_mid(project, edge)
        for name, value, axis in (("x0", x0, 0), ("x1", x1, 0), ("y0", y0, 1), ("y1", y1, 1)):
            if abs(mid[axis] - value) < 1e-9:
                groups[name].append(edge)
    return {k: tuple(v) for k, v in groups.items() if v}


def global_project():
    project, faces = grid_project("global", LINES, LINES)
    case = project.load_case()
    for face in faces.values():
        case.add_pressure(project.face(face), PRESSURE)
    for edge in boundary_edges(project, faces.values()):
        mid = edge_mid(project, edge)
        if abs(mid[0]) < 1e-9:
            project.add_support(pinned(project.edge(edge)))
        else:
            project.add_support(support(project.edge(edge), uz=0.0))
        if abs(mid[0] - 1) < 1e-9:
            case.add_line_load(project.edge(edge), (1.0e5, 0.0, 0.0))
        if abs(mid[1] - 1) < 1e-9:
            case.add_line_load(project.edge(edge), (2.0e4, 0.0, 0.0))
    return project, faces


def solved(project, size=SIZE, *, load_case="default", mesh=None):
    mesh = project.generate_mesh(size) if mesh is None else mesh
    solution = solve_linear_static(project, mesh=mesh, load_case=load_case)
    return mesh, solution, bind_source(solution)


def global_solution(size=SIZE):
    project, faces = global_project()
    return (project, faces, *solved(project, size))


@pytest.fixture(scope="module")
def source():
    return global_solution()


def inside_model(xs=LINES[1:3], ys=LINES[1:3], size=SIZE, *, loaded=True):
    project, faces = grid_project("inside", xs, ys)
    if loaded:
        for face in faces.values():
            project.load_case().add_pressure(project.face(face), PRESSURE)
    return project, faces, project.generate_mesh(size)


def node_match(sub_mesh, global_mesh):
    out = {}
    for n, x in sub_mesh.nodes.items():
        g = min(global_mesh.nodes, key=lambda m: np.linalg.norm(np.asarray(global_mesh.nodes[m]) - x))
        assert np.linalg.norm(np.asarray(global_mesh.nodes[g]) - x) < 1e-12
        out[n] = g
    return out


def on_box(x, box):
    (x0, x1), (y0, y1) = box
    return min(abs(x[0] - x0), abs(x[0] - x1)) < 1e-12 or min(abs(x[1] - y0), abs(x[1] - y1)) < 1e-12


BOX = ((0.3, 0.7), (0.3, 0.7))


@pytest.mark.parametrize("settings", [DRIVE_ALL, RELEASE], ids=["P2", "P1"])
def test_element_aligned_submodel_reproduces_the_global_solution(source, settings):
    gproject, gfaces, gmesh, gsolution, binding = source
    project, faces, mesh = inside_model()
    lineage = SubmodelLineage({faces[0, 0]: (gfaces[1, 1],)}, sides(project, faces.values(), BOX))
    result = solve_submodel(gsolution, binding, project, mesh, lineage, settings)
    expected_driven = {n for n, x in mesh.nodes.items() if on_box(x, BOX)}
    assert set(result.transfer) == expected_driven
    match = node_match(mesh, gmesh)
    errors = np.array([np.abs(result.solution.node_displacement(n) - gsolution.node_displacement(match[n]))
                       for n in set(mesh.nodes) - expected_driven])
    worst = errors.max(axis=0)
    labels = {(r.node, r.label) for r in result.trace.rows}
    if settings is DRIVE_ALL:
        assert np.max(worst[:3]) < 1e-14 and np.max(worst[3:]) < 1e-13
        assert all((n, "rz") in labels for n in expected_driven)
    else:
        # Releasing the normal rotation: two directional rotation rows per coplanar node and a
        # small, real change of the coupled field (S0: about 2.5e-4 relative in rotation).
        assert all((n, "t0") in labels and (n, "t1") in labels and (n, "rz") not in labels for n in expected_driven)
        assert 0.0 < np.max(worst[3:]) < 1e-5 and np.max(worst[:3]) < 1e-7
    d = result.diagnostics
    assert d["free_body"]["force_ratio"] < 1e-12 and d["free_body"]["moment_ratio"] < 1e-12
    assert len(d["corner_records"]) == 4 and set(d["per_cut_face"]) == {"x0", "x1", "y0", "y1"}
    assert d["grouping_reconstruction_error"] < 1e-9
    assert abs(d["whole_cut_resultant"][2] + PRESSURE * 0.16) < 1e-6 * PRESSURE * 0.16
    assert d["near_far_error"]["available"] is False


def test_subdivided_straight_cut_has_no_false_corner(source):
    _gproject, gfaces, _gmesh, gsolution, binding = source
    project, faces, mesh = inside_model(xs=[0.3, 0.5, 0.7])
    groups = sides(project, faces.values(), BOX)
    assert len(groups["y0"]) == 2  # two collinear segments on one cut face
    lineage = SubmodelLineage({f: (gfaces[1, 1],) for f in faces.values()}, groups)
    d = solve_submodel(gsolution, binding, project, mesh, lineage, DRIVE_ALL).diagnostics
    corner_positions = [tuple(np.round(mesh.nodes[n], 9)) for n in d["corner_records"]]
    assert len(corner_positions) == 4 and (0.5, 0.3, 0.0) not in corner_positions
    assert d["grouping_reconstruction_error"] < 1e-9


def tension_source():
    """Uniform tension: x = 0 held in x, one corner in y, bending held; exact affine field."""

    project, faces = grid_project("tension", LINES, LINES)
    case = project.load_case()
    for face in faces.values():
        project.add_support(support(project.face(face), uz=0.0, rx=0.0, ry=0.0))
    g = project.geometry
    for edge in boundary_edges(project, faces.values()):
        mid = edge_mid(project, edge)
        if abs(mid[0]) < 1e-9:
            project.add_support(support(project.edge(edge), ux=0.0))
        if abs(mid[0] - 1) < 1e-9:
            case.add_line_load(project.edge(edge), (1.0e5, 0.0, 0.0))
    corner = next(v for v in g.vertices if np.allclose(g.vertex_position(v), (0, 0, 0)))
    project.add_support(support(project.point(corner), uy=0.0))
    mesh = project.generate_mesh(SIZE)
    solution = solve_linear_static(project, mesh=mesh)
    return project, faces, mesh, solution


def affine_fit(mesh, solution, component):
    xy = np.array([[1.0, x[0], x[1]] for x in mesh.nodes.values()])
    values = np.array([solution.node_displacement(n)[component] for n in mesh.nodes])
    coef, *_ = np.linalg.lstsq(xy, values, rcond=None)
    assert np.max(np.abs(xy @ coef - values)) < 1e-15  # the global solution is exactly affine
    return coef


def test_nonmatching_submodel_reproduces_an_exact_affine_field():
    gproject, gfaces, gmesh, gsolution = tension_source()
    binding = bind_source(gsolution)
    fx, fy = affine_fit(gmesh, gsolution, 0), affine_fit(gmesh, gsolution, 1)
    project, faces, mesh = inside_model(size=0.03, loaded=False)
    for face in faces.values():
        project.add_support(support(project.face(face), uz=0.0, rx=0.0, ry=0.0))  # carried support
    lineage = SubmodelLineage({faces[0, 0]: (gfaces[1, 1],)}, sides(project, faces.values(), BOX))
    result = solve_submodel(gsolution, binding, project, mesh, lineage, DRIVE_ALL)
    scale = max(abs(fx[1]), abs(fy[2]))
    for node, x in mesh.nodes.items():
        u = result.solution.node_displacement(node)
        assert abs(u[0] - (fx[0] + fx[1] * x[0] + fx[2] * x[1])) < 1e-12 * scale
        assert abs(u[1] - (fy[0] + fy[1] * x[0] + fy[2] * x[1])) < 1e-12 * scale
    # The carried membrane support on the cut is redundant, not refused.
    redundant = {(result.trace.rows[i].node, result.trace.rows[i].label) for i in result.trace.redundant}
    assert {(n, c) for n in result.transfer for c in ("uz", "rx", "ry")} <= redundant
    # The transfer allowance reaches the trace rows.
    for i, row in enumerate(result.trace.rows):
        if row.direction is None:
            component = ["ux", "uy", "uz", "rx", "ry", "rz"].index(row.label)
            assert result.trace.uncertainty[i] >= result.transfer_allowance[row.node][component]


def junction_models():
    """An L: plate A in the xy-plane and plate B in the xz-plane share the x-axis edge."""

    def build(name, xs, *, loaded):
        project = Project(name=name)
        project.add_material(steel("S355", 0.01))
        project.add_plate_section("plate", thickness=0.01, material="S355")
        g = project.geometry
        axis = [g.add_point(x, 0, 0) for x in xs]
        top_a = [g.add_point(x, 1, 0) for x in xs]
        top_b = [g.add_point(x, 0, 1) for x in xs]
        base = [g.add_line(axis[i], axis[i + 1]) for i in range(len(xs) - 1)]
        faces = {}
        for name_, tops in (("A", top_a), ("B", top_b)):
            sides_ = [g.add_line(axis[i], tops[i]) for i in range(len(xs))]
            caps = [g.add_line(tops[i], tops[i + 1]) for i in range(len(xs) - 1)]
            v_dir = (0.0, 1.0, 0.0) if name_ == "A" else (0.0, 0.0, 1.0)
            surface = Plane(origin=(0.0, 0.0, 0.0), u_vector=(1.0, 0.0, 0.0), v_vector=v_dir)
            for i in range(len(xs) - 1):
                face = g.add_face([base[i], sides_[i + 1], caps[i], sides_[i]], surface=surface)
                project.assign_plate(face, "plate")
                faces[name_, i] = (face, sides_[i], sides_[i + 1])
        if loaded:
            for (plate, _i), (face, _l, _r) in faces.items():
                if plate == "A":
                    project.load_case().add_pressure(project.face(face), PRESSURE)
            for (plate, i), (face, left, _r) in faces.items():
                if i == 0:
                    project.add_support(pinned(project.edge(left)))
        return project, faces

    gproject, gfaces = build("L-global", [0.0, 0.3, 0.7, 1.0], loaded=True)
    gmesh = gproject.generate_mesh(SIZE)
    gsolution = solve_linear_static(gproject, mesh=gmesh)
    sproject, sfaces = build("L-sub", [0.3, 0.7], loaded=False)
    return gproject, gfaces, gmesh, gsolution, sproject, sfaces


def test_junction_node_gets_all_rotations_from_target_connectivity():
    gproject, gfaces, gmesh, gsolution, sproject, sfaces = junction_models()
    binding = bind_source(gsolution)
    smesh = sproject.generate_mesh(SIZE)
    (a_face, a_left, a_right), (b_face, _bl, _br) = sfaces["A", 0], sfaces["B", 0]
    # Only plate A's edges are cut; plate B meets the cut only at the junction nodes.
    lineage = SubmodelLineage({a_face: (gfaces["A", 1][0],), b_face: (gfaces["B", 1][0],)},
                              {"left": (a_left,), "right": (a_right,)})
    result = solve_submodel(gsolution, binding, sproject, smesh, lineage, RELEASE)
    junction = next(n for n, x in smesh.nodes.items() if np.allclose(x, (0.3, 0.0, 0.0)))
    inner = next(n for n, x in smesh.nodes.items() if np.allclose(x, (0.3, 0.5, 0.0)))
    labels = {(r.node, r.label) for r in result.trace.rows}
    assert {(junction, "rx"), (junction, "ry"), (junction, "rz")} <= labels  # rank 3
    assert {(inner, "t0"), (inner, "t1")} <= labels and (inner, "rz") not in labels
    assert len(result.hits[junction].incident_source_faces) == 2


def test_source_admission_and_binding(source):
    gproject, gfaces, gmesh, gsolution, binding = source
    modal = solve_modal(gproject, mesh=gmesh, num_modes=2)
    with pytest.raises(SubmodelError, match="SOURCE_NOT_ADMITTED"):
        admit_source(modal)
    broken = copy.copy(gsolution)
    broken.info = {"convergence_info": {"status": "failed"}}
    with pytest.raises(SubmodelError, match="SOURCE_NOT_CERTIFIED"):
        admit_source(broken)
    project, faces, mesh = inside_model()
    lineage = SubmodelLineage({faces[0, 0]: (gfaces[1, 1],)}, sides(project, faces.values(), BOX))
    altered = copy.copy(gsolution)
    altered.displacements = np.asarray(gsolution.displacements).copy()
    altered.displacements[0] += 1e-9
    with pytest.raises(SubmodelError, match="SOURCE_STALE"):
        solve_submodel(altered, binding, project, mesh, lineage, DRIVE_ALL)
    short = copy.copy(gsolution)
    short.displacements = np.asarray(gsolution.displacements)[:-1]
    with pytest.raises(SubmodelError, match="SOURCE_FIELD"):
        solve_submodel(short, binding, project, mesh, lineage, DRIVE_ALL)


def test_source_edit_after_binding_is_stale():
    gproject, gfaces, gmesh, gsolution, binding = global_solution()
    gproject.load_case().add_pressure(gproject.face(gfaces[0, 0]), 1.0)  # a solver-affecting edit
    project, faces, mesh = inside_model()
    lineage = SubmodelLineage({faces[0, 0]: (gfaces[1, 1],)}, sides(project, faces.values(), BOX))
    with pytest.raises(SubmodelError, match="SOURCE_STALE"):
        solve_submodel(gsolution, binding, project, mesh, lineage, DRIVE_ALL)


def test_activity_and_unsupported_inputs_refuse(source):
    gproject, gfaces, gmesh, gsolution, binding = source
    project, faces, mesh = inside_model()
    lineage = SubmodelLineage({faces[0, 0]: (gfaces[1, 1],)}, sides(project, faces.values(), BOX))
    project.add_imperfection(plate_mode(project.face(faces[0, 0]), amplitude=0.001))
    with pytest.raises(SubmodelError, match="SUBMODEL_UNSUPPORTED"):
        solve_submodel(gsolution, binding, project, mesh, lineage, DRIVE_ALL)
    active = copy.copy(gsolution)
    active.built = copy.copy(gsolution.built)
    fe = copy.deepcopy(gsolution.built.fe_model)
    fe.mesh.__dict__["element_activity"] = object()
    active.built.fe_model = fe
    with pytest.raises(SubmodelError, match="SOURCE_ACTIVITY"):
        admit_source(active)


def test_lineage_refusals(source):
    _gproject, gfaces, _gmesh, gsolution, binding = source
    project, faces, mesh = inside_model()
    groups = sides(project, faces.values(), BOX)
    with pytest.raises(SubmodelError, match="LINEAGE_MISSING"):
        solve_submodel(gsolution, binding, project, mesh, SubmodelLineage({}, groups), DRIVE_ALL)
    with pytest.raises(SubmodelError, match="LINEAGE_UNKNOWN"):
        solve_submodel(gsolution, binding, project, mesh, SubmodelLineage({faces[0, 0]: (9999,)}, groups), DRIVE_ALL)
    with pytest.raises(SubmodelError, match="CORRESPONDENCE_NOT_LOCATED"):
        solve_submodel(gsolution, binding, project, mesh, SubmodelLineage({faces[0, 0]: (gfaces[0, 0],)}, groups),
                       DRIVE_ALL)


def test_compression_guard_sees_the_driven_offset(source):
    from anyfem.solve.build import build_fe_model
    from anyfem.solve.run import _COMPRESSION_MIN_CASES, _compressible

    _gproject, gfaces, _gmesh, gsolution, binding = source
    project, faces, mesh = inside_model()
    for edge in boundary_edges(project, faces.values()):
        project.add_support(pinned(project.edge(edge)))
    control = build_fe_model(project, mesh)
    assert _compressible(control.fe_model, [control.load_case] * _COMPRESSION_MIN_CASES)
    project.supports.clear()
    lineage = SubmodelLineage({faces[0, 0]: (gfaces[1, 1],)}, sides(project, faces.values(), BOX))
    result = solve_submodel(gsolution, binding, project, mesh, lineage, DRIVE_ALL)
    model = result.solution.built.fe_model
    assert not _compressible(model, [result.solution.built.load_case] * _COMPRESSION_MIN_CASES)


def test_free_body_ratios_separate_force_and_moment(source):
    _gproject, _gfaces, _gmesh, gsolution, _binding = source
    model = gsolution.built.fe_model
    a, b = sorted(model.mesh.nodes)[:2]
    couple = {a: np.array([0, 0, 0, 0, 0, 5.0]), b: np.array([0, 0, 0, 0, 0, -5.0])}
    assert free_body_ratios(model, [couple], (0, 0, 0))["moment_ratio"] < 1e-15
    unbalanced = {a: np.array([0, 0, 0, 0, 0, 5.0])}
    ratios = free_body_ratios(model, [unbalanced], (0, 0, 0))
    assert ratios["force_ratio"] == 0.0 and ratios["moment_ratio"] == pytest.approx(1.0)


def _box_case(gfaces):
    project, faces, mesh = inside_model()
    return project, faces, mesh, SubmodelLineage({faces[0, 0]: (gfaces[1, 1],)}, sides(project, faces.values(), BOX))


@pytest.mark.parametrize("edit, caught_by", [("element_property", "solver_fingerprint"),
                                             ("load_case", "load_fingerprint"),
                                             ("face_membership", "mesh_fingerprint")])
def test_solved_input_edit_after_binding_is_stale(edit, caught_by):
    """Edits to the solved operator, its load case or the face/element association
    after binding refuse before correspondence."""

    _gproject, gfaces, gmesh, gsolution, binding = global_solution()
    built = gsolution.built
    if edit == "element_property":
        next(iter(built.fe_model.mesh.elements.values())).thickness *= 2.0
    elif edit == "load_case":
        built.load_case.add_nodal_load(next(iter(built.fe_model.mesh.nodes)), np.array([1.0, 0.0, 0.0]))
    else:
        a, b = list(built.mesh.elements_of_face)[:2]
        moved = list(built.mesh.elements_of_face[a])
        built.mesh.elements_of_face[a] = type(built.mesh.elements_of_face[a])(moved[1:])
        built.mesh.elements_of_face[b] = type(built.mesh.elements_of_face[b])(list(built.mesh.elements_of_face[b]) + moved[:1])
    project, _faces, mesh, lineage = _box_case(gfaces)
    with pytest.raises(SubmodelError, match=f"SOURCE_STALE.*{caught_by} changed"):
        solve_submodel(gsolution, binding, project, mesh, lineage, DRIVE_ALL)


class _StubFace:
    def __init__(self, points):
        self.nodes = {k + 1: np.asarray(x, dtype=float) for k, x in enumerate(points)}

    def face(self, face_id):
        return face_id

    def nodes_on(self, _selector):
        return list(self.nodes)


def test_face_normal_accepts_rotated_planes_and_refuses_warped_faces():
    rng = np.random.default_rng(3)
    q, _r = np.linalg.qr(rng.normal(size=(3, 3)))
    flat = [(x, y, 0.0) for x in (0.0, 0.5, 1.0) for y in (0.0, 0.4, 0.8)]
    rotated = _StubFace([q @ np.asarray(x) + (2.0, -1.0, 5.0) for x in flat])
    normal = _face_normal(rotated, rotated, 1)
    assert abs(abs(normal @ q[:, 2]) - 1.0) < 1e-12
    # Triangulated warped face: one node 1e-4 of the size off the plane of the others.
    warped = [(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 1.0e-4), (0.5, 0.5, 0)]
    stub = _StubFace(warped)
    with pytest.raises(SubmodelError, match="SUBMODEL_FACE_NOT_PLANAR"):
        _face_normal(stub, stub, 1)
    line = _StubFace([(0, 0, 0), (1, 1, 1), (2, 2, 2)])
    with pytest.raises(SubmodelError, match="SUBMODEL_FACE_DEGENERATE"):
        _face_normal(line, line, 1)


def test_empty_cut_face_group_refuses(source):
    _gproject, gfaces, _gmesh, gsolution, binding = source
    project, faces, mesh, lineage = _box_case(gfaces)
    empty = SubmodelLineage(lineage.source_faces, {**lineage.cut_faces, "ghost": ()})
    with pytest.raises(SubmodelError, match="LINEAGE_EMPTY_CUT_FACE"):
        solve_submodel(gsolution, binding, project, mesh, empty, DRIVE_ALL)


def test_whole_cut_resultant_rebuilds_from_reported_records(source):
    """Independent check: per-face records moved to the centre plus corners equal the
    whole-cut resultant computed directly from the interface forces."""

    _gproject, gfaces, _gmesh, gsolution, binding = source
    project, faces, mesh, lineage = _box_case(gfaces)
    result = solve_submodel(gsolution, binding, project, mesh, lineage, DRIVE_ALL)
    d = result.diagnostics
    centre = np.asarray(d["about"], dtype=float)
    fe_mesh = result.solution.built.fe_model.mesh

    def at_centre(forces):
        total = np.zeros(6)
        for node, six in forces.items():
            f = np.asarray(six, dtype=float)
            total[:3] += f[:3]
            total[3:] += f[3:] + np.cross(np.asarray(fe_mesh.get_node(int(node)).coords()) - centre, f[:3])
        return total

    direct = at_centre(result.interface.node_forces)
    rebuilt = at_centre(d["corner_records"])
    for record in d["per_cut_face"].values():
        force, moment = np.asarray(record["resultant"][:3]), np.asarray(record["resultant"][3:])
        rebuilt[:3] += force
        rebuilt[3:] += moment + np.cross(np.asarray(record["about"]) - centre, force)
    scale = sum(np.linalg.norm(np.asarray(f)[:3]) for f in result.interface.node_forces.values())
    assert scale > 0.0
    assert np.max(np.abs(rebuilt - direct)) <= 1e-12 * scale
    assert np.max(np.abs(np.asarray(d["whole_cut_resultant"]) - direct)) <= 1e-12 * scale
    assert d["grouping_reconstruction_error"] <= 1e-12 * scale
    assert len(d["per_cut_face"]) == 4 and len(d["corner_records"]) == 4


def _independent_change(first, second, near_distance):
    """Near/away maxima recomputed from the two child solutions."""

    mesh = first.solution.built.mesh
    driven = np.array([mesh.nodes[n] for n in first.transfer])
    near, far, counts = np.zeros(6), np.zeros(6), [0, 0]
    for node, x in mesh.nodes.items():
        if node in first.transfer:
            continue
        d = np.abs(np.asarray(first.solution.node_displacement(node)) - np.asarray(second.solution.node_displacement(node)))
        if np.min(np.linalg.norm(driven - np.asarray(x), axis=1)) <= near_distance:
            near, counts[0] = np.maximum(near, d), counts[0] + 1
        else:
            far, counts[1] = np.maximum(far, d), counts[1] + 1
    return near, far, counts


def test_refinement_change_compares_two_source_refinements():
    """One source project meshed twice; variants that change the source problem,
    the child, the cut or the policy refuse, each for its own reason."""

    gproject, gfaces = global_project()
    alt = gproject.load_case("alt")
    for face in gfaces.values():
        alt.add_pressure(gproject.face(face), 2.0 * PRESSURE)
    _cmesh, coarse_solution, coarse_binding = solved(gproject, SIZE)
    project, faces, mesh, lineage = _box_case(gfaces)
    coarse = solve_submodel(coarse_solution, coarse_binding, project, mesh, lineage, DRIVE_ALL)

    _fmesh, fine_solution, fine_binding = solved(gproject, SIZE / 2)
    fine = solve_submodel(fine_solution, fine_binding, project, mesh, lineage, DRIVE_ALL)
    change = refinement_change(coarse, fine, near_distance=0.1)
    near, far, (near_count, far_count) = _independent_change(coarse, fine, 0.1)
    assert near_count > 0 and far_count > 0
    assert change["near_nodes"] == near_count and change["away_nodes"] == far_count
    assert np.array_equal(change["near_cut_max_change"], near)
    assert np.array_equal(change["away_from_cut_max_change"], far)
    assert np.max(near) > 0.0 and np.max(far) > 0.0  # the refinement does change the child
    assert "not an error" in change["kind"]

    with pytest.raises(SubmodelError, match="REFINEMENT_SAME_SOURCE"):
        refinement_change(coarse, coarse, near_distance=0.1)

    # Source problem changes, one at a time on the same project (each restored, so
    # each refusal names exactly one difference): material, loading outside the
    # child, selected load case.
    def refused(solution_binding, reason):
        _m, sol, bnd = solution_binding
        other = solve_submodel(sol, bnd, project, mesh, lineage, DRIVE_ALL)
        with pytest.raises(SubmodelError, match=f"REFINEMENT_SOURCE_PROBLEM: the two sources differ in {reason}, not"):
            refinement_change(coarse, other, near_distance=0.1)

    original = gproject.materials["S355"]
    gproject.materials["S355"] = Material("S355", elastic_modulus=1.1 * 2.1e11, poisson_ratio=0.3)
    refused(solved(gproject, SIZE / 2), "model")
    gproject.materials["S355"] = original
    gproject.load_case().add_pressure(gproject.face(gfaces[0, 0]), PRESSURE)
    refused(solved(gproject, SIZE / 2), "loading")
    gproject.load_case().pressures.pop()
    refused(solved(gproject, SIZE / 2, load_case="alt"), "load case")
    assert refinement_change(coarse, solve_submodel(*solved(gproject, SIZE / 2)[1:], project, mesh, lineage, DRIVE_ALL),
                             near_distance=0.1)["away_nodes"] == far_count  # restored

    # Child changes: policy, cut grouping, child loading.
    other_policy = solve_submodel(fine_solution, fine_binding, project, mesh, lineage, RELEASE)
    with pytest.raises(SubmodelError, match="REFINEMENT_INCOMPATIBLE"):
        refinement_change(coarse, other_policy, near_distance=0.1)
    merged = SubmodelLineage(lineage.source_faces, {"all": lineage.cut_edges})
    other_cut = solve_submodel(fine_solution, fine_binding, project, mesh, merged, DRIVE_ALL)
    with pytest.raises(SubmodelError, match="REFINEMENT_INCOMPATIBLE"):
        refinement_change(coarse, other_cut, near_distance=0.1)
    project.load_case().add_pressure(project.face(faces[0, 0]), 1.0)
    other_loading = solve_submodel(fine_solution, fine_binding, project, mesh, lineage, DRIVE_ALL)
    with pytest.raises(SubmodelError, match="REFINEMENT_INCOMPATIBLE"):
        refinement_change(coarse, other_loading, near_distance=0.1)


def test_coupling_coefficient_edit_after_binding_is_stale():
    """A real eccentric beam-shell coupling in the solved source: changing its
    interpolation weight after binding changes the bound operator rows."""

    from anymesher import Coupling
    from anysolver.mesh_gen import InterpolatedBeamShellMPCElement

    gproject, gfaces = global_project()
    mesh = gproject.generate_mesh(SIZE)
    element = next(iter(mesh.elements_of_face[gfaces[1, 1]]))
    plate = tuple(int(n) for n in mesh.shells[element])
    beam_node = max(mesh.nodes) + 1
    centre = np.mean([mesh.nodes[n] for n in plate], axis=0)
    mesh.nodes[beam_node] = centre + np.array([0.0, 0.0, 0.05])
    mesh.couplings[max(mesh.shells) + 1000] = Coupling(
        beam_node=beam_node, plate_nodes=plate, weights=(0.25,) * 4, eccentricity=(0.0, 0.0, 0.05))
    _mesh, solution, binding = solved(gproject, mesh=mesh)
    fe_model = solution.built.fe_model
    coupling = next(e for e in fe_model.mesh.elements.values() if isinstance(e, InterpolatedBeamShellMPCElement))
    before = (_solver_fingerprint(fe_model), _operator_fingerprint(fe_model))
    coupling.shape_weights = np.array([0.4, 0.2, 0.2, 0.2])
    after = (_solver_fingerprint(fe_model), _operator_fingerprint(fe_model))
    assert before[0] != after[0] and before[1] != after[1]  # both the state and the operator rows see it
    project, _faces, smesh, lineage = _box_case(gfaces)
    with pytest.raises(SubmodelError, match="SOURCE_STALE"):
        solve_submodel(solution, binding, project, smesh, lineage, DRIVE_ALL)


def test_public_state_is_complete_or_refused():
    _gproject, gfaces, _gmesh, gsolution, binding = global_solution()
    fe_model = gsolution.built.fe_model
    element = next(iter(fe_model.mesh.elements.values()))
    element.notes = {"factor": [1.0, 2.0]}
    bound = _solver_fingerprint(fe_model)
    element.notes["factor"][1] = 3.0  # a nested public value
    assert _solver_fingerprint(fe_model) != bound
    element.notes = object()
    with pytest.raises(SubmodelError, match="SOURCE_STATE_UNSUPPORTED"):
        _solver_fingerprint(fe_model)
    element.notes = lambda: 1.0
    with pytest.raises(SubmodelError, match="SOURCE_STATE_UNSUPPORTED"):
        _solver_fingerprint(fe_model)


@pytest.mark.parametrize("edit", ["connectivity", "missing_element", "node_position"])
def test_mesher_mesh_must_match_the_solved_shells(edit):
    _gproject, _gfaces, gmesh, gsolution, _binding = global_solution()
    fe_mesh = gsolution.built.fe_model.mesh
    element_id = next(iter(next(iter(gmesh.elements_of_face.values()))))
    if edit == "connectivity":
        el = fe_mesh.elements[element_id]
        el.node_ids = tuple(el.node_ids)[1:] + tuple(el.node_ids)[:1]
    elif edit == "missing_element":
        del fe_mesh.elements[element_id]
    else:
        node = gmesh.shells[element_id][0]
        gmesh.nodes[node] = np.asarray(gmesh.nodes[node]) + np.array([1e-6, 0.0, 0.0])
    with pytest.raises(SubmodelError, match="SOURCE_INCONSISTENT"):
        bind_source(gsolution)
