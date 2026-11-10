"""Numbered regional shell publications driving an archived static job and a box cut."""

from dataclasses import replace

import pytest

from anygeometry.volume_cut import BoxVolume
from anyfem import fixed
from anyfem.document import DocumentSession
from anyfem.io.artifacts import ArtifactStore
from anyfem.io.project_file import load_project
from anyfem.io.result_artifact import write_solution_artifact
from anyfem.jobs import JobManager
from anyfem.model.records import AnalysisDefinition, JobStatus
from anyfem.model.submodels import SourceReference
from anyfem.solve.run import solve_linear_static
from anyfem.submodel import SubmodelError
from anyfem.submodel_cut import cut_submodel
from anyfem.submodel_jobs import (
    add_submodel, discard_staged_run, job_source, publish_submodel_run, run_submodel,
    stage_submodel_run, submodel_run_staleness, submodel_snapshot,
)
from test_regional_mesh import cells, settings, two_plates


def solve_parent(workflow, analysis, store, *, timeout=30):
    session = workflow.session
    record = workflow.project.mesh_records[workflow.mesh_record_id]
    jobs = JobManager(executor="thread")
    try:
        job = jobs.submit(analysis, session.snapshot(), solve_linear_static,
                          mesh_hash=record.mesh_hash, kwargs={"mesh": workflow.mesh})
        assert jobs.wait(job.id, timeout=timeout).status == JobStatus.COMPLETED, job
        artifact = write_solution_artifact(
            store, jobs.result(job.id), job_id=job.id, document_id=workflow.project.document_id,
            mesh_id=record.id, model_hash=job.model_hash, mesh_hash=record.mesh_hash,
            analysis_hash=job.analysis_hash,
        )
    finally:
        jobs.shutdown()
    with session.transaction("Archive parent job", solver_affecting=False):
        job.result_artifact_id = artifact.id
        workflow.project.artifacts[artifact.id] = artifact
        workflow.project.jobs[job.id] = job
    return job


def cut_and_record(workflow, job):
    session = workflow.session
    cut = cut_submodel(session.snapshot(), BoxVolume((.2, .2, -.1), (.8, .8, .1)),
                       source=SourceReference(job.id, "default"), policy="drive_all", target_size=.15)
    with session.transaction("Register box cut", solver_affecting=False):
        add_submodel(workflow.project, cut.definition)
    return cut.definition


@pytest.mark.parametrize("changed_plate, clockwise", [(0, False), (1, False), (0, True), (1, True)],
                         ids=["inside-cut-plate", "outside-cut-plate", "clockwise-inside", "clockwise-outside"])
def test_regional_source_is_bound_to_the_current_published_mesh(tmp_path, changed_plate, clockwise):
    project, faces, edges = two_plates(clockwise_first=clockwise)
    for face, boundary in zip(faces, edges):
        project.add_support(fixed(project.edge(boundary[3])))
        project.load_case().add_pressure(project.face(face), -100.)
    analysis = project.add_analysis(AnalysisDefinition(name="Static", target_id="default",
                                                       settings={"load_case": "default"}))
    path = tmp_path / "regional.anyfem"
    store = ArtifactStore(path)
    workflow = project.mesh_session(path)
    base = settings()
    old_mesh = workflow.generate_mesh(base)
    old_id = workflow.mesh_record_id
    workflow.save()
    first_job = solve_parent(workflow, analysis, store)
    first_definition = cut_and_record(workflow, first_job)
    old_run = run_submodel(workflow.session, first_definition.id, store).run
    expected_cut_reaction = (-1 if clockwise else 1) * 100. * .6 ** 2
    assert old_run.diagnostics["whole_cut_resultant"][2] == pytest.approx(expected_cut_reaction)
    original_snapshot = workflow.session.snapshot()
    assert submodel_run_staleness(original_snapshot, old_run, store) == ()
    assert old_run.source["mesh_record_id"] == old_id
    # A second headless session can publish a different active mesh on the shared
    # project without advancing the staging session's own revision.
    staging_session = DocumentSession(project)
    staged = stage_submodel_run(submodel_snapshot(staging_session), first_definition.id, store)
    try:
        new_mesh = workflow.update_mesh(replace(base, overrides=((edges[changed_plate][0], 4),)))
        new_id = workflow.mesh_record_id
        assert new_id != old_id
        assert cells(new_mesh, faces[1 - changed_plate]) == cells(old_mesh, faces[1 - changed_plate])
        assert set(cells(new_mesh, faces[changed_plate])).isdisjoint(cells(old_mesh, faces[changed_plate]))
        with pytest.raises(SubmodelError, match="SUBMODEL_INPUTS_CHANGED"):
            publish_submodel_run(staging_session, staged)
    finally:
        discard_staged_run(staged)
    assert all(not file.exists() for file in staged.files)
    workflow.save()
    workflow.close()

    reopened = load_project(path).mesh_session(path)
    try:
        snapshot = reopened.session.snapshot()
        assert submodel_run_staleness(snapshot, old_run, store) == ("SOURCE_MESH_CHANGED",)
        assert submodel_run_staleness(snapshot, old_run, store, mesh_record_id=old_id) == ("SOURCE_MESH_CHANGED",)
        archived = job_source(original_snapshot.thaw(), first_definition.source, store,
                              document=original_snapshot.document, model_hash=original_snapshot.revision.model_hash)
        assert archived.manifest["mesh_selection"] == "explicit"
        assert archived.manifest["mesh_record_id"] == old_id
        before = set(reopened.project.artifacts)
        for explicit in (None, old_id):
            with pytest.raises(SubmodelError, match="SOURCE_MESH"):
                run_submodel(reopened.session, first_definition.id, store, mesh_record_id=explicit)
        assert set(reopened.project.artifacts) == before
        assert set(reopened.project.submodel_runs) == {old_run.id}
        for ref in (old_run.source["mesh_artifact"], old_run.source["result_artifact"],
                    old_run.child["mesh_artifact"], old_run.child["result_artifact"]):
            assert store.verify(reopened.project.artifacts[ref["id"]])

        second_job = solve_parent(reopened, reopened.project.analyses[analysis.id], store)
        definition = cut_and_record(reopened, second_job)
        new_run = run_submodel(reopened.session, definition.id, store).run
        assert new_run.diagnostics["whole_cut_resultant"][2] == pytest.approx(expected_cut_reaction)
        assert new_run.source["mesh_record_id"] == new_id
        assert new_run.source["job_id"] == second_job.id
        assert new_run.source["job_mesh_hash"] != old_run.source["job_mesh_hash"]
        assert submodel_run_staleness(reopened.session.snapshot(), new_run, store) == ()
        reopened.save()
    finally:
        reopened.close()
    restored = load_project(path)
    assert set(restored.submodel_runs) == {old_run.id, new_run.id}
