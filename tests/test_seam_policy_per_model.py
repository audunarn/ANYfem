"""A model selects the owner's intersection seam policy itself (NativeMeshSettings.parameters['seam_policy']).

Nothing is read from or written to the process environment; an unset model keeps every call, hash and file unchanged.
"""
from __future__ import annotations

import pytest
import anygeometry
from anygeometry import GeometryModel

from anyfem import _batch_prepare, thread_route as tr
from anyfem.io.project_file import project_from_dict, project_to_dict
from anyfem.model.project import Project, ProjectError
from anyfem.native_meshing import NativeMeshSettings
from anyfem.structural_preparation import prepare_structural_connectivity, seam_policy_of, StructuralPreparationError


def crossing() -> GeometryModel:
    model = GeometryModel()
    model.add_plate(model.add_points(((0, 0, 0), (4, 0, 0), (4, 4, 0), (0, 4, 0))))
    model.add_plate(model.add_points(((2, 0, -1), (2, 4, -1), (2, 4, 1), (2, 0, 1))))
    return model


def settings(**parameters) -> NativeMeshSettings:
    return NativeMeshSettings.create(.5, element_order="linear", backend="quad_first", parameters=parameters)


def test_make_policy_unset_adds_nothing_and_set_is_carried(monkeypatch):
    monkeypatch.delenv("ANYGEOMETRY_SEAM_POLICY", raising=False)
    assert _batch_prepare.make_policy().seam_policy is None
    assert _batch_prepare.make_policy(None, "ring_split").seam_policy == "ring_split"
    assert _batch_prepare.resolved_route("ring_split")[1] == "ring_split"
    assert _batch_prepare.resolved_route()[1] == "baseline"


def test_unset_policy_passes_no_keyword_to_the_owner(monkeypatch):
    seen = []
    real = _batch_prepare.IntersectionBatchPolicy

    def spy(*args, **kwargs):
        seen.append(kwargs)
        return real(*args, **kwargs)

    monkeypatch.setattr(_batch_prepare, "IntersectionBatchPolicy", spy)
    _batch_prepare.make_policy()
    _batch_prepare.make_policy(None, None)
    assert seen and all("seam_policy" not in kwargs for kwargs in seen)
    _batch_prepare.make_policy(None, "ring_split")
    assert seen[-1]["seam_policy"] == "ring_split"


def test_per_model_value_beats_the_process_variable(monkeypatch):
    monkeypatch.setenv("ANYGEOMETRY_SEAM_POLICY", "baseline")
    assert _batch_prepare.resolved_route("ring_split")[1] == "ring_split"
    assert _batch_prepare.resolved_route()[1] == "baseline"


def test_seam_policy_of_validates():
    assert seam_policy_of(None) is None and seam_policy_of(settings()) is None
    assert seam_policy_of(settings(seam_policy="ring_split")) == "ring_split"
    assert seam_policy_of(settings(seam_policy="baseline")) == "baseline"
    for bad in ("", "Ring_Split", "other", 1, True):
        with pytest.raises(StructuralPreparationError):
            seam_policy_of(settings(seam_policy=bad))


@pytest.mark.parametrize("given", [None, "ring_split"])
def test_in_process_preparation_plans_with_the_policy_and_leaves_the_environment_alone(monkeypatch, given):
    monkeypatch.delenv("ANYGEOMETRY_SEAM_POLICY", raising=False)
    seen = []
    real = anygeometry.plan_intersections

    def spy(geometry, operands, *, policy):
        seen.append(policy.seam_policy)
        return real(geometry, operands, policy=policy)

    monkeypatch.setattr(anygeometry, "plan_intersections", spy)
    import os
    prepare_structural_connectivity(crossing(), seam_policy=given)
    assert seen == [given]
    assert "ANYGEOMETRY_SEAM_POLICY" not in os.environ


def test_thread_route_job_carries_the_policy_and_the_child_does_not_inherit_the_variable(monkeypatch):
    monkeypatch.setenv("ANYGEOMETRY_SEAM_POLICY", "baseline")
    assert "ANYGEOMETRY_SEAM_POLICY" in tr._planning_env()
    assert "ANYGEOMETRY_SEAM_POLICY" not in tr._planning_env("ring_split")
    captured = {}

    class Health:
        status = tr.OK
        reason = ""
        body = {"health": {}}

    monkeypatch.setattr(tr, "route_enabled", lambda: True)
    monkeypatch.setattr(tr, "_min_faces", lambda: 0)
    monkeypatch.setattr(tr, "locate_runtime", lambda: __import__("pathlib").Path("python.exe"))
    monkeypatch.setattr(tr, "check_health", lambda *a, **k: Health())
    monkeypatch.setattr(tr, "_declined_before_spawn", lambda adoption, geometry, operands, seam_policy=None: captured.update(declined=seam_policy) or "")

    def fake_run_child(python, job, document, **kwargs):
        captured.update(job=job, env=kwargs.get("extra_env"))
        return tr.ChildOutcome(status=tr.UNAVAILABLE, reason="captured")

    monkeypatch.setattr(tr, "run_child", fake_run_child)
    if __import__("os").name != "nt":
        pytest.skip("the thread route is Windows only")
    model = crossing()
    operands = tuple(model.handle("face", f) for f in sorted(model.faces))
    assert tr.try_prepare(model, operands, seam_policy="ring_split") is None
    assert captured["job"]["seam_policy"] == "ring_split" and captured["declined"] == "ring_split"
    assert "ANYGEOMETRY_SEAM_POLICY" not in captured["env"]
    captured.clear()
    assert tr.try_prepare(model, operands) is None
    assert "seam_policy" not in captured["job"] and captured["env"]["ANYGEOMETRY_SEAM_POLICY"] == "baseline"


