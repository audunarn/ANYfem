"""Phase 11: local refinement, graded seeding and quadratic elements.

The thread running through these is that a mapped mesh has a hard limit: the
interior of a plate is the transfinite blend of its boundary, so refining
inside one means decomposing it.  Several tests exist to pin that down rather
than to check a feature works, because it is the kind of limit that otherwise
gets rediscovered as a bug.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import numpy as np
import pytest

from anyfem import Project, fixed, pinned, solve_linear_static, steel, support
from anyfem.commands import (
    AddRefinement,
    CommandStack,
    RefineForImpact,
    SetElementOrder,
)
from anyfem.geometry import GeometryModel
from anyfem.mesh import MeshError, generate_mesh, refine_around, refine_at
from anyfem.mesh.refinement import Refinement, SizeField
from anyfem.mesh.seeding import edge_demand, solve_seeding
from anyfem.model import BeamSection, Collision, ProjectError


@pytest.fixture
def workspace():
    with tempfile.TemporaryDirectory() as directory:
        yield Path(directory)


def square(side: float = 4.0) -> tuple[GeometryModel, int]:
    model = GeometryModel()
    points = model.add_points(
        [(0, 0, 0), (side, 0, 0), (side, side, 0), (0, side, 0)]
    )
    return model, model.add_face(model.add_polyline(points, close=True))


def plate_project(side: float = 1.0, thickness: float = 0.010) -> Project:
    project = Project(name="plate")
    project.add_material(steel("S355", thickness))
    project.add_plate_section("plate", thickness=thickness, material="S355")
    geometry = project.geometry
    points = geometry.add_points(
        [(0, 0, 0), (side, 0, 0), (side, side, 0), (0, side, 0)]
    )
    edges = geometry.add_polyline(points, close=True)
    face = geometry.add_face(edges)
    project.assign_plate(face, "plate")
    for edge in edges:
        project.add_support(pinned(project.edge(edge)))
    project.load_case().add_pressure(project.face(face), 10_000.0)
    return project


# ----------------------------------------------------------------------
# the size field
# ----------------------------------------------------------------------
def test_a_zone_needs_exactly_one_of_a_ref_and_a_point():
    model, face = square()
    reference = model.entity_ref("face", face)

    with pytest.raises(ValueError, match="either ref"):
        Refinement(size=0.1)
    with pytest.raises(ValueError, match="either ref"):
        Refinement(size=0.1, ref=reference, center=(0.0, 0.0, 0.0))
    # Either one alone is fine.
    assert Refinement(size=0.1, ref=reference).ref == reference
    assert Refinement(size=0.1, center=(0.0, 0.0, 0.0)).center is not None


def test_a_zone_coarser_than_the_target_is_refused():
    model, _face = square()
    zone = refine_at((0, 0, 0), size=1.0, radius=0.5)
    with pytest.raises(ValueError, match="coarser than the global target"):
        SizeField(model, 0.5, [zone])


def test_growth_of_one_would_never_return_to_the_target():
    with pytest.raises(ValueError, match="growth must exceed"):
        refine_at((0, 0, 0), size=0.1, radius=0.1, growth=1.0)


def test_a_field_with_no_zones_is_uniform():
    model, _face = square()
    field = SizeField(model, 0.5)
    assert field.is_uniform
    assert field.size_at(np.array([[1.0, 1.0, 0.0]]))[0] == pytest.approx(0.5)


def test_size_grows_from_the_zone_back_to_the_target():
    model, _face = square()
    zone = refine_at((0, 0, 0), size=0.1, radius=0.2, growth=1.5)
    field = SizeField(model, 0.5, [zone])

    inside = field.size_at(np.array([[0.1, 0.0, 0.0]]))[0]
    edge_of_zone = field.size_at(np.array([[0.2, 0.0, 0.0]]))[0]
    far = field.size_at(np.array([[4.0, 4.0, 0.0]]))[0]

    assert inside == pytest.approx(0.1)
    assert edge_of_zone == pytest.approx(0.1)
    assert far == pytest.approx(0.5)
    # Monotone in between, and never coarser than the target.
    samples = field.size_at(
        np.column_stack(
            [np.linspace(0.2, 3.0, 20), np.zeros(20), np.zeros(20)]
        )
    )
    assert np.all(np.diff(samples) >= -1e-12)
    assert np.all(samples <= 0.5 + 1e-12)


def test_a_zone_on_a_line_measures_distance_to_the_whole_line():
    model, face = square()
    edge = model.faces[face].loop[0].edge
    field = SizeField(
        model, 0.5, [refine_around(model.entity_ref("edge", edge), 0.1, 0.05)]
    )
    # Both ends of the line are equally refined, which a single-point source
    # could not do.
    for x in (0.5, 2.0, 3.5):
        assert field.size_at(np.array([[x, 0.0, 0.0]]))[0] == pytest.approx(0.1)


# ----------------------------------------------------------------------
# graded seeding
# ----------------------------------------------------------------------
def test_a_uniform_field_asks_for_length_over_target():
    model, face = square(side=4.0)
    edge = model.faces[face].loop[0].edge
    field = SizeField(model, 0.5)
    assert edge_demand(model, edge, field) == pytest.approx(8.0)


def test_grading_asks_for_more_elements_than_a_uniform_field():
    model, face = square(side=4.0)
    edge = model.faces[face].loop[0].edge
    zone = refine_at((0, 0, 0), size=0.1, radius=0.5)
    graded = edge_demand(model, edge, SizeField(model, 0.5, [zone]))
    assert graded > edge_demand(model, edge, SizeField(model, 0.5))


def test_the_ungraded_mesh_is_untouched_by_the_size_field():
    """Adding the field must not move a model that does not use it."""

    model, _face = square()
    by_target = generate_mesh(model, target_size=0.5)
    by_field = generate_mesh(model, target_size=0.5, refinements=[])

    assert by_target.num_nodes == by_field.num_nodes
    for node in by_target.nodes:
        assert by_target.nodes[node] == pytest.approx(by_field.nodes[node])


def test_a_graded_mesh_puts_small_elements_where_asked():
    model, face = square(side=4.0)
    zone = refine_at((0.0, 0.0, 0.0), size=0.1, radius=0.3)
    mesh = generate_mesh(model, target_size=0.5, refinements=[zone])

    bottom = mesh.nodes_of_edge[model.faces[face].loop[0].edge]
    stations = np.sort([mesh.nodes[node][0] for node in bottom])
    spacings = np.diff(stations)

    # Fine at the zone, coarse away from it, and no step is coarser than the
    # global target.
    assert spacings[0] == pytest.approx(0.1, rel=0.25)
    assert spacings[-1] > 3.0 * spacings[0]
    assert spacings.max() <= 0.5 * 1.2


def test_a_graded_mesh_stays_conformal_across_a_shared_edge():
    model = GeometryModel()
    points = model.add_points(
        [(0, 0, 0), (1, 0, 0), (2, 0, 0), (2, 1, 0), (1, 1, 0), (0, 1, 0)]
    )
    left_bottom = model.add_line(points[0], points[1])
    right_bottom = model.add_line(points[1], points[2])
    right = model.add_line(points[2], points[3])
    right_top = model.add_line(points[3], points[4])
    left_top = model.add_line(points[4], points[5])
    left = model.add_line(points[5], points[0])
    shared = model.add_line(points[1], points[4])
    first = model.add_face([left_bottom, shared, left_top, left])
    second = model.add_face([right_bottom, right, right_top, shared])

    mesh = generate_mesh(
        model,
        target_size=0.25,
        refinements=[refine_at((0.0, 0.0, 0.0), size=0.05, radius=0.1)],
    )
    left_nodes = set(mesh.nodes_on(model.entity_ref("face", first)))
    right_nodes = set(mesh.nodes_on(model.entity_ref("face", second)))
    shared_nodes = set(mesh.nodes_on(model.entity_ref("edge", shared)))
    assert (left_nodes & right_nodes) == shared_nodes


def test_seeding_still_closes_its_opposite_sides_when_graded():
    model, _face = square(side=4.0)
    seeding = solve_seeding(
        model,
        size_field=SizeField(
            model, 0.5, [refine_at((0, 0, 0), size=0.05, radius=0.2)]
        ),
    )
    for face in model.faces.values():
        sides = face.sides()
        assert seeding.side_divisions(sides[0]) == seeding.side_divisions(
            sides[2]
        )
        assert seeding.side_divisions(sides[1]) == seeding.side_divisions(
            sides[3]
        )


def test_a_seeding_carries_the_field_it_was_solved_against():
    model, _face = square()
    field = SizeField(model, 0.5, [refine_at((0, 0, 0), size=0.1, radius=0.2)])
    assert solve_seeding(model, size_field=field).size_field is field


def test_a_target_size_disagreeing_with_the_field_is_refused():
    model, _face = square()
    field = SizeField(model, 0.5)
    with pytest.raises(ValueError, match="one or the other"):
        solve_seeding(model, target_size=0.25, size_field=field)


# ----------------------------------------------------------------------
# quadratic elements
# ----------------------------------------------------------------------
def test_an_unknown_element_order_is_refused():
    model, _face = square()
    with pytest.raises(MeshError, match="unknown element order"):
        generate_mesh(model, target_size=0.5, order="cubic")
    with pytest.raises(ProjectError, match="unknown element order"):
        plate_project().set_element_order("cubic")


def test_a_quadratic_mesh_has_eight_node_shells():
    model, _face = square(side=2.0)
    mesh = generate_mesh(model, target_size=0.5, order="quadratic")

    assert mesh.is_quadratic
    assert len(mesh.quads) == 16
    for nodes in mesh.quads.values():
        assert len(nodes) == 8
        assert len(set(nodes)) == 8


def test_a_quadratic_mesh_adds_only_mid_side_nodes():
    """Same elements, mid-sides added, and no unused centre node."""

    model, _face = square(side=2.0)
    linear = generate_mesh(model, target_size=0.5)
    quadratic = generate_mesh(model, target_size=0.5, order="quadratic")

    assert len(quadratic.quads) == len(linear.quads)
    divisions = 4
    stations = 2 * divisions + 1
    centres = divisions * divisions
    assert quadratic.num_nodes == stations * stations - centres


def test_mid_side_nodes_sit_between_their_corners():
    model, _face = square(side=2.0)
    mesh = generate_mesh(model, target_size=0.5, order="quadratic")

    for element_id, nodes in mesh.quads.items():
        corners = mesh.corners_of(element_id)
        for index in range(4):
            first = mesh.nodes[corners[index]]
            second = mesh.nodes[corners[(index + 1) % 4]]
            middle = mesh.nodes[nodes[4 + index]]
            assert middle == pytest.approx(0.5 * (first + second))


def test_a_mid_side_node_on_an_arc_follows_the_arc():
    """A Q8 edge is curved, so its mid node belongs on the curve."""

    radius = 2.0
    model = GeometryModel()
    start = model.add_point(radius, 0.0, 0.0)
    end = model.add_point(radius, 0.0, 3.0)
    edge = model.add_line(start, end)
    model.revolve([edge], (0, 0, 0), (0, 0, 1), np.pi)
    mesh = generate_mesh(model, target_size=0.8, order="quadratic")

    positions = mesh.node_positions()
    worst = float(
        np.abs(np.linalg.norm(positions[:, :2], axis=1) - radius).max()
    )
    assert worst < 1.0e-9


def test_a_quadratic_mesh_stays_conformal():
    model = GeometryModel()
    points = model.add_points(
        [(0, 0, 0), (1, 0, 0), (2, 0, 0), (2, 1, 0), (1, 1, 0), (0, 1, 0)]
    )
    left_bottom = model.add_line(points[0], points[1])
    right_bottom = model.add_line(points[1], points[2])
    right = model.add_line(points[2], points[3])
    right_top = model.add_line(points[3], points[4])
    left_top = model.add_line(points[4], points[5])
    left = model.add_line(points[5], points[0])
    shared = model.add_line(points[1], points[4])
    first = model.add_face([left_bottom, shared, left_top, left])
    second = model.add_face([right_bottom, right, right_top, shared])

    mesh = generate_mesh(model, target_size=0.25, order="quadratic")
    left_nodes = set(mesh.nodes_on(model.entity_ref("face", first)))
    right_nodes = set(mesh.nodes_on(model.entity_ref("face", second)))
    shared_nodes = set(mesh.nodes_on(model.entity_ref("edge", shared)))
    assert (left_nodes & right_nodes) == shared_nodes


def test_a_quadratic_beam_spans_three_nodes():
    project = Project(name="beam")
    project.add_material(steel("S355", 0.020))
    project.add_beam_section(
        BeamSection(
            name="bar", profile="Flatbar", material="S355",
            flange_width=0.10, flange_thickness=0.02, web_direction=(0, 0, 1),
        )
    )
    geometry = project.geometry
    root = geometry.add_point(0.0, 0.0, 0.0)
    tip = geometry.add_point(2.0, 0.0, 0.0)
    edge = geometry.add_line(root, tip)
    project.assign_beam(edge, "bar")

    mesh = project.generate_mesh(0.5, order="quadratic")
    assert len(mesh.beams) == 4
    for span in mesh.beams.values():
        assert len(span) == 3
        start, middle, end = (mesh.nodes[node] for node in span)
        assert middle == pytest.approx(0.5 * (start + end))


def test_a_quadratic_beam_on_a_curve_is_refused():
    """The solver's B3 is straight-sided, so this fails at mesh time."""

    project = Project(name="arc")
    project.add_material(steel("S355", 0.020))
    project.add_beam_section(
        BeamSection(
            name="bar", profile="Flatbar", material="S355",
            flange_width=0.10, flange_thickness=0.02, web_direction=(0, 0, 1),
        )
    )
    geometry = project.geometry
    start = geometry.add_point(1.0, 0.0, 0.0)
    via = geometry.add_point(0.7071067811865476, 0.7071067811865476, 0.0)
    end = geometry.add_point(0.0, 1.0, 0.0)
    project.assign_beam(geometry.add_arc(start, via, end), "bar")

    with pytest.raises(MeshError, match="straight-sided"):
        project.generate_mesh(0.3, order="quadratic")
    # The same model meshes fine with 2-node beams.
    assert project.generate_mesh(0.3, order="linear").beams


