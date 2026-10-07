"""Connections: a reference point tied to selected geometry, loaded there.

The solver-level checks compare against closed-form answers or against an
independently built, statically equivalent model, because a connection that
runs but loses part of a moment would otherwise look fine.
"""

from __future__ import annotations

import numpy as np
import pytest
from anygeometry.entities import EntityRef, OrientedEdge

from anyfem import Project, commands as cmd, fixed, pinned, solve_linear_static, steel
from anyfem.io.project_file import project_from_dict, project_to_dict
from anyfem.model import (
    CONNECTION_PRESETS,
    Connection,
    ConnectionLoad,
    ProjectError,
)
from anyfem.model.connections import (
    CONNECTION_DOF_NAMES,
    distributing_equations,
    null_moment_directions,
    resolve_dofs,
    rigid_equations,
)

E = 2.1e11


# ----------------------------------------------------------------------
# the equations, without a solver
# ----------------------------------------------------------------------
def _residuals(equations, values):
    return [sum(c * values[(o, i)] for o, i, c in e.terms) for e in equations]


def _rigid_motion(points, reference, translation, rotation, ids):
    values = {}
    for node, point in zip(ids, points):
        u = translation + np.cross(rotation, point - reference)
        for axis in range(3):
            values[(node, axis)] = u[axis]
            values[(node, 3 + axis)] = rotation[axis]
    for axis in range(3):
        values[("ref", axis)] = translation[axis]
        values[("ref", 3 + axis)] = rotation[axis]
    return values


def test_rigid_equations_hold_for_any_rigid_body_motion():
    rng = np.random.default_rng(3)
    points = rng.normal(size=(7, 3))
    ids = list(range(7))
    reference = np.array([0.3, -0.2, 0.1])
    values = _rigid_motion(
        points, reference, rng.normal(size=3) * 1e-3, rng.normal(size=3) * 1e-3, ids
    )
    equations = rigid_equations(ids, points, reference, CONNECTION_DOF_NAMES)
    assert len(equations) == 7 * 6
    assert max(abs(r) for r in _residuals(equations, values)) < 1e-15


def test_distributing_reference_follows_a_rigid_body_motion_of_the_nodes():
    rng = np.random.default_rng(4)
    points = rng.normal(size=(9, 3))
    ids = list(range(9))
    weights = rng.uniform(0.5, 2.0, size=9)
    reference = np.array([0.4, 0.1, -0.3])  # not the weighted centroid
    values = _rigid_motion(
        points, reference, rng.normal(size=3) * 1e-3, rng.normal(size=3) * 1e-3, ids
    )
    equations, inertia = distributing_equations(
        ids, points, weights, reference, CONNECTION_DOF_NAMES
    )
    assert len(equations) == 6
    assert max(abs(r) for r in _residuals(equations, values)) < 1e-15
    assert null_moment_directions(inertia).shape[1] == 0


def test_a_straight_line_cannot_carry_a_moment_about_its_own_axis():
    line = np.array([[0.0, y, 0.0] for y in np.linspace(-1.0, 1.0, 5)])
    _, inertia = distributing_equations(
        list(range(5)), line, np.ones(5), np.zeros(3), CONNECTION_DOF_NAMES
    )
    null = null_moment_directions(inertia)
    assert null.shape[1] == 1
    np.testing.assert_allclose(np.abs(null[:, 0]), (0.0, 1.0, 0.0), atol=1e-12)


def test_translation_only_distribution_is_the_weighted_mean():
    points = np.array([[0.0, 0.0, 0.0], [2.0, 0.0, 0.0], [0.0, 2.0, 0.0]])
    equations, inertia = distributing_equations(
        [1, 2, 3], points, np.array([1.0, 1.0, 2.0]), np.zeros(3), ("ux", "uy", "uz")
    )
    assert inertia.shape == (0, 0)
    ux = next(e for e in equations if e.dependent == ("ref", 0))
    coefficients = {(owner, index): c for owner, index, c in ux.terms}
    assert coefficients[(3, 0)] == pytest.approx(-0.5)
    assert coefficients[(1, 0)] == pytest.approx(-0.25)


