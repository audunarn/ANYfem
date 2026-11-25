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