def test_project_roundtrip_keeps_the_policy_and_unset_files_are_unchanged():
    plain, chosen = Project(), Project()
    for project, value in ((plain, None), (chosen, "ring_split")):
        project.target_size = .5
        project.set_native_mesh_settings(settings() if value is None else settings(seam_policy=value))
    assert "seam_policy" not in str(project_to_dict(plain)["meshing"])
    restored = project_from_dict(project_to_dict(chosen))
    assert seam_policy_of(restored.native_mesh_settings) == "ring_split"
    assert project_to_dict(restored)["meshing"] == project_to_dict(chosen)["meshing"]


def test_project_refuses_an_unknown_policy():
    with pytest.raises(ProjectError, match="seam_policy"):
        Project().set_native_mesh_settings(settings(seam_policy="nope"))


def crossing_project(seam_policy=None) -> Project:
    from anyfem.model.materials import MaterialSpec
    project = Project("seam crossing")
    project.add_material(MaterialSpec("steel", constants={"elastic_modulus": 210e9, "poisson_ratio": 0.3}))
    project.add_plate_section("plate", 0.01, "steel")
    for points in (((0, 0, 0), (4, 0, 0), (4, 4, 0), (0, 4, 0)), ((2, 0, -1), (2, 4, -1), (2, 4, 1), (2, 0, 1))):
        project.assign_plate(project.geometry.add_plate(project.geometry.add_points(points)), "plate")
    project.set_native_mesh_settings(NativeMeshSettings.create(
        1., element_order="linear", backend="mapped",
        parameters={} if seam_policy is None else {"seam_policy": seam_policy}))
    return project


@pytest.mark.parametrize("given", [None, "ring_split"])
def test_mesh_job_prepares_with_the_models_policy_and_keys_the_prepared_cache(monkeypatch, given):
    """Off route: the model's policy reaches the owner plan; a different policy is a different prepared model."""
    from anyfem.jobs import _cancellation_token
    from anyfem.mesh_execution import MeshExecutionPolicy
    from anyfem.mesh_jobs import MeshSettings, run_mesh_job
    monkeypatch.delenv("ANYGEOMETRY_SEAM_POLICY", raising=False)
    seen = []
    real = anygeometry.plan_intersections

    def spy(geometry, operands, *, policy):
        seen.append(policy.seam_policy)
        return real(geometry, operands, policy=policy)

    monkeypatch.setattr(anygeometry, "plan_intersections", spy)
    project = crossing_project(given)
    result = run_mesh_job(project, "m", MeshSettings.create(1., element_order="linear", strategy="mapped",
                                                           execution_policy=MeshExecutionPolicy.off()),
                          lambda *a: None, _cancellation_token())
    assert result.mesh_hash
    assert seen and set(seen) == {given}, seen
    key = project._mesh_preparation_cache[0]
    assert ("ring_split" in key) is (given is not None)


def test_the_policy_enters_the_mesh_identity_only_when_set():
    from anyfem.jobs import _cancellation_token
    from anyfem.mesh_execution import MeshExecutionPolicy
    from anyfem.mesh_jobs import MeshSettings, run_mesh_job

    project = crossing_project()

    def run(seam):  # one project: model ids are random per project and part of every mesh hash
        project.set_native_mesh_settings(NativeMeshSettings.create(
            1., element_order="linear", backend="mapped", parameters={} if seam is None else {"seam_policy": seam}))
        return run_mesh_job(project, "m", MeshSettings.create(
            1., element_order="linear", strategy="mapped", execution_policy=MeshExecutionPolicy.off()),
            lambda *a: None, _cancellation_token())
    plain, chosen, again = run(None), run("ring_split"), run(None)
    assert plain.mesh_hash == again.mesh_hash
    assert chosen.mesh_hash != plain.mesh_hash  # different policy = different identity, even where the mesh is equal


def test_snapshot_for_the_process_route_keeps_the_policy():
    from anyfem.document import DocumentSession
    project = crossing_project("ring_split")
    restored = DocumentSession(project).snapshot().thaw()
    assert seam_policy_of(restored.native_mesh_settings) == "ring_split"