# ----------------------------------------------------------------------
# the record
# ----------------------------------------------------------------------
def test_connection_validation_and_dof_presets():
    ref = EntityRef("edge", 1)
    assert Connection(ref=ref).dofs == CONNECTION_DOF_NAMES
    assert Connection(ref=ref, dofs="plane_section_x").dofs == ("ux",)
    assert Connection(ref=ref, dofs="ry, ux").dofs == ("ux", "ry")
    assert resolve_dofs(("translations", "rz")) == ("ux", "uy", "uz", "rz")
    assert set(CONNECTION_PRESETS) >= {"full", "translations", "plane_section_x"}
    with pytest.raises(ValueError, match="kind"):
        Connection(ref=ref, kind="elastic")
    with pytest.raises(ValueError, match="subset"):
        Connection(ref=ref, dofs=("ux", "wx"))
    with pytest.raises(ValueError, match="reference_point"):
        Connection(ref=ref, reference="point")
    with pytest.raises(ValueError, match="only used"):
        Connection(ref=ref, reference_point=(0, 0, 0))
    with pytest.raises(ValueError, match="translation"):
        Connection(ref=ref, kind="distributing", dofs=("rx", "ry", "rz"))
    with pytest.raises(ValueError, match="points, lines or plates"):
        Connection(ref=EntityRef("part", 1))
    with pytest.raises(ValueError):
        ConnectionLoad(connection="x", moment=(0, 0))


# ----------------------------------------------------------------------
# models
# ----------------------------------------------------------------------
def _cantilever_plate():
    """A 2 x 1 m, 20 mm plate in z = 0, clamped along x = 0."""

    project = Project(name="cantilever")
    project.add_material(steel("S355", 0.010))
    project.add_plate_section("plate", thickness=0.020, material="S355")
    geometry = project.geometry
    points = geometry.add_points([(0, 0, 0), (2, 0, 0), (2, 1, 0), (0, 1, 0)])
    edges = geometry.add_polyline(points, close=True)
    face = geometry.add_face(edges)
    project.assign_plate(face, "plate")
    project.add_support(fixed(project.edge(edges[3])))
    return project, edges, face


def _box_beam():
    """A 2 m long, 1 m x 1 m box of four plates, clamped at x = 0."""

    project = Project(name="box")
    project.add_material(steel("S355", 0.010))
    project.add_plate_section("plate", thickness=0.020, material="S355")
    geometry = project.geometry
    vertices, edges = {}, {}

    def vertex(point):
        if point not in vertices:
            vertices[point] = geometry.add_point(*point)
        return vertices[point]

    def edge(first, second):
        a, b = vertex(first), vertex(second)
        key = tuple(sorted((a, b)))
        if key not in edges:
            edges[key] = geometry.add_line(a, b)
        return OrientedEdge(edges[key], geometry.edges[edges[key]].start == a)

    def plate(corners):
        loop = tuple(
            edge(a, b) for a, b in zip(corners, corners[1:] + corners[:1])
        )
        positions = [geometry.vertex_position(vertex(c)) for c in corners]
        from anygeometry.surfaces import Plane

        identifier = geometry.add_face_from_loop(
            loop,
            (0, 1, 2, 3),
            surface=Plane(
                positions[0], positions[1] - positions[0], positions[3] - positions[0]
            ),
        )
        project.assign_plate(identifier, "plate")
        return identifier

    with geometry.transaction():
        plate(((0, 0, 1), (2, 0, 1), (2, 1, 1), (0, 1, 1)))  # top
        plate(((0, 0, 0), (0, 1, 0), (2, 1, 0), (2, 0, 0)))  # bottom
        plate(((0, 0, 0), (2, 0, 0), (2, 0, 1), (0, 0, 1)))  # front web
        plate(((0, 1, 0), (0, 1, 1), (2, 1, 1), (2, 1, 0)))  # back web

    def at_x(x):
        return [
            identifier
            for identifier, item in geometry.edges.items()
            if geometry.vertex_position(item.start)[0]
            == geometry.vertex_position(item.end)[0]
            == x
        ]

    def end_edge(x, z):
        return next(
            identifier
            for identifier in at_x(x)
            if geometry.vertex_position(geometry.edges[identifier].start)[2] == z
            == geometry.vertex_position(geometry.edges[identifier].end)[2]
        )

    for identifier in at_x(0.0):
        project.add_support(fixed(project.edge(identifier)))
    return project, end_edge(2.0, 1.0), end_edge(2.0, 0.0), at_x(2.0)


