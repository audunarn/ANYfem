"""Committed refresh and indexed selection/visibility on real Qt widgets."""
import os
import pytest

if os.environ.get("ANYFEM_RUN_QT_TESTS") != "1":
    pytest.skip("set ANYFEM_RUN_QT_TESTS=1", allow_module_level=True)
pytest.importorskip("PySide6")
from PySide6.QtCore import Qt, QItemSelectionModel
from anyfem import commands as cmd
from anyfem.model.records import JobRecord, MeshRecord
from test_qt_geometry_actions import qapp, window, geometry


def selected_refs(tree):
    return [index.data(Qt.UserRole) for index in tree.selectionModel().selectedRows()
            if hasattr(index.data(Qt.UserRole), "kind")]


def test_command_refreshes_once_at_committed_revision(window, qapp, monkeypatch):
    calls=[]
    original=window.tree.refresh
    def refresh():
        calls.append(window.session.revision.sequence)
        original()
    monkeypatch.setattr(window.tree,"refresh",refresh)
    sequence=window.session.revision.sequence
    identifier=window.run(cmd.AddPoint(4,5))
    assert calls == [sequence+1]
    assert identifier in window.tree._rows["Points"]
    assert window._actions["Undo"].isEnabled()
    qapp.processEvents()
    assert calls == [sequence+1]
    window.undo()
    assert identifier not in window.tree._rows["Points"]
    window.redo()
    assert identifier in window.tree._rows["Points"]
    qapp.processEvents()
    assert calls == [sequence+1,sequence+2,sequence+3]


def test_failed_transaction_keeps_tree_and_recovers_queued_refresh(window,qapp,monkeypatch):
    identifier=window.run(cmd.AddPoint(1,2))
    revision=window.session.revision
    calls=[];original=window.tree.refresh
    def refresh():
        calls.append(window.session.revision.id);original()
    monkeypatch.setattr(window.tree,"refresh",refresh)
    # A stale UI refresh token must never authorize displaying trial state.
    window._last_refreshed_revision=None
    with pytest.raises(RuntimeError,match="refused"):
        with window.session.transaction("refused"):
            extra=window.commands.run(cmd.AddPoint(8,9))
            assert extra not in window.tree._rows["Points"]
            raise RuntimeError("refused")
    assert window.session.revision is revision
    assert identifier in window.tree._rows["Points"]
    assert extra not in window.tree._rows["Points"]
    qapp.processEvents()
    assert calls == [revision.id]
    assert extra not in window.tree._rows["Points"]


def test_bare_stack_still_refreshes_synchronously(window):
    identifier=window.commands.run(cmd.AddPoint(2,3))
    assert identifier in window.tree._rows["Points"]


def test_badge_updates_do_not_traverse_geometry_and_expose_stale(window,monkeypatch):
    tree=window.tree
    current=window.session.revision.model_hash
    window.project.jobs["j"]=JobRecord("",id="j",name="Finished",model_hash=current,result_artifact_id="r")
    window.project.mesh_records["m"]=MeshRecord("Mesh",current,"input","mesh",id="m")
    monkeypatch.setattr(window,"_job_is_stale",lambda job:job.model_hash!=window.session.revision.model_hash)
    def forbidden(*args,**kwargs):raise AssertionError("geometry traversal")
    monkeypatch.setattr(tree,"refresh",forbidden)
    monkeypatch.setattr(tree,"generated_entity_owners",forbidden)
    tree.refresh_job_states();tree.refresh_mesh_states()
    assert tree._rows["Jobs"]["j"][1].text()=="queued"
    assert tree._rows["Results"]["r"][1].text()==""
    assert tree._rows["Meshes"]["m"][1].text()=="completed"
    window.project.jobs["j"].model_hash="old"
    window.project.mesh_records["m"].source_model_hash="old"
    tree.refresh_job_states();tree.refresh_mesh_states()
    assert "stale" in tree._rows["Jobs"]["j"][1].text()
    assert tree._rows["Results"]["r"][1].text()=="stale"
    assert "stale" in tree._rows["Meshes"]["m"][1].text()


def test_index_selects_all_duplicates_and_survives_search_and_rebuild(window):
    vertices,refs=geometry(window)
    point=refs[0]
    region=window.geometry_actions.new_set((point,),"Repeated point")
    tree=window.tree
    window.selection.set_mode("vertex");window.selection.select(point)
    assert selected_refs(tree).count(point)==2
    ordered=window.selection.ordered_items
    tree.search("99999999")
    assert not selected_refs(tree)
    assert window.selection.ordered_items==ordered
    tree.search("")
    assert selected_refs(tree).count(point)==2
    record=tree._rows["Sets"][region.id][0]
    tree._refreshing=True
    tree.selectionModel().select(record.index(),QItemSelectionModel.Select|QItemSelectionModel.Rows)
    tree._refreshing=False
    tree.sync_from_selection()
    assert tree.selectionModel().isSelected(record.index())
    window.selection.clear()
    assert not selected_refs(tree)
    # An external selection clear deliberately dismisses collection highlights.
    assert not tree.selectionModel().isSelected(record.index())
    window._delete_tree_items((f"region:{region.id}",))
    window.selection.select(point)
    assert selected_refs(tree).count(point)==1


def test_unchanged_selection_and_visibility_emit_no_model_changes(window,monkeypatch):
    vertices,refs=geometry(window)
    point=refs[0]
    region=window.geometry_actions.new_set((point,refs[1]),"Mixed")
    tree=window.tree
    window.selection.set_mode("vertex");window.selection.select(point)
    selected=[];changed=[]
    tree.selectionModel().selectionChanged.connect(lambda *args:selected.append(args))
    tree._model.dataChanged.connect(lambda *args:changed.append(args))
    tree.sync_from_selection();tree.refresh_visibility()
    assert not selected and not changed
    window.viewport.visibility.set_hidden((point,))
    tree.refresh_visibility()
    assert all(item.toolTip()=="Hidden in viewport" for item in tree._ref_rows[point])
    assert tree._rows["Sets"][region.id][0].toolTip()=="Partly hidden in viewport"
    changed.clear()
    tree.refresh_visibility()
    assert not changed
    tree.refresh()
    assert all(item.toolTip()=="Hidden in viewport" for item in tree._ref_rows[point])
    window.viewport.visibility.set_hidden((point,),False)
    tree.refresh_visibility()
    assert all(not item.toolTip() for item in tree._ref_rows[point])
    assert not tree._rows["Sets"][region.id][0].toolTip()


def test_exposed_feature_duplicates_and_project_replacement(window):
    feature=window.run(cmd.AddFeature("generator.plate",parameters={"length":2,"width":1}))
    tree=window.tree
    point=next(ref for ref,owner in tree.generated_entity_owners().items()
               if owner==feature.feature_id and ref.kind=="vertex")
    window.geometry_actions.new_set((point,),"Generated point")
    window.selection.set_mode("vertex");window.selection.select(point)
    assert selected_refs(tree).count(point)==1
    tree.toggle_feature_topology((feature.feature_id,))
    assert selected_refs(tree).count(point)==2
    tree.toggle_feature_topology((feature.feature_id,))
    assert selected_refs(tree).count(point)==1
    from anyfem import Project
    window._set_project(Project("Replacement"))
    assert not tree._ref_rows
    identifier=window.run(cmd.AddPoint(9,8))
    replacement=window.project.geometry.entity_ref("vertex",identifier)
    window.selection.select(replacement)
    assert selected_refs(tree)==[replacement]
