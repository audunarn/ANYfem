"""Fake-only tests for the gated mesh fault-injection helpers.

These pin the helper contract — acknowledged task receipt, cooperative
cancellation, hard crash on release, bounded timeout and component
restore/isolation — without Qt, native meshing or a real mesh run.  The
contained behaviour itself is exercised only by the authorized Qt parallel
integration tests.
"""
import os
import sys
import types

import pytest

import job_process_helpers as helpers


class _FakeClock:
    """Advances ``MESH_GATE_TIMEOUT_SECONDS`` past its deadline in a few ticks."""

    def __init__(self, step=6.0):
        self.now = 0.0
        self.step = step

    def monotonic(self):
        self.now += self.step
        return self.now

    def sleep(self, seconds):
        pass


def _gate(tmp_path, monkeypatch):
    gate = tmp_path / "gate"
    gate.mkdir()
    monkeypatch.setenv(helpers.MESH_GATE_ENV, str(gate))
    return gate


def _fake_stack(monkeypatch):
    """Install fake ``anyfem.mesh_jobs`` and ``anymesher.component_parallel``.

    The gated entry point must work against whatever modules resolve at call
    time, so the fakes record the production inputs, the component seen while
    the job runs, and restore-relevant state.
    """
    anyfem = types.ModuleType("anyfem")
    mesh_jobs = types.ModuleType("anyfem.mesh_jobs")
    anymesher = types.ModuleType("anymesher")
    component_parallel = types.ModuleType("anymesher.component_parallel")
    state = {"calls": [], "seen_component": None, "raise": None}

    def fake_entry(*, project, progress, cancellation_token, settings, model_hash):
        state["calls"].append((project, progress, cancellation_token, settings, model_hash))
        state["seen_component"] = component_parallel._mesh_component
        if state["raise"] is not None:
            raise state["raise"]
        return "transport"

    fake_entry.error_diagnostic = lambda error: {"message": str(error), "fake": True}
    mesh_jobs.mesh_job_entry = fake_entry
    anyfem.mesh_jobs = mesh_jobs
    component_parallel._mesh_component = lambda payload: "real component"
    anymesher.component_parallel = component_parallel
    monkeypatch.setitem(sys.modules, "anyfem", anyfem)
    monkeypatch.setitem(sys.modules, "anyfem.mesh_jobs", mesh_jobs)
    monkeypatch.setitem(sys.modules, "anymesher", anymesher)
    monkeypatch.setitem(sys.modules, "anymesher.component_parallel", component_parallel)
    return state, component_parallel


def test_gated_component_acknowledges_receipt_then_crashes_on_release(tmp_path, monkeypatch):
    gate = _gate(tmp_path, monkeypatch)
    exits = []
    monkeypatch.setattr(helpers.os, "_exit", exits.append)
    (gate / "release").write_text("release", encoding="utf-8")
    helpers.gated_component({"options": {}}, cancellation_check=None)
    assert list(gate.glob("ready-*")) == [gate / f"ready-{os.getpid()}"]
    assert exits == [3]


def test_gated_component_honours_cancellation_without_crashing(tmp_path, monkeypatch):
    gate = _gate(tmp_path, monkeypatch)
    exits = []
    monkeypatch.setattr(helpers.os, "_exit", exits.append)

    class Cancelled(Exception):
        pass

    def cancel(phase):
        assert phase == "gated mesh component"
        raise Cancelled(phase)

    with pytest.raises(Cancelled):
        helpers.gated_component({"options": {}}, cancellation_check=cancel)
    assert (gate / f"ready-{os.getpid()}").exists()
    assert not (gate / "release").exists()
    assert exits == []


def test_gated_component_times_out_when_never_released(tmp_path, monkeypatch):
    gate = _gate(tmp_path, monkeypatch)
    monkeypatch.setattr(helpers, "time", _FakeClock())
    with pytest.raises(TimeoutError, match="never released"):
        helpers.gated_component({"options": {}}, cancellation_check=None)
    assert (gate / f"ready-{os.getpid()}").exists()


def test_gated_component_requires_gate_directory(monkeypatch):
    monkeypatch.delenv(helpers.MESH_GATE_ENV, raising=False)
    with pytest.raises(RuntimeError, match=helpers.MESH_GATE_ENV):
        helpers.gated_component({"options": {}}, cancellation_check=None)


def test_gated_entry_swaps_and_restores_component_with_unchanged_inputs(monkeypatch):
    state, component_parallel = _fake_stack(monkeypatch)
    real_component = component_parallel._mesh_component
    inputs = {"project": object(), "progress": lambda value: None,
              "cancellation_token": object(), "settings": object(),
              "model_hash": "model-hash"}
    result = helpers.gated_mesh_job_entry(**inputs)
    assert result == "transport"
    assert state["calls"] == [(inputs["project"], inputs["progress"],
                               inputs["cancellation_token"], inputs["settings"],
                               inputs["model_hash"])]
    assert state["seen_component"] is helpers.gated_component
    assert component_parallel._mesh_component is real_component


def test_gated_entry_restores_component_when_job_fails(monkeypatch):
    state, component_parallel = _fake_stack(monkeypatch)
    real_component = component_parallel._mesh_component
    state["raise"] = RuntimeError("a component worker process died")
    with pytest.raises(RuntimeError, match="worker process died"):
        helpers.gated_mesh_job_entry(project=None, progress=None,
                                     cancellation_token=None, settings=None,
                                     model_hash="")
    assert component_parallel._mesh_component is real_component


def test_gated_entry_keeps_production_failure_diagnostic(monkeypatch):
    _fake_stack(monkeypatch)
    diagnostic = helpers.gated_mesh_job_entry.error_diagnostic(RuntimeError("boom"))
    assert diagnostic == {"message": "boom", "fake": True}
