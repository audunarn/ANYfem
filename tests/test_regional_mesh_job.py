"""Regional mesh job transport, hashing and cancellation (no real meshing)."""
from __future__ import annotations

import pickle
import sys
import types
from dataclasses import replace
from types import SimpleNamespace

import pytest

from anyfem import mesh_jobs
from anyfem.mesh_jobs import (
    MeshJobResult,
    MeshSettings,
    RegionalJobInput,
    _MeshTransport,
    run_mesh_job,
)


class _Token:
    def __init__(self, cancelled=False):
        self.is_cancelled = cancelled

    def raise_if_cancelled(self, stage=""):
        if self.is_cancelled:
            raise RuntimeError("cancelled at " + stage)


class _Project:
    native_mesh_settings = None
    native_triangulation_backend = "auto"
    _last_mesh_preparation = {"prep": 1}
    _last_mesh_execution = {"route": "serial"}

    def __init__(self):
        self.generate_calls = 0

    def generate_mesh(self, *args, **kwargs):
        self.generate_calls += 1
        return SimpleNamespace(hybrid_diagnostics={})


def _settings(**extra):
    return MeshSettings.create(0.5, element_order="linear", strategy="mapped", **extra)


def _regional():
    return RegionalJobInput(
        baseline={"version": 1}, old_mesh={"nodes": [1, 2]},
        node_high_water=10, element_high_water=20,
    )


@pytest.fixture
def stubbed(monkeypatch):
    import anymesher

    monkeypatch.setattr(
        anymesher, "verify_mesh_quality",
        lambda mesh: SimpleNamespace(as_dict=lambda: {"ok": True}),
    )
    monkeypatch.setattr(mesh_jobs, "mesh_semantic_hash", lambda *a, **k: "hash")
    return monkeypatch


def _install_regional(monkeypatch, **functions):
    module = types.ModuleType("anyfem.regional_dispatch")
    for name, function in functions.items():
        setattr(module, name, function)
    monkeypatch.setitem(sys.modules, "anyfem.regional_dispatch", module)
    return module


def test_regional_input_excluded_from_hash_and_picklable():
    plain = _settings()
    regional = replace(plain, regional=_regional())
    assert regional.input_hash == plain.input_hash
    assert regional == plain
    restored = pickle.loads(pickle.dumps(regional))
    assert restored.regional.node_high_water == 10
    assert restored.regional.old_mesh == {"nodes": [1, 2]}
    with pytest.raises(TypeError):
        MeshSettings(target_size=1.0, regional={"baseline": {}})
    with pytest.raises(ValueError):
        RegionalJobInput({}, {}, -1, 0)


def test_full_worker_leaves_baseline_capture_to_the_numbered_publisher(stubbed):
    seen = {}

    def capture(project, settings, mesh):
        seen["args"] = (project, settings, mesh)
        return {"baseline": True}

    _install_regional(stubbed, capture_regional_baseline=capture)
    project = _Project()
    result = run_mesh_job(project, "m", _settings(), lambda *a: None, _Token())
    assert project.generate_calls == 1
    assert result.regional_baseline is None
    assert result.retained_node_ids == () and result.retained_element_ids == ()
    assert result.execution == {"route": "serial"}
    assert result.mesh_hash == "hash"
    assert not seen


def test_full_path_ineligible_baseline_is_none(stubbed):
    _install_regional(stubbed, capture_regional_baseline=lambda *a: None)
    result = run_mesh_job(_Project(), "m", _settings(), lambda *a: None, _Token())
    assert result.regional_baseline is None


def test_regional_path_calls_update_and_reports_ids(stubbed):
    calls = {}

    def update(project, settings, baseline, old_mesh, **kwargs):
        calls.update(baseline=baseline, old_mesh=old_mesh, **kwargs)
        kwargs["cancellation_check"]("regional stage")
        return SimpleNamespace(
            mesh=SimpleNamespace(hybrid_diagnostics={}),
            baseline={"new": 1},
            retained_nodes={3, 1, 2},
            retained_elements=frozenset({9, 8}),
            plan=object(),
        )

    def forbidden(*args):
        raise AssertionError("full capture must not run")

    _install_regional(stubbed, generate_regional_update=update,
                      capture_regional_baseline=forbidden)
    project = _Project()
    settings = replace(_settings(), regional=_regional())
    result = run_mesh_job(project, "m", settings, lambda *a: None, _Token())
    assert project.generate_calls == 0
    assert calls["baseline"] == {"version": 1}
    assert calls["old_mesh"] == {"nodes": [1, 2]}
    assert calls["node_high_water"] == 10 and calls["element_high_water"] == 20
    assert result.regional_baseline == {"new": 1}
    assert result.retained_node_ids == (1, 2, 3)
    assert result.retained_element_ids == (8, 9)
    assert result.execution["route"] == "regional"


