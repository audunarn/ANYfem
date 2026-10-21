"""First publication transfer and current-result appearance regressions."""
from dataclasses import replace
from types import SimpleNamespace
from uuid import UUID

import numpy as np
import pytest

from any3dview import PickBinding, PickOwner
from any3dview.ownership import ModelOwner
from anyfem.post.fields import Field
from anyfem.presentation import retained_results
from anyfem.presentation.retained_results import prepare, geometry_style_key
from anyfem.presentation.viewport import SceneViewport
from anyfem.presentation.visibility import SelectionVisibility
from anyfem.selection import MeshEntityRef
from anyfem.ui.scene import Scene, FacePatch, Polyline
from anyfem.ui.visualization import VisualizationStyle


def result_scene():
    owner = MeshEntityRef('element', 1)
    return Scene(faces=[FacePatch(owner, [np.array([[0., 0., 0.], [1., 0., 0.],
                                                   [0., 1., 0.]])], ['red'])],
                 result_field=Field('ux', 'm', element_values={1: 2.}),
                 result_mesh=SimpleNamespace(shells={1: (1, 2, 3)}, beams={}))


def producer(binding=None):
    return SimpleNamespace(visualization=VisualizationStyle(),
        _face_style=lambda patch: ('black', 1., True),
        _pick_binding=lambda *args: binding)


@pytest.mark.parametrize('change', ['coordinate', 'dtype', 'mapping', 'key', 'kind',
                                   'priority', 'document', 'overlay', 'style'])
def test_complete_prepared_identity_detects_mutations(change):
    scene = result_scene()
    binding = PickBinding((PickOwner('one', 'mesh.element', 10),))
    if change == 'document':binding = (ModelOwner(UUID(int=1), 'element', 1),)
    view = producer(binding)
    before = prepare(view, scene)[0]
    if change == 'coordinate':scene.faces[0].polygons[0][1, 0] = 7.
    elif change == 'dtype':scene.faces[0].polygons[0] = scene.faces[0].polygons[0].astype('f4')
    elif change == 'mapping':scene.faces[0].ref = MeshEntityRef('element', 2)
    elif change in {'key', 'kind', 'priority'}:
        owner = replace(binding.owners[0], **{change: {'key': 'two', 'kind': 'face', 'priority': 9}[change]})
        view._pick_binding = lambda *args: PickBinding((owner,))
    elif change == 'document':view._pick_binding = lambda *args: (ModelOwner(UUID(int=2), 'element', 1),)
    elif change == 'overlay':scene.lines.append(Polyline(None, np.zeros((2, 3)), 'blue'))
    else:view.visualization = replace(view.visualization, edge_width=2)
    assert prepare(view, scene)[0] != before


def test_field_and_legend_do_not_invalidate_geometry():
    scene = result_scene(); view = producer()
    before = prepare(view, scene)[0]
    scene.result_field.element_values[1] = 99.
    scene.legend = {'title': 'new field'}
    assert prepare(view, scene)[0] == before


@pytest.mark.parametrize('container', [PickBinding, tuple, list])
def test_custom_identity_keeps_primitive_association(container):
    scene = result_scene(); patch = scene.faces[0]
    patch.polygons = patch.polygons * 3
    view = producer()
    def key(identities):
        bindings = iter(container((PickOwner('same', 'mesh.element', 10, identity=value),))
                        for value in identities)
        view._pick_binding = lambda *args: next(bindings)
        return prepare(view, scene)[0]
    assert key(['x', 'y', 'x']) != key(['x', 'y', 'y'])


@pytest.mark.parametrize('hidden', [False, True])
def test_qt_transfers_preparation_only_for_unfiltered_copy(monkeypatch, hidden):
    pytest.importorskip('PySide6')
    from anyfem.ui.qt.viewport import QtViewport
    scene = result_scene(); prepared = object(); scene._prepared_result_batches = prepared
    view = SimpleNamespace(_window=SimpleNamespace(project=None, mesh=None, active_job_id=None),
        _visibility_mesh=None, _visibility_job=None, visibility=SelectionVisibility())
    if hidden:view.visibility.hidden.add(('element', 1))
    captured = []
    monkeypatch.setattr(SceneViewport, 'show', lambda self, visible, **kw: captured.append(visible))
    # super() requires an actual QtViewport instance, but no widget/context.
    instance = QtViewport.__new__(QtViewport); instance.__dict__.update(view.__dict__)
    QtViewport.show(instance, scene)
    assert getattr(captured[0], '_prepared_result_batches', None) is (None if hidden else prepared)
    assert not hasattr(scene, '_prepared_result_batches')
    assert bool(captured[0].faces) is (not hidden)


class Canvas:
    backend_name = 'gpu'
    event_widget = SimpleNamespace()
    def __init__(self):self.calls = []; self.draws = 0; self.fail = None
    def set_mesh_opacity(self, handle, opacity):
        if handle is self.fail:raise RuntimeError('update refused')
        self.calls.append((handle, opacity))
    def redraw(self):self.draws += 1


