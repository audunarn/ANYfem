"""Renderer-neutral ANYfem viewport switching contracts."""

from __future__ import annotations

import pytest

from anyfem.selection import Selection
from anyfem.ui import viewport as viewport_module
from anyfem.ui.scene import Scene


class _EventWidget:
    def __init__(self) -> None:
        self.bindings = []

    def bind(self, sequence, callback, add="+"):
        self.bindings.append((sequence, callback, add))


class _Viewer:
    def __init__(self, backend: str) -> None:
        self.backend_name = backend
        self.backend_diagnostics = ()
        self.event_widget = _EventWidget()
        self.state = {"camera": "iso", "section": (1.0, 0.0, 0.0, 0.25)}
        self.applied_state = None
        self.applied_redraw = None
        self.packed = []
        self.clear_calls = 0
        self.redraw_calls = 0
        self.destroyed = False
        self.highlights = []

    def set_pick_callback(self, *_args, **_kwargs):
        pass

    def set_highlight(self, tags):
        self.highlights.append(tuple(tags))

    def clear(self, **_kwargs):
        self.clear_calls += 1

    def redraw(self):
        self.redraw_calls += 1

    def clear_thickness_legend(self):
        pass

    def export_view_state(self):
        return dict(self.state)

    def apply_view_state(self, state, *, redraw=True):
        self.applied_state = state
        self.applied_redraw = redraw

    def pack(self, **options):
        self.packed.append(options)

    def destroy(self):
        self.destroyed = True


def _point(*values):
    return tuple(float(value) for value in values)


def test_switch_is_transactional_and_rebinds_input(monkeypatch) -> None:
    created = []

    def factory(_master, *, backend, **_options):
        concrete = "software" if backend == "auto" else backend
        viewer = _Viewer(concrete)
        created.append(viewer)
        return viewer

    monkeypatch.setattr(viewport_module, "require_canvas", lambda: (_point, factory))
    selection = Selection("face")
    viewport = viewport_module.Viewport(
        object(), selection=selection, commercial_interaction=False
    )
    viewport.pack(fill="both", expand=True)
    viewport.show(Scene())
    viewport.bind_event("<Delete>", lambda _event: None)
    old = viewport.canvas

    assert viewport.switch_backend("gpu") == "gpu"

    assert viewport.requested_backend == "gpu"
    assert viewport.active_backend == "gpu"
    assert old.destroyed
    assert viewport.canvas.applied_state == old.state
    assert viewport.canvas.applied_redraw is False
    assert viewport.canvas.clear_calls == 1
    assert viewport.canvas.redraw_calls == 1
    assert {item[0] for item in viewport.canvas.event_widget.bindings} >= {
        "<Escape>",
        "<Return>",
        "<KP_Enter>",
        "<Delete>",
    }
    assert viewport.canvas.packed[-1]["fill"] == "both"


def test_failed_explicit_switch_keeps_working_canvas(monkeypatch) -> None:
    software = _Viewer("software")

    def factory(_master, *, backend, **_options):
        if backend == "gpu":
            raise RuntimeError("OpenGL 3.3 is unavailable")
        return software

    monkeypatch.setattr(viewport_module, "require_canvas", lambda: (_point, factory))
    viewport = viewport_module.Viewport(object(), commercial_interaction=False)
    viewport.show(Scene())

    with pytest.raises(RuntimeError, match="OpenGL 3.3"):
        viewport.switch_backend("gpu")

    assert viewport.canvas is software
    assert viewport.requested_backend == "auto"
    assert not software.destroyed


def test_switch_supports_legacy_state_importer_without_redraw_keyword(
    monkeypatch,
) -> None:
    class LegacyViewer(_Viewer):
        def apply_view_state(self, state):
            self.applied_state = state

    created = []

    def factory(_master, *, backend, **_options):
        viewer_type = LegacyViewer if backend == "gpu" else _Viewer
        viewer = viewer_type("software" if backend == "auto" else backend)
        created.append(viewer)
        return viewer

    monkeypatch.setattr(viewport_module, "require_canvas", lambda: (_point, factory))
    viewport = viewport_module.Viewport(object(), commercial_interaction=False)
    old = viewport.canvas

    assert viewport.switch_backend("gpu") == "gpu"
    assert viewport.canvas.applied_state == old.state
    assert viewport.canvas.redraw_calls == 1
