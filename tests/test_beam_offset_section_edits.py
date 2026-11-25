"""A published mesh is stale when an edit changes the beam offsets it was generated with.

Contract (owner decision 2026-11-25): element thickness, section stiffness and
materials are read from the project when the solver model is built, so a
generated mesh depends on the beam and plate sections only through the beam
offset each line hands the mesher.  The mesh record stores those offsets at
publication; any edit that changes them (profile dimensions, eccentricity,
offset mode, attachment side, rotation, the supporting plate's thickness)
makes the mesh stale, and Update mesh then requires a full rebuild.  An edit
that leaves every offset unchanged (a material, or a plate without beams)
keeps the mesh.  A record published before offsets were recorded is stale
after the next model edit when the model has beam lines.
"""
from dataclasses import replace

import pytest

from anyfem.commands import AddBeamSection, AddPlateSection
from anyfem.io.project_file import load_project
from anyfem.mesh_jobs import MeshSettings
from anyfem.mesh_session import ProjectMeshSession
from anyfem.model import BeamSection, PlateSection
from anyfem.model.project import ProjectError
from anyfem.solve.build import build_fe_model
from test_mesh_publication_lifecycle import FileWorkbench
from test_regional_mesh import two_plates

SETTINGS = MeshSettings.create(.5, element_order="linear", strategy="mapped", quality_policy={})
BEAM = BeamSection(name="beam", profile="Flatbar", material="S355", flange_width=.01,
                   flange_thickness=.1, offset_mode="automatic")
CHANGED = "beam offsets changed"


def _model():
    project, faces, edges = two_plates()
    # The second plate carries no beam: its thickness does not reach any offset.
    project.add_plate_section("bare", thickness=0.01, material="S355")
    project.assign_plate(faces[1], "bare")
    project.add_beam_section(BEAM)
    project.assign_beam(edges[0][0], "beam")
    assert project.beam_offsets
    return project, edges


def _session(tmp_path):
    project, edges = _model()
    session = ProjectMeshSession(project)
    session.generate_mesh(SETTINGS)
    path = session.save(tmp_path / "doc.anyfem")
    session.close()
    reopened = load_project(path)
    return ProjectMeshSession(reopened, path), reopened, edges


def _build(project, mesh):
    return build_fe_model(project, mesh, require_loads=False, require_supports=False)


def _active(project):
    return project.mesh_records[project.mesh_numbering.active_mesh_id]


EDITS = {
    "plate thickness": lambda p: p.plate_sections.__setitem__(
        "plate", replace(p.plate_sections["plate"], thickness=0.02)),
    "profile dimension": lambda p: p.add_beam_section(replace(p.beam_sections["beam"], flange_thickness=.15)),
    "offset mode": lambda p: p.add_beam_section(replace(p.beam_sections["beam"], offset_mode="centerline")),
    "attachment side": lambda p: p.add_beam_section(replace(p.beam_sections["beam"], attachment_side="back")),
    "rotation": lambda p: p.add_beam_section(replace(p.beam_sections["beam"], rotation_deg=30.0)),
    "manual eccentricity": lambda p: p.add_beam_section(
        replace(p.beam_sections["beam"], offset_mode="manual", eccentricity=.05)),
}


@pytest.mark.parametrize("edit", sorted(EDITS))
def test_headless_solve_after_an_offset_changing_edit_is_refused(tmp_path, edit):
    session, project, _ = _session(tmp_path)
    try:
        mesh = session.mesh
        _build(project, mesh)
        before = dict(project.beam_offsets)
        EDITS[edit](project)
        assert project.beam_offsets != before
        with pytest.raises(ProjectError, match=CHANGED):
            _build(project, mesh)
    finally:
        session.close()


def test_edits_that_keep_every_offset_keep_the_mesh(tmp_path):
    session, project, _ = _session(tmp_path)
    try:
        mesh = session.mesh
        before = dict(project.beam_offsets)
        project.plate_sections["bare"] = replace(project.plate_sections["bare"], thickness=0.03)
        project.add_beam_section(replace(project.beam_sections["beam"], eccentricity=.2))  # ignored when automatic
        assert project.beam_offsets == before
        _build(project, mesh)
    finally:
        session.close()


