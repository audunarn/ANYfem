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
        "operation_counts": {"geometry.attachment_bounds_pruned": 37},
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
    from contextlib import nullcontext
    from anyfem import _meshing_runtime
    from anyfem.model import project as project_module

    monkeypatch.setattr(_meshing_runtime, "collect_geometry_runtime", lambda: nullcontext({}))

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


def test_outer_geometry_counts_merge_without_changing_semantics():
    from anyfem._meshing_runtime import merge_geometry_runtime

    mesh = Mesh()
    original = {"operation_counts": {"geometry.attachment_clip_calls": 2,
                                      "mesher.bvh_builds": 1}}
    mesh.hybrid_diagnostics = {"runtime": original}
    before = mesh_semantic_hash(mesh, model_hash="model", mesh_input_hash="settings")
    merge_geometry_runtime(mesh, {"attachment_clip_calls": 3, "attachment_bounds_pruned": 4})
    assert mesh.hybrid_diagnostics["runtime"]["operation_counts"] == {
        "geometry.attachment_clip_calls": 5, "geometry.attachment_bounds_pruned": 4,
        "mesher.bvh_builds": 1}
    assert original["operation_counts"]["geometry.attachment_clip_calls"] == 2
    assert mesh_semantic_hash(mesh, model_hash="model", mesh_input_hash="settings") == before


def test_project_transfers_outer_preparation_counters(monkeypatch):
    from contextlib import contextmanager
    from anyfem import _meshing_runtime

    @contextmanager
    def captured():
        yield {"attachment_clip_calls": 17, "attachment_bounds_pruned": 23}

    monkeypatch.setattr(_meshing_runtime, "collect_geometry_runtime", captured)
    project = Project("outer-runtime")
    vertices = project.geometry.add_points(((0., 0., 0.), (1., 0., 0.), (1., 1., 0.), (0., 1., 0.)))
    project.geometry.add_plate(vertices)
    mesh = project.generate_mesh(.5, order="linear", execution_policy=MeshExecutionPolicy.off())
    counts = mesh.hybrid_diagnostics["runtime"]["operation_counts"]
    assert counts["geometry.attachment_clip_calls"] == 17
    assert counts["geometry.attachment_bounds_pruned"] == 23
    assert project._last_mesh_execution["meshing"]["operation_counts"] == counts
    assert "operation_counts" not in project._last_mesh_preparation


def test_geometry_collection_supports_absent_optional_module(monkeypatch):
    import builtins
    from anyfem._meshing_runtime import collect_geometry_runtime

    original_import = builtins.__import__
    def missing(name, *args, **kwargs):
        if name == "anygeometry.runtime_diagnostics":
            raise ModuleNotFoundError("older geometry", name=name)
        return original_import(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", missing)
    with collect_geometry_runtime() as counts:
        assert counts == {}


def test_geometry_collection_does_not_hide_broken_owner_import(monkeypatch):
    import builtins
    import pytest
    from anyfem._meshing_runtime import collect_geometry_runtime

    original_import = builtins.__import__
    def broken(name, *args, **kwargs):
        if name == "anygeometry.runtime_diagnostics":
            raise ModuleNotFoundError("broken dependency", name="owner_dependency")
        return original_import(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", broken)
    with pytest.raises(ModuleNotFoundError, match="broken dependency"):
        with collect_geometry_runtime():
            pass
