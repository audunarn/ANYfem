"""The menu's example is a connected, editable and persistable section."""
import numpy as np

from anyfem.application.examples import floating_barge_section
from anyfem.io.project_file import project_from_dict, project_to_dict


def test_barge_section_roundtrip():
    project = floating_barge_section()
    positions = np.array([project.geometry.vertex_position(identifier) for identifier in project.geometry.vertices])
    np.testing.assert_allclose(positions.max(axis=0)-positions.min(axis=0), (5, 11, 2.5))
    assert len(project.geometry.vertices) == 12
    assert len(project.geometry.faces) == len(project.face_sections) == 9
    restored = project_from_dict(project_to_dict(project))
    assert restored.face_sections == project.face_sections
    sets = {region.name: region for region in restored.regions}
    for name in ("Deck", "Bottom", "Port side", "Starboard side", "Transverse bulkhead"):
        assert name in sets
    assert restored.plate_sections["Deck 12 mm"].thickness == .012
    assert restored.plate_sections["Hull 10 mm"].thickness == .010
    assert restored.plate_sections["Bulkhead 8 mm"].thickness == .008