def test_headless_update_mesh_after_an_offset_edit_requires_a_full_rebuild(tmp_path):
    session, project, edges = _session(tmp_path)
    try:
        EDITS["plate thickness"](project)
        with pytest.raises(ValueError, match=CHANGED + ".*full rebuild"):
            session.update_mesh(replace(SETTINGS, overrides=((edges[0][1], 4),)))
        with pytest.raises(ValueError, match=CHANGED):
            session.update_mesh(SETTINGS)  # never an unchanged no-op either
        session.generate_mesh(SETTINGS)
        _build(project, session.mesh)
    finally:
        session.close()


def test_recorded_offsets_survive_save_and_reopen(tmp_path):
    session, project, _ = _session(tmp_path)
    session.close()
    record = _active(project)
    assert record.summary["beam_offsets"]
    assert set(record.summary["beam_offsets"]) == {str(e) for e in project.beam_offsets}


def _workbench(tmp_path):
    session, project, _ = _session(tmp_path)
    path = session.path
    session.close()
    editor = FileWorkbench(_model()[0])
    editor.open_project(str(path))
    return editor


@pytest.mark.parametrize("command", ["plate", "beam"])
def test_workbench_marks_the_mesh_stale_after_a_section_edit_and_refuses_the_solve(tmp_path, command):
    editor = _workbench(tmp_path)
    try:
        record = _active(editor.project)
        assert editor.mesh_record_state(record) == "completed"
        if command == "plate":
            editor.run(AddPlateSection(PlateSection(name="plate", thickness=0.02, material="S355")))
        else:
            editor.run(AddBeamSection(replace(editor.project.beam_sections["beam"], flange_thickness=.15)))
        assert editor.mesh_record_state(record) == "stale"
        with pytest.raises((ValueError, ProjectError)):
            editor.solve("Linear static")
        with pytest.raises(ValueError, match=CHANGED):
            editor.update_mesh(.5, strategy="mapped")
    finally:
        editor._project_lock.release()


def test_workbench_edit_of_a_plate_without_beams_keeps_the_mesh(tmp_path):
    editor = _workbench(tmp_path)
    try:
        record = _active(editor.project)
        editor.run(AddPlateSection(replace(editor.project.plate_sections["bare"], thickness=0.04)))
        assert editor.mesh_record_state(record) == "completed"
    finally:
        editor._project_lock.release()


def test_a_record_without_recorded_offsets_is_stale_after_the_next_edit(tmp_path):
    session, project, _ = _session(tmp_path)
    try:
        mesh = session.mesh
        record = _active(project)
        record.summary = {k: v for k, v in record.summary.items() if k != "beam_offsets"}
        _build(project, mesh)  # no edit since publication: still admitted
        project.plate_sections["bare"] = replace(project.plate_sections["bare"], thickness=0.03)
        with pytest.raises(ProjectError, match=CHANGED):
            _build(project, mesh)
    finally:
        session.close()


def test_unnumbered_document_refuses_a_tagged_mesh_after_an_offset_edit(tmp_path):
    session, project, _ = _session(tmp_path)
    try:
        mesh = session.mesh
    finally:
        session.close()
    project.mesh_numbering = replace(project.mesh_numbering, generation=0, active_mesh_id=None)
    _build(project, mesh)
    EDITS["rotation"](project)
    with pytest.raises(ProjectError, match=CHANGED):
        _build(project, mesh)


def test_headless_update_in_an_unnumbered_pre_rule_document_gives_the_offset_rule_message(tmp_path):
    import json
    project, edges = _model()
    session = ProjectMeshSession(project)
    session.generate_mesh(SETTINGS)
    path = session.save(tmp_path / "old.anyfem")
    session.close()
    data = json.loads(path.read_text(encoding="utf-8"))
    data["anyfem"].pop("beam_offset_rule", None)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    project = load_project(path)
    project.mesh_numbering = replace(project.mesh_numbering, generation=0, active_mesh_id=None)
    session = ProjectMeshSession(project, path)
    try:
        with pytest.raises(ValueError, match="offset rule.*full rebuild"):
            session.update_mesh(replace(SETTINGS, overrides=((edges[0][1], 4),)))
    finally:
        session.close()


