"""Production generator/edit controls and full-member viewport selection."""
import os
from dataclasses import replace
from uuid import uuid4
import numpy as np
import pytest
if os.environ.get('ANYFEM_RUN_QT_TESTS')!='1':pytest.skip('Qt opt-in',allow_module_level=True)
from PySide6.QtCore import Qt,QPoint
from PySide6.QtTest import QTest
from anygeometry import EntityRef
from anyfem import commands as cmd
from anyfem.ui.qt.geometry_edit import GeometryEditDialog
from test_qt_geometry_actions import qapp,window
from test_stiffened_panel_sections import axes


def add_girder(window):
    section=replace(window.project.beam_sections['stiffener'],name='girder',id=str(uuid4()),web_height=.7,offset_mode='manual',eccentricity=.35)
    window.run(cmd.AddBeamSection(section))


def refs(window,member):
    g=window.project.geometry
    return tuple(EntityRef('edge',g.member_edge_uses[use].edge_id) for use in member.edge_use_ids)


def click(window,qapp,edge,modifiers=Qt.NoModifier):
    canvas=window.viewport.canvas;point=window.project.geometry.sample_edge(edge,[.37])[0]
    if window.viewport.visualization.show_beam_sections and edge in window.project.edge_sections:point=point+np.asarray(window.project.beam_offset_vector(edge))
    x,y,_=canvas.project_point(point);surface=canvas.event_widget;ratio=surface.devicePixelRatioF()
    QTest.mouseClick(surface,Qt.LeftButton,modifiers,pos=QPoint(round(x/ratio),round(y/ratio)))
    qapp.processEvents()


def test_generator_directional_controls_create_and_edit_existing_feature(window,qapp):
    add_girder(window);panel=window.panels['Generators']
    window.notebook.select('Generators');panel.kind.setCurrentText('Stiffened panel');qapp.processEvents()
    assert panel.longitudinal_beam_section.isVisible() and panel.transverse_beam_section.isVisible()
    for name,value in [('length','5'),('width','4'),('longitudinal_spacing','1'),('transverse_spacing','2')]:panel.form.fields[name][0].setText(value)
    panel.longitudinal_beam_section.setCurrentText('stiffener');panel.transverse_beam_section.setCurrentText('girder')
    QTest.mouseClick(panel.apply,Qt.LeftButton);qapp.processEvents()
    record=next(r for r in window.project.geometry.features.records if r.kind=='generator.stiffened_panel')
    groups=axes(window.project,record.feature_id)
    assert {window.project.edge_sections[e] for e in groups['longitudinal']}=={'stiffener'}
    assert {window.project.edge_sections[e] for e in groups['transverse']}=={'girder'}
    window.tree.edit_record(('geometry',record.feature_id));qapp.processEvents()
    assert panel.transverse_beam_section.currentData()=='girder' and panel.new_button.isVisible()
    panel.transverse_beam_section.setCurrentText('stiffener')
    QTest.mouseClick(panel.apply,Qt.LeftButton);qapp.processEvents()
    assert len([r for r in window.project.geometry.features.records if r.kind=='generator.stiffened_panel'])==1
    assert set(window.project.edge_sections.values())=={'stiffener'}
    window.undo();qapp.processEvents()
    assert {window.project.edge_sections[e] for e in axes(window.project,record.feature_id)['transverse']}=={'girder'}
    window.redo();qapp.processEvents();assert set(window.project.edge_sections.values())=={'stiffener'}


