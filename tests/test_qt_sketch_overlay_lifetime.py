"""Draft annotation lifetime leaves retained model and unrelated text alone."""
import os
import numpy as np
import pytest
if os.environ.get('ANYFEM_RUN_QT_TESTS')!='1':pytest.skip('Qt opt-in',allow_module_level=True)
from any3dview import MeshArrays,Point3D
from test_qt_geometry_actions import qapp,window
from test_qt_sketcher_workflow import enter_sketch,click_uv


def test_annotation_group_removal_and_exception_are_scoped(window,qapp):
    canvas=window.viewport.canvas
    original=canvas.add_mesh_arrays(MeshArrays(np.array([[0.,0.,0.],[1.,0.,0.]]),np.empty((0,3),np.uint32),lines=np.array([[0,1]],np.uint32)))
    canvas.add_text(Point3D(0,0,0),'keep')
    with canvas.annotation_group() as group:
        canvas.add_text(Point3D(0,0,0),'draft')
        canvas.add_line(Point3D(0,0,0),Point3D(0,1,0))
    handles=tuple(group.handles)
    assert handles and not original.removed
    group.remove();group.remove()
    assert all(h.removed for h in handles) and not original.removed
    assert [v['text'] for v in canvas._world_text]==['keep']
    with pytest.raises(ValueError):
        with canvas.annotation_group() as failed:
            canvas.add_text(Point3D(0,0,0),'failed')
            raise ValueError('rollback')
    assert failed.removed and [v['text'] for v in canvas._world_text]==['keep']


def test_warm_sketch_preview_retains_scene_and_bounded_annotations(window,qapp,monkeypatch):
    panel=enter_sketch(window,qapp);click_uv(window,qapp,(1,1));click_uv(window,qapp,(3,1))
    viewport=window.viewport;task=viewport.construction_task
    cleared=[];original=viewport.canvas.clear
    def clear(*args,**kwargs):cleared.append(1);return original(*args,**kwargs)
    monkeypatch.setattr(viewport.canvas,'clear',clear)
    counts=[]
    for i in range(8):
        task.hover=task.plane.world((2+i*.01,2));viewport.refresh_construction_overlay()
        counts.append((len(viewport.canvas._entries),len(viewport.canvas._world_text)))
    assert not cleared and len(set(counts))==1
    original(keep_canvas=True)
    viewport.refresh_construction_overlay()
    assert len(cleared)==1 # external clear invalidates the background lifetime guard
    panel.cancel();qapp.processEvents()
    assert viewport._construction_overlay_group is None
