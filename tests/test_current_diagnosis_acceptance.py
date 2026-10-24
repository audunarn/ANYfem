"""Independent acceptance of the current-state support snapshot."""
import json
from collections.abc import Mapping
from types import SimpleNamespace
import numpy as np

from anyfem import Project
from anyfem.diagnostics import build_diagnostic_report
from anyfem.model.records import AnalysisDefinition, JobRecord, JobStatus, MeshRecord
from anyfem.model.sections import PlateSection
from anyfem.native_meshing import NativeMeshSettings


def report(project, **kwargs):
    return json.loads(build_diagnostic_report(
        project, version_reader=lambda _: "test", origin_reader=lambda _: "test", **kwargs))


def test_current_inputs_and_failed_output_are_reported_without_mutation():
    project = Project("Support snapshot acceptance")
    vertices = [project.geometry.add_point(x, y, 0) for x, y in ((0, 0), (2, 0), (2, 1), (0, 1))]
    project.geometry.add_plate(vertices)
    project.plate_sections["Skin 12mm"] = PlateSection("Skin 12mm", .012, "Steel")
    project.native_mesh_settings = NativeMeshSettings.create(.125, backend="automatic")
    analysis = AnalysisDefinition("Service pressure", settings={"max_iterations": 17})
    project.analyses[analysis.id] = analysis
    mesh = MeshRecord("Panel mesh", "old-model", "mesh-input", "mesh-hash", summary={"nodes": 42})
    project.mesh_records[mesh.id] = mesh
    job = JobRecord(analysis.id, name="Failed attempt", status=JobStatus.FAILED,
                    outcome={"reason": "singular stiffness"}, diagnostics=[{"message": "unrestrained body"}])
    project.jobs[job.id] = job
    before = (len(project.geometry.vertices), len(project.geometry.faces), tuple(project.jobs), analysis.settings.copy())
    payload = report(project)
    state = payload["current_state"]
    assert {"geometry_input", "meshing", "solver_input", "solver_output"} <= state.keys()
    assert "Skin 12mm" in json.dumps(state["geometry_input"])
    assert "0.125" in json.dumps(state["meshing"])
    assert "Service pressure" in json.dumps(state["solver_input"])
    assert "max_iterations" in json.dumps(state["solver_input"])
    assert "singular stiffness" in json.dumps(state["solver_output"])
    assert "failed" in json.dumps(state["solver_output"]).lower()
    assert before == (len(project.geometry.vertices), len(project.geometry.faces), tuple(project.jobs), analysis.settings)


def test_empty_project_diagnosis_has_explicit_state_and_no_fabricated_success():
    payload = report(Project("Empty snapshot"))
    assert payload["report"] == "ANYfem diagnostic"
    state = payload["current_state"]
    assert all(isinstance(state[key], dict) and state[key] for key in
               ("geometry_input", "meshing", "solver_input", "solver_output"))
    assert "completed" not in json.dumps(state["solver_output"]).lower()


def test_large_registries_and_loaded_arrays_are_sampled_before_traversal():
    class VirtualMeshes(Mapping):
        read = 0
        def __len__(self): return 1_000_000
        def __iter__(self): return iter(range(1_000_000))
        def __getitem__(self, key):
            self.read += 1
            assert self.read <= 20, "diagnosis traversed the entire registry"
            return MeshRecord("Sample mesh", "old-model", "input", "mesh")
    project = Project()
    project.mesh_records = VirtualMeshes()
    mesh = SimpleNamespace(num_nodes=1_000_000, num_elements=2_000_000)
    # Broadcast storage is tiny; exposing this result must never expand it.
    solution = SimpleNamespace(U=np.broadcast_to(np.array(7.), (1_000_000, 6)))
    payload = report(project, loaded_mesh=mesh, solution=solution)
    table = payload["current_state"]["meshing"]["records"]
    assert table["total"] == 1_000_000 and table["included"] == 20 and table["truncated"]
    assert project.mesh_records.read == 20
    assert payload["current_state"]["meshing"]["loaded_mesh"]["counts"]["num_nodes"] == 1_000_000
    assert payload["current_state"]["solver_output"]["loaded_result"]["arrays"]["U"]["shape"] == [1_000_000, 6]
    assert len(json.dumps(payload)) < 100_000


def test_one_broken_optional_registry_does_not_discard_other_sections():
    class BrokenMeshes(dict):
        def __len__(self): raise RuntimeError("mesh metadata unavailable")
    project = Project()
    project.mesh_records = BrokenMeshes()
    payload = report(project)
    assert payload["current_state"]["meshing"]["status"] == "unavailable"
    assert payload["current_state"]["solver_input"]["status"] == "configuration_only"
    assert payload["current_state"]["geometry_input"]["counts"]["points"] == 0


def test_real_loaded_mesh_and_result_keep_available_diagnostics_without_recovery():
    from anyfem.mesh.mapped import Mesh
    from anyfem.post.results import LinearSolution
    mesh = Mesh(hybrid_diagnostics={"quality": {"minimum_angle": 31}},
                structural_preparation={"coupled_beams": 4})
    solution = LinearSolution(np.zeros(6), None, info={"converged": True, "iterations": 3})
    payload = report(Project(), loaded_mesh=mesh, solution=solution)
    state = payload["current_state"]
    assert "minimum_angle" in json.dumps(state["meshing"])
    assert "coupled_beams" in json.dumps(state["meshing"])
    assert state["solver_output"]["loaded_result"]["metadata"]["info"]["iterations"] == 3
    assert solution._stress is None


def test_diagnosis_never_deepcopies_even_one_large_feature(monkeypatch):
    class ExpensiveHistory:
        def __len__(self): return 1
        @property
        def records(self):
            raise AssertionError("Public records deep-copies an arbitrary materialization")
    project = Project()
    monkeypatch.setattr(type(project.geometry), "features", property(lambda _: ExpensiveHistory()))
    features = report(project)["current_state"]["geometry_input"]["features"]
    assert features["total"] == 1 and features["included"] == 0
    assert features["status"] == "details_omitted"


def test_stale_records_are_distinguished_from_successful_current_results():
    project = Project()
    old = MeshRecord("Stale mesh", "old", "input", "mesh")
    project.mesh_records[old.id] = old
    analysis = AnalysisDefinition("Check")
    project.analyses[analysis.id] = analysis
    job = JobRecord(analysis.id, model_hash="current", status=JobStatus.COMPLETED)
    project.jobs[job.id] = job
    state = report(project, context={"revision": {"model_hash": "current"}})["current_state"]
    assert state["meshing"]["records"]["items"][0]["model_state"] == "stale"
    output = state["solver_output"]
    assert output["status"] == "recorded_not_loaded"
    assert output["loaded_result"]["status"] == "not_loaded"
    assert output["jobs"]["items"][0]["model_state"] == "matches_current_model"
