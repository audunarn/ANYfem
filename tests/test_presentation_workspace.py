"""Presentation persistence, isolation and shared memory contracts."""
import threading
import time
import pytest
from anyfem.model.project import Project
from anyfem.document import DocumentSession
from anyfem.io.project_file import project_to_dict,project_from_dict,ProjectFileError
from anyfem.presentation.workspace import store_presentation,stable_sample_ids,annotation_stale,section_box_values
from anyfem.post.access import LiveResultAccess
from anyfem.post.fields import Field


def test_presentation_roundtrip_is_dirty_without_engineering_revision():
    project=Project();session=DocumentSession(project);before=session.revision
    store_presentation(session,lambda state:state.update(views={'Cut':{'label':'saved'}}))
    assert session.dirty and session.revision.document_hash!=before.document_hash
    assert session.revision.model_hash==before.model_hash
    data=project_to_dict(project)
    assert data['anyfem']['format']==16
    restored=project_from_dict(data)
    assert restored.presentation==project.presentation
    assert DocumentSession(restored).revision.model_hash==before.model_hash
    assert 'presentation' not in project_to_dict(Project())


def test_metadata_refusal_rolls_back_and_rejects_old_format():
    session=DocumentSession(Project())
    with pytest.raises(ValueError):store_presentation(session,lambda state:state.update(workspace={'x':float('nan')}))
    assert session.project.presentation=={} and not session.dirty
    data=project_to_dict(Project());data['presentation']={'version':1}
    with pytest.raises(ProjectFileError):project_from_dict(data)


def test_sampling_staleness_and_box_validation():
    assert stable_sample_ids(range(10000))==stable_sample_ids(reversed(range(10000)))
    assert len(stable_sample_ids(range(10000)))==2000
    assert section_box_values([0,0,0],[1,2,3],[0,0,0,2])[2]==[0,0,0,1]
    with pytest.raises(ValueError):section_box_values([0,0,0],[-1,2,3])
    assert annotation_stale({'document_id':'a','mesh_identity':'x'},'a','y')


def test_reference_publication_shares_cache_and_release():
    access=LiveResultAccess(cache_bytes=100000,working_bytes=100000)
    field=Field('ux',node_values={1:1.0})
    main=access.reserve_publication(field,5000);access.commit_publication(main)
    ref=access.reserve_publication(field,5000);access.commit_pane_publication('reference',ref)
    assert access._publication_bytes==main+ref
    next_main=access.reserve_publication(field,1000);access.commit_publication(next_main)
    assert access._publication_bytes==next_main+ref
    access.release_pane_publication('reference')
    assert access._publication_bytes==next_main
    access.close()


def test_reference_publication_respects_ceiling():
    from anypost.errors import WorkingSetError
    access=LiveResultAccess(cache_bytes=16000,working_bytes=100000)
    field=Field('ux',node_values={1:1.0})
    main=access.reserve_publication(field,5000);access.commit_publication(main)
    ref=access.reserve_publication(field,5000);access.commit_pane_publication('reference',ref)
    with pytest.raises(WorkingSetError):access.reserve_publication(field,5000)
    assert access._publication_bytes==main+ref
    access.close()


def test_cancelling_reference_channel_preserves_primary():
    from anyfem.application.result_requests import ResultRequests
    callbacks=[];coordinator=ResultRequests(callbacks.append);outcomes=[]
    release=threading.Event()
    coordinator.request(lambda cancel:release.wait(.1),lambda result:outcomes.append(('main',result)),channel='main:display')
    coordinator.request(lambda cancel:3,lambda result:outcomes.append(('reference',result)),channel='reference:display')
    coordinator.cancel_channel('reference:display');release.set()
    deadline=time.monotonic()+2
    while len(outcomes)<2 and time.monotonic()<deadline:
        while callbacks:callbacks.pop(0)()
        time.sleep(.002)
    coordinator.close()
    assert dict(outcomes)['main'].cancelled is False
    assert dict(outcomes)['reference'].cancelled is True

