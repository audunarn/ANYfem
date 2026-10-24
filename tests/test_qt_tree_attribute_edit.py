import os
from dataclasses import replace
import pytest
import numpy as np
if os.environ.get('ANYFEM_RUN_QT_TESTS')!='1':pytest.skip('enable Qt tests explicitly',allow_module_level=True)
from test_qt_geometry_actions import qapp,window,geometry
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from anyfem import commands
from anyfem.model.attributes import Support


def click(window,qapp,group,key,column=0):
    tree=window.tree;item=tree._rows[group][key][0];parent=item.parent()
    while parent is not None:tree.expand(parent.index());parent=parent.parent()
    tree.scrollTo(item.index());qapp.processEvents()
    index=item.index().siblingAtColumn(column)
    QTest.mouseClick(tree.viewport(),Qt.LeftButton,pos=tree.visualRect(index).center());qapp.processEvents()
    return item


def test_immediate_expand_then_native_click_opens_support_even_from_state_cell(window,qapp):
    _,refs=geometry(window);record=window.run(commands.AddSupport(Support('Boundary',refs[1],{'ux':.002})))
    tree=window.tree;assert not tree.isAnimated()
    click(window,qapp,'Supports',record.id,column=1)
    panel=window.panels['Loads & BC'];assert panel._editing.record is record
    assert refs[1] in window.viewport._collection_refs
    panel.name.setText('Discard draft');click(window,qapp,'Supports',record.id)
    assert panel.name.text()=='Boundary'
    panel.new_button.click();assert panel._editing is None and not panel.edit_bar.isVisible()
    assert len(window.project.supports)==1


@pytest.mark.parametrize('operation,collection',[('AddPointLoad','point_loads'),('AddLineLoad','line_loads'),('AddPressure','pressures'),('AddSurfaceTraction','surface_tractions'),('AddMass','masses')])
def test_friendly_edit_existing_all_primary_types_preserves_stored_semantics(window,qapp,operation,collection):
    _,refs=geometry(window);ref=refs[0] if operation in {'AddPointLoad','AddMass'} else refs[1] if operation=='AddLineLoad' else refs[2]
    if operation=='AddMass':record=window.run(commands.AddMass(ref,12.,distribution_policy='total_distributed'))
    elif operation=='AddPressure':record=window.run(commands.AddPressure(ref,12.))
    else:
        field='force' if operation=='AddPointLoad' else 'force_per_length' if operation=='AddLineLoad' else 'traction'
        record=window.run(getattr(commands,operation)(ref,**{field:(0,0,12.)},envelope={'start':0,'stop':3}))
    panel=window.panels['Loads & BC'];panel.edit_existing(record)
    window.selection.restore(tuple(refs));qapp.processEvents()
    if operation in {'AddMass','AddPressure'}:panel.scalar.setText('24')
    else:panel.vector.components[2].setText('24')
    updated=panel.execute();records=window.project.masses if operation=='AddMass' else getattr(window.project.load_case('default'),collection)
    assert len(records)==1 and updated.id==record.id and updated.region==record.region and updated.ref==record.ref
    if hasattr(record,'envelope'):assert updated.envelope is record.envelope
    assert updated.value==24 if operation in {'AddMass','AddPressure'} else getattr(updated,field)[2]==24
    window.undo();assert (window.project.masses if operation=='AddMass' else getattr(window.project.load_case('default'),collection))[0] is record


def test_edit_existing_invalid_stale_readonly_and_advanced_envelope(window,qapp):
    _,refs=geometry(window);record=window.run(commands.AddPressure(refs[2],10.,envelope={'start':0,'stop':2}))
    panel=window.panels['Loads & BC'];panel.edit_existing(record);panel.scalar.setText('invalid')
    with pytest.raises(ValueError):panel.execute()
    assert window.project.load_case('default').pressures==[record]
    panel.scalar.setText('20');panel.advanced_form.setChecked(True);qapp.processEvents()
    assert panel.choice.currentData()=='EditAttribute'
    panel.execute();assert len(window.project.load_case('default').pressures)==1
    panel.advanced_form.setChecked(False);qapp.processEvents();assert panel.choice.currentData()=='AddPressure'
    prior=panel._editing.record;window.run(commands.EditAttribute(replace(prior,value=30)))
    with pytest.raises(ValueError,match='changed'):panel.execute()
    assert not panel.apply.isEnabled()


def test_tree_inspection_selection_event_does_not_refresh_whole_tree(window,qapp,monkeypatch):
    _,refs=geometry(window);events=[];calls=[]
    window.workbench.add_listener(lambda event:events.append((event.kind,event.field)))
    original=window.tree.refresh
    monkeypatch.setattr(window.tree,'refresh',lambda *a,**k:(calls.append(1),original(*a,**k))[1])
    click(window,qapp,'Plates',refs[2].id)
    assert not calls, events


def test_mixed_canonical_region_highlight_ignores_current_face_filter(window,qapp):
    from anyfem.model.regions import Region,ManualRegion,RegionRef
    _,refs=geometry(window);region=Region('Mixed targets','geometry','mixed',ManualRegion(tuple(refs)));window.project.regions.add(region)
    record=window.run(commands.AddSupport(Support('All targets',refs[0],{'ux':0.},region=RegionRef(region.id))))
    window._quick_select('face');click(window,qapp,'Supports',record.id)
    assert window.viewport._collection_refs==frozenset(refs)
    assert '3 existing target' in window.panels['Loads & BC'].target_summary.text()
    window.refresh_all();assert window.viewport._collection_refs==frozenset(refs)
