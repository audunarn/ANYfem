"""Synthetic result UI checks; no solving, meshing or scientific acceptance."""
import os
from types import SimpleNamespace

import numpy as np
import pytest

if os.environ.get("ANYFEM_RUN_QT_TESTS")!="1":
    pytest.skip("set ANYFEM_RUN_QT_TESTS=1 for real Qt widgets",allow_module_level=True)
pytest.importorskip("PySide6")
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from anyfem.ui.qt.app import QtFemWindow
from anyfem.io.artifacts import ArtifactStore
from anyfem.model.records import ResultQuantityDescriptor
from test_qt_workbench import settle_result_requests,wait_until


@pytest.fixture(scope="module")
def qapp():return QApplication.instance() or QApplication([])


@pytest.fixture
def window(qapp,tmp_path,monkeypatch):
    from anyfem.io import recovery
    monkeypatch.setattr(recovery,"default_recovery_root",lambda:tmp_path/"recovery")
    app=QtFemWindow(viewer_backend="software");app.show();qapp.processEvents()
    yield app
    app.session.mark_saved();app.close();qapp.processEvents()


@pytest.fixture
def retained(window,qapp,tmp_path,monkeypatch):
    from anymesher import Mesh
    mesh=Mesh(nodes={1:np.array([0.,0.,0.]),2:np.array([1.,0.,0.]),3:np.array([1.,1.,0.]),4:np.array([0.,1.,0.])},quads={1:(1,2,3,4)},elements_of_face={1:[1]})
    fields={
        "displacement":(ResultQuantityDescriptor("displacement","Displacement","node","m",("ux","uy","uz"),frames=(0.,1.)),np.array([[[.01,.02,.03]]*4,[[.02,.03,.04]]*4])),
        "stress":(ResultQuantityDescriptor("stress","Stress","element","Pa",("xx","yy","xy"),frames=(0.,1.)),np.array([[[1e6,-2e6,3e6]],[[2e6,-3e6,4e6]]])),
        "strain":(ResultQuantityDescriptor("strain","Strain","element","1",("xx","yy","xy"),frames=(0.,1.)),np.array([[[.001,-.002,.003]],[[.002,-.003,.004]]])),
        "energy":(ResultQuantityDescriptor("energy","Energy","global","J",("kinetic","strain"),frames=(0.,1.)),np.array([[[12.,23.]],[[34.,45.]]])),
        "last_displacement":(ResultQuantityDescriptor("last_displacement","Last displacement","node","m",("uz",),frames=(1.,)),np.array([[[.05]]*4])),
    }
    store=ArtifactStore(tmp_path/"synthetic.anyfem")
    artifact=store.write_result(job_id="synthetic",document_id=window.project.document_id,mesh_id="submitted",model_hash="model",mesh_hash="mesh",analysis_hash="analysis",
        fields=fields,frames=(0.,1.),tables={"displacement_node_ids":np.array([1,2,3,4]),"stress_element_ids":np.array([1]),"strain_element_ids":np.array([1]),"last_displacement_node_ids":np.array([1,2,3,4])},
        histories={"load_displacement":(np.array([0.,1.]),np.array([0.,2.]))})
    dataset=store.open_result(artifact);window.active_job_id="synthetic";window.result_datasets["synthetic"]=dataset
    monkeypatch.setattr(window,"retained_result_mesh",lambda *_:mesh)
    window.refresh_all();settle_result_requests(window,qapp);return dataset


def click_entry(window,qapp,identity):
    browser=window.result_browser;window.show_result_browser();settle_result_requests(window,qapp);browser.tree.expandAll()
    item=browser._items[identity];browser.tree.scrollTo(item.index());qapp.processEvents()
    QTest.mouseClick(browser.tree.viewport(),Qt.LeftButton,pos=browser.tree.visualRect(item.index()).center())
    settle_result_requests(window,qapp);return window.panels["Results"]


def test_left_tree_component_click_contour_and_inspection_are_synchronized(window,qapp,retained):
    before=retained.field("stress").read(0).copy()
    panel=click_entry(window,qapp,("field","stress","yy"))
    assert window._view_mode=="results" and panel.field_name()=="stress" and panel.component.text()=="yy"
    assert window.viewport._scene.faces
    assert window.viewport._scene.legend["title"]=="Stress · yy"
    assert window.left_tabs.currentWidget() is window.result_browser
    panel.inspect_quantity();settle_result_requests(window,qapp)
    assert panel.table.values.headers==["element_id","yy"]
    assert panel.table.values.rows==[[1,-2e6]]
    np.testing.assert_array_equal(retained.field("stress").read(0),before)
    click_entry(window,qapp,("field","strain","xx"))
    panel.inspect_quantity();settle_result_requests(window,qapp);assert panel.table.values.rows==[[1,.001]]
    parent=window.result_browser._items[("field_table","stress",None)]
    assert parent.hasChildren()