def test_qt_controls_named_box_and_independent_pane(tmp_path,monkeypatch):
    pytest.importorskip('PySide6')
    from PySide6.QtWidgets import QApplication
    from anyfem.ui.qt.app import QtFemWindow
    from anyfem.io import recovery
    monkeypatch.setattr(recovery,'default_recovery_root',lambda:tmp_path/'recovery')
    app=QApplication.instance() or QApplication([])
    window=QtFemWindow(viewer_backend='software')
    try:
        controls=window.panels['Visualization'].presentation_controls
        before=window.session.revision.model_hash
        controls.apply_box()
        assert window.viewport.canvas.section_box is not None
        assert window.session.dirty and window.session.revision.model_hash==before
        record=controls.record();controls.restore_record(record)
        controls.compare.setChecked(True)
        assert controls.reference is not None and controls.reference is not window.viewport
        assert controls.reference.canvas is not window.viewport.canvas
        assert controls.reference.selection is not window.selection
        controls.compare.setChecked(False)
        assert window.active_job_id is None
    finally:
        window.session.mark_saved();window.close();app.processEvents()

def test_canonical_nodal_connectivity_survives_cold_warm_frames():
    from test_result_frame_reuse import setup,scene
    from anyfem.presentation.frame_updates import snapshot,prepare_frame
    import numpy as np
    view,mesh=setup();first=scene(mesh)
    first.faces[0].polygon_node_ids=[(4,2,1,3)]
    first.result_field=Field('ux',node_values={1:10.,2:20.,3:30.,4:40.})
    prepare_frame(snapshot(view),first)
    batch=first._prepared_result_batches[1][0][0]
    np.testing.assert_array_equal(batch.node_scalars,[40.,20.,10.,30.])
    from any3dview.webgpu.renderer import scalar_payload
    from any3dview.appearance import validated_appearance
    assert batch.element_scalars is None and scalar_payload(batch,validated_appearance())[0]==1
    view._render_scene(first)
    second=scene(mesh,.2)
    second.faces[0].polygon_node_ids=[(4,2,1,3)]
    second.result_field=Field('ux',node_values={1:100.,2:200.,3:300.,4:400.})
    prepare_frame(snapshot(view),second,first._retained_frame_layout)
    view._render_scene(second)
    np.testing.assert_array_equal(view._retained_result[1][0].mesh.node_scalars,[400.,200.,100.,300.])
    assert view._retained_result[1][0].mesh.element_scalars is None
    assert scalar_payload(view._retained_result[1][0].mesh,validated_appearance())[0]==1
    view.update_result_field(Field('ux',node_values={1:1.,2:2.,3:3.,4:4.}),scalar_range=(0,5),colormap=first.result_colormap,legend={})
    np.testing.assert_array_equal(view._retained_result[1][0].mesh.node_scalars,[4.,2.,1.,3.])
    assert view._retained_result[1][0].mesh.element_scalars is None
    assert scalar_payload(view._retained_result[1][0].mesh,validated_appearance())[0]==1


def test_atomic_animation_encoding_preserves_previous_on_cancel(tmp_path):
    from threading import Event
    from PIL import Image
    from anyfem.ui.qt.animation_export import encode_frames
    directory=tmp_path/'frames';directory.mkdir()
    Image.new('RGB',(8,8),'red').save(directory/'frame-000000.png')
    destination=tmp_path/'sequence.zip';destination.write_bytes(b'prior')
    cancelled=Event();cancelled.set()
    assert encode_frames(directory,destination,'PNG sequence ZIP',20,cancelled) is None
    assert destination.read_bytes()==b'prior'
    assert encode_frames(directory,destination,'PNG sequence ZIP',20,Event())==destination
    import zipfile
    with zipfile.ZipFile(destination) as archive:assert archive.namelist()==['frame-000000.png']


def test_optional_encoder_absence_and_changed_binary_refuse(tmp_path):
    from anyfem.ui.qt.optional_encoder import probe_encoder,encode_mp4,EncoderUnavailable
    with pytest.raises(EncoderUnavailable):probe_encoder(tmp_path/'absent.exe')
    executable=tmp_path/'ffmpeg.exe';executable.write_bytes(b'changed')
    with pytest.raises(EncoderUnavailable):encode_mp4(tmp_path,tmp_path/'output.mp4',20,threading.Event(),dict(path=str(executable),sha256='wrong',codec='mpeg4'))

