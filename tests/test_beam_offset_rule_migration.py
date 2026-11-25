"""Meshes saved before the foot-on-plate-surface automatic offset rule are stale on open.

Contract (owner decision B, 2026-11-25): a document whose header does not name
the current beam offset rule, and that defines any beam section, opens with
every saved generated mesh marked stale.  Such a mesh is not adopted, reused
or solved until the mesh is generated again (full rebuild).
"""
import json
from dataclasses import replace

import pytest

from anyfem.io.artifacts import ArtifactStore
from anyfem.io.project_file import load_project, project_to_dict
from anyfem.mesh_jobs import MeshSettings
from anyfem.mesh_session import ProjectMeshSession
from anyfem.model import BeamSection
from anyfem.model.project import ProjectError
from anyfem.solve.build import build_fe_model
from test_mesh_publication_lifecycle import FileWorkbench
from test_regional_mesh import two_plates

RULE_KEY = "beam_offset_rule"
SETTINGS = MeshSettings.create(.5, element_order="linear", strategy="mapped", quality_policy={})


def _model(with_beam=True):
    project, faces, edges = two_plates()
    if with_beam:
        project.add_beam_section(BeamSection(name="beam", profile="Flatbar", material="S355",
                                             flange_width=.01, flange_thickness=.1))
        project.assign_beam(edges[0][0], "beam")
    return project, edges


def _saved(tmp_path, *, with_beam=True, pre_rule=True, name="doc.anyfem"):
    """Save a generated mesh; strip the rule from the header to make it pre-rule."""
    project, edges = _model(with_beam)
    session = ProjectMeshSession(project)
    session.generate_mesh(SETTINGS)
    path = session.save(tmp_path / name)
    session.close()
    if pre_rule:
        data = json.loads(path.read_text(encoding="utf-8"))
        data["anyfem"].pop(RULE_KEY, None)
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return path, edges


def _generated(project):
    return [r for r in project.mesh_records.values() if r.kind == "generated"]


def test_pre_rule_document_with_a_beam_opens_with_its_meshes_stale(tmp_path):
    path, _ = _saved(tmp_path)
    project = load_project(path)
    records = _generated(project)
    assert records
    for record in records:
        assert record.status == "stale"
        assert "regional_baseline" not in record.summary
        assert "offset rule" in record.summary["regional_refusal"]


def test_headless_solve_of_a_pre_rule_mesh_is_refused(tmp_path):
    path, _ = _saved(tmp_path)
    project = load_project(path)
    session = ProjectMeshSession(project, path)
    try:
        with pytest.raises(ProjectError, match="offset rule"):
            build_fe_model(project, session.mesh, require_loads=False, require_supports=False)
    finally:
        session.close()


def test_a_pre_rule_mesh_read_from_its_artifact_is_refused_in_an_unnumbered_document(tmp_path):
    path, _ = _saved(tmp_path)
    project = load_project(path)
    project.mesh_numbering = replace(project.mesh_numbering, generation=0, active_mesh_id=None)
    record = _generated(project)[0]
    mesh = ArtifactStore(path).read_mesh(project.artifacts[record.artifact_id])
    with pytest.raises(ProjectError, match="offset rule"):
        build_fe_model(project, mesh, require_loads=False, require_supports=False)


def test_update_mesh_refuses_a_pre_rule_parent_and_a_full_rebuild_clears_it(tmp_path):
    path, edges = _saved(tmp_path)
    session = ProjectMeshSession(load_project(path), path)
    try:
        with pytest.raises(ValueError, match="offset rule"):
            session.update_mesh(replace(SETTINGS, overrides=((edges[0][1], 4),)))
        mesh = session.generate_mesh(SETTINGS)
        build_fe_model(session.project, mesh, require_loads=False, require_supports=False)
        session.save()
    finally:
        session.close()
    reopened = load_project(path)
    active = reopened.mesh_records[reopened.mesh_numbering.active_mesh_id]
    assert active.status == "completed"
    assert json.loads(path.read_text(encoding="utf-8"))["anyfem"][RULE_KEY]