@pytest.mark.parametrize('sections,assigned',[(False,True),(True,True),(True,False)])
def test_member_click_shift_and_requested_sub_edges(window,qapp,sections,assigned):
    add_girder(window)
    options={'beam_section':'stiffener','transverse_beam_section':'girder'} if assigned else {}
    window.run(cmd.AddStiffenedPanel(5,4,1,2,**options))
    g=window.project.geometry;record=next(r for r in g.features.records if r.kind=='generator.stiffened_panel')
    long=next(m for m in g.members.values() if m.name.startswith('longitudinal_'))
    trans=next(m for m in g.members.values() if m.name.startswith('transverse_'))
    long_refs=refs(window,long);trans_refs=refs(window,trans)
    assert len(long_refs)>1 and len(trans_refs)>1
    window.viewport.set_visualization(replace(window.viewport.visualization,show_beam_sections=sections))
    window.show_geometry(reset_view=True);window.viewport.set_view('top');window.viewport.fit()
    window.selection.set_mode('edge');qapp.processEvents()
    click(window,qapp,long_refs[0].id)
    assert set(window.selection.items)==set(long_refs)
    assert any(index.data(Qt.UserRole)==('members',long.id) for index in window.tree.selectionModel().selectedRows())
    click(window,qapp,trans_refs[0].id,Qt.ShiftModifier)
    assert set(window.selection.items)==set(long_refs)|set(trans_refs)
    window.set_selection_hidden(True)
    assert all(window.viewport.visibility.is_hidden(ref) for ref in long_refs+trans_refs)
    window.set_selection_hidden(False)
    window.tree.toggle_feature_topology({record.feature_id});window.show_geometry();window.selection.clear();qapp.processEvents()
    click(window,qapp,long_refs[0].id)
    assert window.selection.items==[long_refs[0]]
    window.tree.toggle_feature_topology({record.feature_id});window.show_geometry();window.selection.clear();qapp.processEvents()
    click(window,qapp,long_refs[0].id);assert set(window.selection.items)==set(long_refs)


def test_cancel_and_stale_generator_editor_preserve_model(window):
    from anyfem.document import canonical_hash
    from anyfem.io.project_file import project_to_dict
    window.run(cmd.AddStiffenedPanel(5,4,1,2,beam_section='stiffener'))
    record=next(r for r in window.project.geometry.features.records if r.kind=='generator.stiffened_panel')
    panel=window.panels['Generators'];before=canonical_hash(project_to_dict(window.project))
    panel.load_feature(record.feature_id);panel.form.fields['length'][0].setText('99');panel.new_feature()
    assert canonical_hash(project_to_dict(window.project))==before and panel._editing_feature is None
    panel.load_feature(record.feature_id);window.run(cmd.AddPoint(99,99,0))
    current=canonical_hash(project_to_dict(window.project))
    with pytest.raises(ValueError,match='model changed'):panel.execute()
    assert canonical_hash(project_to_dict(window.project))==current


def test_edit_preserves_unassigned_transverse_unless_same_is_requested(window,qapp):
    from anyfem.ui.qt.generators import SAME_AS_LONGITUDINAL
    window.run(cmd.AddStiffenedPanel(5,4,1,2,longitudinal_beam_section='stiffener'))
    record=next(r for r in window.project.geometry.features.records if r.kind=='generator.stiffened_panel')
    panel=window.panels['Generators'];panel.load_feature(record.feature_id)
    assert panel.transverse_beam_section.currentData() is None
    panel.execute();qapp.processEvents()
    assert not (axes(window.project,record.feature_id)['transverse'] & set(window.project.edge_sections))
    panel.load_feature(record.feature_id)
    panel.transverse_beam_section.setCurrentIndex(panel.transverse_beam_section.findData(SAME_AS_LONGITUDINAL))
    panel.execute();qapp.processEvents()
    assert {window.project.edge_sections[e] for e in axes(window.project,record.feature_id)['transverse']}=={'stiffener'}