def test_optional_encoder_cancel_terminates_and_reaps_owned_process(tmp_path,monkeypatch):
    from anyfem.ui.qt import optional_encoder as encoder
    executable=tmp_path/'encoder.exe';executable.write_bytes(b'fixture')
    cancelled=threading.Event();cancelled.set();events=[]
    class Process:
        returncode=None
        def poll(self):return self.returncode
        def terminate(self):events.append('terminate');self.returncode=-15
        def kill(self):events.append('kill');self.returncode=-9
        def wait(self,timeout=None):events.append('wait');return self.returncode
    monkeypatch.setattr(encoder.subprocess,'Popen',lambda *a,**k:Process())
    record=dict(path=str(executable),sha256=encoder.file_hash(executable),codec='mpeg4')
    with pytest.raises(encoder.EncodingCancelled):encoder.encode_mp4(tmp_path,tmp_path/'movie.mp4',20,cancelled,record)
    assert events==['terminate','wait']


def test_independent_reference_survives_main_invalidation():
    from anyfem.application.result_requests import ResultRequests
    callbacks=[];outcomes=[];release=threading.Event();coordinator=ResultRequests(callbacks.append)
    coordinator.request(lambda cancel:release.wait(.05),outcomes.append,channel='reference:display',independent=True)
    coordinator.invalidate(source=True,reads=True);release.set()
    deadline=time.monotonic()+2
    while not outcomes and time.monotonic()<deadline:
        while callbacks:callbacks.pop(0)()
        time.sleep(.002)
    coordinator.close()
    assert len(outcomes)==1 and not outcomes[0].cancelled

def test_animation_cancellation_restores_original_frame_and_preserves_output(tmp_path):
    from types import SimpleNamespace
    from anyfem.ui.qt.animation_export import AnimationExport
    class Value:
        def __init__(self,value):self.value_=value
        def value(self):return self.value_
    frame=Value(4);restores=[]
    panel=SimpleNamespace(frame=frame,_set_frame_index=lambda value:setattr(frame,'value_',value),show_results=lambda:restores.append(frame.value()))
    controls=SimpleNamespace(app=None,export_status=SimpleNamespace(setText=lambda value:None))
    app=SimpleNamespace(panels={'Results':panel},_closing=False,guarded=lambda callback:callback)
    controls.app=app;export=AnimationExport(controls);export.context=lambda:('same',)
    import tempfile
    directory=tempfile.TemporaryDirectory(dir=tmp_path)
    export.state=dict(context=('same',),original=2,expected_frame=4,cancel=threading.Event(),future=None,directory=directory)
    export.cancel()
    assert frame.value()==2 and restores==[2] and export.state is None


def test_annotation_missing_mesh_is_stale_and_empty_has_no_numeric_extrema():
    from types import SimpleNamespace
    from anyfem.ui.qt.presentation_controls import PresentationControls
    from anyfem.ui.scene import Scene
    assert annotation_stale({'document_id':'a','mesh_identity':None},'a',None)
    records=[]
    controls=SimpleNamespace(app=SimpleNamespace(panels={'Results':SimpleNamespace(field_name=lambda:'ux',component=SimpleNamespace(text=lambda:''),frame=SimpleNamespace(value=lambda:0),scale=SimpleNamespace(text=lambda:'1'))},
        viewport=SimpleNamespace(_scene=Scene(result_field=Field('ux','m'))),project=SimpleNamespace(document_id='a'),active_job_id='job',selection=SimpleNamespace(items=[])),
        mesh_identity=lambda:'mesh',annotation_name=SimpleNamespace(text=lambda:'group'),persist=lambda change,label:change({'annotations':records}),update_annotations=lambda:None)
    PresentationControls.save_annotation(controls)
    assert records[0]['extrema']=={'available':False} and records[0]['pins']==[]

def test_pins_use_canonical_positions_frame_scale_and_stable_callouts():
    from types import SimpleNamespace
    import numpy as np
    from anyfem.ui.qt.presentation_controls import PresentationControls
    from anyfem.ui.scene import Scene,FacePatch,MeshEntityRef
    class Group:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def remove(self):pass
    labels=[]
    canvas=SimpleNamespace(annotation_group=Group,add_text=lambda point,text,**options:labels.append((point,text,options)))
    field=Field('ux','m',node_values={7:3.,9:-1.,12:2.})
    patch=FacePatch(MeshEntityRef('element',1),[np.array([[1,2,3],[2,2,3],[1,3,3]])],['red'],polygon_node_ids=[(7,9,12)])
    record=dict(document_id='doc',mesh_identity='mesh',group='Inspection',job='job',field='ux',unit='m',component='',pins=[dict(kind='node',id=7,role='probe')])
    results=SimpleNamespace(component=SimpleNamespace(text=lambda:''),frame=SimpleNamespace(value=lambda:4),scale=SimpleNamespace(text=lambda:'2'))
    app=SimpleNamespace(viewport=SimpleNamespace(_scene=Scene(faces=[patch],result_field=field),canvas=canvas),project=SimpleNamespace(document_id='doc',presentation={'annotations':[record]}),panels={'Results':results})
    controls=SimpleNamespace(app=app,annotations_visible=SimpleNamespace(isChecked=lambda:True),mesh_identity=lambda:'mesh')
    PresentationControls.update_annotations(controls)
    point,text,options=labels[0]
    assert point.to_tuple()==(1.,2.,3.) and 'frame 5' in text and 'deformation 2' in text
    assert options['callout'] is True and options['callout_key']=='doc/Inspection/job/0/0'