def test_regional_cancellation_propagates(stubbed):
    token = _Token()

    def update(project, settings, baseline, old_mesh, **kwargs):
        token.is_cancelled = True
        kwargs["cancellation_check"]("regional stage")
        raise AssertionError("not reached")

    _install_regional(stubbed, generate_regional_update=update)
    settings = replace(_settings(), regional=_regional())
    with pytest.raises(RuntimeError, match="cancelled"):
        run_mesh_job(_Project(), "m", settings, lambda *a: None, token)


def test_worker_transport_roundtrip_is_picklable_and_unpacked():
    transport = _MeshTransport(
        mesh={"nodes": []}, mesh_hash="h", quality={"q": 1},
        structural_preparation={}, execution={},
        regional_baseline={"b": 1}, retained_node_ids=(1, 2),
        retained_element_ids=(5,),
    )
    restored = pickle.loads(pickle.dumps(transport))
    assert restored == transport
    default = MeshJobResult(mesh=None, mesh_hash="h", quality={}, structural_preparation={})
    assert default.regional_baseline is None
    assert default.retained_node_ids == ()


def test_mesh_job_entry_packs_regional_fields(monkeypatch):
    import anymesher.serialize as serialize

    monkeypatch.setattr(serialize, "mesh_to_dict", lambda mesh: {"encoded": True})
    result = MeshJobResult(
        mesh=object(), mesh_hash="h", quality={}, structural_preparation={},
        regional_baseline={"b": 1}, retained_node_ids=(1,), retained_element_ids=(2,),
    )
    monkeypatch.setattr(mesh_jobs, "run_mesh_job", lambda *a, **k: result)
    transport = mesh_jobs.mesh_job_entry(
        project=None, progress=lambda value: None, cancellation_token=_Token(),
        settings=_settings(), model_hash="m",
    )
    assert transport.mesh == {"encoded": True}
    assert transport.regional_baseline == {"b": 1}
    assert transport.retained_node_ids == (1,)
    assert transport.retained_element_ids == (2,)
    pickle.dumps(transport)


@pytest.fixture
def regional_update_panel():
    pytest.importorskip("PySide6")
    from anyfem import Project
    from anyfem.document import DocumentSession

    events = []
    project = Project("regional preview")
    app = SimpleNamespace(
        project=project, session=DocumentSession(project), mesh_record_id=None, seeding_overrides={},
        review_mesh_reference_repairs=object(),
        preview_regional_update=lambda **o: events.append(("preview", o)) or "3 faces change",
        update_mesh_async=lambda **o: events.append(("update", o)),
        set_status=lambda text, **k: events.append(("status", text)),
        dialogs=SimpleNamespace(confirm=lambda title, message: events.append(("confirm", message)) or answer[0]),
    )
    answer = [False]

    def settings(*, updating):
        assert updating is True
        return {"target_size": 0.5}

    panel = SimpleNamespace(
        app=app, order=SimpleNamespace(currentText=lambda: "linear"),
        s3_reference=SimpleNamespace(currentData=lambda: project.shell_formulation_policy.s3_reference_surface),
        settings=settings,
    )
    return panel, events, answer


def test_qt_update_requires_preview_and_confirmation(regional_update_panel):
    from anyfem.ui.qt.tasks import MeshTask

    panel, events, answer = regional_update_panel
    app = panel.app
    MeshTask.start_update(panel)
    assert [e[0] for e in events] == ["preview", "confirm", "status"]
    assert "3 faces change" in events[1][1]
    events.clear()
    answer[0] = True
    MeshTask.start_update(panel)
    assert [e[0] for e in events] == ["preview", "confirm", "update"]
    assert events[-1][1]["reference_repairs"] is app.review_mesh_reference_repairs
    panel.order = SimpleNamespace(currentText=lambda: "quadratic")
    events.clear()
    with pytest.raises(ValueError, match="full rebuild"):
        MeshTask.start_update(panel)
    assert events == []


@pytest.mark.parametrize(("change", "reason"), [
    ("seeding", "settings changed during confirmation"),
    ("geometry", "superseded by a model edit"),
])
def test_modal_confirmation_refuses_a_stale_regional_preview(regional_update_panel, change, reason):
    from anyfem.ui.qt.tasks import MeshTask

    panel, events, _ = regional_update_panel

    def confirm(title, message):
        events.append(("confirm", message))
        if change == "seeding":
            panel.app.seeding_overrides[1] = 4
        else:
            panel.app.project.geometry.add_points([(0.0, 0.0, 0.0)])
        return True

    panel.app.dialogs.confirm = confirm
    with pytest.raises(ValueError, match=reason):
        MeshTask.start_update(panel)
    assert [event[0] for event in events] == ["preview", "confirm"]
