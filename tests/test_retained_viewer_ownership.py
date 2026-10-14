"""Retained result handles belong to one viewer; no widget/context is created."""
from types import SimpleNamespace

import numpy as np
import pytest

from anyfem.post.fields import Field
from anyfem.presentation.viewport import SceneViewport
from anyfem.ui.scene import Scene


class Handle:
    def __init__(self, owner, mesh):
        self.owner, self.mesh = owner, mesh
        self.removed = False
        self.scalar_updates = 0

    def update_scalar_fields(self, **values):
        if self.owner.closed or self.removed:
            raise RuntimeError("libshiboken: Internal C++ object (_GLSurface) already deleted.")
        self.scalar_updates += 1
        self.mesh.element_scalars = values["element_scalars"]

    def update_appearance(self, **values):
        assert not self.owner.closed


class Canvas:
    backend_name = "gpu"

    def __init__(self):
        self.closed = False
        self.handles = []
        self.event_widget = SimpleNamespace(installEventFilter=lambda keys: None)

    def add_mesh_arrays(self, mesh, **options):
        handle = Handle(self, mesh)
        self.handles.append(handle)
        return handle

    def clear(self, **options):
        for handle in self.handles:
            handle.removed = True

    def destroy(self):
        self.closed = True
        self.clear()

    def export_view_state(self):
        return "unchanged-camera"

    def apply_view_state(self, state, **options):
        assert state == "unchanged-camera"

    def redraw(self):
        assert not self.closed

    def show(self):
        assert not self.closed


class View(SceneViewport):
    def __init__(self, canvas):
        self.canvas = canvas
        self._retained_result = self._retained_result_canvas = None
        self._scene = Scene(result_field=Field("ux", "m", element_values={1: 0.}),
                            result_mesh=object())
        self._interaction_scene = None
        self._interaction_scene_active = False
        self._keys = object()
        self.widget = SimpleNamespace(parent=lambda: None)
        self.layout = SimpleNamespace(replaceWidget=lambda old, new: None)

    def cancel_plate(self): pass
    def _cancel_interaction_restore(self): pass
    def _restore_full_scene(self): pass
    def _configure_canvas(self, canvas): pass
    def _watch_backend(self, parent): pass
    def _draw(self, scene): pass
    def _draw_construction_overlay(self): pass
    def _draw_legend(self, scene): pass
    def _apply_highlight(self): pass
    def _clear_hover_state(self): pass


@pytest.fixture
def view(monkeypatch):
    from anyfem.presentation import retained_results

    def prepare(view, scene):
        mesh = SimpleNamespace(element_scalars=np.array([0.]), element_ids=np.array([1]))
        return b"same-geometry", [(mesh, {})], Scene()

    monkeypatch.setattr(retained_results, "prepare", prepare)
    view = View(Canvas())
    view._render_scene(view._scene)
    return view


def field_for(view, value=2.):
    field = Field("ux", "m", element_values={1: value})
    field._display_buffers = (b"same-geometry", (np.array([value]),), ([], []))
    return field


def update(view, field):
    return view.update_result_field(field, scalar_range=(0., 3.),
                                   colormap=((0., "#0000ff"), (1., "#ff0000")), legend={})


def test_actual_qt_gpu_to_gpu_switch_publishes_through_new_viewer(view, monkeypatch):
    pytest.importorskip("PySide6")
    from anyfem.ui.qt import viewport as qt
    old, new = view.canvas, Canvas()
    old_handle = old.handles[0]
    monkeypatch.setattr(qt, "create_viewer", lambda *args, **kwargs: new)
    assert qt.QtViewport.switch_backend(view, "gpu") == "gpu"
    assert old.closed and view.canvas is new
    field = field_for(view)
    assert update(view, field)
    assert len(new.handles) == 1
    assert new.handles[0].scalar_updates == 1
    assert old_handle.scalar_updates == 0
    assert field.element_values == {1: 2.}


def test_current_viewer_keeps_scalar_only_fast_path(view):
    handle = view.canvas.handles[0]
    view._render_scene(view._scene)
    assert len(view.canvas.handles) == 1
    assert handle.scalar_updates == 1
    assert update(view, field_for(view))
    assert handle.scalar_updates == 2


def test_field_update_refuses_handles_from_other_viewer(view):
    old = view.canvas
    view.canvas = Canvas()
    old.destroy()
    assert update(view, field_for(view)) is False
    assert old.handles[0].scalar_updates == 0


def test_removed_handles_are_rebuilt_even_with_unchanged_geometry(view):
    old = view.canvas.handles[0]
    old.removed = True
    view._render_scene(view._scene)
    assert len(view.canvas.handles) == 2
    assert view._retained_result[1] == [view.canvas.handles[-1]]
    assert old.scalar_updates == 0


def test_clear_invalidates_retained_field_cache(view):
    view.clear()
    assert view._retained_result is None
    assert view._retained_result_canvas is None
    assert update(view, field_for(view)) is False


@pytest.mark.parametrize("phase", ["create", "upload"])
def test_failed_backend_candidate_keeps_old_result_handles_live(view, monkeypatch, phase):
    pytest.importorskip("PySide6")
    from anyfem.ui.qt import viewport as qt
    old = view.canvas
    previous = view._retained_result
    candidate = Canvas()

    def refused(*args, **kwargs):
        raise RuntimeError("candidate refused")

    if phase == "create":
        monkeypatch.setattr(qt, "create_viewer", refused)
    else:
        candidate.add_mesh_arrays = refused
        monkeypatch.setattr(qt, "create_viewer", lambda *args, **kwargs: candidate)
    with pytest.raises(RuntimeError, match="candidate refused"):
        qt.QtViewport.switch_backend(view, "gpu")
    assert view.canvas is old and not old.closed
    assert view._retained_result is previous
    assert view._retained_result_canvas is old
    assert update(view, field_for(view))
    if phase == "upload":
        assert candidate.closed


def test_qt_close_drops_handles_before_destroying_the_viewer(view):
    pytest.importorskip("PySide6")
    from anyfem.ui.qt import viewport as qt
    view.selection = SimpleNamespace(remove_listener=lambda callback: None)
    qt.QtViewport.close(view)
    assert view.canvas.closed
    assert view._retained_result is view._retained_result_canvas is None
    assert update(view, field_for(view)) is False
