"""Exercise the actual Help action and desktop clipboard."""
import json
import os
import pytest

if os.environ.get("ANYFEM_RUN_QT_TESTS") != "1":
    pytest.skip("set ANYFEM_RUN_QT_TESTS=1", allow_module_level=True)

from test_qt_geometry_actions import qapp, window
from anyfem import commands as cmd


def test_help_copy_diagnosis_copies_empty_and_fresh_model(qapp, window):
    qapp.clipboard().setText("old clipboard sentinel")
    window._actions["Copy diagnosis"].trigger()
    qapp.processEvents()
    empty = json.loads(qapp.clipboard().text())
    assert empty["report"] == "ANYfem diagnostic"
    assert "current_state" in empty and "qt_runtime" in empty
    window.run(cmd.AddPoint(3, 4, 5))
    history = window.commands.history()
    window._actions["Copy diagnosis"].trigger()
    qapp.processEvents()
    fresh = json.loads(qapp.clipboard().text())
    assert fresh["project"]["geometry"]["points"] == 1
    assert window.commands.history() == history
    assert "copied" in window._status.get().lower()


def test_copy_reports_persisted_result_presence_without_opening_it(qapp, window):
    class LazyResultProbe:
        metadata_ready = False
        reads = 0
        @property
        def field_keys(self):
            self.reads += 1
            raise AssertionError("Diagnosis opened a result artifact")
        frames = identity = path = field_keys
    probe = LazyResultProbe()
    qapp.processEvents()
    old_job = window.active_job_id
    try:
        window.result_datasets["stored-job"] = probe
        window.active_job_id = "stored-job"
        # QAction delivery is synchronous. Do not run unrelated result-browser
        # refresh timers while the deliberately incomplete probe is installed.
        window._actions["Copy diagnosis"].trigger()
        payload = json.loads(qapp.clipboard().text())
        assert "LazyResultProbe" in json.dumps(payload["current_state"]["solver_output"])
        assert probe.reads == 0
    finally:
        window.result_datasets.pop("stored-job", None)
        window.active_job_id = old_job