def _connect(project, refs, **options):
    stack = cmd.CommandStack(project)
    stack.run(cmd.AddConnection(refs=tuple(refs), **options))
    return stack


# ----------------------------------------------------------------------
# reference points
# ----------------------------------------------------------------------
def test_reference_point_is_the_length_weighted_centre_of_the_lines():
    project, top, bottom, _ = _box_beam()
    refs = (project.edge(top), project.edge(bottom))
    _connect(project, refs, name="end")
    connection = project.connection_for("end")
    np.testing.assert_allclose(
        project.connection_reference_point(connection), (2.0, 0.5, 0.5), atol=1e-12
    )


def test_unequal_lines_pull_the_centroid_to_the_longer_one():
    project, edges, _ = _cantilever_plate()
    # edges: bottom (2 m long, y = 0), right (1 m, x = 2)
    _connect(project, (project.edge(edges[0]), project.edge(edges[1])), name="L")
    point = project.connection_reference_point(project.connection_for("L"))
    np.testing.assert_allclose(point, ((2 * 1.0 + 1 * 2.0) / 3, (0 * 2 + 0.5 * 1) / 3, 0.0))


def test_bounding_box_and_explicit_reference_points():
    project, edges, _ = _cantilever_plate()
    refs = (project.edge(edges[0]), project.edge(edges[1]))
    _connect(project, refs, name="box", reference="bounding_box")
    np.testing.assert_allclose(
        project.connection_reference_point(project.connection_for("box")),
        (1.0, 0.5, 0.0),
    )
    _connect(
        project, refs[:1], name="pt", reference="point", reference_point=(5.0, 6.0, 7.0)
    )
    np.testing.assert_allclose(
        project.connection_reference_point(project.connection_for("pt")), (5, 6, 7)
    )


def test_plate_reference_point_is_its_area_centre():
    project, edges, face = _cantilever_plate()
    _connect(project, (project.face(face),), name="plate")
    np.testing.assert_allclose(
        project.connection_reference_point(project.connection_for("plate")),
        (1.0, 0.5, 0.0),
        atol=1e-12,
    )


# ----------------------------------------------------------------------
# commands
# ----------------------------------------------------------------------
def test_add_connection_is_undoable_and_names_are_unique():
    project, top, bottom, _ = _box_beam()
    regions = len(project.regions)
    stack = _connect(project, (project.edge(top), project.edge(bottom)), name="end")
    assert len(project.connections) == 1
    assert len(project.regions) == regions + 1
    assert project.regions[project.connections[0].region.id].hidden
    stack.undo()
    assert not project.connections and len(project.regions) == regions
    stack.redo()
    assert len(project.connections) == 1 and len(project.regions) == regions + 1
    with pytest.raises(ProjectError, match="already exists"):
        stack.run(cmd.AddConnection(refs=(project.edge(top),), name="end"))
    with pytest.raises(ProjectError, match="one kind"):
        stack.run(
            cmd.AddConnection(
                refs=(project.edge(top), project.face(next(iter(project.geometry.faces)))),
                name="mixed",
            )
        )
    with pytest.raises(ProjectError, match="select"):
        stack.run(cmd.AddConnection(refs=(), name="none"))


def test_connection_load_command_resolves_a_name_and_undoes():
    project, top, bottom, _ = _box_beam()
    stack = _connect(project, (project.edge(top), project.edge(bottom)), name="end")
    stack.run(cmd.AddConnectionLoad("end", moment=(0, 1e5, 0)))
    load = project.load_case().connection_loads[0]
    assert load.connection == project.connections[0].id
    stack.undo()
    assert not project.load_case().connection_loads
    with pytest.raises(ProjectError, match="no connection"):
        stack.run(cmd.AddConnectionLoad("nope", moment=(0, 1, 0)))