def test_a_meshing_snapshot_that_differs_from_the_ticket_is_never_published(tmp_path):
    """Offsets are recorded from the ticket's model, so the meshed snapshot must be that model."""
    project, _ = _model()
    session = ProjectMeshSession(project)
    original = session.session.snapshot

    def edited_snapshot():
        thin = project.plate_sections["plate"]
        project.plate_sections["plate"] = replace(thin, thickness=0.05)
        try:
            return original()
        finally:
            project.plate_sections["plate"] = thin

    session.session.snapshot = edited_snapshot
    try:
        with pytest.raises(ValueError, match="superseded"):
            session.generate_mesh(SETTINGS)
        assert not project.mesh_records
    finally:
        session.close()


def test_unchanged_record_does_not_reuse_a_parent_with_other_offsets(tmp_path, monkeypatch):
    from types import SimpleNamespace
    import anyfem.regional_dispatch as dispatch
    from anyfem.application.mesh_publication import _model_hash, unchanged_record

    def reuse_attempted(*args, **kwargs):
        raise AssertionError("a parent with other offsets reached the no-op reuse planning")

    monkeypatch.setattr(dispatch, "plan_regional_update", reuse_attempted)
    session, project, _ = _session(tmp_path)
    session.close()
    parent = _active(project)
    parent.summary["regional_settings_hash"] = SETTINGS.input_hash
    EDITS["plate thickness"](project)
    ticket = SimpleNamespace(parent_id=parent.id, model_hash=_model_hash(project))
    assert unchanged_record(SimpleNamespace(project=project), ticket, SETTINGS, baseline={}) is None


def _scalar_model():
    project, faces, edges = two_plates()
    project.add_beam_section(BeamSection(name="beam", profile="Flatbar", material="S355", flange_width=.01,
                                         flange_thickness=.1, eccentricity=.04))
    project.assign_beam(edges[0][0], "beam")
    assert project.beam_offsets == {edges[0][0]: .04}
    return project, edges


def test_scalar_offsets_are_recorded_exactly_and_checked_after_reopen(tmp_path):
    project, edges = _scalar_model()
    session = ProjectMeshSession(project)
    session.generate_mesh(SETTINGS)
    path = session.save(tmp_path / "scalar.anyfem")
    session.close()
    reopened = load_project(path)
    record = _active(reopened)
    assert record.summary["beam_offsets"] == {str(edges[0][0]): .04}
    session = ProjectMeshSession(reopened, path)
    try:
        mesh = session.mesh
        reopened.materials["S355"] = reopened.materials["S355"]  # no-op edit keeps the mesh
        _build(reopened, mesh)
        reopened.add_beam_section(replace(reopened.beam_sections["beam"], eccentricity=.05))
        with pytest.raises(ProjectError, match=CHANGED):
            _build(reopened, mesh)
    finally:
        session.close()


def test_unevaluable_offsets_mark_the_mesh_stale_instead_of_raising(tmp_path):
    from anyfem.application.mesh_publication import beam_offsets_changed
    session, project, edges = _session(tmp_path)
    session.close()
    record = _active(project)
    start, end = (project.geometry.vertices[v] for v in (project.geometry.edges[edges[0][0]].start,
                                                         project.geometry.edges[edges[0][0]].end))
    along = tuple(float(b - a) for a, b in zip(start.position if hasattr(start, "position") else start,
                                                end.position if hasattr(end, "position") else end))
    project.add_beam_section(replace(project.beam_sections["beam"], web_direction=along))
    with pytest.raises(Exception):
        _ = project.beam_offsets
    assert beam_offsets_changed(project, record) is True


def test_workbench_verdict_cache_is_not_used_inside_a_transaction(tmp_path):
    editor = _workbench(tmp_path)
    try:
        record = _active(editor.project)
        editor.run(AddPlateSection(replace(editor.project.plate_sections["bare"], thickness=0.04)))
        assert editor.mesh_record_state(record) == "completed"  # primes the cache
        with editor.session.transaction("open edit"):
            editor.project.plate_sections["plate"] = replace(editor.project.plate_sections["plate"], thickness=0.02)
            assert editor.mesh_record_state(record) == "stale"
            editor.project.plate_sections["plate"] = replace(editor.project.plate_sections["plate"], thickness=0.01)
    finally:
        editor._project_lock.release()


