"""Submodels driven from completed jobs: persistence, revalidated source, staged
runs with compare-and-publish, staleness (workflow contract, section 11)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading

import numpy as np
import pytest

from anyfem.document import DocumentSession, _document_payload, canonical_hash
from anyfem.io.artifacts import ArtifactStore
from anyfem.io.project_file import (
    ProjectFileError,
    load_project,
    project_from_dict,
    project_to_dict,
    save_project,
)
from anyfem.io.result_artifact import write_solution_artifact
from anyfem.jobs import analysis_hash
from anyfem.mesh_jobs import mesh_semantic_hash
from anyfem.model.materials import Material
from anyfem.model.records import AnalysisDefinition, JobRecord, JobStatus, MeshRecord
from anyfem.model.submodels import SourceReference, SubmodelDefinition, SubmodelRun
from anyfem.post.results import LinearSolution
from anyfem.solve.run import solve_linear_static
from anyfem.submodel import SubmodelError, SubmodelLineage, SubmodelSettings, bind_source, solve_submodel
import anyfem.submodel_jobs as jobs_module
from anyfem.submodel_jobs import (
    add_submodel,
    discard_staged_run,
    job_source,
    publish_submodel_run,
    run_submodel,
    stage_submodel_run,
    submodel_orphans,
    submodel_run_staleness,
    submodel_snapshot,
)
from test_submodel_workflow import BOX, PRESSURE, SIZE, global_project, inside_model, sides


# ----------------------------------------------------------------------
# a job made the way the workbench makes one
# ----------------------------------------------------------------------
def make_job(session, store, *, case="default", settings=None, mesh=None, analysis_overrides=None):
    project = session.project
    mesh = project.generate_mesh(SIZE) if mesh is None else mesh
    model_hash = session.revision.model_hash
    preparation = dict(getattr(project, "_last_mesh_preparation", {}) or {})
    mesh_hash = mesh_semantic_hash(mesh, model_hash=model_hash, mesh_input_hash="sha256:test-mesh-input",
                                   structural_preparation=preparation)
    record = MeshRecord(name="Mesh", source_model_hash=model_hash, mesh_input_hash="sha256:test-mesh-input",
                        mesh_hash=mesh_hash, structural_preparation=preparation)
    mesh_ref = store.write_mesh(mesh, mesh_id=record.id, document_id=project.document_id,
                                model_hash=model_hash, mesh_hash=mesh_hash)
    record.artifact_id = mesh_ref.id
    analysis = AnalysisDefinition(name="Linear static", target_id=case,
                                  settings={"load_case": case} if settings is None else settings)
    for key, value in (analysis_overrides or {}).items():
        setattr(analysis, key, value)  # recorded as such, past the constructor's own checks
    with session.transaction("analysis and mesh", solver_affecting=False):
        project.analyses[analysis.id] = analysis
        project.mesh_records[record.id] = record
        project.artifacts[mesh_ref.id] = mesh_ref
    document = session.snapshot().document
    submitted = analysis_hash(analysis, project.output_requests, document=document)
    solution = solve_linear_static(project, mesh=mesh, load_case=case)
    job = JobRecord(analysis_id=analysis.id, name="job", model_hash=model_hash, mesh_hash=mesh_hash,
                    analysis_hash=submitted, input_hash="sha256:test-input", status=JobStatus.COMPLETED)
    result_ref = write_solution_artifact(store, solution, job_id=job.id, document_id=project.document_id,
                                         mesh_id=record.id, model_hash=model_hash, mesh_hash=mesh_hash,
                                         analysis_hash=submitted)
    job.result_artifact_id = result_ref.id
    with session.transaction("job", solver_affecting=False):
        project.jobs[job.id] = job
        project.artifacts[result_ref.id] = result_ref
    return job, record, mesh, solution


def child_definition(gfaces, job, *, case="default", child_case="default", policy="drive_all", child=None):
    if child is None:
        child, faces, _mesh = inside_model()
    else:
        child, faces = child
    return SubmodelDefinition(
        name="box", volume={"kind": "box", "lower": [0.3, 0.3, -0.1], "upper": [0.7, 0.7, 0.1], "transform": None},
        target_size=SIZE, policy=policy, source=SourceReference(job.id, case), child_case=child_case, child=child,
        source_faces={faces[0, 0]: (gfaces[1, 1],)}, cut_faces=sides(child, faces.values(), BOX),
    )


class Setup:
    def __init__(self, tmp_path, *, mesh=None):
        self.project, self.gfaces = global_project()
        self.session = DocumentSession(self.project)
        self.store = ArtifactStore(tmp_path / "parent.anyfem")
        self.path = tmp_path / "parent.anyfem"
        self.job, self.record, self.mesh, self.solution = make_job(self.session, self.store, mesh=mesh)
        self.definition = child_definition(self.gfaces, self.job)
        with self.session.transaction("add submodel", solver_affecting=False):
            add_submodel(self.project, self.definition)

    def snapshot(self):
        return self.session.snapshot()

    def source(self, reference=None, **kwargs):
        snap = self.snapshot()
        return job_source(snap.thaw(), reference or self.definition.source, self.store, document=snap.document,
                          model_hash=snap.revision.model_hash, **kwargs)

    def edit(self, change, *, solver_affecting=False):
        with self.session.transaction("edit", solver_affecting=solver_affecting):
            change(self.project)

    def run(self):
        return run_submodel(self.session, self.definition.id, self.store).run

    def staleness(self, run, **kwargs):
        return submodel_run_staleness(self.snapshot(), run, self.store, **kwargs)


@pytest.fixture
def setup(tmp_path):
    return Setup(tmp_path)


def _files(store):
    return sorted(p.name for folder in ("meshes", "results") if (store.root / folder).is_dir()
                  for p in (store.root / folder).iterdir())


def _document_hash(project):
    return canonical_hash(_document_payload(project_to_dict(project)))


# ----------------------------------------------------------------------
# end to end
# ----------------------------------------------------------------------
def test_run_from_a_job_reproduces_the_in_memory_result(setup):
    published = run_submodel(setup.session, setup.definition.id, setup.store)
    run = published.run
    assert published.notification_error is None
    assert run.id in setup.project.submodel_runs
    assert {run.child["mesh_artifact"]["id"], run.child["result_artifact"]["id"]} <= set(setup.project.artifacts)
    assert run.source["mesh_selection"] == "job_historical"
    assert run.source["revalidation"]["eta_scaled"] <= 1e-10
    assert setup.staleness(run) == ()
    in_memory = solve_submodel(
        setup.solution, bind_source(setup.solution), setup.definition.child,
        setup.definition.child.generate_mesh(SIZE),
        SubmodelLineage(setup.definition.source_faces, setup.definition.cut_faces), SubmodelSettings(policy="drive_all"),
    )
    staged = stage_submodel_run(submodel_snapshot(setup.session), setup.definition.id, setup.store)
    try:
        assert np.array_equal(np.asarray(staged.result.solution.displacements),
                              np.asarray(in_memory.solution.displacements))
        assert staged.run.identity == in_memory.identity
    finally:
        discard_staged_run(staged)


def test_nondefault_source_and_child_cases_are_the_ones_solved(tmp_path):
    project, gfaces = global_project()
    alt = project.load_case("alt")
    for face in gfaces.values():
        alt.add_pressure(project.face(face), 3.0 * PRESSURE)
    session = DocumentSession(project)
    store = ArtifactStore(tmp_path / "parent.anyfem")
    job, _record, mesh, solution = make_job(session, store, case="alt")
    child, faces, _mesh = inside_model()
    child_alt = child.load_case("child_alt")
    child_alt.add_pressure(child.face(faces[0, 0]), 3.0 * PRESSURE)
    definition = child_definition(gfaces, job, case="alt", child_case="child_alt", child=(child, faces))
    with session.transaction("add", solver_affecting=False):
        add_submodel(project, definition)
    staged = stage_submodel_run(submodel_snapshot(session), definition.id, store)
    try:
        lineage = SubmodelLineage(definition.source_faces, definition.cut_faces)
        expected = solve_submodel(solution, bind_source(solution), definition.child, definition.child.generate_mesh(SIZE),
                                  lineage, SubmodelSettings(policy="drive_all"), load_case="child_alt")
        assert np.array_equal(np.asarray(staged.result.solution.displacements),
                              np.asarray(expected.solution.displacements))
        other = solve_submodel(solution, bind_source(solution), definition.child, definition.child.generate_mesh(SIZE),
                               lineage, SubmodelSettings(policy="drive_all"), load_case="default")
        assert not np.allclose(np.asarray(other.solution.displacements), np.asarray(expected.solution.displacements))
        assert staged.run.source["case"] == "alt" and staged.run.child_case == "child_alt"
    finally:
        discard_staged_run(staged)


# ----------------------------------------------------------------------
# persistence
# ----------------------------------------------------------------------
def test_format_12_round_trip_reopens_in_a_fresh_process(setup):
    run = setup.run()
    document = project_to_dict(setup.project)
    assert document["anyfem"]["format"] == 12
    path = save_project(setup.project, setup.path)
    first = path.read_bytes()
    reopened = load_project(path)
    assert reopened.submodel_runs[run.id].to_dict() == run.to_dict()
    assert reopened.submodels[setup.definition.id].to_dict() == setup.definition.to_dict()
    save_project(reopened, path)
    assert path.read_bytes() == first  # byte-stable re-save
    code = (
        "import sys, json\n"
        "from anyfem.io.project_file import load_project\n"
        "from anyfem.io.artifacts import ArtifactStore\n"
        "from anyfem.document import DocumentSession\n"
        "from anyfem.submodel_jobs import submodel_run_staleness\n"
        "p = load_project(sys.argv[1]); store = ArtifactStore(sys.argv[1])\n"
        "ok = all(store.verify(a) for a in p.artifacts.values())\n"
        "s = DocumentSession(p)\n"
        "print(json.dumps({'ok': ok, 'reasons': [list(submodel_run_staleness(s.snapshot(), r, store)) for r in p.submodel_runs.values()]}))\n"
    )
    env = dict(os.environ, PYTHONPATH=os.pathsep.join(p for p in sys.path if p))
    output = subprocess.run([sys.executable, "-c", code, str(path)], capture_output=True, text=True, env=env, check=True)
    assert json.loads(output.stdout.strip().splitlines()[-1]) == {"ok": True, "reasons": [[]]}


def test_format_is_promoted_only_when_submodels_are_used(setup):
    plain, _faces = global_project()
    assert project_to_dict(plain)["anyfem"]["format"] < 12
    assert project_from_dict(project_to_dict(plain)).submodels == {}
    run = setup.run()
    setup.edit(lambda p: p.submodels.clear())  # runs only
    assert project_to_dict(setup.project)["anyfem"]["format"] == 12
    assert run.id in project_from_dict(project_to_dict(setup.project)).submodel_runs
    setup.edit(lambda p: (p.submodel_runs.clear(), p.submodels.__setitem__(setup.definition.id, setup.definition)))
    assert project_to_dict(setup.project)["anyfem"]["format"] == 12  # definitions only
    older = project_to_dict(plain)
    older["submodels"] = []
    with pytest.raises(ProjectFileError, match="cannot hold submodels"):
        project_from_dict(older)


def test_malformed_duplicate_and_non_detached_entries_refuse(setup):
    run = setup.run()
    document = project_to_dict(setup.project)
    broken = json.loads(json.dumps(document))
    broken["submodel_runs"][0]["child"]["result_artifact"]["sha256"] = "abc"
    with pytest.raises(ProjectFileError, match="sha256"):
        project_from_dict(broken)
    duplicate = json.loads(json.dumps(document))
    duplicate["submodel_runs"].append(duplicate["submodel_runs"][0])
    with pytest.raises(ProjectFileError, match="duplicate id"):
        project_from_dict(duplicate)
    attached = json.loads(json.dumps(document))
    attached["submodels"][0]["child"]["jobs"] = document["jobs"]
    with pytest.raises(ProjectFileError, match="not detached"):
        project_from_dict(attached)
    child, faces, _mesh = inside_model()
    child.jobs[setup.job.id] = setup.job
    with pytest.raises(SubmodelError, match="not detached"):
        add_submodel(setup.project, child_definition(setup.gfaces, setup.job, child=(child, faces)))
    assert run.id in project_from_dict(document).submodel_runs


def test_submodels_do_not_change_parent_hashes(tmp_path):
    project, gfaces = global_project()
    session = DocumentSession(project)
    store = ArtifactStore(tmp_path / "parent.anyfem")
    job, *_ = make_job(session, store)
    model_before = session.revision.model_hash
    analysis = project.analyses[job.analysis_id]
    analysis_before = analysis_hash(analysis, project.output_requests, document=session.snapshot().document)
    with session.transaction("add", solver_affecting=True):
        add_submodel(project, child_definition(gfaces, job))
    assert session.revision.model_hash == model_before
    assert analysis_hash(analysis, project.output_requests, document=session.snapshot().document) == analysis_before


def test_deleted_definition_and_unregistered_artifact_survive_save_and_reopen(setup):
    run = setup.run()
    setup.edit(lambda p: (p.submodels.clear(), p.artifacts.pop(run.child["result_artifact"]["id"])))
    path = save_project(setup.project, setup.path)
    reopened = load_project(path)
    assert reopened.submodel_runs[run.id].to_dict() == run.to_dict()
    reasons = submodel_run_staleness(DocumentSession(reopened).snapshot(), reopened.submodel_runs[run.id], setup.store)
    assert reasons == ("DEFINITION_MISSING", "RESULT_ARTIFACT_CHANGED")


# ----------------------------------------------------------------------
# job-bound source refusals (contract 11.3)
# ----------------------------------------------------------------------
@pytest.mark.parametrize("change, code", [
    (lambda s: setattr(s.job, "status", JobStatus.FAILED), "SOURCE_JOB"),
    (lambda s: setattr(s.job, "partial", True), "SOURCE_JOB"),
    (lambda s: s.project.analyses.clear(), "SOURCE_JOB"),
    (lambda s: s.project.load_case().add_pressure(s.project.face(s.gfaces[0, 0]), 1.0), "SOURCE_LOADING_CHANGED"),
    (lambda s: s.project.materials.__setitem__("S355", Material("S355", elastic_modulus=2.3e11, poisson_ratio=0.3)),
     "SOURCE_MODEL_CHANGED"),
    (lambda s: setattr(s.record, "status", "failed"), "SOURCE_MESH"),
    (lambda s: s.project.mesh_records.__setitem__("twin", MeshRecord(
        name="twin", source_model_hash=s.record.source_model_hash, mesh_input_hash="x", mesh_hash=s.record.mesh_hash,
        id="twin")), "SOURCE_MESH"),
    (lambda s: s.project.artifacts.pop(s.record.artifact_id), "SOURCE_ARTIFACT"),
])
def test_job_source_refusals(setup, change, code):
    setup.edit(lambda _p: change(setup), solver_affecting=True)
    with pytest.raises(SubmodelError, match=code):
        setup.source()


def test_wrong_case_and_foreign_mesh_selection_refuse(setup):
    with pytest.raises(SubmodelError, match="SOURCE_RECIPE_UNSUPPORTED"):
        setup.source(SourceReference(setup.job.id, "other"))
    other = MeshRecord(name="other", source_model_hash=setup.record.source_model_hash, mesh_input_hash="x",
                       mesh_hash="sha256:other", id="other")
    setup.edit(lambda p: p.mesh_records.__setitem__("other", other))
    with pytest.raises(SubmodelError, match="SOURCE_MESH"):
        setup.source(mesh_record_id="other")
    assert setup.source(mesh_record_id=setup.record.id).manifest["mesh_selection"] == "explicit"


@pytest.mark.parametrize("settings, overrides", [
    ({"load_case": "default", "solver": "pardiso"}, None),  # an extra recorded setting
    ({"load_case": "alt"}, None),  # a conflicting selector; the archived field would revalidate
    ({"combination": "c1"}, None),
])
def test_recipe_is_checked_independently_of_the_numerical_gate(tmp_path, settings, overrides):
    project, gfaces = global_project()
    project.load_case("alt")
    session = DocumentSession(project)
    store = ArtifactStore(tmp_path / "parent.anyfem")
    job, *_ = make_job(session, store, settings=settings, analysis_overrides=overrides)
    snap = session.snapshot()
    with pytest.raises(SubmodelError, match="SOURCE_RECIPE_UNSUPPORTED"):
        job_source(snap.thaw(), SourceReference(job.id, "default"), store, document=snap.document,
                   model_hash=snap.revision.model_hash)


def test_recipe_refuses_non_default_beam_formulation():
    """ANYfem's own record checks tie a non-default formulation to its own analysis
    type (refused above); the recipe guard also refuses it directly."""

    from types import SimpleNamespace

    from anyfem.submodel_jobs import _require_recipe

    base = dict(type="linear_static", target_kind="load_case", target_id="default", settings={},
                beam_formulation="legacy-b3", beam_formulation_options={})
    _require_recipe(SimpleNamespace(**base), SourceReference("job", "default"))
    for change in ({"beam_formulation": "b3-ge"}, {"beam_formulation_options": {"stations": 3}},
                   {"type": "b3_ge_native"}):
        with pytest.raises(SubmodelError, match="SOURCE_RECIPE_UNSUPPORTED"):
            _require_recipe(SimpleNamespace(**{**base, **change}), SourceReference("job", "default"))


def _rewrite_result(setup, *, displacements=None, mesh_id=None, document_id=None, fields=None, tables=None):
    """Replace the source result with a new, checksum-valid artifact (registry updated)."""

    project = setup.project
    job = setup.job
    if fields is None:
        solution = LinearSolution(displacements=displacements, built=setup.solution.built, info=setup.solution.info)
        ref = write_solution_artifact(setup.store, solution, job_id=job.id,
                                      document_id=document_id or project.document_id,
                                      mesh_id=mesh_id or setup.record.id, model_hash=job.model_hash,
                                      mesh_hash=job.mesh_hash, analysis_hash=job.analysis_hash)
    else:
        ref = setup.store.write_result(job_id=job.id, document_id=project.document_id, mesh_id=setup.record.id,
                                       model_hash=job.model_hash, mesh_hash=job.mesh_hash,
                                       analysis_hash=job.analysis_hash, fields=fields, frames=(0.0,),
                                       tables=tables or {})
    setup.edit(lambda p: p.artifacts.__setitem__(ref.id, ref))
    return ref


def test_numerical_gate_refuses_a_checksum_valid_perturbed_field(setup):
    u = np.asarray(setup.solution.displacements, dtype=float).copy()
    free = int(np.argmax(np.abs(u)))
    u[free] *= 1.0 + 1.0e-6
    _rewrite_result(setup, displacements=u)
    with pytest.raises(SubmodelError, match="SOURCE_REVALIDATION_FAILED"):
        setup.source()


def test_numerical_gate_refuses_a_violated_support(setup):
    model = setup.solution.built.fe_model
    bc = model.boundary_conditions[0]
    node = int(bc.node_ids[0])
    name = sorted(bc.dof_constraints)[0]
    dof = int(model.mesh.dof_manager.get_node_dofs(node)[["ux", "uy", "uz", "rx", "ry", "rz"].index(name)])
    u = np.asarray(setup.solution.displacements, dtype=float).copy()
    u[dof] += 1.0e-6
    _rewrite_result(setup, displacements=u)
    with pytest.raises(SubmodelError, match="SOURCE_REVALIDATION_FAILED"):
        setup.source()


def test_numerical_gate_refuses_a_violated_coupling(tmp_path):
    from anymesher import Coupling
    from anysolver.mesh_gen import InterpolatedBeamShellMPCElement

    project, gfaces = global_project()
    mesh = project.generate_mesh(SIZE)
    element = next(iter(mesh.elements_of_face[gfaces[1, 1]]))
    plate = tuple(int(n) for n in mesh.shells[element])
    beam = max(mesh.nodes) + 1
    mesh.nodes[beam] = np.mean([mesh.nodes[n] for n in plate], axis=0) + np.array([0.0, 0.0, 0.05])
    mesh.couplings[max(mesh.shells) + 1000] = Coupling(beam_node=beam, plate_nodes=plate, weights=(0.25,) * 4,
                                                       eccentricity=(0.0, 0.0, 0.05))
    setup = Setup.__new__(Setup)
    setup.project, setup.gfaces = project, gfaces
    setup.session = DocumentSession(project)
    setup.store = ArtifactStore(tmp_path / "parent.anyfem")
    setup.job, setup.record, setup.mesh, setup.solution = make_job(setup.session, setup.store, mesh=mesh)
    setup.definition = child_definition(gfaces, setup.job)
    assert setup.source().manifest["revalidation"]["eta_scaled"] <= 1e-10  # the coupled source revalidates
    model = setup.solution.built.fe_model
    assert any(isinstance(e, InterpolatedBeamShellMPCElement) for e in model.mesh.elements.values())
    u = np.asarray(setup.solution.displacements, dtype=float).copy()
    u[int(model.mesh.dof_manager.get_node_dofs(beam)[2])] += 1.0e-6  # the coupled slave
    _rewrite_result(setup, displacements=u)
    with pytest.raises(SubmodelError, match="SOURCE_REVALIDATION_FAILED"):
        setup.source()


def test_foreign_or_tampered_result_artifacts_refuse(setup):
    path = setup.store.resolve(setup.project.artifacts[setup.job.result_artifact_id].uri)
    original = path.read_bytes()
    path.write_bytes(original[:-1] + bytes([original[-1] ^ 1]))  # registry not updated
    with pytest.raises(SubmodelError, match="SOURCE_ARTIFACT"):
        setup.source()
    path.write_bytes(original)
    _rewrite_result(setup, displacements=np.asarray(setup.solution.displacements), mesh_id="another-mesh")
    with pytest.raises(SubmodelError, match="SOURCE_ARTIFACT.*mesh_id"):
        setup.source()
    _rewrite_result(setup, displacements=np.asarray(setup.solution.displacements), document_id="another-document")
    with pytest.raises(SubmodelError, match="SOURCE_ARTIFACT.*document_id"):
        setup.source()


def _field_parts(setup):
    from anyfem.io.artifacts import ArtifactStore as _Store  # noqa: F401

    dataset = setup.store.open_result(setup.project.artifacts[setup.job.result_artifact_id])
    field = dataset.field("displacement")
    return field.descriptor, np.asarray(field.read()), np.asarray(dataset.table("displacement_node_ids"))


@pytest.mark.parametrize("variant", ["permuted_components", "duplicate_node", "missing_node", "extra_frame",
                                     "wrong_unit", "local_basis"])
def test_malformed_displacement_fields_refuse(setup, variant):
    from dataclasses import replace

    descriptor, values, nodes = _field_parts(setup)
    provenance = dict(descriptor.provenance)
    dofs = list(provenance["global_dof_indices"])
    if variant == "permuted_components":
        order = [1, 0, 2, 3, 4, 5]
        values = values[:, :, order]
        descriptor = replace(descriptor, components=tuple(descriptor.components[i] for i in order))
        dofs = [dofs[6 * (k // 6) + order[k % 6]] for k in range(len(dofs))]
    elif variant == "duplicate_node":
        nodes = nodes.copy()
        nodes[1] = nodes[0]
    elif variant == "missing_node":
        values, nodes, dofs = values[:, 1:, :], nodes[1:], dofs[6:]
    elif variant == "extra_frame":
        values = np.concatenate([values, values], axis=0)
    elif variant == "wrong_unit":
        descriptor = replace(descriptor, unit="m")
    else:
        descriptor = replace(descriptor, basis="local")
    descriptor = replace(descriptor, provenance={**provenance, "global_dof_indices": dofs})
    _rewrite_result(setup, fields={"displacement": (descriptor, values)},
                    tables={"displacement_node_ids": np.asarray(nodes, dtype=np.int64)})
    with pytest.raises(SubmodelError, match="SOURCE_FIELD|SOURCE_LAYOUT"):
        setup.source()


# ----------------------------------------------------------------------
# stage and publish (contract 11.4)
# ----------------------------------------------------------------------
def _with_unrelated_jobs(setup):
    queued = JobRecord(analysis_id="other", name="queued", status=JobStatus.QUEUED, id="queued-job")
    running = JobRecord(analysis_id="other", name="running", status=JobStatus.RUNNING, id="running-job")
    setup.project.jobs[queued.id] = queued  # live jobs, as the job manager holds them
    setup.project.jobs[running.id] = running
    return queued, running


def _state(setup):
    p = setup.project
    return (setup.session.revision.id, dict(p.jobs), {k: v.status for k, v in p.jobs.items()}, dict(p.artifacts),
            dict(p.submodel_runs), dict(p.submodels))


def test_injected_failures_leave_no_record_and_no_attempt_files(setup, monkeypatch):
    queued, running = _with_unrelated_jobs(setup)
    before_files = _files(setup.store)
    before = _state(setup)
    child_before = project_to_dict(setup.definition.child)

    real_write = jobs_module.__dict__.get("write_solution_artifact")
    import anyfem.io.result_artifact as result_module

    def failing_write(*args, **kwargs):
        raise RuntimeError("disk full")

    monkeypatch.setattr(result_module, "write_solution_artifact", failing_write)
    with pytest.raises(RuntimeError, match="disk full"):
        setup.run()
    monkeypatch.undo()
    assert _files(setup.store) == before_files and _state(setup) == before
    assert project_to_dict(setup.definition.child) == child_before

    # A failure inside the session's revision step, after the insertions.
    def failing_revision(*args, **kwargs):
        raise RuntimeError("revision refused")

    monkeypatch.setattr(setup.session, "_make_revision", failing_revision)
    with pytest.raises(RuntimeError, match="revision refused"):
        setup.run()
    monkeypatch.undo()
    assert _files(setup.store) == before_files and _state(setup) == before
    assert setup.project.jobs["queued-job"] is queued and queued.status == JobStatus.QUEUED
    assert setup.project.jobs["running-job"] is running and running.status == JobStatus.RUNNING
    assert real_write is None  # the module imports the writer lazily

    run = setup.run()  # a retry succeeds
    assert run.id in setup.project.submodel_runs and set(before_files) <= set(_files(setup.store))


def test_cancellation_leaves_nothing(setup):
    from anysolver import CancellationToken

    token = CancellationToken()
    token.cancel()
    before_files, before = _files(setup.store), _state(setup)
    with pytest.raises(SubmodelError, match="SUBMODEL_CANCELLED"):
        run_submodel(setup.session, setup.definition.id, setup.store, cancellation_token=token)
    assert _files(setup.store) == before_files and _state(setup) == before


def test_staging_leaves_the_live_project_untouched(setup):
    revision, document_hash = setup.session.revision.id, _document_hash(setup.project)
    child_before = project_to_dict(setup.definition.child)
    staged = stage_submodel_run(submodel_snapshot(setup.session), setup.definition.id, setup.store)
    assert setup.session.revision.id == revision and _document_hash(setup.project) == document_hash
    assert project_to_dict(setup.definition.child) == child_before
    assert staged.run.id not in setup.project.submodel_runs
    discard_staged_run(staged)


def test_publication_refuses_changed_inputs(setup):
    staged = stage_submodel_run(submodel_snapshot(setup.session), setup.definition.id, setup.store)
    setup.edit(lambda p: setattr(p, "name", "renamed"))  # any committed edit
    before = _state(setup)
    with pytest.raises(SubmodelError, match="SUBMODEL_INPUTS_CHANGED"):
        publish_submodel_run(setup.session, staged)
    assert _state(setup) == before
    discard_staged_run(staged, setup.project)

    staged = stage_submodel_run(submodel_snapshot(setup.session), setup.definition.id, setup.store)
    path = setup.store.resolve(staged.run.source["result_artifact"]["uri"])
    original = path.read_bytes()
    path.write_bytes(original + b"\0")  # a source file changed, no revision
    with pytest.raises(SubmodelError, match="SUBMODEL_INPUTS_CHANGED"):
        publish_submodel_run(setup.session, staged)
    path.write_bytes(original)
    discard_staged_run(staged, setup.project)


def test_session_misuse_refuses(setup):
    with setup.session.transaction("open", solver_affecting=False):
        with pytest.raises(SubmodelError, match="SUBMODEL_SESSION_BUSY"):
            submodel_snapshot(setup.session)
    staged = stage_submodel_run(submodel_snapshot(setup.session), setup.definition.id, setup.store)
    with setup.session.transaction("open", solver_affecting=False):
        with pytest.raises(SubmodelError, match="SUBMODEL_SESSION_BUSY"):
            publish_submodel_run(setup.session, staged)
    errors = []
    worker = threading.Thread(target=lambda: errors.append(_capture(publish_submodel_run, setup.session, staged)))
    worker.start()
    worker.join()
    assert "SUBMODEL_SESSION_THREAD" in errors[0]
    discard_staged_run(staged, setup.project)


def _capture(function, *args):
    try:
        function(*args)
    except SubmodelError as error:
        return str(error)
    return ""


def test_a_raising_listener_does_not_undo_a_committed_publication(setup):
    def listener(_revision):
        raise RuntimeError("view refresh failed")

    setup.session.add_listener(listener)
    published = run_submodel(setup.session, setup.definition.id, setup.store)
    setup.session.remove_listener(listener)
    assert isinstance(published.notification_error, RuntimeError)
    run = published.run
    assert run.id in setup.project.submodel_runs
    for key in ("mesh_artifact", "result_artifact"):
        assert setup.store.resolve(run.child[key]["uri"]).is_file()
    assert setup.staleness(run) == ()


def test_orphans_are_listed_not_deleted(setup):
    staged = stage_submodel_run(submodel_snapshot(setup.session), setup.definition.id, setup.store)
    orphans = submodel_orphans(setup.project, setup.store)
    assert {p.name for p in orphans} == {p.name for p in staged.files}
    assert all(p.is_file() for p in staged.files)
    discard_staged_run(staged)


# ----------------------------------------------------------------------
# staleness (contract 11.6)
# ----------------------------------------------------------------------
def _child_edit(p, definition_id):
    p.submodels[definition_id].child.load_case().add_pressure(
        p.submodels[definition_id].child.face(next(iter(p.submodels[definition_id].source_faces))), 1.0)


@pytest.mark.parametrize("change, reason", [
    (lambda s, p: setattr(p.submodels[s.definition.id], "policy", "release_normal_rotation"), "DEFINITION_CHANGED"),
    (lambda s, p: _child_edit(p, s.definition.id), "DEFINITION_CHANGED"),
    (lambda s, p: p.submodels.clear(), "DEFINITION_MISSING"),
    (lambda s, p: p.jobs.pop(s.job.id), "SOURCE_JOB_MISSING"),
    (lambda s, p: setattr(s.job, "input_hash", "sha256:changed"), "SOURCE_JOB_CHANGED"),
    (lambda s, p: setattr(s.job, "status", JobStatus.FAILED), "SOURCE_JOB_CHANGED"),
    (lambda s, p: p.analyses.clear(), "SOURCE_ANALYSIS_MISSING"),
    (lambda s, p: p.load_case().add_pressure(p.face(s.gfaces[0, 0]), 1.0), "SOURCE_LOADING_CHANGED"),
    (lambda s, p: p.materials.__setitem__("S355", Material("S355", elastic_modulus=2.3e11, poisson_ratio=0.3)),
     "SOURCE_MODEL_CHANGED"),
    (lambda s, p: setattr(s.record, "status", "failed"), "SOURCE_MESH_CHANGED"),
    (lambda s, p: setattr(s.record, "source_model_hash", "sha256:other"), "SOURCE_MESH_CHANGED"),
    (lambda s, p: setattr(s.record, "mesh_input_hash", "sha256:other"), "SOURCE_MESH_CHANGED"),
    (lambda s, p: setattr(s.record, "structural_preparation", {"edited": True}), "SOURCE_MESH_CHANGED"),
    (lambda s, p: setattr(s.record, "artifact_id", "elsewhere"), "SOURCE_MESH_CHANGED"),
    (lambda s, p: p.artifacts.pop(s.record.artifact_id), "SOURCE_ARTIFACT_CHANGED"),
])
def test_each_staleness_reason_alone(setup, change, reason):
    run = setup.run()
    assert setup.staleness(run) == ()
    setup.edit(lambda p: change(setup, p), solver_affecting=True)
    assert setup.staleness(run) == (reason,)


def test_staleness_sees_files_and_the_active_mesh_without_a_revision(setup, monkeypatch):
    run = setup.run()
    revision = setup.session.revision.id
    assert setup.staleness(run, mesh_record_id="another") == ("SOURCE_MESH_CHANGED",)
    source_path = setup.store.resolve(run.source["result_artifact"]["uri"])
    original = source_path.read_bytes()
    source_path.write_bytes(original + b"\0")
    assert setup.staleness(run) == ("SOURCE_ARTIFACT_CHANGED",)
    source_path.write_bytes(original)
    child_path = setup.store.resolve(run.child["result_artifact"]["uri"])
    child_original = child_path.read_bytes()
    child_path.write_bytes(child_original + b"\0")
    assert setup.staleness(run) == ("RESULT_ARTIFACT_CHANGED",)
    child_path.write_bytes(child_original)
    assert setup.session.revision.id == revision
    monkeypatch.setattr(jobs_module, "_producer_versions", lambda: {"anysolver": "99"})
    assert setup.staleness(run) == ("REVALIDATION_REQUIRED",)


def test_a_child_label_edit_does_not_stale(setup):
    run = setup.run()
    setup.edit(lambda p: setattr(p.submodels[setup.definition.id].child, "name", "renamed child"))
    setup.edit(lambda p: setattr(p.submodels[setup.definition.id], "name", "renamed submodel"))
    assert setup.staleness(run) == ()


# ----------------------------------------------------------------------
# code review round 1
# ----------------------------------------------------------------------
def test_discard_refuses_after_either_committed_publication(setup):
    staged = stage_submodel_run(submodel_snapshot(setup.session), setup.definition.id, setup.store)
    publish_submodel_run(setup.session, staged)
    with pytest.raises(SubmodelError, match="published"):
        discard_staged_run(staged)
    assert all(p.is_file() for p in staged.files)

    def listener(_revision):
        raise RuntimeError("view refresh failed")

    staged = stage_submodel_run(submodel_snapshot(setup.session), setup.definition.id, setup.store)
    setup.session.add_listener(listener)
    result = publish_submodel_run(setup.session, staged)
    setup.session.remove_listener(listener)
    assert result.notification_error is not None
    with pytest.raises(SubmodelError, match="published"):
        discard_staged_run(staged)
    assert all(p.is_file() for p in staged.files)


@pytest.mark.parametrize("writer", ["mesh", "result"])
def test_a_writer_that_writes_then_raises_leaves_no_files(setup, monkeypatch, writer):
    import anyfem.io.result_artifact as result_module

    before_files, before = _files(setup.store), _state(setup)
    if writer == "mesh":
        real = setup.store.write_mesh

        def wrapped(*args, **kwargs):
            real(*args, **kwargs)
            raise RuntimeError("descriptor failed")

        monkeypatch.setattr(setup.store, "write_mesh", wrapped)
    else:
        real = result_module.write_solution_artifact

        def wrapped(*args, **kwargs):
            real(*args, **kwargs)
            raise RuntimeError("descriptor failed")

        monkeypatch.setattr(result_module, "write_solution_artifact", wrapped)
    with pytest.raises(RuntimeError, match="descriptor failed"):
        setup.run()
    assert _files(setup.store) == before_files and _state(setup) == before


def _rewrite_child_result(setup, staged, **changes):
    from anyfem.model.submodels import descriptor as describe

    run = staged.run
    identity = dict(document_id=setup.project.document_id, mesh_id=f"{run.id}-mesh",
                    model_hash=run.child["model_hash"], mesh_hash=run.child["mesh_fingerprint"],
                    analysis_hash=run.definition_hash)
    identity.update(changes)
    ref = write_solution_artifact(setup.store, staged.result.solution, job_id=run.id, **identity)
    return ref, describe(ref)


def test_publication_checks_child_artifacts_against_the_manifest(setup):
    staged = stage_submodel_run(submodel_snapshot(setup.session), setup.definition.id, setup.store)
    before = _state(setup)
    # A checksum-valid replacement the staged handle points at, unlike the manifest.
    ref, _ = _rewrite_child_result(setup, staged, analysis_hash="sha256:" + "0" * 64)
    staged.artifacts = (staged.artifacts[0], ref)
    with pytest.raises(SubmodelError, match="differs from the run's manifest"):
        publish_submodel_run(setup.session, staged)
    assert _state(setup) == before
    # The same replacement with the manifest updated too: the embedded identity refuses.
    staged.run.child["result_artifact"] = _rewrite_child_result(setup, staged, analysis_hash="sha256:" + "0" * 64)[1]
    staged.artifacts = (staged.artifacts[0], ref.__class__.from_dict({**ref.to_dict(),
                                                                      **staged.run.child["result_artifact"]}))
    with pytest.raises(SubmodelError, match="analysis_hash"):
        publish_submodel_run(setup.session, staged)
    assert _state(setup) == before
    discard_staged_run(staged)


@pytest.mark.parametrize("damage, match", [
    (lambda r: r["source"].pop("binding"), "binding"),
    (lambda r: r["source"]["binding"].pop("operator_fingerprint"), "operator_fingerprint"),
    (lambda r: r["source"].pop("revalidation"), "revalidation"),
    (lambda r: r["source"]["revalidation"].__setitem__("eta_scaled", "nan"), "eta_scaled"),
    (lambda r: r["source"]["revalidation"].__setitem__("eta_scaled", 1.0), "certified"),
    (lambda r: r["child"]["result_artifact"].__setitem__("uri", "../outside.anyres.h5"), "uri"),
    (lambda r: r["child"]["mesh_artifact"].__setitem__("uri", "results/x.anymesh.h5"), "uri"),
    (lambda r: r["source"]["result_artifact"].__setitem__("kind", "log"), "kind"),
    (lambda r: r["source"].__setitem__("mesh_selection", "guessed"), "mesh_selection"),
])
def test_incomplete_or_invalid_run_manifests_refuse(setup, damage, match):
    setup.run()
    document = json.loads(json.dumps(project_to_dict(setup.project)))
    damage(document["submodel_runs"][0])
    with pytest.raises(ProjectFileError, match=rf"submodel_runs\[0\].*{match}"):
        project_from_dict(document)


@pytest.mark.parametrize("variant", ["fractional_node_ids", "fractional_dof_indices", "boolean_dof_indices"])
def test_noninteger_field_identities_refuse(setup, variant):
    from dataclasses import replace

    descriptor, values, nodes = _field_parts(setup)
    provenance = dict(descriptor.provenance)
    dofs = list(provenance["global_dof_indices"])
    table = np.asarray(nodes, dtype=np.int64)
    if variant == "fractional_node_ids":
        table = table.astype(float) + 0.25
    elif variant == "fractional_dof_indices":
        dofs = [d + 0.25 for d in dofs]
    else:
        dofs = [True] + dofs[1:]
    descriptor = replace(descriptor, provenance={**provenance, "global_dof_indices": dofs})
    _rewrite_result(setup, fields={"displacement": (descriptor, values)}, tables={"displacement_node_ids": table})
    with pytest.raises(SubmodelError, match="SOURCE_FIELD.*integers"):
        setup.source()


def test_orphans_honour_retained_runs(setup):
    run = setup.run()
    child_result = setup.store.resolve(run.child["result_artifact"]["uri"])
    setup.edit(lambda p: p.artifacts.pop(run.child["result_artifact"]["id"]))
    assert setup.staleness(run) == ("RESULT_ARTIFACT_CHANGED",)
    assert child_result not in submodel_orphans(setup.project, setup.store)
    assert child_result.is_file()
