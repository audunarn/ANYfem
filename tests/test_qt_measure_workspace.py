"""Read-only measurement workspace driven through real Qt controls."""
from test_qt_geometry_actions import qapp, window
from anyfem import commands
from anyfem.io.project_file import project_to_dict
from anyfem.model.units import unit_profile
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
import pytest


def fixture(window):
    points = [window.run(commands.AddPoint(*xyz)) for xyz in ((-1,0,0),(2,4,0),(2,0,0),(-1,4,0))]
    face = window.run(commands.AddPlate((points[0],points[2],points[1],points[3])))
    return points, face


def select(window, kind, ids):
    window.selection.set_mode(kind)
    window.selection.select_many([window.project.geometry.entity_ref(kind, value) for value in ids])


def measure(window, quantity, qapp):
    panel = window.panels['Measure']
    window._select_workspace(list(window.panels).index('Measure'))
    panel.choice.setCurrentIndex(panel.choice.findData(quantity))
    qapp.processEvents()
    QTest.mouseClick(panel.apply, Qt.LeftButton); qapp.processEvents()
    return panel


def test_workspace_order_launcher_and_read_only(window, qapp):
    names = list(window.panels)
    assert names[names.index('Visualization') + 1:names.index('Visualization') + 3] == ['Measure','Sectioning']
    points, _ = fixture(window)
    select(window, 'vertex', points[:2])
    window.session.read_only = True
    editor = window.panels['Geometry']
    editor.choice.setCurrentIndex(editor.choice.findData('MeasureGeometry')); editor.refresh()
    assert not editor.fields and editor.apply.isEnabled()
    editor.apply.click()
    assert window.details.currentIndex() == names.index('Measure')
    before = project_to_dict(window.project)
    revision = window.session.revision
    undo = window.commands.can_undo
    panel = measure(window, 'distance', qapp)
    assert panel.measurement.value == pytest.approx(5)
    assert panel.result.text() == 'Distance: 5 m'
    assert project_to_dict(window.project) == before
    assert window.session.revision == revision and window.commands.can_undo == undo
    select(window, 'vertex', points[1:2])
    assert panel.measurement is None and 'cleared' in panel.result.text()


def test_coordinates_area_angle_and_unit_invalidation(window, qapp):
    points, face = fixture(window)
    select(window, 'vertex', points[:1])
    panel = measure(window, 'coordinates', qapp)
    assert 'X = -1' in panel.result.text() and 'Y = 0' in panel.result.text()
    select(window, 'face', [face])
    panel = measure(window, 'area', qapp)
    assert panel.measurement.value == pytest.approx(12)
    assert panel.result.text() == 'Area: 12 m²'
    window.project.units = unit_profile('SI-mm-N-MPa')
    panel.refresh()
    assert panel.measurement is None and 'cleared' in panel.result.text()
    panel.measure()
    assert panel.result.text() == 'Area: 12000000 mm²'
    edges = [item.edge for item in window.project.geometry.faces[face].loop]
    select(window, 'edge', edges[:2])
    panel = measure(window, 'angle', qapp)
    assert panel.measurement.value == pytest.approx(1.5707963267948966)
    assert 'unsigned midpoint-tangent' in panel.result.text()


def test_invalid_stale_and_project_reset(window, qapp):
    points, face = fixture(window)
    select(window, 'vertex', points[:1])
    panel = measure(window, 'distance', qapp)
    assert 'exactly 2' in panel.result.text() and panel.measurement is None
    select(window, 'face', [face]); panel = measure(window, 'radius', qapp)
    assert 'Cannot measure' in panel.result.text()
    select(window, 'vertex', points[:1]); panel = measure(window, 'coordinates', qapp)
    window.run(commands.MovePoint(points[0], -2, 0, 0))
    assert panel.measurement is None and 'cleared' in panel.result.text()
    panel.measure(); assert panel.measurement.value[0] == -2
    from anyfem.model import Project
    window._set_project(Project())
    assert panel.measurement is None and 'cleared' in panel.result.text()


def test_sampled_distance_label_and_unsupported_radius(window, qapp):
    _, face = fixture(window)
    edges = [item.edge for item in window.project.geometry.faces[face].loop]
    select(window, 'edge', [edges[0], edges[2]])
    panel = measure(window, 'distance', qapp)
    assert 'sampled minimum estimate' in panel.result.text()
    select(window, 'edge', [edges[0]])
    panel = measure(window, 'radius', qapp)
    assert panel.measurement is None and 'circular arc' in panel.result.text()


@pytest.mark.parametrize('quantity,kind,expected', [
    ('auto','vertex','Coordinates: X = -1, Y = 0, Z = 0 m'),
    ('length','edge','Length: 3 m'),
    ('perimeter','face','Perimeter: 14 m'),
    ('centroid','face','Centroid: X = 0.5, Y = 2, Z = 0 m'),
    ('normal','face','Normal: X = 0, Y = 0, Z = 1 (dimensionless)'),
])
def test_positive_tools_in_read_only_project(window, qapp, quantity, kind, expected):
    points, face = fixture(window)
    identifier = {'vertex': points[0], 'edge': window.project.geometry.faces[face].loop[0].edge, 'face':face}[kind]
    select(window, kind, [identifier])
    window.project.read_only_reason = 'Review geometry'
    before = project_to_dict(window.project)
    panel = measure(window, quantity, qapp)
    assert panel.result.text() == expected
    assert project_to_dict(window.project) == before


def test_circular_arc_radius(window, qapp):
    points = [window.run(commands.AddPoint(*xyz)) for xyz in ((2,0,0),(0,2,0),(-2,0,0))]
    edge = window.run(commands.AddArc(*points))
    select(window, 'edge', [edge])
    panel = measure(window, 'radius', qapp)
    assert panel.result.text() == 'Radius: 2 m'


def test_activation_from_mesh_preserves_geometry_refs_and_model(window, qapp):
    points, face = fixture(window)
    geometry = window.project.geometry
    edge = geometry.faces[face].loop[0].edge
    refs = (geometry.entity_ref('vertex', points[3]), geometry.entity_ref('edge', edge))
    # Model-tree mixed refs can be restored while the previous filter is mesh.
    window.selection.set_domain('mesh')
    window.selection.restore(refs)
    mesh_marker = object()
    window.mesh = mesh_marker
    window._view_mode = 'mesh'
    before = project_to_dict(window.project)
    revision = window.session.revision
    history = window.commands.history()
    panel = measure(window, 'distance', qapp)
    assert tuple(window.selection.ordered_items) == refs
    assert panel.measurement is not None and 'sampled' in panel.result.text()
    assert window.mesh is mesh_marker
    assert window.session.revision == revision and window.commands.history() == history
    assert project_to_dict(window.project) == before
    from anyfem.selection import MeshEntityRef
    window.selection.set_domain('mesh'); window.selection.restore((MeshEntityRef('node', 1),))
    panel.measure()
    assert panel.measurement is None and 'mesh nodes' in panel.result.text()
