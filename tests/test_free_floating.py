"""Explicit free-floating settings through the application production route."""
import numpy as np
import pytest

from anyfem import Project, solve_linear_static, steel
from anyfem.model import BeamSection, ProjectError
from anyfem.model.records import AnalysisDefinition
from anyfem.solve.run import preflight, solve_linear_static_many
from anyfem.solve.build import build_fe_model
from anysolver.static_batch import StaticBatchError


def free_beam(*, balanced=True, count=1):
    project = Project(name="free beam")
    project.add_material(steel("S355", 0.02))
    project.add_beam_section(BeamSection(
        name="bar", profile="Flatbar", material="S355",
        flange_width=0.2, flange_thickness=0.02,
        web_direction=(0.0, 0.0, 1.0)))
    a = project.geometry.add_point(0, 0, 0)
    b = project.geometry.add_point(2, 0, 0)
    project.assign_beam(project.geometry.add_line(a, b), "bar")
    names = ["default" if index == 0 else f"case{index}" for index in range(count)]
    for index, name in enumerate(names):
        case = project.load_case(name)
        case.add_point_load(project.point(b), force=(100.0 * (index + 1), 0, 0))
        if balanced:
            case.add_point_load(project.point(a), force=(-100.0 * (index + 1), 0, 0))
    return project, project.generate_mesh(1.0), names


@pytest.mark.parametrize("solver", [solve_linear_static, solve_linear_static_many])
@pytest.mark.parametrize("options", [{}, {"allow_unbalanced_free_free": True}, {"constraint_mode": "auto"}])
def test_ordinary_unsupported_project_still_refused(solver, options):
    project, mesh, _ = free_beam()
    with pytest.raises(ProjectError, match="support"):
        solver(project, mesh=mesh, **options)


@pytest.mark.parametrize("solver", [solve_linear_static, solve_linear_static_many])
def test_invalid_constraint_mode_fails_before_build(solver):
    with pytest.raises(ValueError, match="constraint_mode"):
        solver(constraint_mode="inertia_relief")


def test_balanced_nullspace_single_and_preflight():
    project, mesh, _ = free_beam()
    result = solve_linear_static(project, mesh=mesh, constraint_mode="nullspace")
    info = result.info["convergence_info"]
    assert info["status"] == "converged"
    assert info["method"] == "rigid_body_nullspace_augmented"
    assert info["nullspace_rank"] == 6
    assert info["relative_backward_error"] <= info["relative_backward_error_limit"]
    assert np.linalg.norm(result.displacements) > 0
    assert np.linalg.norm(info["rigid_body_lagrange_multipliers"]) < 1e-8
    assert any(issue.code == "MECH001" for issue in preflight(
        result.built, analysis_type="Linear static").errors)
    assert not preflight(result.built, analysis_type="Linear static", constraint_mode="nullspace").errors
    assert any(issue.code == "MECH001" for issue in preflight(
        result.built, analysis_type="Nonlinear static", constraint_mode="nullspace").errors)


def test_unbalanced_single_refusal_and_separate_opt_in():
    project, mesh, _ = free_beam(balanced=False)
    with pytest.raises(StaticBatchError, match="rigid-body component"):
        solve_linear_static(project, mesh=mesh, constraint_mode="nullspace")
    result = solve_linear_static(project, mesh=mesh, constraint_mode="nullspace",
                                 allow_unbalanced_free_free=True)
    info = result.info["convergence_info"]
    assert info["status"] == "converged"
    assert np.linalg.norm(info["rigid_body_lagrange_multipliers"]) > 1
    assert any("generalized reactions" in warning for warning in info["warnings"])


@pytest.mark.parametrize("balanced", [True, False])
def test_batch_default_compression_tolerance_keeps_original_nullspace_diagnostics(balanced):
    project, mesh, names = free_beam(balanced=balanced, count=10)
    if not balanced:
        with pytest.raises(StaticBatchError, match="rigid-body component"):
            solve_linear_static_many(project, mesh=mesh, constraint_mode="nullspace")
    result = solve_linear_static_many(project, mesh=mesh, constraint_mode="nullspace",
                                      allow_unbalanced_free_free=not balanced)
    assert result.status == "converged"
    assert result.case_names == tuple(names)
    assert "load_compression" not in result.info
    assert len(result.info["case_results"]) == 10
    for case in result.info["case_results"]:
        info = case["solver_info"]
        assert info["method"] == "rigid_body_nullspace_augmented"
        assert info["nullspace_rank"] == 6
        multipliers = np.linalg.norm(info["rigid_body_lagrange_multipliers"])
        assert multipliers < 1e-8 if balanced else multipliers > 1
    for index, shape in enumerate(result.shapes):
        single = solve_linear_static(project, mesh=mesh, load_case=names[index],
                                     constraint_mode="nullspace", allow_unbalanced_free_free=not balanced)
        np.testing.assert_allclose(shape.displacements, single.displacements, rtol=1e-8, atol=1e-12)
    assert [case["solver_info"]["input_index"] for case in result.info["case_results"]] == list(range(10))