def test_workbench_verdict_cache_sees_a_changed_record(tmp_path):
    editor = _workbench(tmp_path)
    try:
        record = _active(editor.project)
        editor.run(AddPlateSection(PlateSection(name="plate", thickness=0.02, material="S355")))
        assert editor.mesh_record_state(record) == "stale"
        from anyfem.application.mesh_publication import recorded_beam_offsets
        record.summary["beam_offsets"].clear()
        record.summary["beam_offsets"].update(recorded_beam_offsets(editor.project))
        assert editor.mesh_record_state(record) == "completed"
    finally:
        editor._project_lock.release()


def test_synchronous_generation_refuses_offsets_that_differ_from_the_ticket(tmp_path):
    """An edit that reaches the mesher and is reverted before commit must not be published."""
    editor = _workbench(tmp_path)
    try:
        project = editor.project
        before = set(project.mesh_records)
        original = project.generate_mesh

        def edited_generation(*args, **kwargs):
            plate = project.plate_sections["plate"]
            project.plate_sections["plate"] = replace(plate, thickness=0.05)
            try:
                return original(*args, **kwargs)
            finally:
                project.plate_sections["plate"] = plate

        project.generate_mesh = edited_generation
        with pytest.raises(ValueError, match="superseded"):
            editor.generate_mesh(.5, strategy="mapped")
        assert {r.id for r in project.mesh_records.values() if r.status == "completed"} <= before
    finally:
        editor._project_lock.release()


def test_workbench_state_inside_the_first_transaction_after_publication_is_live(tmp_path):
    editor = _workbench(tmp_path)
    try:
        record = _active(editor.project)
        assert editor.mesh_record_state(record) == "completed"
        with editor.session.transaction("open edit"):
            plate = editor.project.plate_sections["plate"]
            editor.project.plate_sections["plate"] = replace(plate, thickness=0.02)
            assert editor.mesh_record_state(record) == "stale"
            editor.project.plate_sections["plate"] = plate
        assert editor.mesh_record_state(record) == "completed"
    finally:
        editor._project_lock.release()


def test_workbench_verdict_cache_sees_a_changed_record_kind(tmp_path):
    editor = _workbench(tmp_path)
    try:
        record = _active(editor.project)
        editor.run(AddPlateSection(PlateSection(name="plate", thickness=0.02, material="S355")))
        assert editor.mesh_record_state(record) == "stale"
        record.kind = "imported"
        assert editor.mesh_record_state(record) == "completed"
    finally:
        editor._project_lock.release()


def test_headless_update_names_the_offset_rule_before_reading_retained_meshes(tmp_path):
    import json
    project, edges = _model()
    session = ProjectMeshSession(project)
    session.generate_mesh(SETTINGS)
    path = session.save(tmp_path / "old.anyfem")
    session.close()
    data = json.loads(path.read_text(encoding="utf-8"))
    data["anyfem"].pop("beam_offset_rule", None)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    project = load_project(path)
    project.mesh_numbering = replace(project.mesh_numbering, generation=0, active_mesh_id=None)
    for record in project.mesh_records.values():
        record.artifact_id = "missing-sidecar"
    session = ProjectMeshSession(project, path)
    try:
        with pytest.raises(ValueError, match="offset rule.*full rebuild"):
            session.update_mesh(replace(SETTINGS, overrides=((edges[0][1], 4),)))
    finally:
        session.close()


def test_a_wrapper_that_returns_an_earlier_pass_is_refused(tmp_path):
    """The offsets travel on the returned mesh, not in a slot a later pass overwrites."""
    editor = _workbench(tmp_path)
    try:
        project = editor.project
        original = project.generate_mesh

        def two_passes(*args, **kwargs):
            plate = project.plate_sections["plate"]
            project.plate_sections["plate"] = replace(plate, thickness=0.05)
            try:
                first = original(*args, **kwargs)
            finally:
                project.plate_sections["plate"] = plate
            original(*args, **kwargs)
            return first

        project.generate_mesh = two_passes
        with pytest.raises(ValueError, match="superseded"):
            editor.generate_mesh(.5, strategy="mapped")
    finally:
        editor._project_lock.release()


