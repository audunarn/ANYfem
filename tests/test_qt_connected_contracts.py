"""Small UI adapter/readiness controls, no solving or meshing."""
import inspect,json,os
from types import SimpleNamespace
import pytest
if os.environ.get('ANYFEM_RUN_QT_TESTS')!='1':
    pytest.skip('set ANYFEM_RUN_QT_TESTS=1 for real Qt widgets',allow_module_level=True)
from anygeometry.entities import EntityRef
from anyfem.ui.qt.editors import CommandEditor
from test_qt_result_browser import qapp,window,retained,click_entry
from test_qt_workbench import settle_result_requests,wait_until


@pytest.mark.parametrize('text,optional,expected',[
    ('',True,None),('{}',True,{}),('{"authored":"face:2"}',True,{'authored':EntityRef('face',2)})])
def test_mapping_reference_editor_retains_authored_presence(text,optional,expected):
    geometry=SimpleNamespace(entity_ref=lambda kind,key:EntityRef(kind,key))
    app=SimpleNamespace(selection=SimpleNamespace(ordered_items=[EntityRef('face',9)]),
        project=SimpleNamespace(geometry=geometry))
    editor=SimpleNamespace(app=app)
    result=CommandEditor.value(editor,SimpleNamespace(text=lambda:text),'Mapping[str, EntityRef] | None',None)
    assert result==expected


@pytest.mark.parametrize('text',['[]','{"key":""}','{"key":2}','{"key":null}','{"":"face:2"}'])
def test_mapping_reference_editor_rejects_wrong_value_shapes(text):
    app=SimpleNamespace(selection=SimpleNamespace(ordered_items=[EntityRef('face',9)]))
    with pytest.raises(ValueError):
        CommandEditor.value(SimpleNamespace(app=app),SimpleNamespace(text=lambda:text),'Mapping[str, EntityRef] | None',None)


def test_mapping_reference_editor_required_blank_refuses():
    with pytest.raises(ValueError):
        CommandEditor.value(SimpleNamespace(app=object()),SimpleNamespace(text=lambda:''),'Mapping[str, EntityRef]',inspect.Parameter.empty)


def test_playback_metadata_readiness_is_explicit_then_ready_play_completes(window,qapp,retained,monkeypatch):
    panel=click_entry(window,qapp,('field','displacement','ux'))
    source=retained.field('displacement').read(0).copy()
    ready=False;callbacks=[]
    def prepare(dataset,*,on_ready=None,on_cancel=None):
        assert dataset is retained
        if ready:return False
        callbacks.append((on_ready,on_cancel));return True
    monkeypatch.setattr(window,'prepare_result_dataset',prepare)
    # Reopening metadata has not yet established the panel preview owner.
    panel._preview_owner=None
    scene=window.viewport._scene
    with pytest.raises(ValueError,match='Results are still loading; try Play after loading finishes.'):
        panel.play()
    assert callbacks and (panel._playback is None or not panel._playback.active)
    assert panel._playback is None or not panel._playback.inflight
    assert window.viewport._scene is scene
    window.guarded(panel.play)()
    assert 'Results are still loading; try Play after loading finishes.' in window._status.get()
    ready=True;panel.refresh()
    for on_ready,_ in callbacks:
        if on_ready is not None:on_ready()
    settle_result_requests(window,qapp)
    panel.play();wait_until(qapp,lambda:panel._playback.completed_frames>0)
    assert panel.playback_active and window.viewport._scene.faces
    assert window.viewport.canvas.animation_frames==0
    panel.stop_playback()
    import numpy as np
    np.testing.assert_array_equal(retained.field('displacement').read(0),source)


def test_pending_play_cannot_start_after_source_replacement(window,qapp,retained,monkeypatch):
    panel=click_entry(window,qapp,('field','displacement','ux'));callbacks=[]
    def prepare(dataset,*,on_ready=None,on_cancel=None):callbacks.append((on_ready,on_cancel));return True
    monkeypatch.setattr(window,'prepare_result_dataset',prepare)
    window.guarded(panel.play)()
    assert 'Results are still loading' in window._status.get()
    window.new_project()
    for ready,_ in callbacks:
        if ready is not None:ready()
    qapp.processEvents()
    assert panel._playback is None or (not panel._playback.active and not panel._playback.inflight)
    assert not window.result_datasets


def test_scalar_and_sequence_reference_adapters_stay_unchanged():
    geometry=SimpleNamespace(entity_ref=lambda kind,key:EntityRef(kind,key))
    editor=SimpleNamespace(app=SimpleNamespace(project=SimpleNamespace(geometry=geometry),
        selection=SimpleNamespace(ordered_items=[EntityRef('face',9)])))
    assert CommandEditor.value(editor,SimpleNamespace(text=lambda:''),'EntityRef',None)==EntityRef('face',9)
    assert CommandEditor.value(editor,SimpleNamespace(text=lambda:'face:2,vertex:3'),'Sequence[EntityRef]',None)==(EntityRef('face',2),EntityRef('vertex',3))


def test_retained_field_action_refuses_unpublished_catalog_then_uses_exact_ready_key(window,qapp,retained,monkeypatch):
    """Header completion can precede publication of the selector's new items."""
    from PySide6.QtCore import QSignalBlocker
    import numpy as np
    panel=click_entry(window,qapp,('field','displacement','ux'))
    original=retained.field('displacement').read(0).copy();scene=window.viewport._scene
    pending=True
    def prepare(dataset,**callbacks):
        assert dataset is retained
        return pending
    monkeypatch.setattr(window,'prepare_result_dataset',prepare)
    # Exact reopening order: refresh returns before catalog normalization.
    blocker=QSignalBlocker(panel.field);panel.field.clear();panel.field.addItem('magnitude');del blocker
    panel.refresh()
    assert panel.field_name()=='magnitude'
    pending=False # The I/O header is ready, its queued Qt publication has not run.
    panel.field.setCurrentText('displacement')
    assert panel.field_name()=='magnitude' # Noneditable QComboBox ignores unknown text.
    window.guarded(panel.show_results)()
    assert 'ValueError: Choose a field available in this retained result.' in window._status.get()
    assert window.viewport._scene is scene
    np.testing.assert_array_equal(retained.field('displacement').read(0),original)
    panel.refresh();panel.field.setCurrentText('displacement')
    assert panel.field_name()=='displacement'
    panel.show_results();settle_result_requests(window,qapp)
    assert window.viewport._scene.faces and panel.field_name()=='displacement'
    np.testing.assert_array_equal(retained.field('displacement').read(0),original)