def test_deleting_a_connection_takes_its_loads_and_undo_restores_both():
    project, top, bottom, _ = _box_beam()
    stack = _connect(project, (project.edge(top), project.edge(bottom)), name="end")
    stack.run(cmd.AddConnectionLoad("end", moment=(0, 1e5, 0)))
    stack.run(cmd.DeleteAttribute(project.connections[0].id))
    assert not project.connections and not project.load_case().connection_loads
    stack.undo()
    assert len(project.connections) == 1
    assert len(project.load_case().connection_loads) == 1


def test_deleting_the_geometry_takes_the_connection_and_undo_restores_it():
    project, _, _, _ = _box_beam()
    geometry = project.geometry
    free = geometry.add_line(
        geometry.add_point(5.0, 0.0, 0.0), geometry.add_point(5.0, 1.0, 0.0)
    )
    stack = _connect(project, (project.edge(free),), name="free end")
    stack.run(cmd.AddConnectionLoad("free end", force=(1, 0, 0)))
    stack.run(cmd.DeleteEntity(project.edge(free)))
    assert not project.connections and not project.load_case().connection_loads
    stack.undo()
    assert len(project.connections) == 1
    assert len(project.load_case().connection_loads) == 1


def test_edit_attribute_replaces_a_connection_and_keeps_its_id():
    project, top, bottom, _ = _box_beam()
    stack = _connect(project, (project.edge(top),), name="end")
    from dataclasses import replace

    original = project.connections[0]
    stack.run(cmd.EditAttribute(replace(original, kind="distributing")))
    assert project.connections[0].kind == "distributing"
    assert project.connections[0].id == original.id
    stack.undo()
    assert project.connections[0].kind == "rigid"


# ----------------------------------------------------------------------
# solving
# ----------------------------------------------------------------------
def _solve(project, **options):
    return solve_linear_static(project, target_size=0.1, **options)


def test_rigid_connection_carries_an_in_plane_end_moment_like_a_beam():
    project, edges, _ = _cantilever_plate()
    stack = _connect(project, (project.edge(edges[1]),), name="end")
    stack.run(cmd.AddConnectionLoad("end", moment=(0, 0, 1e3)))
    solution = _solve(project)
    motion = solution.connection_motion("end")
    # theta = M L / (E I) with I = t b^3 / 12 for the deep plate (L = 2 m).
    expected = 1e3 * 2.0 / (E * 0.020 * 1.0**3 / 12.0)
    assert motion["rotation"][2] == pytest.approx(expected, rel=0.02)
    np.testing.assert_allclose(motion["reference_point"], (2.0, 0.5, 0.0))
    # The end edge really is rigid: every node rotates with the reference point.
    for node, displacement in solution.displacement_at(project.edge(edges[1])).items():
        assert displacement[5] == pytest.approx(motion["rotation"][2], rel=1e-9)


def test_rigid_connection_carries_a_force_at_the_reference_point():
    project, edges, _ = _cantilever_plate()
    stack = _connect(project, (project.edge(edges[1]),), name="end")
    stack.run(cmd.AddConnectionLoad("end", force=(0, 0, 100.0)))
    solution = _solve(project)
    uz = solution.connection_motion("end")["displacement"][2]
    # A tip load on a wide plate: Euler-Bernoulli with I = b t^3 / 12 and the
    # shear flexibility of a thick plate, so allow a few percent.
    expected = 100.0 * 2.0**3 / (3 * E * 1.0 * 0.020**3 / 12.0)
    assert uz == pytest.approx(expected, rel=0.08)