def test_quadratic_shells_converge_faster_than_linear_ones():
    """The point of Q8: fewer elements for the same accuracy.

    Compared against each order's own converged answer rather than a thin-plate
    coefficient, because the two are not the same number -- the elements are
    Mindlin-Reissner and include transverse shear.
    """

    fine = plate_project()
    fine.set_element_order("quadratic")
    converged = solve_linear_static(fine, target_size=1 / 24).max_translation()[1]

    errors = {}
    for order in ("linear", "quadratic"):
        project = plate_project()
        project.set_element_order(order)
        coarse = solve_linear_static(project, target_size=1 / 4)
        assert len(coarse.built.mesh.quads) == 16
        errors[order] = abs(coarse.max_translation()[1] / converged - 1.0)

    assert errors["quadratic"] < 0.5 * errors["linear"]


def test_a_quadratic_traction_puts_the_whole_load_on(workspace):
    """Serendipity shares are negative at the corners and must still sum to 1."""

    from anysolver import assemble_load_vector

    for order in ("linear", "quadratic"):
        project = plate_project(side=2.0)
        project.set_element_order(order)
        project.load_cases.clear()
        project.load_case().add_surface_traction(
            project.face(next(iter(project.geometry.faces))), (0.0, 0.0, -1000.0)
        )
        solution = solve_linear_static(project, target_size=0.5)
        vector = assemble_load_vector(
            solution.built.fe_model, solution.built.load_case
        )
        if isinstance(vector, tuple):
            vector = vector[0]
        assert float(vector[2::6].sum()) == pytest.approx(-4000.0, rel=1e-9)


