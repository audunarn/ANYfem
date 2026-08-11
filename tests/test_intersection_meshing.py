"""ANYfem orchestration of ANYmesher's automatic plate imprint."""

from __future__ import annotations

from anyfem import Project, steel
from anyfem.model import BeamSection
from anyfem.solve.build import build_fe_model


def _plate(project: Project, points) -> int:
    vertices = [project.geometry.add_point(*point) for point in points]
    return project.geometry.add_plate(vertices)


def _beam(project: Project, start, end) -> int:
    first = project.geometry.add_point(*start)
    second = project.geometry.add_point(*end)
    return project.geometry.add_line(first, second)


def test_project_meshes_crossing_plates_with_shared_intersection_nodes():
    project = Project()
    horizontal = _plate(
        project,
        ((-1, 0, 0), (1, 0, 0), (1, 2, 0), (-1, 2, 0)),
    )
    vertical = _plate(
        project,
        ((0, 0, -1), (0, 2, -1), (0, 2, 1), (0, 0, 1)),
    )
    project.add_material(steel())
    project.add_plate_section("plate", 0.01, "S355")
    project.assign_plate(horizontal, "plate")
    project.assign_plate(vertical, "plate")

    mesh = project.generate_mesh(0.5)

    shared = set(mesh.nodes_on(project.geometry.entity_ref("face", horizontal))) & set(
        mesh.nodes_on(project.geometry.entity_ref("face", vertical))
    )
    assert len(shared) == 5
    assert mesh.automatic_intersections == 1
    # Design faces remain the original user-visible owners.
    assert set(project.geometry.faces) == {horizontal, vertical}

    built = build_fe_model(
        project,
        mesh,
        load_case=None,
        require_loads=False,
        require_supports=False,
    )
    assert len(built.fe_model.mesh.elements) == len(mesh.shells)


def test_beam_crossing_shell_is_connected_and_builds_as_solver_mpc():
    project = Project()
    plate = _plate(
        project,
        ((0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)),
    )
    beam = _beam(project, (0.3, 0.4, -1), (0.3, 0.4, 1))
    project.add_material(steel())
    project.add_plate_section("plate", 0.01, "S355")
    project.add_beam_section(
        BeamSection(
            name="beam",
            profile="Flatbar",
            material="S355",
            flange_width=0.01,
            flange_thickness=0.1,
        )
    )
    project.assign_plate(plate, "plate")
    project.assign_beam(beam, "beam")

    mesh = project.generate_mesh(0.5)

    assert mesh.automatic_beam_connections >= 1
    assert any(
        len(coupling.plate_nodes) > 1 for coupling in mesh.couplings.values()
    )
    built = build_fe_model(
        project,
        mesh,
        load_case=None,
        require_loads=False,
        require_supports=False,
    )
    assert len(built.fe_model.mesh.elements) == (
        len(mesh.shells) + len(mesh.beams) + len(mesh.couplings)
    )