def test_distributing_connection_equals_the_statically_equivalent_line_loads():
    """A moment at a distributing connection is the load the barge applied as
    two line loads; the displacement fields must agree."""

    project, top, bottom, _ = _box_beam()
    stack = _connect(
        project, (project.edge(top), project.edge(bottom)), name="end",
        kind="distributing", dofs="full",
    )
    moment = 1.0e5
    stack.run(cmd.AddConnectionLoad("end", moment=(0, moment, 0)))
    connected = _solve(project)

    reference, _, _, _ = _box_beam()
    q = moment / (1.0 * 1.0)  # M / (h * b)
    loads = cmd.CommandStack(reference)
    loads.run(cmd.AddLineLoad(reference.edge(top), (q, 0, 0)))
    loads.run(cmd.AddLineLoad(reference.edge(bottom), (-q, 0, 0)))
    explicit = _solve(reference)

    np.testing.assert_allclose(
        connected.translations(), explicit.translations(), atol=1e-12, rtol=1e-8
    )
    # The reference point turns with the section: theta_y = M L_eff / (E I).
    assert connected.connection_motion("end")["rotation"][1] != 0.0


def test_rigid_connection_stiffens_the_section_relative_to_distributing():
    results = {}
    for kind in ("rigid", "distributing"):
        project, top, bottom, _ = _box_beam()
        stack = _connect(
            project, (project.edge(top), project.edge(bottom)), name="end", kind=kind
        )
        stack.run(cmd.AddConnectionLoad("end", moment=(0, 1.0e5, 0)))
        results[kind] = _solve(project).connection_motion("end")["rotation"][1]
    assert abs(results["rigid"]) < abs(results["distributing"])
    assert abs(results["rigid"]) > 0.5 * abs(results["distributing"])


def test_plane_section_connection_leaves_the_section_free_in_its_plane():
    """Tying only ux keeps a plane section plane but lets it contract, so a
    transverse force at the reference point has nothing to act through."""

    project, top, bottom, _ = _box_beam()
    stack = _connect(
        project, (project.edge(top), project.edge(bottom)), name="end",
        dofs="plane_section_x",
    )
    stack.run(cmd.AddConnectionLoad("end", moment=(0, 1.0e5, 0)))
    solution = _solve(project)
    motion = solution.connection_motion("end")
    assert motion["rotation"][1] != 0.0
    # The end section moves axially like a plane: u_x = u_ref + theta_y * z.
    z = (0.0, 1.0)
    ux = [
        solution.displacement_at(project.edge(edge))
        for edge in (top, bottom)
    ]
    for edge_ux, height in zip(ux, (1.0, 0.0)):
        for displacement in edge_ux.values():
            assert displacement[0] == pytest.approx(
                motion["displacement"][0]
                + motion["rotation"][1] * (height - 0.5),
                abs=1e-12,
                rel=1e-8,
            )
    # Other components of the tied nodes are not tied.
    uy = {
        round(float(d[1]), 12)
        for d in solution.displacement_at(project.edge(top)).values()
    }
    assert len(uy) > 1

    stack.run(cmd.AddConnectionLoad("end", force=(0, 5.0, 0)))
    with pytest.raises(ProjectError, match="does not transmit a uy"):
        _solve(project)


def test_distributing_connection_refuses_a_moment_about_its_line():
    project, edges, _ = _cantilever_plate()
    stack = _connect(
        project, (project.edge(edges[1]),), name="end", kind="distributing"
    )
    # The end edge runs along y; a moment about y is the one it cannot carry.
    stack.run(cmd.AddConnectionLoad("end", moment=(0, 1e3, 0)))
    with pytest.raises(ProjectError, match="about its own axis"):
        _solve(project)


def test_rigid_connection_may_not_tie_a_degree_of_freedom_a_support_holds():
    project, edges, _ = _cantilever_plate()
    stack = _connect(project, (project.edge(edges[3]),), name="clamped")
    stack.run(cmd.AddConnectionLoad("clamped", force=(1.0, 0, 0)))
    with pytest.raises(ProjectError, match="support"):
        _solve(project)


def test_two_rigid_connections_may_not_share_a_node():
    project, edges, _ = _cantilever_plate()
    stack = _connect(project, (project.edge(edges[1]),), name="a")
    stack.run(cmd.AddConnection(refs=(project.edge(edges[1]),), name="b"))
    stack.run(cmd.AddConnectionLoad("a", force=(0, 0, 1.0)))
    with pytest.raises(ProjectError, match="both tie"):
        _solve(project)