# ----------------------------------------------------------------------
# refinement inside a plate needs decomposition
# ----------------------------------------------------------------------
def test_a_zone_inside_a_plate_alone_changes_nothing():
    """The limit worth pinning down: a Coons interior follows its boundary.

    A size zone in the middle of a plate, far from every edge, cannot refine
    anything -- the interior grid is the blend of the four sides. This is why
    refining locally means decomposing, and why RefineForImpact is a geometry
    command rather than a meshing option.
    """

    model, _face = square(side=4.0)
    plain = generate_mesh(model, target_size=0.5)
    with_zone = generate_mesh(
        model,
        target_size=0.5,
        refinements=[refine_at((2.0, 2.0, 0.0), size=0.05, radius=0.1)],
    )
    assert with_zone.num_nodes == plain.num_nodes


def test_decomposing_first_lets_the_zone_bite():
    from anyfem.geometry.operations import split_face_at

    model, face = square(side=4.0)
    plain = generate_mesh(model, target_size=0.5)

    _edge, (lower, upper) = split_face_at(model, face, 0, 0.45)
    split_face_at(model, lower, 1, 0.45)
    split_face_at(model, upper, 1, 0.45)
    refined = generate_mesh(
        model,
        target_size=0.5,
        refinements=[refine_at((1.8, 1.8, 0.0), size=0.15, radius=0.3)],
    )
    assert refined.num_nodes > plain.num_nodes


