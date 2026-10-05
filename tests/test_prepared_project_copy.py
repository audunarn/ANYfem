"""Owner-container copying independent of unpublished S3 qualification."""
import pytest
from anygeometry import GeometryModel
from anyfem import Project
from anyfem.model.materials import steel
from anyfem.prepared_mesh_preassembly import _copy_project_for_preassembly
from anyfem.prepared_reference_scope import _project_inputs


@pytest.mark.parametrize('normalize', [False, True])
def test_detached_project_copy_preserves_authored_legacy_input(normalize):
    geometry = GeometryModel()
    face = geometry.add_plate(geometry.add_points(((0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0))))
    geometry.add_sheet((face,))
    project = Project(geometry=geometry)
    project.add_material(steel('S355', .01))
    project.add_plate_section('shell', .01, 'S355')
    project.face_sections[face] = 'shell'
    assert not project.section_assignments
    before = _project_inputs(project)
    staged = _copy_project_for_preassembly(project)
    assert staged is not project and staged.geometry is not geometry
    assert _project_inputs(staged) == before
    if normalize:
        staged.validate(require_loads=False, require_supports=False)
        assert staged.section_assignments
    else:
        staged.geometry.add_point(2, 2, 2)
        staged.face_sections.clear()
    assert _project_inputs(project) == before
    assert not project.section_assignments
