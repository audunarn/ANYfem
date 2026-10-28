"""Mass-based relief through the existing ANYfem static application boundary."""
import json

import numpy as np
import pytest

from anyfem import solve_linear_static
from anyfem.model import ProjectError
from anyfem.model.records import AnalysisDefinition, JobStatus
from anyfem.solve.run import solve_linear_static_many, preflight
from test_free_floating import free_beam, follower_plate


@pytest.mark.parametrize("solver", [solve_linear_static, solve_linear_static_many])
def test_explicit_relief_end_force_acceleration_and_result_diagnostics(solver):
    project, mesh, _ = free_beam(balanced=False)
    messages = []
    result = solver(project, mesh=mesh, inertia_relief=True, progress=messages.append)
    shape = result.shapes[0] if hasattr(result, "shapes") else result
    summary = shape.inertia_relief
    assert summary["status"] == "converged"
    assert summary["total_mass"] == pytest.approx(7850.*.2*.02*2.)
    np.testing.assert_allclose(summary["acceleration_at_center_of_mass"], [100./62.8, 0., 0.], rtol=1e-10, atol=1e-12)
    assert shape.info["reactions"] == {}
    for name in ("force_balance_error", "moment_balance_error", "elastic_backward_error", "gauge_error"):
        assert summary["diagnostics"][name] <= 1e-10
    assert "inertia relief" in shape.summary()
    assert any("acceleration at reference" in str(m) for m in messages)
    assert any("maximum force balance error" in str(m) for m in messages)
    assert not preflight(shape.built, analysis_type="Linear static", inertia_relief=True).errors
    assert shape.stresses().element_stresses
    assert not any("not mass-based inertia relief" in str(m) for m in messages)
    json.dumps(summary, allow_nan=False)


def test_batch_relief_refuses_unsupported_solver_options():
    project,mesh,_=free_beam(balanced=False)
    with pytest.raises(TypeError,match="solver_type"):
        solve_linear_static_many(project,mesh=mesh,inertia_relief=True,solver_type="iterative")


def test_batch_keeps_original_case_accelerations_and_bypasses_compression(monkeypatch):
    import anyfem.solve.run as module
    project, mesh, names = free_beam(balanced=False, count=10)
    def forbidden(*args, **kwargs): pytest.fail("relief used synthetic compressed loads")
    monkeypatch.setattr(module, "_load_blocks", forbidden)
    result = solve_linear_static_many(project, mesh=mesh, inertia_relief=True)
    assert result.case_names == tuple(names)
    assert len(result.info["case_results"]) == 10
    for i, shape in enumerate(result.shapes):
        expected = solve_linear_static(project, mesh=mesh, load_case=names[i], inertia_relief=True)
        np.testing.assert_allclose(shape.displacements, expected.displacements, rtol=1e-8, atol=1e-12)
        np.testing.assert_allclose(shape.inertia_relief["acceleration_at_reference"], [(i+1)*100./62.8, 0., 0.], rtol=1e-10, atol=1e-12)
        assert len(shape.info["case_results"]) == 1


def test_reference_point_and_angular_acceleration_are_owner_values():
    from anysolver import solve_inertia_relief
    project, mesh, _ = free_beam(balanced=False)
    end = project.load_case().point_loads[0].ref
    project.load_case().add_point_load(end, moment=(0., 0., 25.))
    point = [3., 1., -.5]
    actual = solve_linear_static(project, mesh=mesh, inertia_relief=True, reference_point=point)
    owner = solve_inertia_relief(actual.built.fe_model, actual.built.load_case, reference_point=point)
    np.testing.assert_allclose(actual.inertia_relief["acceleration_at_reference"], owner.acceleration_at_reference, rtol=1e-10)
    np.testing.assert_allclose(actual.inertia_relief["angular_acceleration"], owner.angular_acceleration, rtol=1e-10)


@pytest.mark.parametrize("solver", [solve_linear_static, solve_linear_static_many])
@pytest.mark.parametrize("options", [{"constraint_mode": "transformation"}, {"allow_unbalanced_free_free": True}, {"inertia_relief": "true"}])
def test_incompatible_options_refused_before_build(solver, options):
    with pytest.raises(ValueError): solver(**{"inertia_relief": True, **options})


@pytest.mark.parametrize("solver", [solve_linear_static, solve_linear_static_many])
def test_reference_without_relief_refused(solver):
    with pytest.raises(ValueError, match="reference_point"):
        solver(reference_point=[1., 2., 3.])


@pytest.mark.parametrize("solver", [solve_linear_static, solve_linear_static_many])
def test_actual_follower_cases_remain_blocked(solver):
    project, mesh = follower_plate(batch=solver is solve_linear_static_many)
    with pytest.raises(ProjectError, match="LOAD001"):
        solver(project, mesh=mesh, inertia_relief=True)