def test_workbench_open_does_not_adopt_or_solve_a_pre_rule_mesh(tmp_path):
    path, _ = _saved(tmp_path)
    project, _ = _model()
    editor = FileWorkbench(project)
    editor.open_project(str(path))
    try:
        assert editor.mesh is None
        record = editor.project.mesh_records[editor.project.mesh_numbering.active_mesh_id]
        assert editor.mesh_record_state(record) == "stale"
        with pytest.raises(ValueError, match="offset rule.*full rebuild"):
            editor.solve("Linear static")
    finally:
        editor._project_lock.release()


def test_stale_marks_survive_save_without_regenerating(tmp_path):
    path, _ = _saved(tmp_path)
    session = ProjectMeshSession(load_project(path), path)
    try:
        session.save()
    finally:
        session.close()
    assert json.loads(path.read_text(encoding="utf-8"))["anyfem"].get(RULE_KEY)
    assert all(r.status == "stale" for r in _generated(load_project(path)))


def test_current_documents_and_documents_without_beams_are_unchanged(tmp_path):
    current, _ = _saved(tmp_path, pre_rule=False, name="current.anyfem")
    project = load_project(current)
    assert all(r.status == "completed" for r in _generated(project))
    session = ProjectMeshSession(project, current)
    try:
        build_fe_model(project, session.mesh, require_loads=False, require_supports=False)
    finally:
        session.close()

    plain, _ = _saved(tmp_path, with_beam=False, name="plain.anyfem")
    plain_project = load_project(plain)
    assert all(r.status == "completed" for r in _generated(plain_project))
    # Documents without beam sections keep their exact header bytes.
    assert RULE_KEY not in project_to_dict(plain_project)["anyfem"]


def test_submodel_source_and_run_from_a_pre_rule_parent_are_stale(tmp_path, monkeypatch):
    import test_submodel_jobs as jobs_tests
    from anyfem.document import DocumentSession
    from anyfem.io.project_file import project_from_dict
    from anyfem.submodel import SubmodelError
    from anyfem.submodel_jobs import job_source, submodel_run_staleness

    original = jobs_tests.global_project

    def with_beam_section():
        project, gfaces = original()
        project.add_beam_section(BeamSection(name="beam", profile="Flatbar", material="S355",
                                             flange_width=.01, flange_thickness=.1))
        return project, gfaces

    monkeypatch.setattr(jobs_tests, "global_project", with_beam_section)
    setup = jobs_tests.Setup(tmp_path)
    run = setup.run()
    assert setup.staleness(run) == ()

    document = json.loads(json.dumps(setup.snapshot().document))
    assert document["anyfem"].pop(RULE_KEY) == "foot_on_plate_surface"
    session = DocumentSession(project_from_dict(document))
    snap = session.snapshot()
    with pytest.raises(SubmodelError) as error:
        job_source(snap.thaw(), setup.definition.source, setup.store, document=snap.document,
                   model_hash=snap.revision.model_hash)
    assert error.value.code == "SOURCE_MESH"
    assert submodel_run_staleness(snap, run, setup.store) != ()


def _unnumbered_pre_rule(tmp_path):
    path, edges = _saved(tmp_path)
    project = load_project(path)
    project.mesh_numbering = replace(project.mesh_numbering, generation=0, active_mesh_id=None)
    record = _generated(project)[0]
    return path, project, record, edges


def test_untagged_copies_of_a_pre_rule_mesh_are_refused(tmp_path):
    from anymesher.serialize import mesh_from_dict, mesh_to_dict

    path, project, record, _ = _unnumbered_pre_rule(tmp_path)
    store = ArtifactStore(path)
    artifact = project.artifacts[record.artifact_id]
    # A worker transport / decoded copy, and a read by URI string, carry no record tag.
    copies = (mesh_from_dict(mesh_to_dict(store.read_mesh(artifact))), store.read_mesh(artifact.uri))
    for mesh in copies:
        assert getattr(mesh, "_anyfem_mesh_record_id", None) is None
        with pytest.raises(ProjectError, match="offset rule"):
            build_fe_model(project, mesh, require_loads=False, require_supports=False)