def test_distributing_connection_may_share_nodes_with_a_support():
    project, edges, _ = _cantilever_plate()
    project.add_support(pinned(project.edge(edges[1])))
    stack = _connect(
        project, (project.edge(edges[1]),), name="end", kind="distributing",
        dofs="translations",
    )
    stack.run(cmd.AddConnectionLoad("end", force=(0, 0, 10.0)))
    solution = _solve(project)
    assert solution.connection_motion("end")["displacement"][2] == pytest.approx(0.0, abs=1e-12)


def test_connection_load_on_a_missing_connection_is_reported_by_validation():
    project, edges, _ = _cantilever_plate()
    project.load_case().add_connection_load("no-such-id", force=(0, 0, 1))
    with pytest.raises(ProjectError, match="connection that does not exist"):
        project.validate()


def test_mesh_refinement_does_not_move_the_reference_point():
    project, top, bottom, _ = _box_beam()
    stack = _connect(project, (project.edge(top), project.edge(bottom)), name="end")
    stack.run(cmd.AddConnectionLoad("end", moment=(0, 1.0e5, 0)))
    coarse = solve_linear_static(project, target_size=0.25)
    fine = solve_linear_static(project, target_size=0.125)
    np.testing.assert_allclose(
        coarse.connection_motion("end")["reference_point"],
        fine.connection_motion("end")["reference_point"],
    )
    # Different meshes, same physics: the section rotation agrees closely.
    assert coarse.connection_motion("end")["rotation"][1] == pytest.approx(
        fine.connection_motion("end")["rotation"][1], rel=0.05
    )


# ----------------------------------------------------------------------
# persistence and provenance
# ----------------------------------------------------------------------
def test_connections_round_trip_and_force_format_ten():
    project, top, bottom, _ = _box_beam()
    stack = _connect(
        project, (project.edge(top), project.edge(bottom)), name="end",
        kind="distributing", dofs="translations, ry", reference="point",
        reference_point=(2.0, 0.5, 0.5), weighting="uniform",
    )
    stack.run(cmd.AddConnectionLoad("end", force=(1, 2, 3), moment=(4, 5, 6)))
    data = project_to_dict(project)
    assert data["anyfem"]["format"] == 10
    restored = project_from_dict(data)
    before, after = project.connections[0], restored.connections[0]
    assert (after.id, after.name, after.kind, after.dofs, after.reference, after.weighting) == (
        before.id, before.name, before.kind, before.dofs, before.reference, before.weighting,
    )
    assert after.dofs == ("ux", "uy", "uz", "ry")
    np.testing.assert_allclose(after.reference_point, before.reference_point)
    assert after.region == before.region
    load = restored.load_case().connection_loads[0]
    np.testing.assert_allclose(load.force, (1, 2, 3))
    np.testing.assert_allclose(load.moment, (4, 5, 6))
    assert load.connection == after.id
    assert project_to_dict(restored) == data


def test_a_project_without_connections_keeps_its_old_format():
    project, _, _, _ = _box_beam()
    assert project_to_dict(project)["anyfem"]["format"] == 8
    assert "connections" not in project_to_dict(project)


def test_editing_a_connection_makes_a_solved_model_stale():
    from anyfem.document import _model_payload

    project, top, bottom, _ = _box_beam()
    stack = _connect(project, (project.edge(top), project.edge(bottom)), name="end")
    first = _model_payload(project_to_dict(project))
    assert "connections" in first
    from dataclasses import replace

    stack.run(cmd.EditAttribute(replace(project.connections[0], kind="distributing")))
    second = _model_payload(project_to_dict(project))
    assert first != second
    # A rename is presentation, not physics.
    stack.run(cmd.EditAttribute(replace(project.connections[0], name="renamed")))
    assert _model_payload(project_to_dict(project)) == second