@pytest.mark.parametrize("name,solver", [("Linear static", solve_linear_static), ("Batch linear static", solve_linear_static_many)])
def test_saved_settings_through_immutable_job_and_result_transport(tmp_path, name, solver):
    from anyfem.application.workflow import _execute_analysis_job
    from anyfem.io.project_file import save_project, load_project
    from anyfem.result_transport import encode_result, decode_result
    from anyfem.job_process import dumps, loads
    from anyfem.mesh_jobs import clone_mesh_for_job
    project, mesh, _ = free_beam(balanced=False, count=2)
    record = project.add_analysis(AnalysisDefinition("Physical relief", type=name, settings={
        "inertia_relief": True, "reference_point": [1., 2., 3.]}))
    path = save_project(project, tmp_path/"relief.anyfem")
    restored = load_project(path)
    settings = restored.analyses[record.id].settings
    result = _execute_analysis_job(project=restored, solver_function=solver,
        analysis_name=name, options={"mesh": clone_mesh_for_job(mesh), **settings}, progress=lambda _: None,
        requested_stress=True)
    decoded = decode_result(loads(dumps(encode_result(result))))
    first = decoded.shapes[0] if hasattr(decoded, "shapes") else decoded
    assert first.inertia_relief["status"] == "converged"
    assert first.inertia_relief["reference_point"] == [1., 2., 3.]
    assert first.info["reactions"] == {}


@pytest.mark.parametrize("name,solver", [("Linear static", solve_linear_static), ("Batch linear static", solve_linear_static_many)])
def test_real_process_worker_returns_acceleration_and_retires(name, solver):
    from anyfem.application.workflow import _execute_analysis_job
    from anyfem.document import DocumentSession
    from anyfem.mesh_jobs import clone_mesh_for_job
    from anyfem.job_process import run_in_worker_process
    project, mesh, _ = free_beam(balanced=False, count=2)
    captured={}; messages=[]
    options={"mesh":clone_mesh_for_job(mesh),"inertia_relief":True}
    if name=="Batch linear static":options["load_cases"]=["default","case1"]
    result=run_in_worker_process(function=_execute_analysis_job,
        kwargs={"solver_function":solver,"analysis_name":name,
            "options":options,"requested_stress":True},
        snapshot=DocumentSession(project).snapshot(),project_override=None,
        progress=messages.append,token=None,on_process=lambda p:captured.setdefault("process",p))
    shape=result.shapes[0] if hasattr(result,"shapes") else result
    if name=="Batch linear static":assert result.case_names==("default","case1")
    assert shape.inertia_relief["status"]=="converged"
    assert shape.inertia_relief["acceleration_at_reference"][0]==pytest.approx(100./62.8,rel=1e-10)
    assert not captured["process"].is_alive()
    assert any("acceleration at reference" in str(m) for m in messages)


@pytest.mark.parametrize("solver", [solve_linear_static, solve_linear_static_many])
def test_cancelled_relief_never_returns_result(solver):
    from anysolver import CancellationToken, SolveCancelled
    project, mesh, _ = free_beam(balanced=False)
    token = CancellationToken(); token.cancel("test")
    with pytest.raises(SolveCancelled):
        solver(project, mesh=mesh, inertia_relief=True, cancellation_token=token)


def test_numerical_non_success_fails_before_recovery_and_publication(monkeypatch):
    from types import SimpleNamespace
    from anyfem.application.workflow import _execute_analysis_job
    from anyfem.document import DocumentSession
    from anyfem.jobs import JobManager
    from anyfem.post.results import LinearSolution
    import anysolver
    project, mesh, _ = free_beam(balanced=False)
    monkeypatch.setattr(anysolver, "solve_inertia_relief", lambda *args, **kwargs: SimpleNamespace(
        converged=False, displacements=None, status="unresolved_elastic_stiffness", error="numerical nonconvergence"))
    recovered=[]
    monkeypatch.setattr(LinearSolution, "stresses", lambda *args, **kwargs: recovered.append(True))
    manager=JobManager(project, executor="thread")
    record=manager.submit(AnalysisDefinition("IR failure", settings={"inertia_relief":True}),
        DocumentSession(project).snapshot(), _execute_analysis_job,
        kwargs={"solver_function":solve_linear_static, "analysis_name":"Linear static",
            "options":{"mesh":mesh,"inertia_relief":True}, "requested_stress":True})
    manager.wait(record.id, timeout=15); manager.poll()
    assert record.status is JobStatus.FAILED
    assert not recovered
    with pytest.raises(KeyError):manager.result(record.id)


