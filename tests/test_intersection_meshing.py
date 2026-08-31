"""ANYfem orchestration of ANYmesher's automatic plate imprint."""

from __future__ import annotations

from anyfem import Project, steel
from anyfem.commands import AddCylinder, AddFeature, CommandStack
from anyfem.model import BeamSection
from anyfem.solve.build import build_fe_model
from anygeometry.serialization import to_dict as geometry_to_dict
from anymesher import mesh_to_dict


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


def test_project_meshes_exact_floating_plate_and_diagonal_extrusion():
    project = Project()
    first, second, third, fourth = project.geometry.add_points(
        ((0, 0, 0), (2, 0, 0), (0, 2, 0), (2, 2, 0)),
    )
    support = project.geometry.add_plate((first, second, fourth, third))
    fifth, sixth, seventh, eighth = project.geometry.add_points(
        ((0.5, 0.5, 0.5), (1.5, 0.5, 0.5), (0.5, 1.5, 0.5), (1.5, 1.5, 0.5)),
    )
    floating = project.geometry.add_plate((fifth, sixth, eighth, seventh))
    diagonal = project.geometry.add_line(second, third)
    before = set(project.geometry.faces)
    project.geometry.extrude((diagonal,), (0, 0, 1))
    wall = (set(project.geometry.faces) - before).pop()
    project.geometry.add_sheet((support,))
    project.geometry.add_sheet((floating,))

    options = {
        "strategy": "auto",
        "structure_preference": "balanced",
        "quality_policy": {
            "minimum_scaled_jacobian": 0.1,
            "maximum_aspect_ratio": 5.0,
            "minimum_angle": 20.0,
            "maximum_angle": 160.0,
            "maximum_warpage": 0.1,
        },
        "certification_mode": "interactive",
    }
    first = project.generate_mesh(0.25, **options)
    second = project.generate_mesh(0.25, **options)

    first_payload = mesh_to_dict(first)
    second_payload = mesh_to_dict(second)
    for payload in (first_payload, second_payload):
        payload.pop("hybrid_diagnostics")
        payload.pop("structural_preparation")
    assert first_payload == second_payload
    assert first.automatic_intersections == 2
    assert first.hybrid_diagnostics["structured_quality"]["accepted"] is True
    support_nodes = set(first.nodes_on(project.geometry.entity_ref("face", support)))
    floating_nodes = set(first.nodes_on(project.geometry.entity_ref("face", floating)))
    wall_nodes = set(first.nodes_on(project.geometry.entity_ref("face", wall)))
    assert support_nodes & wall_nodes
    assert floating_nodes & wall_nodes
    assert len(project.geometry.vertices) == 10
    assert len(project.geometry.edges) == 12
    assert len(project.geometry.faces) == 3
    assert len(project.geometry.sheets) == 2


def test_project_meshes_plate_on_generated_cylinder_ring_without_unassigned_beams():
    project = Project("cylinder deck")
    commands = CommandStack(project)
    commands.run(
        AddCylinder(
            kind="generator.cylinder",
            name="Cylinder",
            parameters={
                "radius": 0.5,
                "height": 2.0,
                "circumferential_segments": 12,
                "origin": (0.0, 0.0, 0.0),
                "axis": (0.0, 0.0, 1.0),
                "radial_direction": (1.0, 0.0, 0.0),
                "longitudinal_spacing": 0.5,
                "ring_spacing": 1.0,
            },
            label="add cylinder",
        )
    )
    commands.run(
        AddFeature(
            "generator.plate",
            name="Plate",
            parameters={
                "length": 2.0,
                "width": 2.0,
                "origin": (-1.0, -1.0, 1.0),
                "u_direction": (1.0, 0.0, 0.0),
                "v_direction": (0.0, 1.0, 0.0),
                "semantic_group": "shell",
            },
        )
    )
    before = geometry_to_dict(project.geometry)

    mesh = project.generate_mesh(
        0.25,
        strategy="auto",
        structure_preference="balanced",
        quality_policy={
            "minimum_scaled_jacobian": 0.1,
            "maximum_aspect_ratio": 5.0,
            "minimum_angle": 20.0,
            "maximum_angle": 160.0,
            "maximum_warpage": 0.1,
        },
        certification_mode="interactive",
    )

    cylinder_nodes = {
        node
        for face_id in range(1, 25)
        for node in mesh.nodes_on(project.geometry.entity_ref("face", face_id))
    }
    plate_nodes = set(mesh.nodes_on(project.geometry.entity_ref("face", 25)))
    assert len(cylinder_nodes & plate_nodes) == 12
    assert len(mesh.declared_plate_junction_edges) == 12
    assert mesh.automatic_intersections == 1
    assert not mesh.beams
    assert geometry_to_dict(project.geometry) == before


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