class _EditAtPublication:
    """A cancellation token whose second publication check edits the supporting plate."""

    def __init__(self, project):
        self.project, self.calls = project, 0

    def raise_if_cancelled(self, phase="", *args, **kwargs):
        if phase == "mesh publication":
            self.calls += 1
            if self.calls == 2:
                plate = self.project.plate_sections["plate"]
                self.project.plate_sections["plate"] = replace(plate, thickness=0.05)

    def __getattr__(self, name):
        return lambda *args, **kwargs: False


def test_a_publication_callback_that_edits_the_model_is_refused(tmp_path):
    project, _ = _model()
    session = ProjectMeshSession(project)
    token = _EditAtPublication(project)
    try:
        with pytest.raises(ValueError, match="superseded"):
            session.generate_mesh(SETTINGS, cancellation=token)
        assert token.calls == 2
        assert not [r for r in project.mesh_records.values() if r.status == "completed"]
    finally:
        session.close()


def test_workbench_update_in_an_unnumbered_document_refuses_changed_offsets_before_reading_meshes(tmp_path):
    session, project, _ = _session(tmp_path)
    path = session.path
    session.close()
    editor = FileWorkbench(_model()[0])
    editor.open_project(str(path))
    try:
        editor.project.mesh_numbering = replace(editor.project.mesh_numbering, generation=0, active_mesh_id=None)
        record = next(iter(editor.project.mesh_records.values()))
        editor.mesh_record_id = record.id
        editor._meshes.clear()
        record.artifact_id = "missing-sidecar"
        editor.run(AddPlateSection(PlateSection(name="plate", thickness=0.02, material="S355")))
        with pytest.raises(ValueError, match=CHANGED + ".*full rebuild"):
            editor.update_mesh(.5, strategy="mapped")
    finally:
        editor._project_lock.release()


def test_a_cached_offset_failure_is_raised_without_growing_its_traceback(tmp_path):
    import traceback
    from anyfem.application.mesh_publication import _model_hash, recorded_beam_offsets
    project, edges = _model()
    start = project.geometry.vertices[project.geometry.edges[edges[0][0]].start]
    end = project.geometry.vertices[project.geometry.edges[edges[0][0]].end]
    along = tuple(float(b - a) for a, b in zip(getattr(start, "position", start), getattr(end, "position", end)))
    project.add_beam_section(replace(project.beam_sections["beam"], web_direction=along))
    model_hash = _model_hash(project)
    depths = []
    for _ in range(3):
        try:
            recorded_beam_offsets(project, model_hash)
        except ValueError as error:
            depths.append(len(traceback.extract_tb(error.__traceback__)))
    assert len(depths) == 3 and depths[1] == depths[2]


def test_qt_update_mesh_names_changed_offsets_before_reading_retained_meshes(tmp_path):
    pytest.importorskip("PySide6")
    from types import SimpleNamespace
    from anyfem.ui.qt.tasks import MeshTask
    session, project, _ = _session(tmp_path)
    path = session.path
    session.close()
    editor = FileWorkbench(_model()[0])
    editor.open_project(str(path))
    try:
        editor.project.mesh_numbering = replace(editor.project.mesh_numbering, generation=0, active_mesh_id=None)
        record = next(iter(editor.project.mesh_records.values()))
        editor.mesh_record_id = record.id
        editor._meshes.clear()
        record.artifact_id = "missing-sidecar"
        editor.run(AddPlateSection(PlateSection(name="plate", thickness=0.02, material="S355")))
        panel = SimpleNamespace(
            app=editor, settings=lambda updating=False: {},
            order=SimpleNamespace(currentText=lambda: editor.project.element_order),
            s3_reference=SimpleNamespace(
                currentData=lambda: editor.project.shell_formulation_policy.s3_reference_surface))
        with pytest.raises(ValueError, match=CHANGED + ".*full rebuild"):
            MeshTask.start_update(panel)
    finally:
        editor._project_lock.release()