def test_changing_a_connection_load_makes_the_analysis_stale():
    from anyfem.jobs import analysis_hash
    from anyfem.model.records import AnalysisDefinition

    project, top, bottom, _ = _box_beam()
    stack = _connect(project, (project.edge(top), project.edge(bottom)), name="end")
    stack.run(cmd.AddConnectionLoad("end", moment=(0, 1.0e5, 0)))
    definition = AnalysisDefinition("static")
    first = analysis_hash(definition, None, document=project_to_dict(project))
    load = project.load_case().connection_loads[0]
    stack.run(cmd.EditAttribute(
        ConnectionLoad(connection=load.connection, moment=(0, 2.0e5, 0), id=load.id)))
    assert analysis_hash(definition, None, document=project_to_dict(project)) != first


def test_connection_records_compare_and_hash_with_an_explicit_reference_point():
    ref = EntityRef("edge", 1)
    first = Connection(ref=ref, reference="point", reference_point=(1, 2, 3), id="a")
    second = Connection(ref=ref, reference="point", reference_point=[1.0, 2.0, 3.0], id="a")
    assert first == second and hash(first) == hash(second)
    assert first.reference_point == (1.0, 2.0, 3.0)


def test_imperfections_and_modal_analysis_accept_a_connection():
    from anyfem import solve_modal
    from anyfem.model import plate_mode

    project, edges, face = _cantilever_plate()
    stack = _connect(project, (project.edge(edges[1]),), name="end")
    stack.run(cmd.AddConnectionLoad("end", force=(0, 0, 100.0)))
    project.add_imperfection(plate_mode(project.face(face), amplitude=0.004))
    solution = solve_linear_static(project, target_size=0.25)
    assert solution.connection_motion("end")["displacement"][2] > 0.0

    modal = solve_modal(project, target_size=0.25, num_modes=3)
    free = _cantilever_plate()[0]
    free.load_case().add_pressure(free.face(next(iter(free.geometry.faces))), 1.0)
    reference = solve_modal(free, target_size=0.25, num_modes=3)
    # A rigid end only adds stiffness, and a little.
    for with_connection, without in zip(modal.frequencies, reference.frequencies):
        assert without <= with_connection <= 1.05 * without


# ----------------------------------------------------------------------
# spring connections
# ----------------------------------------------------------------------
def test_spring_record_validation_and_two_number_shorthand():
    ref = EntityRef("edge", 1)
    spring = Connection(ref=ref, kind="spring", stiffness=(1e6, 2e5))
    assert spring.stiffness == (1e6,) * 3 + (2e5,) * 3
    assert spring.tied_by == "rigid"
    assert Connection(ref=ref, kind="spring", stiffness="1e6, 1e6, 1e6, 0, 0, 0", tie="distributing").tied_by == "distributing"
    with pytest.raises(ValueError, match="needs stiffness"):
        Connection(ref=ref, kind="spring")
    with pytest.raises(ValueError, match="six numbers"):
        Connection(ref=ref, kind="spring", stiffness=(1, 2, 3))
    with pytest.raises(ValueError, match="not negative"):
        Connection(ref=ref, kind="spring", stiffness=(-1, 1))
    with pytest.raises(ValueError, match="positive stiffness"):
        Connection(ref=ref, kind="spring", stiffness=(0, 0))
    with pytest.raises(ValueError, match="only used by a spring"):
        Connection(ref=ref, stiffness=(1, 1))
    with pytest.raises(ValueError, match="tie"):
        Connection(ref=ref, kind="spring", stiffness=(1, 1), tie="elastic")


