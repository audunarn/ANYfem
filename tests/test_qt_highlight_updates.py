"""One final Qt highlight submission without losing filter/hover updates."""
import os
from types import SimpleNamespace

import pytest

if os.environ.get("ANYFEM_RUN_QT_TESTS") != "1":
    pytest.skip("set ANYFEM_RUN_QT_TESTS=1", allow_module_level=True)
pytest.importorskip("PySide6")

from anyfem.presentation.viewport import SceneViewport
from anyfem.selection import Selection, entity_tag
from test_qt_geometry_actions import qapp, window, geometry


def record_highlights(view, monkeypatch):
    calls = []
    original = view.canvas.set_highlight
    def record(tags, *args, **kwargs):
        values = tuple(tags)
        calls.append(values)
        return original(values, *args, **kwargs)
    monkeypatch.setattr(view.canvas, "set_highlight", record)
    return calls


def test_plain_selection_submits_once_but_empty_dismissal_still_updates_tree(window, qapp, monkeypatch):
    vertices, refs = geometry(window)
    window.selection.set_mode("face")
    calls = record_highlights(window.viewport, monkeypatch)
    dismissed = []
    original = window.tree.dismiss_feature_selection
    monkeypatch.setattr(window.tree, "dismiss_feature_selection", lambda: (dismissed.append(1), original())[-1])
    window.selection.restore([refs[2]])
    assert calls == [(entity_tag(refs[2]),)]
    assert dismissed
    calls.clear()
    window.viewport.dismiss_feature_highlight()
    assert calls == [] and len(dismissed) == 2


def test_collection_only_union_and_clearing_submit_once(window, qapp, monkeypatch):
    vertices, refs = geometry(window)
    window.selection.set_mode("face")
    window.selection.restore([refs[2]])
    calls = record_highlights(window.viewport, monkeypatch)
    def unexpected(*args):
        pytest.fail("collection-only highlight must not resolve feature ownership")
    monkeypatch.setattr("anygeometry.feature_entity_owners", unexpected)
    window.viewport.set_collection_highlight([refs[1]])
    assert calls == [tuple(sorted(entity_tag(ref) for ref in refs[1:]))]
    calls.clear()
    window.viewport.set_collection_highlight([refs[1]])
    assert calls == []
    window.viewport.dismiss_feature_highlight()
    assert calls == [(entity_tag(refs[2]),)]
    assert window.viewport._collection_refs == frozenset()


def test_feature_and_collection_union_is_one_submission_and_recomputes_for_new_scene(window, qapp, monkeypatch):
    vertices, refs = geometry(window)
    window.selection.set_mode("face")
    window.selection.restore([refs[2]])
    monkeypatch.setattr("anygeometry.feature_entity_owners", lambda model: {refs[0]: "feature"})
    calls = record_highlights(window.viewport, monkeypatch)
    window.viewport.set_feature_highlight(["feature"])
    assert calls == [tuple(sorted(entity_tag(ref) for ref in (refs[0], refs[2])))]
    calls.clear()
    window.viewport.set_collection_highlight([refs[1]])
    assert calls == [tuple(sorted(entity_tag(ref) for ref in refs))]
    calls.clear()
    window.viewport.set_feature_highlight(["feature"])
    assert calls == []
    window.viewport.show(window.viewport._visibility_scene.copy())
    assert calls == [tuple(sorted(entity_tag(ref) for ref in refs))]


def test_filter_change_keeps_central_hover_and_selection_config_update(window, qapp, monkeypatch):
    vertices, refs = geometry(window)
    window.selection.set_mode("face")
    window.selection.restore([refs[2]])
    view = window.viewport
    view._hovered = refs[2]
    view._hover_key = entity_tag(refs[2])
    view.canvas.set_preselection(view._hover_key)
    calls = record_highlights(view, monkeypatch)
    window.selection.set_mode("edge")
    assert len(calls) == 1
    assert view._canvas_filter_kinds == frozenset({"geometry.edge"})
    assert view._hovered is None and view._hover_key is None
    # Explicit viewer preselection historically clears on the next hover
    # delivery; changing the filter must preserve that existing distinction.
    view._handle_hover(None)
    assert view.canvas.preselected_key is None


def test_base_highlight_hook_preserves_legacy_selection_tags():
    from anygeometry.entities import EntityRef
    view = SceneViewport()
    view.initialize_state(Selection("face"))
    view._commercial_selection = False
    calls = []
    view.canvas = SimpleNamespace(set_highlight=lambda tags: calls.append(tuple(tags)))
    ref = EntityRef("face", 7)
    view.selection.restore([ref])
    view._apply_highlight()
    assert calls == [(entity_tag(ref),)]
