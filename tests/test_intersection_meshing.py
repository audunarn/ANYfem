"""ANYfem orchestration of ANYmesher's automatic plate imprint."""

from __future__ import annotations

import pytest

from anyfem import Project, steel
from anyfem.commands import AddCylinder, AddFeature, CommandStack
from anyfem.model import BeamSection
from anyfem.solve.build import build_fe_model
from anygeometry.serialization import to_dict as geometry_to_dict
from anymesher import MeshAutomationOptions, MeshRecoveryIncomplete, mesh_to_dict


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


def test_project_accepts_declared_three_plate_junction_without_moving_boundaries():
    project = Project()
    geometry = project.geometry
    first, second, third, fourth = geometry.add_points(
        ((0.0, 0.0, 0.0), (4.0, 0.0, 0.0), (4.0, 2.0, 0.0), (0.0, 2.0, 0.0))
    )
    support = geometry.add_plate((first, second, third, fourth))
    support_edge = geometry.faces[support].loop[0].edge
    edge_wall = geometry.extrude((support_edge,), (0.0, 0.0, 1.0))[0]
    diagonal_start, diagonal_end = geometry.add_points(
        ((0.5, 0.35, 0.0), (3.5, 1.65, 0.0))
    )
    diagonal = geometry.add_line(diagonal_start, diagonal_end)
    top_end, top_start = geometry.add_points(
        ((3.5, 1.80, 1.0), (0.5, 0.35, 1.0))
    )
    diagonal_wall = geometry.add_face(
        (
            diagonal,
            geometry.add_line(diagonal_end, top_end),
            geometry.add_line(top_end, top_start),
            geometry.add_line(top_start, diagonal_start),
        )
    )
    geometry.add_sheet((support,))
    geometry.add_sheet((edge_wall,))
    geometry.add_sheet((diagonal_wall,))
    before = geometry_to_dict(geometry)
    project.set_native_triangulation_backend("python")

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

    assert geometry_to_dict(geometry) == before
    assert mesh.hybrid_diagnostics["structured_quality"]["accepted"] is True
    # The batch decomposition can satisfy the quality gate directly. If a
    # repair is necessary it still has to preserve protected boundary nodes.
    repair = mesh.hybrid_diagnostics.get("junction_growth_repair")
    if repair is not None:
        assert repair["committed"] is True
        assert repair["final_quality"]["growth_violation_count"] == 0
        assert repair["final_quality"]["maximum_aspect_ratio"] <= 5.0
    protected_nodes = {
        node_id
        for sequence in mesh.nodes_of_edge.values()
        for node_id in sequence
    }
    if repair is not None:
        assert protected_nodes.isdisjoint(repair["moved_node_ids"])
    for edge_id, wall_id in (
        (support_edge, edge_wall),
        (diagonal, diagonal_wall),
    ):
        shared = set(mesh.nodes_on(geometry.entity_ref("edge", edge_id)))
        assert shared
        incident_faces = {
            geometry.face_uses[face_use_id].face_id
            for face_use_id in geometry.face_uses_using_edge(edge_id)
        }
        assert wall_id in incident_faces
        assert all(
            shared <= set(mesh.nodes_on(geometry.entity_ref("face", face_id)))
            for face_id in incident_faces
        )


@pytest.mark.parametrize(
    ("target_size", "expected_junction_segments"),
    ((0.25, 12), (0.20, 24)),
)
def test_project_meshes_plate_on_generated_cylinder_ring_without_unassigned_beams(
    target_size,
    expected_junction_segments,
):
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
        target_size,
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
    shared = cylinder_nodes & plate_nodes
    assert len(shared) >= expected_junction_segments
    joint_edges = tuple(edge for edge in mesh.declared_plate_junction_edges
                        if set(edge) <= shared)
    assert len(joint_edges) == len(shared)
    from collections import Counter
    degree = Counter(node for edge in joint_edges for node in edge)
    assert set(degree) == shared and set(degree.values()) == {2}
    import numpy as np
    import math
    assert all(abs(mesh.nodes[node][2]-1.) < 1e-10
               and abs(np.linalg.norm(mesh.nodes[node][:2])-.5) < 1e-10 for node in shared)
    angles = np.sort([math.atan2(mesh.nodes[node][1],mesh.nodes[node][0]) for node in shared])
    assert np.max(np.diff(np.r_[angles, angles[0]+2*math.pi]))*.5 <= target_size+1e-10
    assert mesh.structural_preparation["qualified_s3"]["status"] == "ADMITTED"
    assert mesh.automatic_intersections == 1
    assert not mesh.beams
    assert geometry_to_dict(project.geometry) == before


def _automatic_deck_project() -> Project:
    project = Project("automatic linear cylinder deck")
    commands = CommandStack(project)
    commands.run(AddCylinder(
        kind="generator.cylinder", name="Cylinder",
        parameters={
            "radius": 0.5, "height": 2.0, "circumferential_segments": 12,
            "origin": (0.0, 0.0, 0.0), "axis": (0.0, 0.0, 1.0),
            "radial_direction": (1.0, 0.0, 0.0),
            "longitudinal_spacing": 0.5, "ring_spacing": 1.0,
        },
    ))
    commands.run(AddFeature(
        "generator.plate", name="Plate", parameters={
            "length": 2.0, "width": 2.0, "origin": (-1.0, -1.0, 1.0),
            "u_direction": (1.0, 0.0, 0.0),
            "v_direction": (0.0, 1.0, 0.0),
            "semantic_group": "shell",
        },
    ))
    return project