# ----------------------------------------------------------------------
# impact refinement
# ----------------------------------------------------------------------
def struck_plate() -> Project:
    project = plate_project(side=1.0, thickness=0.008)
    return project


def sphere() -> Collision:
    return Collision(
        mass=200.0, radius=0.15, start=(0.5, 0.5, 0.6),
        direction=(0.0, 0.0, -1.0), speed=4.0,
    )


def test_the_impact_point_is_where_the_sphere_lands():
    from anyfem.model.collision import impact_point

    project = struck_plate()
    point = impact_point(project.generate_mesh(0.125), sphere())
    assert point[2] == pytest.approx(0.0)
    assert point[0] == pytest.approx(0.5, abs=0.07)
    assert point[1] == pytest.approx(0.5, abs=0.07)


def test_a_sphere_that_misses_is_still_refused():
    from anyfem.model.collision import impact_point

    project = struck_plate()
    away = Collision(
        mass=200.0, radius=0.05, start=(5.0, 5.0, 0.6),
        direction=(0.0, 0.0, -1.0), speed=4.0,
    )
    with pytest.raises(ValueError, match="misses the structure"):
        impact_point(project.generate_mesh(0.125), away)


def test_the_impact_zone_is_sized_to_the_sphere():
    from anyfem.model.collision import impact_refinement

    project = struck_plate()
    zone = impact_refinement(
        project.generate_mesh(0.125), sphere(), elements_per_radius=4.0
    )
    assert zone.size == pytest.approx(0.15 / 4.0)
    assert zone.radius == pytest.approx(1.5 * 0.15)
    assert zone.center is not None


