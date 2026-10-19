"""Retained geometry and selection regressions using the real Qt viewer."""
import os
from dataclasses import replace
from types import SimpleNamespace

import numpy as np
import pytest

from anygeometry.entities import EntityRef
from anyfem.presentation.viewport import SceneViewport
from anyfem.selection import Selection, entity_tag
from anyfem.ui.scene import FacePatch, Scene


def triangle_scene():
    return Scene(faces=[FacePatch(EntityRef("face", 1),
        [np.array([[0., 0., 0.], [1., 0., 0.], [0., 1., 0.]])], ["#93b7c9"])])


@pytest.fixture
def viewport(monkeypatch):
    if os.environ.get("ANYFEM_RUN_QT_TESTS") != "1":
        pytest.skip("opt-in Qt widgets")
    from PySide6.QtWidgets import QApplication, QWidget
    from anyfem.ui.qt.viewport import QtViewport

    app = QApplication.instance() or QApplication([])
    parent = QWidget()
    parent.mesh = None
    parent.active_job_id = None
    parent.set_status = lambda *args, **kwargs: None
    selection = Selection("face")
    view = QtViewport(parent, selection, backend="software")
    view.widget.resize(640, 480)
    parent.show()
    view.widget.show()
    app.processEvents()
    yield view, app
    view.close()
    parent.close()
    app.processEvents()


def hit_at_centre(view):
    view.set_view("top")
    view.fit()
    point = view.project_point((.2, .2, 0.))
    assert point is not None
    return view.canvas.query_point(round(point[0]), round(point[1]))


def test_unchanged_show_preserves_submissions_and_pick_index(viewport, monkeypatch):
    view, app = viewport
    scene = triangle_scene()
    view.show(scene, reset_view=True)
    hits = hit_at_centre(view)
    assert hits and hits[0].owner.key == entity_tag(EntityRef("face", 1))
    guard = view._retained_scene[2]
    # Private identity inspection is test evidence only; implementation uses
    # public handles and never reads viewer internals.
    index = view.canvas._selection_index
    handles = tuple(entry["handle"] for entry in view.canvas._entries.values())
    with monkeypatch.context() as checks:
        checks.setattr(view.canvas, "clear", lambda **kw: pytest.fail("unchanged scene cleared"))
        checks.setattr(view.canvas, "add_faces", lambda *a, **kw: pytest.fail("unchanged faces rebuilt"))
        view.show(scene.copy())
    assert view._retained_scene[2] is guard
    assert tuple(entry["handle"] for entry in view.canvas._entries.values()) == handles
    assert view.canvas._selection_index is index
    assert not guard.visible and guard.mesh.positions.shape == (0, 3)
    assert guard.mesh.triangle_count == 0
    assert view.canvas._entries[id(guard)]["appearance"]["pickable"] is False
    assert all(hit.owner.key == entity_tag(EntityRef("face", 1)) for hit in hits)
    assert np.allclose(view.canvas._scene_bounds(), ([0., 0., 0.], [1., 1., 0.]))


@pytest.mark.parametrize("change", ["coordinates", "colors", "owners", "style", "legend"])
def test_changed_payload_invalidates_retained_scene(viewport, change):
    view, app = viewport
    scene = triangle_scene()
    view.show(scene)
    guard = view._retained_scene[2]
    if change == "coordinates":
        scene.faces[0].polygons[0][0, 0] = -.5
    elif change == "colors":
        scene.faces[0].colors[0] = "#ff0000"
    elif change == "owners":
        scene.faces[0].ref = EntityRef("face", 2)
    elif change == "style":
        view.set_visualization(replace(view.visualization, edge_width=2))
    elif change == "legend":
        # Valid empty legend still changes the retained display identity.
        scene.legend = {}
    view.show(scene)
    assert guard.removed
    assert view._retained_scene[2] is not guard
    if change == "owners":
        assert hit_at_centre(view)[0].owner.key == entity_tag(EntityRef("face", 2))


def test_visibility_clear_backend_and_undo_do_not_reuse_stale_contents(viewport):
    view, app = viewport
    scene = triangle_scene()
    view.show(scene)
    original = view._retained_scene[2]
    view.visibility.set_hidden([EntityRef("face", 1)])
    view.refresh_visibility()
    assert original.removed and not view._scene.faces
    assert not hit_at_centre(view)
    view.visibility.set_hidden([EntityRef("face", 1)], hidden=False)
    view.refresh_visibility()
    assert hit_at_centre(view)[0].owner.key == entity_tag(EntityRef("face", 1))
    guard = view._retained_scene[2]
    view.canvas.clear(keep_canvas=True)
    assert guard.removed
    view.show(scene)
    assert view._retained_scene[2] is not guard
    guard = view._retained_scene[2]
    view.switch_backend("software")
    assert guard.removed
    assert view._retained_scene[0] is view.canvas
    assert hit_at_centre(view)[0].owner.key == entity_tag(EntityRef("face", 1))
    replacement = triangle_scene()
    replacement.faces[0].ref = EntityRef("face", 2)
    view.show(replacement)
    assert hit_at_centre(view)[0].owner.key == entity_tag(EntityRef("face", 2))
    view.show(scene)
    assert hit_at_centre(view)[0].owner.key == entity_tag(EntityRef("face", 1))
    guard = view._retained_scene[2]
    view.clear()
    assert guard.removed and view._retained_scene is None
    view.show(scene)
    assert hit_at_centre(view)[0].owner.key == entity_tag(EntityRef("face", 1))