def test_spring_connection_is_a_series_spring_behind_the_section():
    """The section point moves exactly as a rigid connection's reference does,
    and the loaded point moves further by load / stiffness."""

    kz, krot = 5.0e6, 2.0e6
    rigid, edges, _ = _cantilever_plate()
    stack = _connect(rigid, (rigid.edge(edges[1]),), name="end")
    stack.run(cmd.AddConnectionLoad("end", force=(0, 0, 100.0), moment=(0, 50.0, 0)))
    base = _solve(rigid).connection_motion("end")

    sprung, edges, _ = _cantilever_plate()
    stack = _connect(
        sprung, (sprung.edge(edges[1]),), name="end", kind="spring",
        stiffness=(1e9, 1e9, kz, 1e9, krot, 1e9),
    )
    stack.run(cmd.AddConnectionLoad("end", force=(0, 0, 100.0), moment=(0, 50.0, 0)))
    motion = _solve(sprung).connection_motion("end")

    np.testing.assert_allclose(motion["section_displacement"], base["displacement"], rtol=1e-8, atol=1e-14)
    np.testing.assert_allclose(motion["section_rotation"], base["rotation"], rtol=1e-8, atol=1e-14)
    assert motion["displacement"][2] - motion["section_displacement"][2] == pytest.approx(100.0 / kz, rel=1e-6)
    assert motion["rotation"][1] - motion["section_rotation"][1] == pytest.approx(50.0 / krot, rel=1e-6)


def test_a_very_stiff_spring_approaches_the_rigid_connection():
    rigid, edges, _ = _cantilever_plate()
    stack = _connect(rigid, (rigid.edge(edges[1]),), name="end")
    stack.run(cmd.AddConnectionLoad("end", moment=(0, 0, 1e3)))
    expected = _solve(rigid).connection_motion("end")["rotation"][2]
    stiff, edges, _ = _cantilever_plate()
    stack = _connect(stiff, (stiff.edge(edges[1]),), name="end", kind="spring", stiffness=(1e15, 1e15))
    stack.run(cmd.AddConnectionLoad("end", moment=(0, 0, 1e3)))
    assert _solve(stiff).connection_motion("end")["rotation"][2] == pytest.approx(expected, rel=1e-6)


def test_a_spring_component_without_stiffness_carries_no_load():
    project, edges, _ = _cantilever_plate()
    stack = _connect(
        project, (project.edge(edges[1]),), name="end", kind="spring",
        stiffness=(0, 0, 1e6, 0, 0, 0),
    )
    stack.run(cmd.AddConnectionLoad("end", force=(0, 0, 10.0)))
    assert _solve(project).connection_motion("end")["displacement"][2] > 0.0
    stack.run(cmd.AddConnectionLoad("end", force=(5.0, 0, 0)))
    with pytest.raises(ProjectError, match="does not transmit a ux"):
        _solve(project)


def test_spring_connection_over_a_distributing_tie_matches_line_loads_plus_the_spring():
    project, top, bottom, _ = _box_beam()
    stack = _connect(
        project, (project.edge(top), project.edge(bottom)), name="end", kind="spring",
        tie="distributing", stiffness=(1e12, 1e9),
    )
    stack.run(cmd.AddConnectionLoad("end", moment=(0, 1.0e5, 0)))
    sprung = _solve(project)
    motion = sprung.connection_motion("end")
    assert motion["rotation"][1] - motion["section_rotation"][1] == pytest.approx(1.0e5 / 1e9, rel=1e-6)

    plain, top2, bottom2, _ = _box_beam()
    stack = _connect(
        plain, (plain.edge(top2), plain.edge(bottom2)), name="end", kind="distributing",
    )
    stack.run(cmd.AddConnectionLoad("end", moment=(0, 1.0e5, 0)))
    np.testing.assert_allclose(
        sprung.connection_motion("end")["section_rotation"],
        _solve(plain).connection_motion("end")["rotation"], rtol=1e-8, atol=1e-12)
    assert sprung.stresses() is not None  # spring elements recover no stress, and do not break recovery


def test_spring_connections_round_trip_and_work_in_modal_analysis():
    from anyfem import solve_modal

    project, edges, _ = _cantilever_plate()
    stack = _connect(
        project, (project.edge(edges[1]),), name="end", kind="spring",
        stiffness=(1e9, 1e9, 1e9, 1e9, 1e9, 1e9), tie="distributing", dofs="translations",
    )
    stack.run(cmd.AddConnectionLoad("end", force=(0, 0, 1.0)))
    restored = project_from_dict(project_to_dict(project))
    assert restored.connections == project.connections
    assert restored.connections[0].stiffness == (1e9,) * 6
    modal = solve_modal(project, target_size=0.25, num_modes=3)
    assert all(frequency > 0 for frequency in modal.frequencies)
