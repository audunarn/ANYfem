"""Mesher execution measurements cannot change solver-affecting hashes."""
from copy import deepcopy

from anyfem.mesh_jobs import MeshSettings, mesh_semantic_hash, run_mesh_job
from anyfem.mesh_execution import MeshExecutionPolicy
from anyfem.model.project import Project
from anyfem.jobs import _cancellation_token
from anymesher.mesh import Mesh


def test_mesh_hash_excludes_runtime_but_retains_quality_and_coordinates():
    mesh = Mesh()
    mesh.nodes.update({1: (0., 0., 0.), 2: (1., 0., 0.), 3: (0., 1., 0.)})
    mesh.tris[1] = (1, 2, 3)
    mesh.hybrid_diagnostics = {"accepted_quality": {"accepted": True}}

    def digest():
        return mesh_semantic_hash(mesh, model_hash="model", mesh_input_hash="settings")

    reference = digest()
    mesh.hybrid_diagnostics["runtime"] = {
        "candidate_count": 4, "connectivity_application_count": 1,
        "phase_seconds": {"connectivity": 0.75},
    }
    assert digest() == reference
    mesh.hybrid_diagnostics["runtime"]["candidate_count"] = 1
    assert digest() == reference
    mesh.hybrid_diagnostics["accepted_quality"]["accepted"] = False
    assert digest() != reference
    mesh.hybrid_diagnostics["accepted_quality"]["accepted"] = True
    mesh.nodes[2] = (2., 0., 0.)
    assert digest() != reference


def test_job_copies_owner_runtime_into_execution(monkeypatch):
    project = Project("runtime-transfer")
    vertices = project.geometry.add_points(((0., 0., 0.), (1., 0., 0.), (0., 1., 0.)))
    project.geometry.add_plate(vertices)
    mesh = Mesh()
    mesh.nodes.update({1: (0., 0., 0.), 2: (1., 0., 0.), 3: (0., 1., 0.)})
    mesh.tris[1] = (1, 2, 3)
    runtime = {"candidate_count": 4, "connectivity_application_count": 1,
               "phase_seconds": {"connectivity": 0.75}}
    mesh.hybrid_diagnostics = {"runtime": runtime}
    project._last_mesh_execution = {"route": "serial", "reason": "1 component(s)"}
    project._last_mesh_preparation = {}
    monkeypatch.setattr(project, "generate_mesh", lambda *args, **kwargs: mesh)
    result = run_mesh_job(
        project, "model", MeshSettings.create(.5, element_order="linear", execution_policy=MeshExecutionPolicy.off()),
        lambda *args: None, _cancellation_token(),
    )
    expected = deepcopy(runtime)
    assert result.execution["meshing"] == expected
    runtime["phase_seconds"]["connectivity"] = 99.
    assert result.execution["meshing"] == expected


def test_project_retains_owner_runtime_after_source_remapping(monkeypatch):
    from anyfem.model import project as project_module

    project = Project("project-runtime")
    vertices = project.geometry.add_points(((0., 0., 0.), (1., 0., 0.), (1., 1., 0.), (0., 1., 0.)))
    project.geometry.add_plate(vertices)
    original = project_module.generate_hybrid_mesh
    runtime = {"candidate_count": 1, "connectivity_application_count": 1,
               "phase_seconds": {"connectivity": .25}}

    def measured(*args, **kwargs):
        mesh = original(*args, **kwargs)
        mesh.hybrid_diagnostics["runtime"] = deepcopy(runtime)
        return mesh

    monkeypatch.setattr(project_module, "generate_hybrid_mesh", measured)
    mesh = project.generate_mesh(.5, order="linear", execution_policy=MeshExecutionPolicy.off())
    assert project._last_mesh_execution["meshing"] == runtime
    mesh.hybrid_diagnostics["runtime"]["phase_seconds"]["connectivity"] = 99.
    assert project._last_mesh_execution["meshing"] == runtime
