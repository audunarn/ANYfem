"""Semantic cut lines stay available without exposing generated seams."""
from test_qt_geometry_actions import qapp, window
from test_plate_cut_beams import project
from anyfem import commands
from anyfem.io.project_file import project_to_dict, project_from_dict
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest


def click_line(window, qapp, identifier):
    tree = window.tree
    item = tree._rows['Lines'][identifier][0]
    tree.expand(item.parent().index()); tree.scrollTo(item.index()); qapp.processEvents()
    QTest.mouseClick(tree.viewport(), Qt.LeftButton, pos=tree.visualRect(item.index()).center())
    qapp.processEvents()
    return item


def test_cut_trace_tree_select_hide_beam_undo_reopen(window, qapp):
    p, face = project(); window._set_project(p)
    cut = window.run(commands.CutPlate(face, origin=(2,0,0), normal=(1,0,0)))
    edge = cut.trace_edges[0]
    ref = p.geometry.entity_ref('edge', edge)
    curve = p.geometry.edges[edge].curve
    assert edge in window.tree._rows['Lines']
    click_line(window, qapp, edge)
    assert tuple(window.selection.ordered_items) == (ref,)
    assert f"ent_edge{edge}" in window.viewport.highlighted_tags()
    window._actions['Hide selected'].trigger()
    assert window.viewport.visibility.is_hidden(ref)
    window._actions['Show selected'].trigger()
    assert not window.viewport.visibility.is_hidden(ref)
    window.run(commands.AddBeamMember(cut.trace_edges, 'beam'))
    assert edge not in window.tree._rows['Lines'] and len(window.tree._rows['Beams']) == 1
    assert p.geometry.edges[edge].curve is curve
    window.undo(); assert edge in window.tree._rows['Lines']
    window.undo(); assert edge not in window.tree._rows['Lines']
    window.redo(); assert edge in window.tree._rows['Lines']
    window.redo(); assert edge not in window.tree._rows['Lines']
    window._set_project(project_from_dict(project_to_dict(p)))
    assert edge not in window.tree._rows['Lines'] and len(window.tree._rows['Beams']) == 1


def test_generated_panel_only_semantic_traces_become_lines(window, qapp):
    window.run(commands.AddStiffenedPanel(4, 4, 2))
    p = window.project
    assert not window.tree._rows['Lines']
    face = next(iter(p.geometry.faces))
    cut = window.run(commands.CutPlate(face, origin=(1,0,0), normal=(1,0,0)))
    trace = set(cut.trace_edges)
    assert set(window.tree._rows['Lines']) == trace
    assert all(p.geometry.entity_ref('edge', edge) in window.tree.generated_entity_owners() for edge in trace)
    click_line(window, qapp, cut.trace_edges[0])
    assert {ref.id for ref in window.selection.items} == {cut.trace_edges[0]}
    window._set_project(project_from_dict(project_to_dict(p)))
    assert set(window.tree._rows['Lines']) == trace