def test_annotation_group_visibility_edits_only_named_group():
    from types import SimpleNamespace
    from anyfem.ui.qt.presentation_controls import PresentationControls
    state={'annotations':[{'group':'A'},{'group':'B'}]}
    controls=SimpleNamespace(annotation_name=SimpleNamespace(text=lambda:'A'),app=SimpleNamespace(project=SimpleNamespace(presentation=state)),
        persist=lambda change,label:change(state),update_annotations=lambda:None)
    PresentationControls.toggle_annotation_group(controls)
    assert state['annotations']==[{'group':'A','hidden':True},{'group':'B'}]
    PresentationControls.toggle_annotation_group(controls)
    assert state['annotations'][0]['hidden'] is False

def test_manual_frame_change_is_not_overwritten_on_export_cancel(tmp_path):
    from types import SimpleNamespace
    from anyfem.ui.qt.animation_export import AnimationExport
    from tempfile import TemporaryDirectory
    frame=SimpleNamespace(value_=9);restored=[]
    frame.value=lambda:frame.value_
    panel=SimpleNamespace(frame=frame,_set_frame_index=lambda value:restored.append(value),show_results=lambda:None)
    controls=SimpleNamespace(app=SimpleNamespace(panels={'Results':panel},_closing=False),export_status=SimpleNamespace(setText=lambda text:None))
    export=AnimationExport(controls);export.context=lambda:('same',)
    export.state=dict(context=('same',),original=2,expected_frame=4,cancel=threading.Event(),future=None,directory=TemporaryDirectory(dir=tmp_path))
    export.cancel()
    assert restored==[] and frame.value()==9


def test_actual_request_access_migration_preserves_reference_lease_and_close():
    from types import SimpleNamespace
    from anyfem.application.workflow import WorkbenchWorkflow
    from anyfem.application.result_requests import Completion
    old=LiveResultAccess(cache_bytes=100000,working_bytes=20000);old.commit_publication(111);old.commit_pane_publication('reference',222)
    dataset=object();published=[]
    def request(load,complete,**kwargs):complete(Completion(value=load(threading.Event())))
    app=SimpleNamespace(_result_loader=SimpleNamespace(request=request),_closing=False,
        solution=None,project=object(),mesh=None,panels={},result_datasets={'job':dataset},
        _result_revision=0,_live_access_revision=0,_live_result_access=old,_take_retired_result_datasets=lambda:())
    WorkbenchWorkflow._request_result(app,lambda cancel:42,published.append,dataset=dataset,channel='inspection',_status_stamp=())
    current=app._live_result_access
    assert old._closed and current is not old and published==[42]
    assert current._pane_publications=={'main':111,'reference':222}
    assert (current.cache_bytes,current.working_bytes)==(100000,20000)
    current.commit_publication(444)
    assert current._publication_bytes==666
    old.release_pane_publication('reference')
    assert current._publication_bytes==444 and old.publication_owner() is current
    current.close()


def test_real_presentation_transaction_does_not_advance_source_epoch():
    from types import SimpleNamespace
    from anyfem.application.workflow import WorkbenchWorkflow
    session=DocumentSession(Project())
    class App(WorkbenchWorkflow):
        def invalidate_result_requests(self,*,source=False,reads=False):
            if source:self.epoch+=1
    app=App();app.epoch=0
    app.workbench=SimpleNamespace(active_model_hash=session.revision.model_hash)
    app.workbench.update=lambda key,value:setattr(app.workbench,key,value)
    session.add_listener(lambda revision:setattr(app,'_active_model_hash',revision.model_hash))
    store_presentation(session,lambda state:state.update(workspace={'label':'presentation only'}))
    assert session.dirty and app.epoch==0
    app._active_model_hash='changed-engineering-model'
    assert app.epoch==1

