"""The real barge cut retains exact domain topology and display face provenance."""
import json
import numpy as np
import pytest
from anyfem import commands
from anyfem.application.examples import floating_barge_large
from anyfem.io.project_file import project_from_dict, project_to_dict
from anyfem.ui.scene import build_geometry_scene


@pytest.fixture(scope="module")
def cut_barge():
    project = floating_barge_large()
    face = next(i for i, value in project.geometry.faces.items() if len(value.holes) == 3)
    before = {
        "members": dict(project.geometry.members),
        "assignments": dict(project.section_assignments),
        "edge_sections": dict(project.edge_sections),
        "edges": len(project.geometry.edges),
        "attachments": len(project.geometry.attachments),
    }
    result = commands.CommandStack(project).run(commands.CutPlate(
        face, origin=(0, 0, .2), normal=(0, 0, 1)))
    return project, result, before


def test_barge_cut_preserves_domain_and_reopens(cut_barge, tmp_path):
    project, result, before = cut_barge
    geometry = project.geometry
    assert not geometry.validate_topology()
    assert not project.resolve_section_assignments(strict=True)
    assert project.section_assignments == before["assignments"]
    assert geometry.members == before["members"]
    assert project.edge_sections == before["edge_sections"]
    assert len(geometry.attachments) >= before["attachments"]
    assert sorted(len(geometry.faces[i].holes) for i in result.target_faces) == [0, 3]
    assert len(result.trace_edges) == 1
    assert len(geometry.edges) - before["edges"] == 3  # trace plus split outer sides
    for edge in result.trace_edges:
        assert set(geometry.faces_using_edge(edge)) == set(result.target_faces)
        assert np.allclose(geometry.sample_edge(edge, np.linspace(0, 1, 9))[:, 2], .2)
    saved = tmp_path / "barge.json"
    saved.write_text(json.dumps(project_to_dict(project)), encoding="utf-8")
    reopened = project_from_dict(json.loads(saved.read_text(encoding="utf-8")))
    assert not reopened.geometry.validate_topology()
    assert not reopened.resolve_section_assignments(strict=True)
    assert reopened.section_assignments == before["assignments"]
    assert reopened.edge_sections == before["edge_sections"]
    commands.CommandStack(reopened).run(commands.AddPoint(30, 30, 30))
    assert not reopened.resolve_section_assignments(strict=True)
    assert sorted(len(reopened.geometry.faces[i].holes) for i in result.target_faces) == [0, 3]


def test_cut_face_display_groups_are_exact_owner_faces(cut_barge):
    project, result, _ = cut_barge
    scene = build_geometry_scene(project)
    # One collapsed cut feature retains both exact owners and distinct outlines.
    patch = next(p for p in scene.faces if set(p.owners or (p.ref,)) == {
        project.geometry.entity_ref("face", i) for i in result.target_faces})
    assert len(patch.polygons) > 100
    assert len(patch.outline_groups) == len(patch.polygons)
    assert set(patch.outline_groups) == set(result.target_faces)
    for index, group in enumerate(patch.outline_groups):
        assert patch.polygon_owners[index] == (project.geometry.entity_ref("face", group),)
    # The actual cut is also an explicit selectable geometry line.
    assert set(result.trace_edges) <= {line.ref.id for line in scene.lines if line.ref.kind == "edge"}
    # Hole boundaries survive display triangulation; no triangle covers the centre.
    from shapely.geometry import Polygon, Point
    upper = next(i for i in result.target_faces if project.geometry.faces[i].holes)
    triangles = [Polygon(p[:, 1:]) for p, owner in zip(patch.polygons, patch.outline_groups) if owner == upper]
    for y in (-3.4, .1, 3.6):
        assert not any(triangle.contains(Point(y, 1.25)) for triangle in triangles)


def test_barge_cut_ordinary_mesh(cut_barge):
    project, _, _ = cut_barge
    reopened = project_from_dict(project_to_dict(project))
    assert reopened.generate_mesh(.25).num_elements > 0


def test_long_side_cut_preserves_loads_and_undo():
    project = floating_barge_large()
    port_y = min(project.geometry.vertex_position(i)[1] for i in project.geometry.vertices)
    face = next(i for i, f in project.geometry.faces.items()
                if not f.holes and all(np.isclose(project.geometry.vertex_position(
                    project.geometry.oriented_start_vertex(e))[1], port_y) for e in f.loop))
    before = project_to_dict(project)
    before_faces = set(project.geometry.faces)
    before_assignments = dict(project.section_assignments)
    stack = commands.CommandStack(project)
    result = stack.run(commands.CutPlate(face, origin=(0, 0, .2), normal=(0, 0, 1)))
    assert not project.resolve_section_assignments(strict=True)
    assert project.section_assignments == before_assignments
    assert not project.geometry.validate_topology()
    assert len(result.target_faces) == 2 and len(result.trace_edges) == 1
    cut = project_to_dict(project)
    assert cut["load_cases"] == before["load_cases"]
    stack.undo()
    assert set(project.geometry.faces) == before_faces
    assert project.section_assignments == before_assignments
    stack.redo()
    assert not project.resolve_section_assignments(strict=True)