def opacity_view(mode='Shaded with edges'):
    view = SceneViewport.__new__(SceneViewport)
    view.canvas = Canvas(); view._visualization = VisualizationStyle(render_mode=mode)
    face = SimpleNamespace(removed=False, mesh=SimpleNamespace(element_scalars=np.array([2.])))
    line = SimpleNamespace(removed=False, mesh=SimpleNamespace(element_scalars=np.array([3.])))
    view._retained_result = (b'key', [face, line], (np.array([1]), np.array([2])), object())
    view._retained_result_canvas = view.canvas
    view._retained_opacity = (view._retained_result, (face,))
    view._scene = result_scene(); view._interaction_scene_active = False
    view._visibility_scene = view._scene; view.visibility = SelectionVisibility()
    view._refresh_visualization_scene = lambda: view.canvas.calls.append('fallback')
    return view, face, line


@pytest.mark.parametrize('mode,expected', [('Shaded with edges', .4), ('Wireframe', 0.)])
def test_opacity_retains_handles_scalars_and_next_field_identity(monkeypatch, mode, expected):
    view, face, line = opacity_view(mode)
    retained = view._retained_result; scalars = face.mesh.element_scalars
    monkeypatch.setattr(retained_results, 'prepare', lambda *a: pytest.fail('reprepared'))
    before = geometry_style_key(view.visualization)
    view.set_visualization(replace(view.visualization, surface_opacity=.4))
    assert view.canvas.calls == [(face, expected)]
    assert view._retained_result is retained and face.mesh.element_scalars is scalars
    assert view._visibility_scene is view._scene and view.canvas.draws == 1
    assert geometry_style_key(view.visualization) == before
    assert geometry_style_key(replace(view.visualization, edge_width=2)) == before
    assert geometry_style_key(replace(view.visualization, show_beam_sections=False)) != before


@pytest.mark.parametrize('reason', ['removed', 'canvas', 'software', 'missing', 'hidden', 'style', 'unsupported'])
def test_unsupported_opacity_path_rebuilds(reason):
    view, face, line = opacity_view(); style = replace(view.visualization, surface_opacity=.4)
    if reason == 'removed':face.removed = True
    elif reason == 'canvas':view._retained_result_canvas = object()
    elif reason == 'software':view.canvas.backend_name = 'software'
    elif reason == 'missing':view.canvas.set_mesh_opacity = None
    elif reason == 'hidden':view.visibility.hidden.add(('element', 1))
    elif reason == 'style':style = replace(style, edge_width=2)
    else:view._retained_opacity = None
    view.set_visualization(style)
    assert view.canvas.calls == ['fallback']


def test_failed_opacity_update_rolls_back_and_preserves_style():
    view, face, second = opacity_view(); old = view.visualization
    view._retained_opacity = (view._retained_result, (face, second)); view.canvas.fail = second
    with pytest.raises(RuntimeError, match='update refused'):
        view.set_visualization(replace(old, surface_opacity=.4))
    assert view.visualization is old
    assert view.canvas.calls == [(face, .4), (face, 1.)]


def test_clear_releases_opacity_handles():
    view, face, line = opacity_view()
    view._cancel_interaction_restore = lambda: None
    view._clear_hover_state = lambda: None
    view.canvas.clear = lambda **kwargs: None
    view.clear()
    assert view._retained_result is None and view._retained_opacity is None


def test_nonresult_show_releases_opacity_handles(monkeypatch):
    from anyfem.presentation import retained_scene
    monkeypatch.setattr(retained_scene, 'scene_key', lambda *args: None)
    view, face, line = opacity_view()
    view.canvas.clear = lambda **kwargs: None
    view._clear_hover_state = lambda: None
    view._draw = lambda scene: None
    view._draw_construction_overlay = lambda: None
    view._apply_highlight = lambda: None
    view._render_scene(Scene())
    assert view._retained_result is None and view._retained_opacity is None


def test_field_after_opacity_preserves_handles_and_qt_visibility_source(monkeypatch):
    view, face, line = opacity_view()
    retained = view._retained_result
    updates = []
    for handle in (face, line):
        handle.update_scalar_fields = lambda **values: updates.append(values['element_scalars'])
        handle.update_appearance = lambda **values: None
    view._draw_legend = lambda scene: None
    monkeypatch.setattr(retained_results, 'prepare', lambda *a: pytest.fail('geometry reloaded'))
    monkeypatch.setattr(retained_results, 'prepare_warm_field', lambda *a, **kw: pytest.fail('field reloaded'))
    view.set_visualization(replace(view.visualization, surface_opacity=.4))
    field = Field('uy', 'm', element_values={1: 8.})
    field._display_buffers = (b'key', (np.array([8.]), np.array([9.])), ([], []))
    assert view.update_result_field(field, scalar_range=(0., 9.), colormap=None, legend={})
    assert view._retained_result is retained and len(updates) == 2
    assert view._visibility_scene.result_field is field
    view.visibility.hidden.add(('element', 1))
    assert not view.visibility.filter_scene(view._visibility_scene).faces
    view.visibility.hidden.clear()
    restored = view.visibility.filter_scene(view._visibility_scene)
    assert restored.result_field is field and restored.faces