def test_refining_for_impact_decomposes_and_concentrates_elements():
    project = struck_plate()
    before = project.generate_mesh(0.125)

    stack = CommandStack(project)
    outcome = stack.run(
        RefineForImpact(collision=sphere(), target_size=0.125)
    )
    after = project.generate_mesh(0.125)

    assert len(outcome["faces"]) > 1
    assert outcome["patch"] in project.geometry.faces
    assert after.num_nodes > before.num_nodes
    # Every new plate kept the section the original had.
    assert set(project.geometry.faces) <= set(project.face_sections)


def test_refining_for_impact_is_undoable():
    project = struck_plate()
    plates = set(project.geometry.faces)

    stack = CommandStack(project)
    stack.run(RefineForImpact(collision=sphere(), target_size=0.125))
    assert len(project.refinements) == 1

    stack.undo()
    assert set(project.geometry.faces) == plates
    assert project.refinements == []


def test_the_time_step_respects_the_smallest_contact_element():
    """A refined contact needs a finer step, and the timing must know it.

    Refining under the sphere raises the local frequencies without changing the
    contact period, so a step taken from the contact period alone is too coarse
    and the contact iteration diverges. The bound only binds when it has to.
    """

    from anyfem.model.collision import auto_timing

    project = struck_plate()
    mesh = project.generate_mesh(0.125)
    penalty = 1.68e11
    wave_speed = float(np.sqrt(210e9 / 7850.0))

    plain = auto_timing(mesh, sphere(), penalty_stiffness=penalty)
    # A coarse mesh: the transit time is longer than the step, so nothing moves.
    coarse = auto_timing(
        mesh, sphere(), penalty_stiffness=penalty,
        wave_speed=wave_speed, min_element_size=0.125,
    )
    assert coarse.dt == pytest.approx(plain.dt)

    fine = auto_timing(
        mesh, sphere(), penalty_stiffness=penalty,
        wave_speed=wave_speed, min_element_size=0.0375,
    )
    assert fine.dt < plain.dt
    assert fine.dt == pytest.approx(0.0375 / wave_speed)
    assert any("smallest element" in note for note in fine.notes)