def test_reassigning_mutable_source_identity_still_invalidates():
    from types import SimpleNamespace
    from anyfem.application.workflow import WorkbenchWorkflow
    class App(WorkbenchWorkflow):
        def invalidate_result_requests(self,*,source=False,reads=False):self.invalidations.append((source,reads))
    app=App();app.invalidations=[];source=SimpleNamespace(changed=True)
    app.workbench=SimpleNamespace(solution=source,project=source,mesh=source)
    app.workbench.update=lambda key,value:setattr(app.workbench,key,value)
    for key in ('solution','project','mesh'):setattr(app,key,source)
    assert app.invalidations==[(True,False)]*3

def test_publication_migration_and_reference_release_are_atomic():
    from collections import UserDict
    entered=threading.Event();proceed=threading.Event();released=threading.Event()
    class BlockingMap(UserDict):
        def __iter__(self):
            entered.set()
            assert proceed.wait(1)
            return iter(self.data)
    old=LiveResultAccess();new=LiveResultAccess()
    old._pane_publications=BlockingMap({'main':11,'reference':22});old._publication_bytes=33
    migration=threading.Thread(target=lambda:old.migrate_publications_to(new))
    release=threading.Thread(target=lambda:(old.release_pane_publication('reference'),released.set()))
    migration.start();assert entered.wait(1)
    release.start()
    assert not released.wait(.02)
    proceed.set();migration.join(1);release.join(1)
    assert not migration.is_alive() and not release.is_alive()
    assert released.is_set() and new._pane_publications=={'main':11}
    assert new._publication_bytes==11 and old.publication_owner() is new
    assert new._publication_lock is old._publication_lock
    old.close();new.close()

def test_cached_nodal_frames_retain_actual_renderer_node_domain(tmp_path):
    from test_result_frame_reuse import setup,scene
    from anyfem.presentation.frame_updates import snapshot,prepare_frame
    from anyfem.presentation.frame_cache import _dump,PreparedFrames
    from any3dview.webgpu.renderer import scalar_payload
    from any3dview.appearance import validated_appearance
    view,mesh=setup();frame=scene(mesh)
    frame.faces[0].polygon_node_ids=[(4,2,1,3)]
    frame.result_field=Field('ux',node_values={1:1.,2:2.,3:3.,4:4.})
    prepared=prepare_frame(snapshot(view),frame);key='nodal'
    _dump(tmp_path/f'layout-{key}.pkl.gz',(prepared[1],prepared[3],prepared[4]))
    positions=[batch.positions for batch,options in prepared[1]]
    scalars=[(batch.element_scalars,batch.node_scalars) for batch,options in prepared[1]]
    frame.result_mesh=None
    _dump(tmp_path/'frame-0.pkl.gz',(frame,key,prepared[0],positions,scalars,prepared[2]))
    loaded=PreparedFrames(tmp_path,mesh,(),(0,)).load(0,threading.Event())
    for batch,options in loaded._prepared_result_batches[1]:
        assert batch.element_scalars is None
        assert scalar_payload(batch,validated_appearance())[0]==1

def test_new_project_clears_previous_presentation_without_dirtying(tmp_path,monkeypatch):
    pytest.importorskip('PySide6')
    from PySide6.QtWidgets import QApplication
    from anyfem.ui.qt.app import QtFemWindow
    from anyfem.io import recovery
    monkeypatch.setattr(recovery,'default_recovery_root',lambda:tmp_path/'recovery')
    app=QApplication.instance() or QApplication([])
    window=QtFemWindow(viewer_backend='software')
    try:
        controls=window.panels['Visualization'].presentation_controls
        controls.apply_box();controls.compare.setChecked(True)
        window.viewport.set_section_plane(normal=(1,0,0),offset=3)
        assert window.viewport.canvas.section_box is not None and controls.compare.isChecked()
        new=Project();window._set_project(new)
        assert window.project is new and new.presentation=={}
        assert not window.session.dirty
        assert not controls.compare.isChecked() and not controls.box_enabled.isChecked()
        assert window.viewport.canvas.section_box is None
        state=window.viewport.canvas.export_view_state()
        assert state.section_plane is None or not state.section_plane.enabled
        assert controls.contour.currentText()=='Continuous'
        assert controls.reference.widget.isHidden()
        assert window.session.revision.model_hash==DocumentSession(new).revision.model_hash
    finally:
        window.session.mark_saved();window.close();app.processEvents()

