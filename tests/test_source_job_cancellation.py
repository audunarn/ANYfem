"""Exercise the mesh job's separate progress and numerical cancellation paths."""
import time
from types import SimpleNamespace

import pytest

from anyfem.mesh_jobs import MeshSettings, _cancellation_token, mesh_job_entry
from anygeometry import is_cancellation_signal


class _ReachedRemap(Exception):
    pass


class _CustomToken:
    def __init__(self):
        self.stages = []

    def raise_if_cancelled(self, stage):
        self.stages.append(stage)


@pytest.mark.parametrize("native", [True, False])
def test_automatic_source_job_polls_owner_signal_and_reports_stages(monkeypatch, native):
    import anymesher
    import anymesher.approximation_policy as policy
    import anymesher.recovery as recovery
    from anyfem.model import project as project_module
    from test_mesh_general_update import panel

    project, _, _, _ = panel("extruded")
    project.native_mesh_settings = None
    token = _cancellation_token() if native else _CustomToken()
    observed = []
    progress = []

    def observe(operation, check):
        assert is_cancellation_signal(check) == native
        if native:
            assert check is token.geometry_cancellation
        else:
            check(operation)
            assert operation in token.stages
        observed.append(operation)

    def resolution(*args, cancellation_check, **kwargs):
        observe("resolution", cancellation_check)
        return SimpleNamespace(mode="not_applicable")

    def selection(*args, cancellation_check, **kwargs):
        observe("selection", cancellation_check)
        return object(), {"selected": True}

    def generation(*args, cancellation_check, **kwargs):
        observe("generation", cancellation_check)
        return SimpleNamespace(mesh=anymesher.Mesh())

    def remap(*args, cancellation_check, **kwargs):
        observe("remap", cancellation_check)
        raise _ReachedRemap

    monkeypatch.setattr(policy, "resolve_geometry_approximation", resolution)
    monkeypatch.setattr(recovery, "select_curvilinear_recovery_policy", selection)
    monkeypatch.setattr(anymesher, "generate_automatic_mesh_result", generation)
    monkeypatch.setattr(project_module, "remap_mesh_to_source", remap)
    with pytest.raises(_ReachedRemap):
        project.mesh_session().generate_mesh(
            MeshSettings.create(.25, strategy="auto", element_order="linear"),
            cancellation=token, progress=lambda stage, message, fraction: progress.append((stage, message)))
    assert observed == ["resolution", "selection", "generation", "remap"]
    assert ("generation", "generating mesh elements") in progress
    assert any(stage == "preparation" for stage, _ in progress)


class _WaitingSourceProject:
    native_mesh_settings = None

    def automatic_source_mesh_candidate(self, strategy, order):
        return strategy == "auto" and order == "linear"

    def generate_mesh(self, *args, cancellation_check, _source_cancellation, **kwargs):
        assert is_cancellation_signal(_source_cancellation)
        cancellation_check("mesh element generation")
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            if _source_cancellation():
                raise RuntimeError("child source owner observed cancellation")
            time.sleep(.01)
        raise AssertionError("child cancellation did not reach its numerical signal")


def test_process_mesh_entry_forwards_cancellation_to_a_polling_source():
    from anyfem.job_process import WorkerProcessError, run_in_worker_process

    token = _cancellation_token()

    def progress(record):
        if record.stage == "generation":
            token.cancel("cancel during source generation")

    with pytest.raises(WorkerProcessError, match="child source owner observed cancellation"):
        run_in_worker_process(
            function=mesh_job_entry, snapshot=None, project_override=_WaitingSourceProject(),
            kwargs={"settings": MeshSettings.create(.25, strategy="auto", element_order="linear"),
                    "model_hash": "child-mesh-test"},
            token=token, progress=progress)


def test_regional_source_job_keeps_the_parent_token_and_progress(monkeypatch):
    from dataclasses import replace

    from anyfem import mesh_general_update as general
    from anyfem.mesh_jobs import RegionalJobInput, run_mesh_job
    from anyfem.native_meshing import NativeMeshCancelled
    from test_mesh_general_update import panel

    project, _, _, _ = panel("extruded")
    project.native_mesh_settings = None
    token = _cancellation_token()
    stages = []

    def generation(self, *args, cancellation_check, _source_cancellation, **kwargs):
        assert _source_cancellation is token.geometry_cancellation
        assert not _source_cancellation()
        cancellation_check("mesh element generation")
        assert _source_cancellation()
        cancellation_check("final source check")

    def report(stage, message, fraction):
        stages.append(stage)
        if stage == "generation":
            token.cancel("regional source cancelled")

    plan = SimpleNamespace(is_noop=False, patched_face=None, reuse_candidates=())
    monkeypatch.setattr(general, "plan_general_update", lambda *args, **kwargs: plan)
    monkeypatch.setattr(general.rm, "_decode", lambda _: object())
    monkeypatch.setattr(general, "_published_mesh_digest", lambda _: "parent")
    monkeypatch.setattr(type(project), "generate_mesh", generation)
    request = RegionalJobInput({"schema": general.SCHEMA, "mesh": "parent"}, {}, 0, 0)
    settings = replace(MeshSettings.create(.25, strategy="auto", element_order="linear"), regional=request)
    with pytest.raises(NativeMeshCancelled, match="regional source cancelled"):
        run_mesh_job(project, "regional-mesh", settings, report, token)
    assert "preparation" in stages and "generation" in stages