def test_a_freshly_generated_mesh_of_a_migrated_unnumbered_project_is_admitted(tmp_path):
    from anymesher.serialize import mesh_from_dict, mesh_to_dict

    path, project, _, _ = _unnumbered_pre_rule(tmp_path)
    fresh = project.generate_mesh(.5, strategy="mapped", quality_policy={})
    build_fe_model(project, fresh, require_loads=False, require_supports=False)
    # A published rebuild takes numbers above every saved mesh, stale ones included,
    # so even its decoded copy (a worker transport) never reproduces a stale record.
    session = ProjectMeshSession(load_project(path), path)
    try:
        session.project.mesh_numbering = replace(session.project.mesh_numbering, generation=0, active_mesh_id=None)
        published = session.generate_mesh(SETTINGS)
        build_fe_model(session.project, mesh_from_dict(mesh_to_dict(published)),
                       require_loads=False, require_supports=False)
    finally:
        session.close()
    # Documented limit: an untagged copy of an unpublished rebuild that reproduces the
    # stale record exactly (unchanged offsets, same numbers) is refused, failing closed.
    with pytest.raises(ProjectError, match="offset rule"):
        build_fe_model(project, mesh_from_dict(mesh_to_dict(fresh)), require_loads=False, require_supports=False)


def test_a_regional_update_from_a_pre_rule_parent_is_refused_on_every_dispatch_path(tmp_path):
    from anymesher.serialize import mesh_to_dict
    from anyfem.regional_dispatch import generate_regional_update
    from anyfem.regional_mesh import RegionalUpdateUnsupported

    current, _ = _saved(tmp_path, pre_rule=False, name="current.anyfem")
    source = load_project(current)
    parent = source.mesh_records[source.mesh_numbering.active_mesh_id]
    baseline = parent.summary["regional_baseline"]
    mesh = ArtifactStore(current).read_mesh(source.artifacts[parent.artifact_id])
    data = json.loads(current.read_text(encoding="utf-8"))
    data["anyfem"].pop(RULE_KEY)
    from anyfem.io.project_file import project_from_dict
    migrated = project_from_dict(data)
    for old in (mesh, mesh_to_dict(mesh)):
        with pytest.raises(RegionalUpdateUnsupported, match="offset rule"):
            generate_regional_update(migrated, SETTINGS, baseline, old,
                                     node_high_water=1000, element_high_water=1000)


def test_migration_marks_every_generated_record_and_leaves_imported_ones(tmp_path):
    from anyfem.beam_offset_rule import migrate_pre_rule_meshes
    from anyfem.model.records import MeshRecord

    project, _ = _model()
    generated = [MeshRecord(name=f"m{i}", source_model_hash="h", mesh_input_hash="i", mesh_hash="x",
                            status=status) for i, status in enumerate(("completed", "inspection_only", "stale"))]
    failed = MeshRecord(name="f", source_model_hash="h", mesh_input_hash="i", mesh_hash="", status="failed")
    imported = MeshRecord(name="imp", kind="imported", source_model_hash="h", mesh_input_hash="", mesh_hash="y")
    for record in (*generated, failed, imported):
        project.mesh_records[record.id] = record
    assert set(migrate_pre_rule_meshes(project)) == {record.id for record in generated}
    assert all(record.status == "stale" for record in generated)
    assert failed.status == "failed" and imported.status == "completed"
    assert "beam_offset_rule_stale" not in imported.summary
    assert set(migrate_pre_rule_meshes(project)) == {record.id for record in generated}  # idempotent


def test_run_staleness_of_a_raw_pre_rule_snapshot_is_stale(tmp_path, monkeypatch):
    import test_submodel_jobs as jobs_tests
    from anyfem.document import ProjectSnapshot
    from anyfem.submodel_jobs import submodel_run_staleness

    original = jobs_tests.global_project

    def with_beam_section():
        project, gfaces = original()
        project.add_beam_section(BeamSection(name="beam", profile="Flatbar", material="S355",
                                             flange_width=.01, flange_thickness=.1))
        return project, gfaces

    monkeypatch.setattr(jobs_tests, "global_project", with_beam_section)
    setup = jobs_tests.Setup(tmp_path)
    run = setup.run()
    snap = setup.snapshot()
    document = json.loads(json.dumps(snap.document))
    document["anyfem"].pop(RULE_KEY)
    raw = ProjectSnapshot(document=document, revision=snap.revision)
    assert "SOURCE_MESH_CHANGED" in submodel_run_staleness(raw, run, setup.store)