def glyph_harness(*,count=2000):
    from types import SimpleNamespace,MethodType
    from queue import SimpleQueue
    import numpy as np
    from anyfem.application.result_requests import ResultRequests
    from anyfem.ui.qt.presentation_controls import PresentationControls
    from anyfem.ui.scene import Scene,FacePatch,MeshEntityRef
    owner=threading.get_ident();reads=[];entered=threading.Event();release=threading.Event();release.set()
    class Value:
        def __init__(self,value):self.selected=value
        def value(self):return self.selected
        def text(self):return str(self.selected)
        def currentText(self):return str(self.selected)
        def isChecked(self):return bool(self.selected)
    class Stored:
        shape=(2,count,3)
        descriptor=SimpleNamespace(location='node',basis='global',components=('ux','uy','uz'))
        def root_frame_index(self,index):return index
        def read(self,index):
            reads.append(threading.get_ident());entered.set();assert release.wait(2)
            return np.tile([1.,0.,0.],(count,1))
    stored=Stored();dataset=SimpleNamespace(field_keys=('displacement',),table_keys=('displacement_node_ids',),
        field=lambda name:stored,table=lambda name:np.arange(1,count+1))
    mesh=SimpleNamespace(nodes={index:(float(index),0.,0.) for index in range(1,count+1)})
    polygon=np.asarray(list(mesh.nodes.values()),dtype=float)
    scene=Scene(faces=[FacePatch(MeshEntityRef('element',1),[polygon],['red'],polygon_node_ids=[tuple(range(1,count+1))])],result_mesh=mesh)
    scene.characteristic_size=lambda:100.
    handles=[];groups=[]
    class Group:
        def __init__(self):self.removed=False;groups.append(self)
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def remove(self):self.removed=True
    canvas=SimpleNamespace(annotation_group=Group,redraw=lambda:None,
        add_mesh_arrays=lambda mesh,**options:handles.append((mesh,options,threading.get_ident())))
    queue=SimpleQueue();coordinator=ResultRequests(queue.put)
    panel=SimpleNamespace(frame=Value(0),field_name=lambda:'displacement',component=Value('ux'),playback_active=False)
    access=LiveResultAccess()
    app=SimpleNamespace(panels={'Results':panel},session=object(),project=Project(),active_job_id='job',result_datasets={'job':dataset},
        viewport=SimpleNamespace(_scene=scene,canvas=canvas),_result_source_epoch=0,_closing=False,_result_loader=coordinator,
        _live_result_access=access,set_status=lambda *args,**kwargs:None)
    controls=SimpleNamespace(app=app,glyph_count=Value(2000),glyph_scale=Value(1),glyph_normalize=Value(True),glyph_rank=Value('All'),
        status=SimpleNamespace(setText=lambda text:None),animation_export=SimpleNamespace(state=None))
    for name in ('_request_glyphs','_glyph_context','clear_glyphs','maintain_glyphs','pause_glyphs'):
        setattr(controls,name,MethodType(getattr(PresentationControls,name),controls))
    def settle():
        from queue import Empty
        deadline=time.monotonic()+3
        while coordinator._records and time.monotonic()<deadline:
            try:queue.get_nowait()()
            except Empty:time.sleep(.002)
        assert not coordinator._records
    return SimpleNamespace(controls=controls,stored=stored,dataset=dataset,mesh=mesh,scene=scene,reads=reads,owner=owner,
        entered=entered,release=release,handles=handles,groups=groups,coordinator=coordinator,access=access,settle=settle)


def test_async_glyphs_read_off_owner_and_publish_one_batch_for_2000_vectors():
    harness=glyph_harness()
    try:
        harness.controls._request_glyphs('vector');harness.settle()
        assert harness.reads and all(thread!=harness.owner for thread in harness.reads)
        assert len(harness.handles)==1 and harness.handles[0][2]==harness.owner
        mesh,options,_=harness.handles[0]
        assert len(mesh.lines)==6000 and options['pickable'] is False
        assert harness.controls._glyph_group is not None
        assert 'glyphs' in harness.access._pane_publications
        harness.controls.clear_glyphs()
        assert harness.controls._glyph_group is None and 'glyphs' not in harness.access._pane_publications
    finally:harness.release.set();harness.coordinator.close();harness.access.close()


