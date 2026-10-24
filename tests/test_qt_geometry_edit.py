"""Unit-aware explicit fields and cancellation in the shared geometry editor."""
import os
import pytest
if os.environ.get('ANYFEM_RUN_QT_TESTS')!='1':pytest.skip('Qt opt-in',allow_module_level=True)
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from test_qt_geometry_actions import qapp,window,geometry
from anyfem import commands
from anyfem.io.project_file import project_to_dict


def assigned(window):
    _,refs=geometry(window);window.run(commands.AssignPlate(refs[2].id,'plate'));window.run(commands.AssignBeam(refs[1].id,'stiffener'))
    return refs


def test_bare_degrees_explicit_units_optin_and_cancel(window,qapp):
    refs=assigned(window);before=project_to_dict(window.project);dialog=window.geometry_actions.edit((refs[1],refs[2]));qapp.processEvents()
    assert dialog.beam_fields['rotation_deg'].isEnabled() is False
    dialog.beam_fields['rotation_deg'].setText('999')
    dialog.cancel.click();qapp.processEvents();assert project_to_dict(window.project)==before
    dialog=window.geometry_actions.edit((refs[1],));qapp.processEvents()
    dialog.beam_checks['rotation_deg'].setChecked(True);dialog.beam_fields['rotation_deg'].setText('30')
    dialog.beam_checks['offset_mode'].setChecked(True);dialog.beam_fields['offset_mode'].setCurrentText('manual')
    dialog.beam_checks['eccentricity'].setChecked(True);dialog.beam_fields['eccentricity'].setText('25 mm')
    dialog.apply.click();qapp.processEvents()
    section=window.project.beam_section_of(refs[1].id);assert section.rotation_deg==30 and section.eccentricity==.025
    dialog=window.geometry_actions.edit((refs[1],));dialog.beam_checks['rotation_deg'].setChecked(True);dialog.beam_fields['rotation_deg'].setText('0.5 rad');dialog.apply.click();qapp.processEvents()
    import math
    assert window.project.beam_section_of(refs[1].id).rotation_deg==pytest.approx(math.degrees(.5))


def test_invalid_or_stale_edit_preserves_draft_and_model(window,qapp):
    refs=assigned(window);dialog=window.geometry_actions.edit((refs[2],));before=project_to_dict(window.project);history=window.commands.history()
    dialog.plate_checks['thickness'].setChecked(True);dialog.plate_fields['thickness'].setText('-1 mm');dialog.apply.click();qapp.processEvents()
    assert dialog.feedback.text() and window.commands.history()==history and project_to_dict(window.project)==before
    dialog.plate_fields['thickness'].setText('20 mm');window.run(commands.AddPoint(3,3));qapp.processEvents();accepted=project_to_dict(window.project)
    dialog.apply.click();qapp.processEvents();assert 'changed' in dialog.feedback.text() and project_to_dict(window.project)==accepted
    dialog.cancel.click()


def test_context_hit_preserves_selected_refs_but_unselected_hit_replaces(window,qapp,monkeypatch):
    from types import SimpleNamespace
    from PySide6.QtCore import QPoint
    from anygeometry import EntityRef
    refs=assigned(window);edges=[EntityRef('edge',use.edge) for use in window.project.geometry.faces[refs[2].id].loop]
    viewport=window.viewport;surface=viewport.canvas.event_widget;window.selection.set_mode('edge');window.selection.restore(edges[:2]);events=[]
    monkeypatch.setattr(window.geometry_actions,'canvas_context_menu',events.append)
    def hits(ref):return (SimpleNamespace(owner=SimpleNamespace(key=str(ref.id),kind='geometry.edge')),)
    monkeypatch.setattr(viewport.canvas,'query_point',lambda *args:hits(edges[0]))
    viewport.context_menu_at(surface,QPoint(10,10));assert set(window.selection.items)==set(edges[:2]) and events[-1].preserve_selection
    monkeypatch.setattr(viewport.canvas,'query_point',lambda *args:hits(edges[2]))
    viewport.context_menu_at(surface,QPoint(10,10));assert window.selection.items==[edges[2]] and not events[-1].preserve_selection
    monkeypatch.setattr(viewport.canvas,'query_point',lambda *args:())
    count=len(events);viewport.context_menu_at(surface,QPoint(10,10));assert len(events)==count
    viewport.context_menu_at(surface,QPoint(10,10),preserve_selection=True);assert len(events)==count+1 and window.selection.items==[edges[2]]


def test_displayed_pristine_value_does_not_create_variant(window,qapp):
    from dataclasses import replace
    refs=assigned(window);section=window.project.beam_sections['stiffener'];precise=replace(section,web_height=.20000000000000001)
    window.run(commands.AddBeamSection(precise));before=window.commands.history();dialog=window.geometry_actions.edit((refs[1],));qapp.processEvents()
    assert '000000000000001' not in dialog.beam_fields['web_height'].text()
    dialog.beam_checks['web_height'].setChecked(True);dialog.apply.click();qapp.processEvents()
    assert window.commands.history()==before and window.project.beam_sections['stiffener'].web_height==precise.web_height


def test_production_scene_refreshes_section_shape_after_edit_and_undo(window,qapp):
    import numpy as np
    from anyfem.geometry_edit import GeometryEditDraft
    refs=assigned(window);window.show_geometry();qapp.processEvents();ref=refs[1]
    def points():
        return np.concatenate([polygon for patch in window.viewport._scene.faces if patch.ref==ref for polygon in patch.polygons])
    original=points().copy();before_cache=window._geometry_scene_cache
    draft=GeometryEditDraft.capture(window.project,(ref,));window.run(draft.command(beam={'rotation_deg':45.,'offset_mode':'manual','eccentricity':.025}));qapp.processEvents()
    assert window._geometry_scene_cache is not before_cache
    assert not np.allclose(points(),original)
    window.undo();qapp.processEvents();np.testing.assert_allclose(points(),original,atol=1e-12)
    cached=window._geometry_scene_cache
    dialog=window.geometry_actions.edit((ref,));dialog.beam_checks['rotation_deg'].setChecked(True);dialog.apply.click();qapp.processEvents()
    assert window._geometry_scene_cache is cached