def test_split_members_from_multiple_panels_remain_distinct_after_reopen(window,qapp,tmp_path):
    add_girder(window)
    window.run(cmd.AddStiffenedPanel(5,4,1,2,beam_section='stiffener',transverse_beam_section='girder'))
    window.run(cmd.AddStiffenedPanel(5,4,1,2,origin=(0,6,0),beam_section='girder',transverse_beam_section='stiffener'))
    def members():
        g=window.project.geometry
        records=[r for r in g.features.records if r.kind=='generator.stiffened_panel']
        result=[]
        for record in records:
            owned={r.id for r in record.outputs.values() if r.kind=='edge'}
            member=next(m for m in g.members.values() if m.name.startswith('longitudinal_') and {ref.id for ref in refs(window,m)}<=owned)
            result.append(refs(window,member))
        return result
    first,second=members();assert len(first)>1 and len(second)>1
    window.selection.set_mode('edge');window.viewport.set_view('top');window.viewport.fit();qapp.processEvents()
    click(window,qapp,first[0].id);assert set(window.selection.items)==set(first)
    click(window,qapp,second[0].id,Qt.ShiftModifier);assert set(window.selection.items)==set(first+second)
    path=tmp_path/'split-members.anyfem';window.save_project(path=str(path));window.flush_project_writes()
    window.session.mark_saved();window.new_project();window.open_project(str(path));qapp.processEvents()
    first,second=members()
    assert {window.project.edge_sections[ref.id] for ref in first}=={'stiffener'}
    assert {window.project.edge_sections[ref.id] for ref in second}=={'girder'}
    window.selection.set_mode('edge');window.viewport.set_view('top');window.viewport.fit();qapp.processEvents()
    click(window,qapp,second[0].id);assert set(window.selection.items)==set(second)


def test_edit_section_definition_refreshes_offsets_and_scene_with_undo(window,qapp):
    window.run(cmd.AddStiffenedPanel(5,4,1,2,beam_section='stiffener'))
    before=dict(window.project.beam_offsets)
    panel=window.panels['Sections'];window.notebook.select('Sections')
    panel.load_values('AddBeamSection',section=window.project.beam_sections['stiffener'])
    fields=panel.record_fields['section'][1]
    for key,value in [('offset_mode','manual'),('eccentricity','0.25 m')]:
        field=fields[key][0]
        if hasattr(field,'setCurrentText'):field.setCurrentText(value)
        else:field.setText(value)
    QTest.mouseClick(panel.apply,Qt.LeftButton);qapp.processEvents()
    assert window.project.beam_offsets and all(value==.25 for value in window.project.beam_offsets.values())
    assert window.project.beam_sections['stiffener'].offset_mode=='manual'
    for line in window.viewport._scene.lines:
        if line.ref is not None and line.ref.kind=='edge' and line.ref.id in window.project.edge_sections:
            np.testing.assert_allclose(line.points[:,2],.25)
    window.undo();qapp.processEvents();assert window.project.beam_offsets==before
    window.redo();qapp.processEvents();assert all(value==.25 for value in window.project.beam_offsets.values())


def test_clicked_member_edit_refreshes_section_and_offset_everywhere(window,qapp,tmp_path):
    add_girder(window);window.run(cmd.AddStiffenedPanel(5,4,1,2,beam_section='stiffener'))
    g=window.project.geometry;member=next(m for m in g.members.values() if m.name.startswith('longitudinal_'))
    chain=refs(window,member)
    window.selection.set_mode('edge');window.viewport.set_view('top');window.viewport.fit();qapp.processEvents()
    click(window,qapp,chain[0].id);assert set(window.selection.items)==set(chain)
    dialog=GeometryEditDialog(window,window.selection.items);dialog.show();qapp.processEvents()
    for key,value in [('section','girder'),('offset_mode','manual'),('eccentricity','.5')]:
        dialog.beam_checks[key].setChecked(True)
        field=dialog.beam_fields[key]
        if hasattr(field,'setCurrentText'):field.setCurrentText(value)
        else:field.setText(value)
    QTest.mouseClick(dialog.apply,Qt.LeftButton);qapp.processEvents()
    from shiboken6 import isValid
    assert not isValid(dialog) or not dialog.isVisible()
    lines={line.ref:line for line in window.viewport._scene.lines}
    for ref in chain:
        assert window.project.beam_section_of(ref.id).eccentricity==.5
        assert window.project.beam_offsets[ref.id]==.5
        np.testing.assert_allclose(lines[ref].points[:,2],.5)
    image=window.viewport.canvas.capture_image();image.save(tmp_path/'updated-member-offset.png')
    window.undo();qapp.processEvents()
    assert all(window.project.edge_sections[ref.id]=='stiffener' for ref in chain)
    window.redo();qapp.processEvents()
    assert all(window.project.beam_offsets[ref.id]==.5 for ref in chain)