def test_quad_first_linear_deck_produces_admitted_automatic_mesh():
    project = _automatic_deck_project()
    before = geometry_to_dict(project.geometry)

    mesh = project.generate_mesh(
        0.25, strategy="quad_first", order="linear",
        automation=MeshAutomationOptions(),
    )

    record = mesh.hybrid_diagnostics["automation"]
    assert record["status"] == "ready"
    assert record["selected_method"] in ("quad_first", "auto", "native")
    assert record["attempts"][-1]["status"] == "selected"
    assert mesh.structural_preparation["qualified_s3"]["status"] == "ADMITTED"
    assert mesh.order == "linear"
    assert geometry_to_dict(project.geometry) == before


def test_unadmitted_candidate_is_inspection_only(monkeypatch):
    import anymesher.recovery as recovery
    from anymesher import Mesh
    from anymesher.s3_quality import evaluate_s3_admission
    from anymesher.s3_repair import S3RepairError
    import numpy as np

    project = _automatic_deck_project()
    monkeypatch.setattr(
        recovery, "_attempt_options",
        lambda first, _fallback: (("quad_first", dict(first)),),
    )
    generate = recovery.generate_hybrid_mesh_result
    def reject_admission(*args, **kwargs):
        candidate = generate(*args, **kwargs)
        bad = Mesh(nodes={0:np.array([0.,0.,0.]),1:np.array([1.,0.,0.]),
                          2:np.array([.5,.001,0.])},tris={0:(0,1,2)})
        failure = S3RepairError("injected admission refusal",attempts=(),
                                admission=evaluate_s3_admission(bad))
        failure.inspectable_result = candidate
        raise failure
    # The improved arrangement no longer needs to fail this particular deck.
    # Inject the owner admission refusal to retain the fail-closed boundary test.
    monkeypatch.setattr(recovery,"generate_hybrid_mesh_result",reject_admission)
    mesh = project.generate_mesh(
        0.25, strategy="quad_first", order="linear",
        automation=MeshAutomationOptions(),
    )

    assert mesh.hybrid_diagnostics["automation"]["status"] == "inspection_only"
    assert mesh.hybrid_diagnostics["automation"]["attempts"][-1]["problem_element_ids"]
    with pytest.raises(ValueError, match="inspection-only mesh"):
        build_fe_model(
            project, mesh, load_case=None,
            require_loads=False, require_supports=False,
        )


def test_strict_method_retains_typed_s3_failure(monkeypatch):
    from anymesher.s3_repair import S3RepairError
    import anymesher.recovery as recovery

    project = _automatic_deck_project()
    before = geometry_to_dict(project.geometry)
    refusal = S3RepairError("injected strict-method refusal",attempts=())
    def reject(*args, **kwargs):
        raise refusal
    monkeypatch.setattr(recovery,"generate_hybrid_mesh_result",reject)
    with pytest.raises(S3RepairError):
        project.generate_mesh(
            0.25, strategy="quad_first", order="linear",
            automation=MeshAutomationOptions(strict_method=True),
        )
    assert geometry_to_dict(project.geometry)==before


def test_automatic_mesh_budget_expires_without_publishing_result():
    project = _automatic_deck_project()
    with pytest.raises(MeshRecoveryIncomplete, match="time budget expired"):
        project.generate_mesh(
            0.25, strategy="quad_first", order="linear",
            automation=MeshAutomationOptions(max_seconds=1e-12),
        )


def test_beam_crossing_shell_has_shared_node_and_solver_connectivity():
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
    common = set(mesh.nodes_on(project.geometry.entity_ref("edge",beam))) & set(
        mesh.nodes_on(project.geometry.entity_ref("face",plate)))
    assert len(common)==1
    import numpy as np
    assert np.allclose(mesh.nodes[next(iter(common))],(.3,.4,0.),rtol=0,atol=1e-12)
    assert sum(next(iter(common)) in nodes for nodes in mesh.beams.values())==2
    assert not mesh.couplings
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


def test_quad_first_quadratic_shared_contact_reaches_solver_unchanged():
    project = Project("RA1 point coupling")
    plate = _plate(project, ((0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)))
    beam = _beam(project, (0.3, 0.4, -1), (0.3, 0.4, 1))
    project.add_material(steel())
    project.add_plate_section("plate", 0.01, "S355")
    project.add_beam_section(
        BeamSection(name="beam", profile="Flatbar", material="S355",
                    flange_width=0.01, flange_thickness=0.1)
    )
    project.assign_plate(plate, "plate")
    project.assign_beam(beam, "beam")
    mesh = project.generate_mesh(0.5, strategy="quad_first", order="quadratic")
    assert mesh.hybrid_diagnostics["high_order_geometry"]["status"] == "CERTIFIED_POSITIVE"
    assert not mesh.couplings
    common = set(mesh.nodes_on(project.geometry.entity_ref("edge",beam))) & set(
        mesh.nodes_on(project.geometry.entity_ref("face",plate)))
    assert len(common)==1
    assert sum(next(iter(common)) in nodes for nodes in mesh.beams.values())==2
    from anymesher.serialize import mesh_from_dict
    assert mesh_from_dict(mesh_to_dict(mesh)).couplings == mesh.couplings
    built = build_fe_model(
        project, mesh, load_case=None, require_loads=False, require_supports=False,
    )
    assert len(built.fe_model.mesh.elements) == len(mesh.shells) + len(mesh.beams)