@pytest.mark.parametrize("name,solver", [("Linear static", solve_linear_static),
                                         ("Batch linear static", solve_linear_static_many)])
def test_saved_settings_immutable_workflow_build_preflight_solver(name, solver, tmp_path):
    from anyfem.application.workflow import _execute_analysis_job
    from anyfem.io.project_file import save_project, load_project
    project, mesh, _ = free_beam()
    definition = project.add_analysis(AnalysisDefinition(
        "Floating", type=name, settings={"constraint_mode": "nullspace",
                                         "allow_unbalanced_free_free": False}))
    path = save_project(project, tmp_path / "floating.anyfem")
    restored = load_project(path)
    settings = restored.analyses[definition.id].settings
    assert settings == definition.settings
    messages = []
    result = _execute_analysis_job(project=restored, solver_function=solver,
        analysis_name=name, options={"mesh": mesh, **settings}, progress=messages.append)
    assert "building immutable model snapshot" in messages
    assert "preflight passed" in messages
    assert (result.info.get("status") or result.info["convergence_info"]["status"]) == "converged"
    assert not restored.supports


def test_nullspace_does_not_hide_other_preflight_errors():
    project, mesh, _ = free_beam()
    built = build_fe_model(project, mesh, require_supports=False)
    # A malformed public material must still be rejected with free modes allowed.
    built.fe_model.materials.clear()
    assert preflight(built, analysis_type="Linear static", constraint_mode="nullspace").errors


@pytest.mark.parametrize("allowed", [False, True])
def test_actual_job_refusal_is_failed_before_recovery_or_publication(allowed, monkeypatch):
    from anyfem.application.workflow import _execute_analysis_job
    from anyfem.document import DocumentSession
    from anyfem.jobs import JobManager
    from anyfem.model import JobStatus
    from anyfem.post.results import LinearSolution
    project, mesh, _ = free_beam(balanced=False)
    recovered = []
    monkeypatch.setattr(LinearSolution, "stresses", lambda self: recovered.append(self) or {})
    manager = JobManager(project, executor="thread")
    analysis = AnalysisDefinition("Floating", settings={"constraint_mode": "nullspace",
        "allow_unbalanced_free_free": allowed})
    record = manager.submit(analysis, DocumentSession(project).snapshot(),
        _execute_analysis_job, kwargs={"solver_function": solve_linear_static,
            "analysis_name": "Linear static", "options": {"mesh": mesh, **analysis.settings},
            "requested_stress": True})
    manager.wait(record.id, timeout=15)
    manager.poll()
    assert record.status is (JobStatus.COMPLETED if allowed else JobStatus.FAILED)
    if allowed:
        assert manager.result(record.id).info["convergence_info"]["status"] == "converged"
        assert len(recovered) == 1
    else:
        with pytest.raises(KeyError):
            manager.result(record.id)
        assert not recovered


@pytest.mark.parametrize("solver", [solve_linear_static, solve_linear_static_many])
def test_disconnected_components_and_balancing_are_visible_in_progress(solver):
    project, _, _ = free_beam(balanced=False, count=2)
    a = project.geometry.add_point(0, 2, 0)
    b = project.geometry.add_point(2, 2, 0)
    project.assign_beam(project.geometry.add_line(a, b), "bar")
    messages = []
    solver(project, mesh=project.generate_mesh(1), constraint_mode="nullspace",
           allow_unbalanced_free_free=True, progress=messages.append)
    assert any("12 rigid modes; 2 connected components" in str(message) for message in messages)
    assert sum("multiple disconnected components" in str(message) for message in messages) == 1
    assert sum("balancing generalized reactions" in str(message) for message in messages) == 1
    assert any("relative rigid-body load imbalance" in str(message) for message in messages)


@pytest.mark.parametrize("solver", [solve_linear_static, solve_linear_static_many])
def test_cancelled_nullspace_never_returns_a_solution(solver):
    from anysolver import CancellationToken, SolveCancelled
    project, mesh, _ = free_beam()
    token = CancellationToken()
    token.cancel("test cancellation")
    with pytest.raises(SolveCancelled):
        solver(project, mesh=mesh, constraint_mode="nullspace", cancellation_token=token)