def test_a_refined_impact_still_converges_on_the_recommended_penalty():
    from anyfem.solve import solve_impact

    project = struck_plate()
    CommandStack(project).run(
        RefineForImpact(collision=sphere(), target_size=0.125)
    )
    solution = solve_impact(project, collision=sphere(), target_size=0.125)

    assert solution.status == "completed"
    assert solution.info["contact_resolution"]["elements_per_radius"] > 3.0


def test_refining_the_contact_keeps_the_energy_and_sharpens_the_force():
    """The global response is already converged; the peak force is not.

    This is the whole argument for contact refinement: absorbed energy barely
    moves, while the reported peak contact force falls by a factor of several,
    because on the coarse mesh it was a property of the discretisation.
    """

    from anyfem.solve import solve_impact

    coarse = solve_impact(struck_plate(), collision=sphere(), target_size=0.125)

    project = struck_plate()
    CommandStack(project).run(
        RefineForImpact(collision=sphere(), target_size=0.125)
    )
    refined = solve_impact(project, collision=sphere(), target_size=0.125)

    assert refined.energy()["absorbed"] == pytest.approx(
        coarse.energy()["absorbed"], rel=0.10
    )
    assert refined.peak_contact_force < 0.5 * coarse.peak_contact_force


def test_an_impact_reports_how_well_the_contact_is_resolved():
    from anyfem.solve import solve_impact

    project = struck_plate()
    project.add_support(pinned(project.point(1)))
    coarse = solve_impact(project, collision=sphere(), target_size=0.125)
    resolution = coarse.info["contact_resolution"]

    assert resolution["element_size"] == pytest.approx(0.125, rel=0.2)
    # One element across the contact patch: the peak force this reports is a
    # property of the mesh, and the number says so.
    assert resolution["elements_per_radius"] < 2.0


# ----------------------------------------------------------------------
# commands and the project file
# ----------------------------------------------------------------------
def test_adding_a_refinement_is_undoable():
    project = plate_project()
    stack = CommandStack(project)
    zone = refine_around(project.point(1), 0.05, 0.1)

    stack.run(AddRefinement(refinement=zone))
    assert project.refinements == [zone]
    stack.undo()
    assert project.refinements == []


def test_setting_the_element_order_is_undoable():
    project = plate_project()
    stack = CommandStack(project)

    stack.run(SetElementOrder(order="quadratic"))
    assert project.element_order == "quadratic"
    assert project.generate_mesh(0.5).is_quadratic
    stack.undo()
    assert project.element_order == "linear"


def test_a_refinement_must_reference_a_real_entity():
    from anyfem.geometry.entities import EntityRef

    project = plate_project()
    with pytest.raises((ProjectError, KeyError, ValueError)):
        project.add_refinement(
            refine_around(EntityRef("face", 999), 0.05, 0.1)
        )


def test_meshing_controls_survive_a_project_file(workspace):
    from anyfem.io import load_project, save_project

    project = plate_project()
    project.set_element_order("quadratic")
    project.add_refinement(refine_around(project.point(1), 0.05, 0.1))
    project.add_refinement(refine_at((0.5, 0.5, 0.0), 0.04, 0.2, growth=1.8))

    restored = load_project(save_project(project, workspace / "model"))

    assert restored.element_order == "quadratic"
    assert len(restored.refinements) == 2
    assert restored.refinements[0].ref == project.refinements[0].ref
    assert restored.refinements[1].center == pytest.approx((0.5, 0.5, 0.0))
    assert restored.refinements[1].growth == pytest.approx(1.8)
    # And it meshes to the same thing.
    assert restored.generate_mesh(0.25).num_nodes == (
        project.generate_mesh(0.25).num_nodes
    )


def test_a_file_without_meshing_controls_still_loads(workspace):
    """Files written before these existed must keep opening."""

    import json

    from anyfem.io import load_project, project_to_dict, save_project

    project = plate_project()
    data = project_to_dict(project)
    del data["meshing"]
    path = workspace / "old.anyfem"
    path.write_text(json.dumps(data), encoding="utf-8")

    restored = load_project(path)
    assert restored.element_order == "linear"
    assert restored.refinements == []
