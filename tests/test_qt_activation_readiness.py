"""Public activation waits for its own retained metadata; no solve or mesh."""
import os
import pytest
if os.environ.get("ANYFEM_RUN_QT_TESTS") != "1":
    pytest.skip("set ANYFEM_RUN_QT_TESTS=1 for real Qt widgets", allow_module_level=True)
from PySide6.QtCore import QSignalBlocker
from test_qt_result_browser import qapp, window, retained
from test_qt_workbench import settle_result_requests


def pending_activation(window, retained, monkeypatch, *, header_race=False):
    panel = window.panels["Results"]
    blocker = QSignalBlocker(panel.field)
    panel.field.clear(); panel.field.addItem("magnitude")
    del blocker
    callbacks = []; ready = [False]; calls = []
    def prepare(dataset, *, on_ready=None, on_cancel=None):
        assert dataset is retained
        calls.append(dataset)
        if ready[0] or (header_race and len(calls) > 1): return False
        callbacks.append((on_ready, on_cancel)); return True
    monkeypatch.setattr(window, "prepare_result_dataset", prepare)
    return panel, callbacks, ready, calls


def test_activation_refreshes_catalog_before_display_when_header_finishes_early(window, qapp, retained, monkeypatch):
    import numpy as np
    original = retained.field("displacement").read(0).copy()
    panel, callbacks, ready, calls = pending_activation(window, retained, monkeypatch, header_race=True)
    scene = window.viewport._scene
    panel.activate_job("synthetic")
    assert window.viewport._scene is scene
    assert len(calls) == 1 and callable(callbacks[0][0])
    ready[0] = True
    panel.refresh()  # Actual workflow publishes its catalog before ready callbacks.
    callbacks[0][0]()
    settle_result_requests(window, qapp)
    assert panel.field_name() in retained.field_keys
    assert window._view_mode == "results" and window.viewport._scene.faces
    assert window.result_datasets["synthetic"] is retained
    np.testing.assert_array_equal(retained.field("displacement").read(0), original)


@pytest.mark.parametrize("invalidate", ["job", "source", "replacement", "cancel", "failure"])
def test_delayed_activation_cannot_publish_after_ownership_or_metadata_loss(window, qapp, retained, monkeypatch, invalidate):
    panel, callbacks, ready, calls = pending_activation(window, retained, monkeypatch)
    panel.activate_job("synthetic")
    assert callable(callbacks[0][0]) and callable(callbacks[0][1])
    if invalidate == "job": window.active_job_id = None
    elif invalidate == "source": window._result_source_epoch = getattr(window, "_result_source_epoch", 0) + 1
    elif invalidate == "replacement": window.new_project()
    elif invalidate == "cancel": callbacks[0][1]()
    else:
        window.result_datasets.pop("synthetic")
        callbacks[0][1]()
    scene = window.viewport._scene
    published = []
    monkeypatch.setattr(panel, "show_results", lambda **kw: published.append(kw))
    ready[0] = True
    callbacks[0][0]()  # A late/duplicate producer completion cannot revive activation.
    qapp.processEvents()
    assert not published
    assert window.viewport._scene is scene
