"""The menu's example is a connected, editable and persistable section."""
import numpy as np

from anyfem.application.examples import floating_barge_section
from anyfem.io.project_file import project_from_dict, project_to_dict


def test_barge_section_roundtrip():
    project = floating_barge_section()
    positions = np.array([project.geometry.vertex_position(identifier) for identifier in project.geometry.vertices])
    np.testing.assert_allclose(positions.max(axis=0)-positions.min(axis=0), (5, 11, 2.5))
    assert len(project.geometry.faces) == len(project.face_sections) == 184
    assert not project.geometry.validate_topology()
    restored = project_from_dict(project_to_dict(project))
    assert restored.face_sections == project.face_sections
    assert restored.edge_sections == project.edge_sections
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
        # The beam is a plate boundary, rather than an unattached duplicate line.
        assert geometry.faces_using_edge(identifier)
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
