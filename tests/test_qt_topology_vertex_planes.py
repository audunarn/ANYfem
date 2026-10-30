"""Quick selectors distinguish authored points, topology vertices and planes."""
from test_qt_geometry_actions import qapp, window
from PySide6.QtCore import Qt, QPoint
from PySide6.QtTest import QTest
from anyfem import commands
from anyfem.io.project_file import project_to_dict
from anyfem.presentation.points import topology_vertex_ids
from anyfem.selection import SelectionFilter


def test_vertex_selector_picks_hidden_intersection_and_updates_after_undo(window,qapp):
    from anyfem.application.examples import floating_barge_section
    p=floating_barge_section();window._set_project(p)
    face=next(iter(p.geometry.faces))
    # A horizontal split of a vertical side creates non-authored vertices.
    face=next(fid for fid in p.geometry.faces if abs(p.geometry.face_normal(fid,.5,.5)[1])>.9)
    before_vertices=set(p.geometry.vertices)
    cut=window.run(commands.CutPlate(face,origin=(0,0,.2),normal=(0,0,1)))
    created=set(p.geometry.vertices)-before_vertices
    endpoints={v for e in cut.trace_edges for v in (p.geometry.edges[e].start,p.geometry.edges[e].end)}
    target=next(iter(created & endpoints))
    window._quick_select('vertex')
    assert target not in {point.ref.id for point in window.viewport._scene.points if point.ref is not None}
    window.quick_selection_actions['topology_vertex'].trigger()
    assert window.selection.filter.flavor=='topology_vertex'
    assert target in window.tree._rows['Points']
    assert {point.ref.id for point in window.viewport._scene.points if point.ref is not None} == set(topology_vertex_ids(p.geometry))
    window.viewport.set_view('iso');window.viewport.fit();qapp.processEvents()
    surface=window.viewport.canvas.event_widget
    x,y,_=window.viewport.canvas.project_point(p.geometry.vertex_position(target))
    ratio=surface.devicePixelRatioF()
    QTest.mouseClick(surface,Qt.LeftButton,pos=QPoint(round(x/ratio),round(y/ratio)));qapp.processEvents()
    assert p.geometry.entity_ref('vertex',target) in window.selection.items
    window.undo()
    assert target not in window.selection.filter.eligible_ids
    assert target not in window.tree._rows['Points']
    window.redo();assert target in window.selection.filter.eligible_ids
    window._quick_select('vertex')
    assert not window.selection.filter.flavor
    assert window.quick_selection_actions['vertex'].isChecked()


def test_topology_vertices_exclude_arc_control_only_points(window):
    g=window.project.geometry
    a,b,c=g.add_points(((1,0,0),(0,1,0),(-1,0,0)))
    g.add_arc(a,b,c)
    window._quick_select('topology_vertex')
    assert window.selection.filter.eligible_ids == frozenset((a,c))
    assert {point.ref.id for point in window.viewport._scene.points} == {a,c}
    assert not window.selection.filter.accepts(g.entity_ref('vertex',b))
    assert SelectionFilter(kinds=frozenset({'vertex'}),flavor='topology_vertex',eligible_ids={a}).accepts(g.entity_ref('vertex',a))


def test_plane_plain_button_tree_and_canvas_select_only_existing_planes(window,qapp):
    from anyfem.model.coordinates import CoordinateSystem
    p=window.project
    plane=CoordinateSystem('Inspection plane',origin=(12,0,0),axis=(0,0,1),reference=(1,0,0))
    p.coordinate_systems[plane.id]=plane
    window.refresh_all()
    marker=object();window.mesh=marker
    window.session.read_only=True
    before=project_to_dict(p);revision=window.session.revision;history=window.commands.history()
    action=window.quick_selection_actions['plane']
    assert action.menu() is None
    window._quick_select('topology_vertex')
    action.trigger();qapp.processEvents()
    assert not window.selection.filter.flavor
    assert action.isChecked() and window.selection_mode.value()=='plane'
    assert {'global','sketch-xz','sketch-yz',plane.id} <= set(window.tree._rows['Construction planes'])
    assert not window.viewport.construction_active
    window.viewport.set_view('top')
    # Reference planes are session overlays, not structural plate faces.
    window.viewport.canvas.set_camera_target(window.viewport._point3d(12,0,0))
    qapp.processEvents()
    x,y,_=window.viewport.canvas.project_point((12,0,0))
    surface=window.viewport.canvas.event_widget;ratio=surface.devicePixelRatioF()
    QTest.mouseClick(surface,Qt.LeftButton,pos=QPoint(round(x/ratio),round(y/ratio)));qapp.processEvents()
    assert window.session.active_workplane.coordinate_system_id==plane.id
    assert not window.selection.items and not window.viewport.construction_active
    assert window.mesh is marker and window.session.revision==revision and window.commands.history()==history
    assert project_to_dict(p)==before
    QTest.keyClick(surface,Qt.Key_Escape);qapp.processEvents()
    assert not action.isChecked() and not window._plane_selection_active
    window._quick_select('face')
    assert not action.isChecked()
    window._refresh_plane_menu()
    assert not any('Add construction' in item.text() for item in window.plane_menu.actions())


def test_plane_selection_exits_before_starting_sketch(window,qapp):
    window._quick_select('plane');window._choose_plane('sketch-xz')
    window.notebook.select('Construction');qapp.processEvents()
    assert not window._plane_selection_active
    assert not window.quick_selection_actions['plane'].isChecked()
    panel=window.panels['Construction']
    panel.buttons['Start sketch'].click();qapp.processEvents()
    assert window.viewport.construction_active
    window.viewport.cancel_construction()


def test_plane_selection_refusal_restores_button_state(window,qapp):
    import pytest
    window.notebook.select('Construction')
    window.panels['Construction'].buttons['Start sketch'].click();qapp.processEvents()
    assert window.viewport.construction_active
    window.quick_selection_actions['plane'].setChecked(True)
    with pytest.raises(ValueError,match='Finish or cancel'):
        window._quick_select('plane')
    assert not window.quick_selection_actions['plane'].isChecked()
    assert not window._plane_selection_active
    window.viewport.cancel_construction()