@pytest.mark.parametrize('change',('clear','frame','source'))
def test_async_glyph_late_completion_is_refused_after_clear_or_source_change(change):
    harness=glyph_harness(count=4);harness.release.clear()
    try:
        harness.controls._request_glyphs('vector');assert harness.entered.wait(1)
        if change=='clear':harness.controls.clear_glyphs()
        elif change=='frame':harness.controls.app.panels['Results'].frame.selected=1
        else:harness.controls.app.result_datasets['job']=object()
        harness.release.set();harness.settle()
        assert harness.handles==[] and getattr(harness.controls,'_glyph_group',None) is None
    finally:harness.release.set();harness.coordinator.close();harness.access.close()


def test_glyph_source_frame_is_refused_before_read_when_working_ceiling_exceeded():
    from anyfem.ui.qt.glyph_preparation import prepare_glyphs
    from anypost.errors import WorkingSetError
    harness=glyph_harness(count=4)
    try:
        settings=dict(mode='vector',field='displacement',frame=0,count=2000,scale=1.,normalized=True,rank='All')
        with pytest.raises(WorkingSetError):prepare_glyphs(harness.dataset,harness.mesh,harness.scene,settings,threading.Event(),1000)
        assert harness.reads==[]
    finally:harness.coordinator.close();harness.access.close()

def test_async_principal_axes_use_at_most_three_rank_batches():
    import numpy as np
    from types import SimpleNamespace
    harness=glyph_harness(count=8)
    try:
        harness.controls.app.panels['Results'].field_name=lambda:'stress'
        harness.dataset.field_keys=('stress',);harness.dataset.table_keys=('stress_node_ids',)
        harness.stored.shape=(2,8,6)
        harness.stored.descriptor=SimpleNamespace(location='node',basis='global',unit='Pa',components=('xx','yy','zz','xy','yz','xz'))
        def read(frame):
            harness.reads.append(threading.get_ident())
            return np.tile([3.,2.,1.,0.,0.,0.],(8,1))
        harness.stored.read=read
        harness.controls._request_glyphs('principal');harness.settle()
        assert len(harness.handles)==3
        assert all(len(batch.lines)==8*2*3 and not options['pickable'] for batch,options,thread in harness.handles)
        assert all(thread!=harness.owner for thread in harness.reads)
        harness.controls.clear_glyphs()
    finally:harness.coordinator.close();harness.access.close()


def test_published_glyphs_hide_on_frame_change_and_pause_during_playback():
    harness=glyph_harness(count=4)
    try:
        harness.controls._request_glyphs('vector');harness.settle();old=harness.controls._glyph_group
        harness.controls.app.panels['Results'].frame.selected=1
        harness.controls.maintain_glyphs()
        assert old.removed and harness.controls._glyph_group is None
        harness.controls._glyph_stable=(harness.controls._glyph_context(),time.monotonic()-.3)
        harness.controls.maintain_glyphs();harness.settle()
        assert harness.controls._glyph_group is not None and harness.controls._glyph_group is not old
        harness.controls.app.panels['Results'].playback_active=True
        harness.controls.maintain_glyphs()
        assert harness.controls._glyph_group is None and not harness.coordinator._records
        assert harness.controls._glyph_mode=='vector'
        harness.controls.clear_glyphs()
        assert harness.controls._glyph_mode is None
    finally:harness.coordinator.close();harness.access.close()

def test_glyph_worker_anchors_follow_displayed_deformation_not_reference_mesh():
    from anyfem.ui.qt.glyph_preparation import prepare_glyphs
    import numpy as np
    harness=glyph_harness(count=4)
    try:
        displayed=harness.scene.faces[0].polygons[0]
        displayed[:,2]=37.0  # exaggerated displayed deformation
        settings=dict(mode='vector',field='displacement',frame=0,count=2000,scale=1.,normalized=True,rank='All')
        batches,caption=prepare_glyphs(harness.dataset,harness.mesh,harness.scene,settings,threading.Event(),harness.access.working_bytes)
        np.testing.assert_array_equal(batches[0][0].positions[::6,2],37.)
        assert all(position[2]==0 for position in harness.mesh.nodes.values())
        harness.scene.faces[0].polygon_node_ids=None
        with pytest.raises(ValueError,match='displayed position'):
            prepare_glyphs(harness.dataset,harness.mesh,harness.scene,settings,threading.Event(),harness.access.working_bytes)
    finally:harness.coordinator.close();harness.access.close()