def test_small_camera_scene_keeps_full_submissions(viewport, monkeypatch):
    from anyfem.presentation import retained_scene
    view, app = viewport
    view.show(triangle_scene())
    guard = view._retained_scene[2]
    calls = []
    original = retained_scene.scene_key
    monkeypatch.setattr(retained_scene, "scene_key", lambda *args: (calls.append(1), original(*args))[-1])
    view._begin_interaction_scene()
    assert view._interaction_scene_active
    for _ in range(20):
        view._begin_interaction_scene()
    assert len(calls) == 1
    view._restore_full_scene()
    assert not view._interaction_scene_active
    assert not guard.removed and view._retained_scene[2] is guard


def test_construction_bypasses_cache_and_invalidates_previous_overlay(viewport, monkeypatch):
    view, app = viewport
    scene = triangle_scene()
    view.show(scene)
    guard = view._retained_scene[2]
    view._construction_task = object()
    monkeypatch.setattr(view, "_draw_construction_overlay", lambda: None)
    view.show(scene)
    assert guard.removed and view._retained_scene is None
    view._construction_task = None
    view.show(scene)
    assert view._retained_scene is not None


def test_empty_guard_does_not_change_capture_or_draw_counts_and_close_removes_it(viewport):
    view, app = viewport
    scene = triangle_scene()
    view.show(scene, reset_view=True)
    app.processEvents()
    guard = view._retained_scene[2]
    before = view.capture_image()
    draw_calls = view.canvas._renderer.draw_calls
    guard.remove()
    after = view.capture_image()
    assert before.size == after.size and before.tobytes() == after.tobytes()
    assert view.canvas._renderer.draw_calls == draw_calls
    view.show(scene)
    guard = view._retained_scene[2]
    view.close()
    assert guard.removed and view._retained_scene is None


def test_failed_replacement_does_not_publish_cache(viewport, monkeypatch):
    view, app = viewport
    scene = triangle_scene()
    view.show(scene)
    guard = view._retained_scene[2]
    scene.faces[0].colors[0] = "#ff0000"
    with monkeypatch.context() as checks:
        def fail(*args, **kwargs):
            raise RuntimeError("submission failed")
        checks.setattr(view.canvas, "add_faces", fail)
        with pytest.raises(RuntimeError, match="submission failed"):
            view.show(scene)
    assert guard.removed and view._retained_scene is None
    view.show(scene)
    assert hit_at_centre(view)[0].owner.key == entity_tag(EntityRef("face", 1))


def test_failed_backend_replacement_preserves_original_cache(viewport, monkeypatch):
    import anyfem.ui.qt.viewport as adapter
    view, app = viewport
    scene = triangle_scene()
    view.show(scene)
    original_cache = view._retained_scene
    factory = adapter.create_viewer
    def broken_candidate(*args, **kwargs):
        candidate = factory(*args, **kwargs)
        def fail(*args, **kwargs):
            raise RuntimeError("candidate submission failed")
        candidate.add_faces = fail
        return candidate
    monkeypatch.setattr(adapter, "create_viewer", broken_candidate)
    with pytest.raises(RuntimeError, match="candidate submission failed"):
        view.switch_backend("software")
    assert view._retained_scene is original_cache
    assert not original_cache[2].removed
    view.show(scene)
    assert view._retained_scene is original_cache
    assert hit_at_centre(view)[0].owner.key == entity_tag(EntityRef("face", 1))


def test_real_viewer_hover_applies_one_mask_pass_and_rejection_clears(viewport, monkeypatch):
    view, app = viewport
    view.show(triangle_scene())
    view.set_view("top")
    view.fit()
    point = view.project_point((.2, .2, 0.))
    updates = []
    original = view.canvas._apply_highlight_masks
    monkeypatch.setattr(view.canvas, "_apply_highlight_masks", lambda: (updates.append(1), original())[-1])
    view.canvas._hover_select(SimpleNamespace(x=round(point[0]), y=round(point[1])))
    assert view.hovered == EntityRef("face", 1)
    assert view.canvas.preselected_key == entity_tag(EntityRef("face", 1))
    assert view.canvas._preselection_from_hit
    assert len(updates) == 1
    view._handle_hover(SimpleNamespace(owner=SimpleNamespace(kind="unknown"), key="bad"))
    assert view.hovered is None and view.canvas.preselected_key is None
    assert len(updates) == 2


