"""Real Qt tree selection and hidden-member rendering."""
from test_qt_geometry_actions import qapp, window
from PySide6.QtCore import Qt, QItemSelectionModel
from anyfem.application.examples import floating_barge_section
from anyfem import commands
from test_physical_beams import axis


def test_beam_row_selects_and_hides_whole_chain(window, qapp):
    p, edges = axis()
    commands.CommandStack(p).run(commands.AddBeamMember(edges, "beam", "Long beam"))
    window._set_project(p)
    tree = window.tree
    assert len(tree._rows["Beams"]) == 1 and not tree._rows["Lines"]
    item = next(iter(tree._rows["Beams"].values()))[0]
    assert item.rowCount() == 0
    tree.selectionModel().select(item.index(), QItemSelectionModel.ClearAndSelect | QItemSelectionModel.Rows)
    qapp.processEvents()
    assert {ref.id for ref in window.selection.items} == set(edges)
    assert {ref.id for ref in window.viewport._collection_refs} == set(edges)
    window._actions["Hide selected"].trigger()
    assert all(window.viewport.visibility.is_hidden(p.geometry.entity_ref("edge", edge)) for edge in edges)
    assert item.foreground().color().name() == "#8794a6"
    window._actions["Show selected"].trigger()
    assert all(not window.viewport.visibility.is_hidden(p.geometry.entity_ref("edge", edge)) for edge in edges)


def test_barge_tree_collapses_axis_subdivisions(window):
    window._set_project(floating_barge_section())
    assert len(window.tree._rows["Beams"]) == 44
    assert not any(identifier in window.project.edge_sections for identifier in window.tree._rows["Lines"])



def test_add_beam_menu_groups_box_selection(window, monkeypatch):
    from PySide6.QtWidgets import QInputDialog
    p, edges = axis()
    window._set_project(p)
    monkeypatch.setattr(QInputDialog, "getItem", lambda *args: ("beam", True))
    refs = tuple(p.geometry.entity_ref("edge", edge) for edge in reversed(edges))
    window.geometry_actions.assign_beams(refs)
    assert len(p.geometry.members) == 1
    assert len(window.tree._rows["Beams"]) == 1
    window.undo()
    assert not p.geometry.members and not p.edge_sections
    window.redo()
    assert len(p.geometry.members) == 1


def test_partial_axis_selection_does_not_claim_whole_member(window, qapp):
    p, edges = axis()
    commands.CommandStack(p).run(commands.AddBeamMember(edges, "beam"))
    window._set_project(p)
    window.selection.set_mode("edge")
    window.selection.select(p.geometry.entity_ref("edge", edges[0]))
    qapp.processEvents()
    assert not window.tree.selectionModel().selectedRows()
