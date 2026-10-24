from anygeometry import EntityRef
from anyfem.selection import Selection
from anyfem.ui.scene import Scene,FacePatch
from test_viewport_commercial_selection import _viewport,_event,_hit
from any3dview.selection import SelectionGesture,SelectionOperation
from types import SimpleNamespace
import numpy as np


def patch(grouped=True):
    faces=(EntityRef('face',1),EntityRef('face',2))
    square=np.asarray(((0,0,0),(1,0,0),(1,1,0),(0,1,0)))
    return FacePatch(faces[0],[square,square+(2,0,0)],['blue','blue'],owners=faces,
        polygon_owners=[(faces[0],),(faces[1],)],grouped_pick=grouped)


def test_grouped_native_hits_expand_once_but_programmatic_refs_stay_exact(monkeypatch):
    selection=Selection('face');viewport=_viewport(monkeypatch,selection)
    viewport.canvas.capabilities=SimpleNamespace(smooth_normals=True)
    viewport.show(Scene(faces=[patch()]))
    callback=viewport.canvas.selection_callback
    callback(_event(_hit(1)))
    assert selection.items==[EntityRef('face',1),EntityRef('face',2)]
    callback(_event(_hit(1),_hit(2),operation=SelectionOperation.TOGGLE,gesture=SelectionGesture.WINDOW))
    assert not selection.items
    selection.apply([EntityRef('face',2)])
    assert selection.items==[EntityRef('face',2)]
    options=viewport.canvas.face_calls[-1][1]
    assert 'smooth_angle' in options
    assert options['tags']==''
    assert [len([owner for owner in binding.owners if owner.kind=='geometry.face']) for binding in options['bindings']]==[1,1]
    assert all(any(owner.kind=='presentation.face_group' for owner in binding.owners) for binding in options['bindings'])


def test_exposed_native_hits_remain_individual(monkeypatch):
    selection=Selection('face');viewport=_viewport(monkeypatch,selection);viewport.show(Scene(faces=[patch(False)]))
    viewport.canvas.selection_callback(_event(_hit(2)))
    assert selection.items==[EntityRef('face',2)]