@pytest.mark.parametrize("options", [{"solver_type": "cg"}, {"precond": False}])
def test_checked_single_rejects_incompatible_solver_options(options):
    project, mesh, _ = free_beam()
    with pytest.raises(ValueError, match="nullspace"):
        solve_linear_static(project, mesh=mesh, constraint_mode="nullspace", **options)


def test_closed_shell_pressure_box_through_immutable_workflow():
    from anyfem.application.workflow import _execute_analysis_job
    project = Project("small floating pressure box")
    project.add_material(steel("S355", 0.01))
    project.add_plate_section("shell", thickness=0.01, material="S355")
    geometry = project.geometry
    points = geometry.add_points([(0,0,0),(2,0,0),(2,1,0),(0,1,0),
                                  (0,0,1),(2,0,1),(2,1,1),(0,1,1)])
    faces = []
    for indices in ((3,2,1,0),(4,5,6,7),(0,1,5,4),(1,2,6,5),(2,3,7,6),(3,0,4,7)):
        edges = geometry.add_polyline([points[i] for i in indices], close=True)
        face = geometry.add_face(edges)
        project.assign_plate(face, "shell")
        faces.append(face)
    for name, pressure in (("default", 100), ("second", 200)):
        project.load_case(name).add_pressure(project.face(faces[0]), pressure)
    mesh = project.generate_mesh(0.5)
    for name, solver in (("Linear static", solve_linear_static), ("Batch linear static", solve_linear_static_many)):
        messages = []
        result = _execute_analysis_job(project=project, solver_function=solver, analysis_name=name,
            options={"mesh": mesh, "constraint_mode": "nullspace", "allow_unbalanced_free_free": True},
            progress=messages.append)
        assert "preflight passed" in messages
        cases = result.info["case_results"]
        assert all(case["solver_info"]["nullspace_rank"] == 6 for case in cases)
        assert all(case["solver_info"]["relative_backward_error"] <=
                   case["solver_info"]["relative_backward_error_limit"] for case in cases)
        shapes = result.shapes if name == "Batch linear static" else [result]
        assert all(np.isfinite(shape.displacements).all() for shape in shapes)


def follower_plate(*, batch=False):
    project = Project("unsupported follower plate")
    project.add_material(steel("S355", 0.01))
    project.add_plate_section("shell", thickness=0.01, material="S355")
    points = project.geometry.add_points([(0,0,0),(1,0,0),(1,1,0),(0,1,0)])
    face = project.geometry.add_face(project.geometry.add_polyline(points, close=True))
    project.assign_plate(face, "shell")
    project.load_case().add_pressure(project.face(face), 100)
    # In a batch, the invalid case is deliberately not the snapshot/default case.
    case = project.load_case("follower") if batch else project.load_case()
    if batch:
        case.add_pressure(project.face(face), 200)
    case.set_follower_pressure(True)
    return project, project.generate_mesh(0.5)


@pytest.mark.parametrize("solver", [solve_linear_static, solve_linear_static_many])
def test_actual_mapped_nullspace_loads_reject_follower_pressure_headless(solver):
    project, mesh = follower_plate(batch=solver is solve_linear_static_many)
    with pytest.raises(ProjectError, match="LOAD001"):
        solver(project, mesh=mesh, constraint_mode="nullspace", allow_unbalanced_free_free=True)


@pytest.mark.parametrize("name,solver", [("Linear static", solve_linear_static),
                                         ("Batch linear static", solve_linear_static_many)])
def test_follower_pressure_immutable_job_fails_without_published_results(name, solver):
    from anyfem.application.workflow import _execute_analysis_job
    from anyfem.document import DocumentSession
    from anyfem.jobs import JobManager
    from anyfem.model import JobStatus
    project, mesh = follower_plate(batch=solver is solve_linear_static_many)
    manager = JobManager(project, executor="thread")
    analysis = AnalysisDefinition("Follower refusal", type=name, settings={
        "constraint_mode": "nullspace", "allow_unbalanced_free_free": True})
    record = manager.submit(analysis, DocumentSession(project).snapshot(),
        _execute_analysis_job, kwargs={"solver_function": solver, "analysis_name": name,
            "options": {"mesh": mesh, **analysis.settings}})
    manager.wait(record.id, timeout=15)
    events = manager.poll()
    assert record.status is JobStatus.FAILED
    assert any("LOAD001" in str(event.message) for event in events)
    with pytest.raises(KeyError):
        manager.result(record.id)
