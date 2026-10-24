import os
import pytest
import numpy as np
if os.environ.get('ANYFEM_RUN_QT_TESTS')!='1':pytest.skip('enable Qt tests explicitly',allow_module_level=True)
from test_qt_geometry_actions import qapp,window,geometry
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from anyfem import commands


def choose(panel,name):panel.choice.setCurrentIndex(panel.choice.findData(name))


def test_support_checkboxes_all_selected_and_single_enter_undo(window,qapp):
    _,refs=geometry(window);panel=window.panels['Loads & BC'];window.selection.restore(refs);qapp.processEvents()
    assert all(check.isChecked() for check in panel.dofs.values())
    for dof in ('rx','ry','rz'):panel.dofs[dof].setChecked(False)
    assert panel.preset.currentText()=='custom'
    panel.name.setFocus();QTest.keyClick(panel.name,Qt.Key_Return);qapp.processEvents()
    assert len(window.project.supports)==3 and all(r.constraints=={'ux':0.,'uy':0.,'uz':0.} for r in window.project.supports)
    ids={r.id for r in window.project.supports};window.undo();assert not window.project.supports
    window.redo();assert {r.id for r in window.project.supports}==ids
    for check in panel.dofs.values():check.setChecked(False)
    assert not panel.apply.isEnabled()


@pytest.mark.parametrize('operation,collection,kind',[('AddPointLoad','point_loads','vertex'),('AddLineLoad','line_loads','edge'),('AddSurfaceTraction','surface_tractions','face'),('AddPressure','pressures','face'),('AddMass','masses','vertex')])
def test_primary_targets_values_distribution_preserved(window,qapp,operation,collection,kind):
    vertices,refs=geometry(window);panel=window.panels['Loads & BC'];choose(panel,operation)
    selected=tuple(window.project.geometry.entity_ref(kind,i) for i in (vertices[:2] if kind=='vertex' else tuple(window.project.geometry.edges)[:2] if kind=='edge' else (refs[2].id,)))
    window.selection.restore(selected);qapp.processEvents()
    if operation in {'AddPressure','AddMass'}:panel.scalar.setText('20')
    else:panel.vector.components[2].setText('20')
    if operation=='AddPointLoad':panel.distribution.setText('total_distributed')
    panel.execute();qapp.processEvents()
    records=window.project.masses if collection=='masses' else getattr(window.project.load_case('default'),collection)
    assert len(records)==len(selected)
    if operation=='AddPointLoad':assert all(np.allclose(r.force,(0,0,10)) for r in records)
    if operation=='AddMass':assert sum(r.value for r in records)==20
    window.undo();assert not (window.project.masses if collection=='masses' else getattr(window.project.load_case('default'),collection))


def test_selection_refresh_keeps_values_and_direction_units_and_stale_refusal(window,qapp):
    vertices,refs=geometry(window);panel=window.panels['Loads & BC'];choose(panel,'AddPointLoad')
    panel.vector.mode.setCurrentIndex(1);panel.vector.magnitude.setText('2 kN');panel.vector.direction[0].setText('1');panel.vector.direction[2].setText('0')
    window.selection.restore((refs[0],));qapp.processEvents();assert panel.vector.magnitude.text()=='2 kN'
    panel.execute();assert window.project.load_case('default').point_loads[0].force==pytest.approx((2000,0,0))
    window.new_project()
    with pytest.raises(ValueError,match='Project changed'):panel.execute()


def test_support_preset_and_nonzero_prescribed_components(window,qapp):
    _,refs=geometry(window);panel=window.panels['Loads & BC'];window.selection.restore((refs[1],))
    panel.preset.setCurrentText('pinned');panel.prescribed['ux'].setText('2 mm');panel.execute()
    record=window.project.supports[-1];assert record.constraints=={'ux':.002,'uy':0.,'uz':0.}
    panel.preset.setCurrentText('symmetry');panel.normal.setCurrentText('x');panel.execute()
    assert window.project.supports[-1].constraints=={'ux':0.,'ry':0.,'rz':0.}


def test_advanced_switch_and_load_values_preserve_units(window,qapp):
    _,refs=geometry(window);panel=window.panels['Loads & BC']
    panel.load_values('AddPointLoad',ref=refs[0],force=(1000,0,0));panel.execute()
    assert window.project.load_case('default').point_loads[-1].force==pytest.approx((1000,0,0))
    choose(panel,'AddConnection');qapp.processEvents();panel.refresh()
    assert panel.choice.currentText().startswith('Advanced') and panel.fields
    choose(panel,'AddPressure');window.selection.restore((refs[2],));panel.scalar.setText('not a number')
    before=len(window.project.load_case('default').pressures)
    with pytest.raises(ValueError):panel.execute()
    assert len(window.project.load_case('default').pressures)==before


def test_advanced_original_envelope_form_is_available_and_bulk_undoable(window,qapp):
    vertices,refs=geometry(window);panel=window.panels['Loads & BC'];choose(panel,'AddPointLoad')
    panel.advanced_form.setChecked(True);qapp.processEvents()
    assert 'envelope' in panel.fields and 'distribution_policy' in panel.fields
    targets=tuple(window.project.geometry.entity_ref('vertex',i) for i in vertices[:2]);window.selection.restore(targets)
    panel.fields['force'][0].setText('0,0,-10')
    panel.fields['envelope'][0].setText('{"start":0,"stop":2}')
    panel.execute();records=window.project.load_case('default').point_loads
    assert len(records)==2 and all(record.envelope is not None for record in records)
    window.undo();assert not window.project.load_case('default').point_loads
    panel.advanced_form.setChecked(False);qapp.processEvents();assert panel.using_primary() and panel.vector


def test_primary_apply_stays_with_target_summary_and_survives_advanced_switch(window,qapp):
    _,refs=geometry(window);panel=window.panels['Loads & BC'];window.selection.restore((refs[1],))
    window.notebook.select('Loads & BC');window.resize(1900,1000);qapp.processEvents()
    assert panel.apply.parentWidget() is panel.scroll.widget()
    assert panel.apply.y()<panel.frame.y()+panel.frame.parentWidget().y()
    assert panel.apply.isVisible() and panel.apply.height()>=25
    panel.advanced_form.setChecked(True);qapp.processEvents()
    assert panel.apply.parentWidget() is panel
    panel.advanced_form.setChecked(False);qapp.processEvents()
    assert panel.apply.parentWidget() is panel.scroll.widget() and panel.apply.isVisible()
    panel.execute();assert len(window.project.supports)==1


def test_changed_project_units_require_explicit_value_review(window,qapp):
    from anyfem.model.units import unit_profile
    _,refs=geometry(window);panel=window.panels['Loads & BC'];choose(panel,'AddPointLoad');window.selection.restore((refs[0],))
    panel.vector.components[0].setText('2');window.project.units=unit_profile('SI-mm-N-MPa');panel.refresh()
    assert panel.vector.components[0].text()=='2' and not panel.apply.isEnabled()
    with pytest.raises(ValueError,match='units changed'):panel.execute()
    panel.bind_targets();assert panel.apply.isEnabled()
