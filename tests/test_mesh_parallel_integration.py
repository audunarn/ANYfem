"""Real public ANYmesh API through ANYfem (explicit installed-candidate gate)."""
import os

import pytest

if os.environ.get("ANYFEM_RUN_PARALLEL_MESH_TESTS") != "1":
    pytest.skip("set ANYFEM_RUN_PARALLEL_MESH_TESTS=1 for real parallel meshing", allow_module_level=True)

import anymesher
from anyfem.document import DocumentSession
from anyfem.io.project_file import load_project, save_project
from anyfem.jobs import _cancellation_token
from anyfem.mesh_execution import MeshExecutionPolicy, parallel_mesh_api
from anyfem.mesh_jobs import MeshSettings, run_mesh_job
from anymesher.refinement import Refinement
from anymesher.seeding import solve_seeding
from parallel_mesh_contract import mesh_signature, separated_plates


@pytest.fixture(scope="module")
def lease():
    assert parallel_mesh_api() is not None, "acceptance requires the public parallel API"
    with anymesher.create_parallel_pool(4) as pool:
        yield pool


@pytest.mark.parametrize("variant", ["plates", "beams", "quadratic", "refinements", "seeding"])
def test_real_project_route_preserves_mesh_semantics(lease, variant, tmp_path):
    project = separated_plates(beams=variant == "beams")
    options = {"order": "quadratic"} if variant == "quadratic" else {}
    if variant == "refinements":
        options["refinements"] = [Refinement(size=0.25, center=(1, 0.5, 0), radius=0.4)]
    if variant == "seeding":
        options["seeding"] = solve_seeding(project.geometry, target_size=0.4)
    serial = project.generate_mesh(0.4, strategy="native", execution_policy=MeshExecutionPolicy.off(), **options)
    routed = project.generate_mesh(0.4, strategy="native", execution_policy=MeshExecutionPolicy.explicit(4),
                                   pool_lease=lease, **options)
    assert routed.hybrid_diagnostics["parallel"]["used"], project._last_mesh_execution
    assert mesh_signature(routed) == mesh_signature(serial)
    if variant == "beams":
        assert routed.beams and routed.couplings
    # Same public project codec used by Qt; no execution policy enters project input.
    path = save_project(project, tmp_path / f"{variant}.anyfem")
    reopened = load_project(path)
    assert reopened.geometry.model_id == project.geometry.model_id
    assert reopened.beam_offsets == project.beam_offsets
    assert "execution_policy" not in path.read_text(encoding="utf-8")


def test_real_worker_counts_keep_job_mesh_hash_and_binding():
    project = separated_plates(16)
    session = DocumentSession(project)
    snapshot = session.snapshot()
    hashes = set()
    signatures = []
    for workers in (4, 8, 16):
        settings = MeshSettings.create(0.5, element_order="linear", strategy="native",
                                      execution_policy=MeshExecutionPolicy.explicit(workers))
        result = run_mesh_job(snapshot.thaw(), snapshot.revision.model_hash, settings,
                              lambda *_: None, _cancellation_token())
        assert result.mesh.hybrid_diagnostics["parallel"]["used"]
        hashes.add(result.mesh_hash)
        signatures.append(mesh_signature(result.mesh))
    assert len(hashes) == 1
    assert signatures[0] == signatures[1] == signatures[2]


def test_parallel_failure_is_not_retried_or_published(monkeypatch):
    project = separated_plates()
    failure = anymesher.errors.MeshError("injected worker failure")
    calls = []

    def fail(model, *, parallel=None, **options):
        calls.append(1)
        raise failure

    monkeypatch.setattr(anymesher, "generate_hybrid_mesh_parallel", fail)
    with pytest.raises(anymesher.errors.MeshError) as error:
        project.generate_mesh(0.4, strategy="native", execution_policy=MeshExecutionPolicy.explicit(4))
    assert error.value is failure
    assert calls == [1]
    assert not project.mesh_records


def test_owner_serial_refusal_records_the_effective_route():
    project = separated_plates(1)
    mesh = project.generate_mesh(0.4, strategy="native", execution_policy=MeshExecutionPolicy.explicit(4))
    assert not mesh.hybrid_diagnostics["parallel"]["used"]
    assert project._last_mesh_execution["route"] == "serial"
    assert project._last_mesh_execution["workers"] is None
    assert "component" in project._last_mesh_execution["reason"]
