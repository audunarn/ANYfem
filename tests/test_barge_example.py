"""The menu's example is a connected, editable and persistable section."""
import numpy as np

from anyfem.application.examples import floating_barge_section
from anyfem.io.project_file import project_from_dict, project_to_dict


def test_barge_section_roundtrip():
    project = floating_barge_section()
    positions = np.array([project.geometry.vertex_position(identifier) for identifier in project.geometry.vertices])
    np.testing.assert_allclose(positions.max(axis=0)-positions.min(axis=0), (5, 11, 2.5))
    assert len(project.geometry.faces) == len(project.face_sections) == 5
    assert not project.geometry.validate_topology()
    restored = project_from_dict(project_to_dict(project))
    assert restored.face_sections == project.face_sections
    assert restored.edge_sections == project.edge_sections
    assert restored.geometry.attachments == project.geometry.attachments
    sets = {region.name: region for region in restored.regions}
    assert {region.name: region.definition for region in restored.regions} == {
        region.name: region.definition for region in project.regions}
    for name in ("Deck", "Bottom", "Port side", "Starboard side", "Transverse bulkhead"):
        assert name in sets
    assert restored.plate_sections["Deck 12 mm"].thickness == .012
    assert restored.plate_sections["Hull 10 mm"].thickness == .010
    assert restored.plate_sections["Bulkhead 8 mm"].thickness == .008


def test_beam_spacing_shared_topology_and_orientation():
    project = floating_barge_section()
    geometry = project.geometry
    stiffeners, girders = [], []
    for identifier, name in project.edge_sections.items():
        edge = geometry.edges[identifier]
        points = np.array([geometry.vertex_position(edge.start), geometry.vertex_position(edge.end)])
        members = geometry.members_using_edge(identifier)
        assert len(members) == 1
        attachments = geometry.attachments_for_member(members[0])
        assert len(attachments) == 1
        attachment = geometry.attachments[attachments[0]]
        assert attachment.kind.value == "member_on_face"
        for point in points:
            assert geometry.project_to_face(attachment.target_id, point)[2] < 1.e-9
        section = project.beam_sections[name]
        axis = points[1]-points[0]
        assert abs(np.dot(axis, section.web_direction)) < 1.e-12
        if "stiffeners" in name:
            assert np.allclose(axis[1:], 0)
            stiffeners.append((name, points))
        else:
            assert points[0,0] == points[1,0]
            girders.append(points)
    assert len(stiffeners) == 108 and len(girders) == 80
    for surface, coordinate in (("Deck",1),("Bottom",1),("Port side",2),("Starboard side",2)):
        positions = sorted({round(points[0,coordinate], 6) for name,points in stiffeners if name.startswith(surface)})
        np.testing.assert_allclose(np.diff(positions), .7)
    np.testing.assert_allclose(sorted({points[0,0] for points in girders}), (0,4))
    assert {region.name for region in project.regions} >= {
        "Longitudinal stiffeners — 700 mm", "Transverse girders — 4000 mm"}
    joints = [attachment for attachment in geometry.attachments.values()
              if attachment.kind.value == "vertex_on_face"]
    assert len(joints) == 44
    for attachment in joints:
        point = geometry.vertex_position(attachment.source_id)
        assert point[0] == 2.5
        assert geometry.project_to_face(attachment.target_id, point)[2] < 1.e-9


def test_continuous_plates_and_only_user_point_markers():
    from anyfem.ui.scene import build_geometry_scene
    from anyfem.presentation.points import displayed_geometry_point_ids
    from anyfem import commands
    project = floating_barge_section()
    scene = build_geometry_scene(project, show_beam_sections=False)
    assert len(scene.faces) == 5
    assert len(scene.points) == 8
    assert all(point.position[0] in (0,5) and abs(point.position[1]) == 5.5
               and point.position[2] in (0,2.5) for point in scene.points)
    restored = project_from_dict(project_to_dict(project))
    assert displayed_geometry_point_ids(restored.geometry) == displayed_geometry_point_ids(project.geometry)
    stack = commands.CommandStack(project)
    identifier = stack.run(commands.AddPoint(6,0,3))
    assert identifier in displayed_geometry_point_ids(project.geometry)
    assert len(build_geometry_scene(project, show_beam_sections=False).points) == 9
    stack.undo()
    assert len(build_geometry_scene(project, show_beam_sections=False).points) == 8


def test_explicit_point_policy_omits_generated_mesh_node_markers():
    from types import SimpleNamespace
    from anyfem.model.project import Project
    from anyfem.presentation.points import displayed_geometry_point_ids
    from anyfem.ui.scene import build_mesh_scene
    project = floating_barge_section()
    identifier = displayed_geometry_point_ids(project.geometry)[0]
    mesh = SimpleNamespace(nodes={1: project.geometry.vertex_position(identifier), 2: np.array((2.,1.,1.))},
                           node_of_vertex={identifier: 1}, shells={}, beams={}, elements_of_face={}, elements_of_edge={})
    assert [point.ref.id for point in build_mesh_scene(project, mesh).points] == [1]
    # Without the explicit policy, mesh-only nodes retain their normal markers.
    assert len(build_mesh_scene(Project(), mesh).points) == 2