def test_modern_selection_does_not_adapt_discarded_legacy_refs():
    view = SceneViewport()
    view.initialize_state(Selection("face"))
    view._event_refs = lambda event: pytest.fail("modern selection took legacy adaptation path")
    view._handle_selection_event(SimpleNamespace(gesture="box", operation="replace",
        hits=[SimpleNamespace(owner=EntityRef("face", i)) for i in range(100)]))
    assert view.selection.items == [EntityRef("face", i) for i in range(100)]


def test_selection_order_duplicates_and_rejections_survive_hash_membership():
    selection = Selection("face")
    a, b, c = (EntityRef("face", i) for i in range(3))
    selection.apply([a, b, a])
    assert selection.items == [a, b]
    selection.apply([b, c, c], "toggle")
    assert selection.items == [a, c]
    selection.apply([a, b, b], "add")
    assert selection.items == [a, c, b]
    rejected = selection.apply([EntityRef("edge", 1)], "replace")
    assert rejected.rejected and selection.items == [a, c, b]
    selection.apply([a, a], "remove")
    assert selection.items == [c, b]
    selection.apply([], "replace")
    assert selection.items == []


def test_large_selection_uses_bounded_equality_work():
    class Ref:
        comparisons = 0

        def __init__(self, identity):
            self.identity = identity

        def __hash__(self):
            return self.identity

        def __eq__(self, other):
            Ref.comparisons += 1
            return self.identity == other.identity

    selection = Selection("face")
    selection._filter = SimpleNamespace(explain_rejection=lambda ref: None)
    refs = [Ref(i) for i in range(400)]
    selection.apply(refs)
    selection.apply([Ref(i) for i in range(200, 600)], "add")
    selection.apply([Ref(i) for i in range(400, 800)], "toggle")
    assert [ref.identity for ref in selection.items] == list(range(400)) + list(range(600, 800))
    assert Ref.comparisons < 5000


@pytest.mark.parametrize("noncontiguous", [False, True])
def test_retained_identity_hashes_complete_mutable_arrays(noncontiguous):
    from anyfem.presentation.retained_scene import scene_key
    from anyfem.presentation.visualization import VisualizationStyle
    # Large enough that repr() would omit the changed interior coordinates.
    source = np.arange(6000., dtype=np.float64).reshape(2000, 3)
    coordinates = source[::2] if noncontiguous else source.copy()
    scene = Scene(faces=[FacePatch(EntityRef("face", 1), [coordinates], ["#93b7c9"])])
    view = SimpleNamespace(construction_active=False, canvas=SimpleNamespace(
        preselected_key=None, add_mesh_arrays=lambda *a, **k: None),
        visualization=VisualizationStyle(), _commercial_selection=True)
    before = scene_key(view, scene)
    assert before is not None and scene_key(view, scene.copy()) == before
    coordinates[500, 1] += .25
    assert scene_key(view, scene) != before
    coordinates[500, 1] -= .25
    assert scene_key(view, scene) == before
    scene.faces[0].polygon_owners = [(EntityRef("face", 2),)]
    assert scene_key(view, scene) != before
    scene.faces[0].polygon_owners = None
    scene.faces[0].colors[0] = "#ff0000"
    assert scene_key(view, scene) != before


def test_unserializable_scene_payload_declines_retention():
    from anyfem.presentation.retained_scene import scene_key
    from anyfem.presentation.visualization import VisualizationStyle
    scene = triangle_scene()
    scene.legend = {"formatter": lambda value: str(value)}
    view = SimpleNamespace(construction_active=False, canvas=SimpleNamespace(
        preselected_key=None, add_mesh_arrays=lambda *a, **k: None),
        visualization=VisualizationStyle(), _commercial_selection=True)
    assert scene_key(view, scene) is None


def test_external_reducer_cannot_hide_mutable_draw_data():
    from anyfem.presentation.retained_scene import scene_key
    from anyfem.presentation.visualization import VisualizationStyle
    class ExternalPatch(FacePatch):
        def __reduce__(self):
            pytest.fail("identity must not invoke an external reducer")
    scene = triangle_scene()
    patch = scene.faces[0]
    scene.faces[0] = ExternalPatch(patch.ref, patch.polygons, patch.colors)
    view = SimpleNamespace(construction_active=False, canvas=SimpleNamespace(
        preselected_key=None, add_mesh_arrays=lambda *a, **k: None),
        visualization=VisualizationStyle(), _commercial_selection=True)
    assert scene_key(view, scene) is None