@pytest.mark.parametrize("context", ["trace", "junction_bounds", "binding_budget"])
def test_explicit_ring_split_that_the_owner_resolves_to_baseline_is_a_typed_refusal(monkeypatch, context):
    """The ONE policy-level check: an explicit ring_split the owner does not resolve to ring_split is refused (serial and
    thread route alike: the refusal comes before any child is spawned); unset and explicit baseline are unaffected."""
    from contextlib import ExitStack
    from anygeometry import arrangement_geometry, cylinder_charts
    from anygeometry.exact_predicates import collect_decisions
    monkeypatch.delenv("ANYGEOMETRY_SEAM_POLICY", raising=False)
    monkeypatch.setattr(tr, "run_child", lambda *a, **k: pytest.fail("a child was spawned for a refused policy"))
    with ExitStack() as stack:
        if context == "trace":
            stack.enter_context(collect_decisions())
        elif context == "junction_bounds":
            token = arrangement_geometry._JUNCTION_BOUNDS.set(True)
            stack.callback(arrangement_geometry._JUNCTION_BOUNDS.reset, token)
        else:
            token = cylinder_charts._binding_budget.set(5)
            stack.callback(cylinder_charts._binding_budget.reset, token)
        for route_on in (False, True):
            if route_on:
                tr.configure(enabled=True, min_faces=0)
            try:
                with pytest.raises(StructuralPreparationError, match="ring_split.*baseline"):
                    prepare_structural_connectivity(crossing(), seam_policy="ring_split")
            finally:
                tr.configure(reset=True)
        prepare_structural_connectivity(crossing())
        prepare_structural_connectivity(crossing(), seam_policy="baseline")


def test_honoured_ring_split_is_not_refused():
    report = prepare_structural_connectivity(crossing(), seam_policy="ring_split")
    assert report.connections


def test_refusal_does_not_depend_on_the_prepared_model_cache(monkeypatch):
    """Review r1: a warm prepared-model cache must not let an explicit ring_split run under decision tracing."""
    from anygeometry.exact_predicates import collect_decisions
    from anyfem.jobs import _cancellation_token
    from anyfem.mesh_execution import MeshExecutionPolicy
    from anyfem.mesh_jobs import MeshSettings, run_mesh_job
    monkeypatch.delenv("ANYGEOMETRY_SEAM_POLICY", raising=False)
    project = crossing_project("ring_split")
    settings = MeshSettings.create(1., element_order="linear", strategy="mapped", execution_policy=MeshExecutionPolicy.off())
    run_mesh_job(project, "m", settings, lambda *a: None, _cancellation_token())      # warms the cache
    with collect_decisions(), pytest.raises(Exception, match="ring_split.*baseline"):
        run_mesh_job(project, "m", settings, lambda *a: None, _cancellation_token())


def test_context_that_starts_during_preparation_is_refused_not_applied_as_baseline():
    from contextlib import ExitStack
    from anygeometry.exact_predicates import collect_decisions
    stack = ExitStack()
    started = []

    def callback(stage):
        if not started:
            started.append(stack.enter_context(collect_decisions()))
    try:
        with pytest.raises(StructuralPreparationError, match="ring_split"):
            prepare_structural_connectivity(crossing(), seam_policy="ring_split", cancellation_check=callback)
    finally:
        stack.close()
    assert started


def test_project_file_with_a_seam_policy_needs_the_new_format_and_unset_files_do_not():
    from anyfem.io import project_file
    plain, chosen = Project(), Project()
    plain.set_native_mesh_settings(settings())
    chosen.set_native_mesh_settings(settings(seam_policy="ring_split"))
    assert project_to_dict(plain)["anyfem"]["format"] < project_file.SEAM_POLICY_FORMAT
    assert project_to_dict(chosen)["anyfem"]["format"] == project_file.SEAM_POLICY_FORMAT == project_file.MAX_READ_FORMAT
    assert seam_policy_of(project_from_dict(project_to_dict(chosen)).native_mesh_settings) == "ring_split"


def test_explicit_null_is_not_unset():
    with pytest.raises(StructuralPreparationError):
        seam_policy_of(settings(seam_policy=None))
    with pytest.raises(ProjectError):
        Project().set_native_mesh_settings(settings(seam_policy=None))


@pytest.mark.parametrize("entry", ["plan_regional_update", "generate_regional_update"])
def test_regional_dispatch_entries_check_the_policy_once_per_request(entry):
    """Owner contract 'once per request': both regional entries refuse an explicit ring_split the owner resolves elsewhere."""
    from anygeometry.exact_predicates import collect_decisions
    from anyfem import regional_dispatch
    function = getattr(regional_dispatch, entry)
    args = ({"schema": "unknown"},) if entry == "plan_regional_update" else ({"schema": "unknown"}, object())
    chosen, plain = crossing_project("ring_split"), crossing_project()
    with collect_decisions():
        with pytest.raises(StructuralPreparationError, match="ring_split.*baseline"):
            function(chosen, None, *args)
        try:  # unset: the seam check is not what stops this
            function(plain, None, *args)
        except StructuralPreparationError:
            pytest.fail("an unset policy must never be refused by the seam check")
        except Exception:
            pass
