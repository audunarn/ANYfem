"""Offscreen Qt coverage for the MeshTask parallel-meshing selection.

The workbench control is execution-only: Off by default, Automatic proposes a
bounded count, Custom count carries an explicit worker count, and the policy
crosses into the async submission as a plain picklable dataclass (no Qt
objects in the payload).
"""

from __future__ import annotations

import os
import pickle

import pytest

if os.environ.get("ANYFEM_RUN_QT_TESTS") != "1":
    pytest.skip("set ANYFEM_RUN_QT_TESTS=1 for real Qt widgets", allow_module_level=True)
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication

from anyfem.mesh_execution import MeshExecutionPolicy
from anyfem.ui.qt.tasks import MeshTask


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(qapp, tmp_path, monkeypatch):
    from anyfem.io import recovery
    from anyfem.ui.qt.app import QtFemWindow

    monkeypatch.setattr(recovery, "default_recovery_root", lambda: tmp_path / "recovery")
    window = QtFemWindow(viewer_backend=os.environ.get("ANYFEM_QT_TEST_BACKEND", "software"))
    window.show()
    qapp.processEvents()
    yield window
    window.session.mark_saved()
    window.close()
    qapp.processEvents()


def _task(window, qapp):
    task = MeshTask(window)
    qapp.processEvents()
    return task


def test_parallel_selection_defaults_to_off(window, qapp):
    task = _task(window, qapp)
    assert task.execution.currentText() == "Off"
    assert not task.workers.isEnabled()
    policy = task.settings()["execution_policy"]
    assert policy == MeshExecutionPolicy.off()
    assert not policy.requests_parallel


def test_parallel_selection_automatic(window, qapp):
    task = _task(window, qapp)
    task.execution.setCurrentText("Automatic")
    qapp.processEvents()
    assert not task.workers.isEnabled()
    policy = task.settings()["execution_policy"]
    assert policy == MeshExecutionPolicy.auto()
    assert policy.requests_parallel


def test_parallel_selection_custom_count(window, qapp):
    task = _task(window, qapp)
    task.execution.setCurrentText("Custom count")
    task.workers.setValue(5)
    qapp.processEvents()
    assert task.workers.isEnabled()
    policy = task.settings()["execution_policy"]
    assert policy == MeshExecutionPolicy.explicit(5)


def test_parallel_selection_resets_to_off(window, qapp):
    task = _task(window, qapp)
    task.execution.setCurrentText("Custom count")
    task.workers.setValue(7)
    qapp.processEvents()
    task.execution.setCurrentText("Off")
    qapp.processEvents()
    assert not task.workers.isEnabled()
    assert task.settings()["execution_policy"] == MeshExecutionPolicy.off()


def test_the_policy_payload_is_plain_and_picklable(window, qapp):
    task = _task(window, qapp)
    task.execution.setCurrentText("Custom count")
    task.workers.setValue(3)
    qapp.processEvents()
    policy = task.settings()["execution_policy"]
    assert pickle.loads(pickle.dumps(policy)) == policy