def test_frame_and_component_controls_update_display_without_show_button(window,qapp,retained,monkeypatch):
    panel=click_entry(window,qapp,("field","displacement","ux"))
    calls=[];original=window.show_persisted_result
    def show(key,**kwargs):calls.append((key,kwargs));original(key,**kwargs)
    monkeypatch.setattr(window,"show_persisted_result",show)
    panel.frame.setValue(1);qapp.processEvents()
    assert calls[-1][1]["frame"]==1
    assert panel.frame.text()=="2" and panel.frame.valueFromText("1")==0
    panel.component.setText("uz");qapp.processEvents()
    assert calls[-1][1]["component"]=="uz"
    assert window.result_browser.tree.currentIndex().data(Qt.UserRole).identity==("field","displacement","uz")
    click_entry(window,qapp,("field","last_displacement","uz"))
    assert panel.frame.maximum()==0 and panel.frame.value()==0
    panel.component.setText("not_present")
    with pytest.raises(ValueError,match="available"):panel.show_results()
    panel.component.setText("uz");qapp.processEvents()


def test_nonspatial_quantities_inspect_components_without_changing_view(window,qapp,retained):
    mode=window._view_mode
    panel=click_entry(window,qapp,("field_table","energy","kinetic"))
    assert window._view_mode==mode
    assert panel.table.values.rows==[[12.]] and panel.table.values.headers==["kinetic"]
    panel.frame.setValue(1);qapp.processEvents()
    assert panel.table.values.rows==[[34.]]
    click_entry(window,qapp,("history","load_displacement",None))
    assert window._view_mode==mode


def test_catalog_is_lazy_incremental_searchable_and_resets_with_project(window,qapp,retained,monkeypatch):
    browser=window.result_browser
    previous=browser._items[("field","stress","xx")]
    monkeypatch.setattr(type(retained.field("stress")),"read",lambda *_:(_ for _ in ()).throw(AssertionError("catalog read data")))
    browser.refresh();assert browser._items[("field","stress","xx")] is previous
    browser.search.setText("strain");qapp.processEvents()
    strain=browser._items[("field","strain","xx")]
    assert not browser.tree.isRowHidden(strain.row(),strain.parent().index())
    window.new_project();qapp.processEvents()
    assert not browser._items and "Run or open" in browser.status.text()


def test_live_fields_and_saved_fields_can_be_selected_in_one_result(window,qapp,retained,monkeypatch):
    solution=SimpleNamespace(available_fields=lambda:["ux","von_mises"])
    window.solution=solution;panel=window.panels["Results"]
    monkeypatch.setattr(panel,"refresh_details",lambda:None)
    monkeypatch.setattr("anyfem.ui.qt.tasks._solution_report",lambda *_:"Synthetic live result")
    calls=[];monkeypatch.setattr(window,"show_results",lambda:calls.append(panel.field_name()))
    window.refresh_panels()
    click_entry(window,qapp,("live_field","von_mises",None));assert calls==["von_mises"]
    click_entry(window,qapp,("field","strain","xy"));assert panel.use_retained() and panel.field_name()=="strain"
    click_entry(window,qapp,("live_field","ux",None));assert calls[-1]=="ux" and not panel.use_retained()


def test_unit_changes_preserve_expansion_and_saved_si_values(window,qapp,retained):
    panel=click_entry(window,qapp,("field","stress","xx"))
    panel.units.setCurrentText("Engineering (mm / MPa)");qapp.processEvents()
    assert "MPa" in panel.selection_details.text()
    browser=window.result_browser;item=browser._items[("field","stress","xx")]
    assert "MPa" in item.parent().child(item.row(),1).text()
    assert browser.tree.isExpanded(item.parent().index())
    assert retained.field("stress").read(0)[0,0]==1e6


def test_playback_uses_selected_quantity_frames_without_a_queued_redraw(window,qapp,retained):
    panel=click_entry(window,qapp,("field","last_displacement","uz"))
    panel.play();wait_until(qapp,lambda:window.viewport.canvas.animation_frames>0)
    assert window.viewport.canvas.animation_frames==1
    assert window.viewport.canvas._animation_frame_active
    window.viewport.canvas.stop_animation()


def test_results_tree_visual_layout(window,qapp,retained):
    panel=click_entry(window,qapp,("field","stress","xx"))
    panel.units.setCurrentText("Engineering (mm / MPa)");qapp.processEvents()
    window.resize(1600,1000);window.result_browser.tree.expandAll();window.viewport.fit();qapp.processEvents()
    assert window.result_browser.tree.visualRect(window.result_browser._items[("field","stress","xx")].index()).isValid()
    destination=os.environ.get("ANYFEM_QT_VISUAL_OUT")
    if destination:
        from pathlib import Path
        path=Path(destination);path.mkdir(parents=True,exist_ok=True)
        assert window.grab().save(str(path/"postprocessing-tree.png"))


def test_queued_redraw_does_not_cross_project_replacement(window,qapp,retained,monkeypatch):
    panel=click_entry(window,qapp,("field","stress","xx"));calls=[]
    monkeypatch.setattr(window,"show_persisted_result",lambda *args,**kwargs:calls.append(args))
    panel.frame.setValue(1);window.new_project();qapp.processEvents()
    assert not calls and not window.result_browser._items