@pytest.mark.parametrize("solver", [solve_linear_static, solve_linear_static_many])
def test_real_plate_pressure_mass_and_balance_through_immutable_job(solver):
    from anyfem.application.workflow import _execute_analysis_job
    from anyfem.mesh_jobs import clone_mesh_for_job
    project,mesh=follower_plate(batch=solver is solve_linear_static_many)
    for case in project.load_cases.values():case.set_follower_pressure(False)
    result=_execute_analysis_job(project=project,solver_function=solver,
        analysis_name="Linear static" if solver is solve_linear_static else "Batch linear static",
        options={"mesh":clone_mesh_for_job(mesh),"inertia_relief":True},progress=lambda _:None)
    shapes=result.shapes if hasattr(result,"shapes") else [result]
    for shape in shapes:
        summary=shape.inertia_relief
        assert summary["total_mass"]==pytest.approx(78.5,rel=1e-10)
        force=100. if shape.built.load_case.name=="default" else 200.
        assert abs(summary["acceleration_at_center_of_mass"][2])==pytest.approx(force/78.5,rel=1e-10)
        assert summary["diagnostics"]["force_balance_error"]<=1e-10
        assert summary["diagnostics"]["moment_balance_error"]<=1e-10
        assert shape.info["reactions"]=={}


@pytest.mark.parametrize("solver", [solve_linear_static, solve_linear_static_many])
def test_physical_supports_are_not_silently_removed(solver):
    from anyfem.solve.build import build_fe_model
    from anysolver import FixedSupport
    project,mesh,_=free_beam(balanced=False)
    built=build_fe_model(project,mesh,require_supports=False)
    built.fe_model.add_boundary_condition(FixedSupport("fixed",[min(built.fe_model.mesh.nodes)]))
    with pytest.raises(ValueError) as error:solver(built=built,inertia_relief=True)
    assert getattr(error.value,"code",None)=="unsupported_constraints" or error.value.diagnostics["code"]=="unsupported_constraints"


def test_reordered_owner_case_summaries_are_refused(monkeypatch):
    import anysolver
    original=anysolver.solve_linear_many
    def reordered(*args,**kwargs):
        matrix,info=original(*args,**kwargs)
        return matrix,{**info,"case_results":tuple(reversed(info["case_results"]))}
    monkeypatch.setattr(anysolver,"solve_linear_many",reordered)
    project,mesh,names=free_beam(balanced=False,count=2)
    with pytest.raises(ProjectError,match="case identity"):
        solve_linear_static_many(project,mesh=mesh,load_cases=names,inertia_relief=True)


@pytest.mark.parametrize("name,solver",[("Linear static",solve_linear_static),("Batch linear static",solve_linear_static_many)])
def test_process_worker_preserves_physical_support_refusal(name,solver):
    from anyfem import fixed
    from anyfem.application.workflow import _execute_analysis_job
    from anyfem.document import DocumentSession
    from anyfem.mesh_jobs import clone_mesh_for_job
    from anyfem.job_process import run_in_worker_process,WorkerProcessError
    project,mesh,_=free_beam(balanced=False)
    project.add_support(fixed(project.load_case().point_loads[0].ref))
    captured={}
    with pytest.raises(WorkerProcessError) as error:
        run_in_worker_process(function=_execute_analysis_job,
            kwargs={"solver_function":solver,"analysis_name":name,
                "options":{"mesh":clone_mesh_for_job(mesh),"inertia_relief":True}},
            snapshot=DocumentSession(project).snapshot(),project_override=None,progress=lambda _:None,
            token=None,on_process=lambda p:captured.setdefault("process",p))
    assert error.value.remote_type in {"InertiaReliefError","StaticBatchError"}
    assert not captured["process"].is_alive()


@pytest.mark.parametrize("solver",[solve_linear_static,solve_linear_static_many])
def test_acceleration_and_balance_survive_saved_result_reopen(tmp_path,solver):
    from anyfem.io.artifacts import ArtifactStore
    from anyfem.io.result_artifact import result_artifact_payload
    project,mesh,names=free_beam(balanced=False,count=2)
    result=solver(project,mesh=mesh,inertia_relief=True,reference_point=[1.,2.,3.])
    payload=result_artifact_payload(result)
    store=ArtifactStore(tmp_path/"relief.anyfem")
    artifact=store.write_result(job_id="job",document_id="document",mesh_id="mesh",
        model_hash="model",mesh_hash="mesh",analysis_hash="analysis",**payload.write_result_inputs())
    saved=store.open_result(artifact).metadata("provenance")
    if solver is solve_linear_static:
        summaries=[saved["inertia_relief"]]
    else:
        entries=saved["inertia_relief_cases"]
        assert [entry["case_name"] for entry in entries]==names
        summaries=[entry["summary"] for entry in entries]
    for i,summary in enumerate(summaries):
        assert summary["reference_point"]==[1.,2.,3.]
        assert summary["acceleration_at_reference"][0]==pytest.approx((i+1)*100./62.8,rel=1e-10)
        assert summary["diagnostics"]["force_balance_error"]<=1e-10
