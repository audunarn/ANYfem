"""Editing the shell child of a submodel: headless validation, application and the Qt editor.

The Qt group needs ANYFEM_RUN_QT_TESTS=1 (as test_qt_submodel_task.py).  Nothing here
has been run by its author; the coordinator runs the public targets.
"""

from __future__ import annotations

import os
from types import SimpleNamespace

import numpy as np
import pytest

from anyfem.commands import MoveEntities
from anyfem.io.project_file import project_to_dict
from anyfem.model.submodels import SubmodelDefinition
from anyfem.submodel import SubmodelError
from anyfem.submodel_child_edit import (
    apply_child_edit,
    build_replacement,
    definition_fingerprint,
    detach_child,
    validate_child_edit,
)
from test_submodel_cut import BOX, Parent  # noqa: F401  (BOX documents the volume used)


@pytest.fixture
def parent(tmp_path):
    made = Parent(tmp_path)
    cut = made.cut()
    made.register(cut.definition)
    made.definition_id = cut.definition.id
    return made


def stored(parent):
    return parent.project.submodels[parent.definition_id]


def cut_vertices(definition):
    geometry = definition.child.geometry
    return {v for edges in definition.cut_faces.values() for e in edges
            for v in (geometry.edges[e].start, geometry.edges[e].end)}


def interior_vertex(definition):
    geometry = definition.child.geometry
    lower, upper = np.asarray(definition.volume["lower"]), np.asarray(definition.volume["upper"])
    skip = cut_vertices(definition)
    for vertex_id, vertex in geometry.vertices.items():
        point = np.asarray(vertex.position, dtype=float)
        if vertex_id not in skip and np.all(point[:2] > lower[:2] + 1e-6) and np.all(point[:2] < upper[:2] - 1e-6):
            return vertex_id
    pytest.skip("the fixture child has no interior vertex")


def move(project, vertex_id, translation):
    ref = project.geometry.entity_ref("vertex", vertex_id)
    MoveEntities([ref], translation).do(project)


# ----------------------------------------------------------------------
# validation
# ----------------------------------------------------------------------
def test_an_unchanged_child_is_accepted_and_changes_nothing(parent):
    previous = stored(parent)
    outcome = validate_child_edit(previous, detach_child(previous))
    assert outcome.changed is False and not outcome.mapped_faces and not outcome.removed_faces
    assert outcome.definition.source_faces == previous.source_faces
    assert outcome.definition.cut_faces == previous.cut_faces


def test_the_detached_clone_is_independent_of_the_stored_child(parent):
    previous = stored(parent)
    clone = detach_child(previous)
    assert clone is not previous.child
    before = definition_fingerprint(previous)
    move(clone, interior_vertex(previous), (0.01, 0.01, 0.0))
    assert definition_fingerprint(previous) == before


def test_an_interior_geometry_edit_is_accepted_with_lineage_intact(parent):
    previous = stored(parent)
    edited = detach_child(previous)
    move(edited, interior_vertex(previous), (0.01, 0.01, 0.0))
    outcome = validate_child_edit(previous, edited)
    assert outcome.changed
    assert outcome.definition.id == previous.id and outcome.definition.source == previous.source
    assert outcome.definition.cut_faces == previous.cut_faces
    assert set(outcome.definition.source_faces) == set(edited.geometry.faces)
    assert not outcome.definition.child.jobs and not outcome.definition.child.mesh_records


def test_a_moved_cut_vertex_is_refused_precisely(parent):
    previous = stored(parent)
    edited = detach_child(previous)
    move(edited, sorted(cut_vertices(previous))[0], (0.0, 0.0, 0.01))
    with pytest.raises(SubmodelError) as raised:
        validate_child_edit(previous, edited)
    assert raised.value.code in ("CHILD_CUT_EDGE_CHANGED", "CHILD_OUTSIDE_CUT")
    assert "Cut edge" in str(raised.value) or "outside" in str(raised.value)


def test_a_new_point_outside_the_original_cut_is_refused(parent):
    # an isolated point never crosses other edges, so the owner's own geometry checks do not pre-empt the guard
    previous = stored(parent)
    edited = detach_child(previous)
    edited.geometry.add_point(5.0, 0.0, 0.0)
    with pytest.raises(SubmodelError) as raised:
        validate_child_edit(previous, edited)
    assert raised.value.code == "CHILD_OUTSIDE_CUT"


def test_a_planar_face_regenerated_as_planar_coons_is_accepted(parent):
    previous = stored(parent)
    edited = detach_child(previous)
    move(edited, interior_vertex(previous), (0.01, 0.01, 0.0))
    types = {type(previous.child.geometry.faces[f].surface).__name__ for f in previous.child.geometry.faces}
    regenerated = {f for f in edited.geometry.faces
                   if type(edited.geometry.faces[f].surface) is not type(previous.child.geometry.faces[f].surface)}
    assert types and regenerated, "the fixture edit should regenerate at least one face's surface class"
    validate_child_edit(previous, edited)  # raises on a refusal


def test_a_face_pushed_off_its_original_plane_is_refused(parent):
    previous = stored(parent)
    edited = detach_child(previous)
    move(edited, interior_vertex(previous), (0.0, 0.0, 0.05))
    with pytest.raises(SubmodelError) as raised:
        validate_child_edit(previous, edited)
    assert raised.value.code == "CHILD_SURFACE_CHANGED"


def test_the_support_check_bounds_every_edge_against_the_original_plane(parent, monkeypatch):
    from anyfem import submodel_child_edit as module

    previous = stored(parent)
    edited = detach_child(previous)
    move(edited, interior_vertex(previous), (0.01, 0.01, 0.0))
    seen = []
    real = module._owner_edge_bounds

    def spy(geometry, edge_id, volume):
        seen.append(edge_id)
        return real(geometry, edge_id, volume)

    monkeypatch.setattr(module, "_owner_edge_bounds", spy)
    validate_child_edit(previous, edited)
    assert seen, "containment and plane proofs must use the owner's conservative bounds"
    # an unprovable (non-finite or failing) bound refuses instead of passing
    monkeypatch.setattr(module, "_owner_edge_bounds",
                        lambda *a: (_ for _ in ()).throw(ValueError("no enclosure")))
    with pytest.raises(SubmodelError) as raised:
        validate_child_edit(previous, edited)
    assert raised.value.code in ("CHILD_SURFACE_CHANGED", "CHILD_CURVE_UNPROVEN")


def test_an_unsupported_cut_edge_curve_that_cannot_be_proved_identical_is_refused(parent):
    from anyfem import submodel_child_edit as module

    class Odd:
        def __eq__(self, other):
            return False

        def __repr__(self):
            return f"Odd@{id(self)}"

    previous = stored(parent)
    edge_id = next(iter(next(iter(previous.cut_faces.values()))))
    old, new = SimpleNamespace(curve=Odd()), SimpleNamespace(curve=Odd())
    problem = module._controls_differ(previous.child.geometry, previous.child.geometry, edge_id, old, new, 1e-9)
    assert problem is not None and "unsupported curve family" in problem
    same = Odd()
    assert module._controls_differ(previous.child.geometry, previous.child.geometry, edge_id,
                                   SimpleNamespace(curve=same), SimpleNamespace(curve=same), 1e-9) is None


def test_lineage_requires_the_new_face_to_lie_in_the_faces_that_carry_it(parent):
    from anygeometry.volume_cut import _interior_point

    from anyfem import submodel_child_edit as module

    previous = stored(parent)
    geometry = previous.child.geometry
    first, second = sorted(geometry.faces)[:2]
    assert previous.source_faces[first] != previous.source_faces[second]
    assert module._lineage_problem(previous, previous.child.geometry, first, previous.source_faces[first]) is None
    problem = module._lineage_problem(previous, previous.child.geometry, first, previous.source_faces[second])
    assert problem is not None and "outside the original faces" in problem
    assert _interior_point(geometry, first) is not None  # the proof rests on an owner-verified interior point


def test_a_solid_definition_is_not_edited_here():
    solid = SimpleNamespace(child_kind="solid")
    with pytest.raises(SubmodelError) as raised:
        validate_child_edit(solid, SimpleNamespace())
    assert raised.value.code == "CHILD_EDIT_KIND"


def test_every_retained_face_must_have_a_plate_section_even_when_strict_resolution_passes(parent):
    previous = stored(parent)
    edited = detach_child(previous)

    for assignment in tuple(edited.section_assignments):
        edited.remove_section_assignment(assignment)
    assert not edited.face_sections
    with pytest.raises(SubmodelError) as raised:
        validate_child_edit(previous, edited)
    assert raised.value.code == "CHILD_SECTION_UNRESOLVED" and "no plate section" in str(raised.value)


def test_embedded_mesh_numbering_is_cleared_with_the_mesh_records(parent):
    from anyfem.mesh_numbering import MeshNumbering

    previous = stored(parent)
    edited = detach_child(previous)
    edited.mesh_numbering = MeshNumbering(node_high_water=10, element_high_water=10, generation=1)
    outcome = validate_child_edit(previous, edited)
    assert not outcome.definition.child.mesh_records
    assert outcome.definition.child.mesh_numbering == MeshNumbering()


def test_the_definition_target_size_follows_the_edited_child(parent):
    previous = stored(parent)
    edited = detach_child(previous)
    edited.target_size = float(previous.target_size) * 0.5
    outcome = validate_child_edit(previous, edited)
    assert outcome.changed and outcome.definition.target_size == edited.target_size
    assert outcome.definition.child.target_size == outcome.definition.target_size
    assert previous.target_size != outcome.definition.target_size  # the stored definition itself is untouched


def test_unusable_target_sizes_are_refused():
    from anyfem import submodel_child_edit as module

    for bad in (float("nan"), float("inf"), 0.0, -1.0, True, "abc"):
        with pytest.raises(SubmodelError) as raised:
            module._positive_finite(bad, "the child target size")
        assert raised.value.code == "CHILD_TARGET_SIZE"


def test_custom_cut_group_names_are_preserved_and_canonical_sides_are_checked(parent):
    from dataclasses import replace

    from anyfem import submodel_child_edit as module

    previous = stored(parent)
    canonical = [name for name in previous.cut_faces if name in module._SIDES]
    if not canonical:
        pytest.skip("the fixture cut uses no canonical side names")
    side = canonical[0]
    custom = replace(previous, cut_faces={**previous.cut_faces, "my group": previous.cut_faces[side]})
    outcome = validate_child_edit(custom, detach_child(custom))
    assert "my group" in outcome.definition.cut_faces and outcome.definition.cut_faces == custom.cut_faces
    axis, upper = module._SIDES[side]
    opposite = f"{side[0]}{1 - upper}"
    swapped = {name: edges for name, edges in previous.cut_faces.items() if name != side}
    swapped[opposite] = previous.cut_faces[side]
    wrong = replace(previous, cut_faces=swapped)
    with pytest.raises(SubmodelError) as raised:
        validate_child_edit(wrong, detach_child(wrong))
    assert raised.value.code == "CHILD_CUT_SIDE"


def test_nonfinite_and_malformed_geometry_refuses_instead_of_passing_quietly():
    from anyfem import submodel_child_edit as module

    assert module._exceeds(float("nan"), 1.0) and module._exceeds(2.0, 1.0) and not module._exceeds(0.5, 1.0)
    bad_vertex = SimpleNamespace(vertices={1: SimpleNamespace(position=[float("nan"), 0.0, 0.0])})
    with pytest.raises(SubmodelError) as raised:
        module._vertex_position(bad_vertex, 1)
    assert raised.value.code == "CHILD_VERTEX_INVALID"
    for sampled in (np.zeros((4, 2)), np.full((33, 3), np.nan)):
        geometry = SimpleNamespace(sample_edge=lambda edge, params, sampled=sampled: sampled)
        with pytest.raises(SubmodelError) as raised:
            module._trace(geometry, 7)
        assert raised.value.code == "CHILD_CURVE_INVALID"


# ----------------------------------------------------------------------
# application
# ----------------------------------------------------------------------
def test_apply_replaces_the_child_and_leaves_the_parent_alone(parent):
    previous = stored(parent)
    parent_before = project_to_dict(parent.project)
    parent_before.pop("submodels", None)
    job_before = dict(parent.project.jobs)
    edited = detach_child(previous)
    move(edited, interior_vertex(previous), (0.01, 0.01, 0.0))
    outcome = apply_child_edit(parent.session, parent.definition_id, edited,
                               base_fingerprint=definition_fingerprint(previous))
    assert outcome.changed
    now = stored(parent)
    assert now.id == previous.id and definition_fingerprint(now) != definition_fingerprint(previous)
    parent_after = project_to_dict(parent.project)
    parent_after.pop("submodels", None)
    assert parent_after == parent_before and parent.project.jobs == job_before


def test_an_applied_interior_edit_solves_on_the_changed_child_and_leaves_the_parent_intact(parent):
    from anyfem.submodel_jobs import discard_staged_run, stage_submodel_run, submodel_snapshot

    previous = stored(parent)
    parent_before = project_to_dict(parent.project)
    parent_before.pop("submodels", None)
    parent_mesh, parent_solution = parent.mesh, parent.solution
    parent_nodes = {n: np.array(parent_solution.node_displacement(n)) for n in parent_mesh.nodes}
    edited = detach_child(previous)
    vertex = interior_vertex(previous)
    moved_from = np.array(previous.child.geometry.vertex_position(vertex))
    move(edited, vertex, (0.01, 0.01, 0.0))
    apply_child_edit(parent.session, parent.definition_id, edited, base_fingerprint=definition_fingerprint(previous))
    changed = stored(parent)
    assert np.allclose(changed.child.geometry.vertex_position(vertex), moved_from + (0.01, 0.01, 0.0))

    staged = stage_submodel_run(submodel_snapshot(parent.session), changed.id, parent.store)
    try:
        result = staged.result
        assert result.transfer, "the changed child is still driven from the solved parent on its cut"
        assert result.solution.built.mesh.nodes
        assert np.isfinite(np.asarray(result.diagnostics["whole_cut_resultant"], dtype=float)).all()
    finally:
        discard_staged_run(staged)

    after = project_to_dict(parent.project)
    after.pop("submodels", None)
    assert after == parent_before
    assert parent.mesh is parent_mesh and parent.solution is parent_solution
    for node, value in parent_nodes.items():
        assert np.array_equal(parent_solution.node_displacement(node), value)


def test_applying_an_unchanged_child_records_no_revision(parent):
    revision = parent.session.revision.id
    outcome = apply_child_edit(parent.session, parent.definition_id, detach_child(stored(parent)))
    assert outcome.changed is False and parent.session.revision.id == revision


def test_a_concurrent_change_is_a_conflict_and_changes_nothing(parent):
    previous = stored(parent)
    base = definition_fingerprint(previous)
    other = detach_child(previous)
    move(other, interior_vertex(previous), (0.01, 0.0, 0.0))
    apply_child_edit(parent.session, parent.definition_id, other, base_fingerprint=base)
    after_first = definition_fingerprint(stored(parent))
    mine = detach_child(previous)
    move(mine, interior_vertex(previous), (0.0, 0.01, 0.0))
    with pytest.raises(SubmodelError) as raised:
        apply_child_edit(parent.session, parent.definition_id, mine, base_fingerprint=base)
    assert raised.value.code == "SUBMODEL_EDIT_CONFLICT"
    assert definition_fingerprint(stored(parent)) == after_first


def test_apply_revalidates_the_stored_definition_right_before_the_command(parent, monkeypatch):
    from anyfem import submodel_child_edit as module
    from anyfem.submodel_design import ReplaceSubmodelDefinition

    previous = stored(parent)
    real, done = module.validate_child_edit, []

    def racing(current, edited):
        outcome = real(current, edited)
        if not done:  # another edit lands after validation but before the command
            done.append(True)
            other = detach_child(current)
            move(other, interior_vertex(current), (0.01, 0.0, 0.0))
            parent.session.execute(ReplaceSubmodelDefinition(current, real(current, other).definition),
                                   solver_affecting=False)
        return outcome

    monkeypatch.setattr(module, "validate_child_edit", racing)
    mine = detach_child(previous)
    move(mine, interior_vertex(previous), (0.0, 0.01, 0.0))
    with pytest.raises(SubmodelError) as raised:
        apply_child_edit(parent.session, parent.definition_id, mine)
    assert raised.value.code == "SUBMODEL_EDIT_CONFLICT"
    assert np.allclose(stored(parent).child.geometry.vertex_position(interior_vertex(previous)),
                       np.array(previous.child.geometry.vertex_position(interior_vertex(previous))) + (0.01, 0.0, 0.0))


def test_a_refused_edit_leaves_the_stored_definition_and_revision(parent):
    previous = stored(parent)
    revision, before = parent.session.revision.id, definition_fingerprint(previous)
    edited = detach_child(previous)
    edited.geometry.add_point(5.0, 0.0, 0.0)
    with pytest.raises(SubmodelError):
        apply_child_edit(parent.session, parent.definition_id, edited)
    assert parent.session.revision.id == revision and definition_fingerprint(stored(parent)) == before


def test_read_only_and_missing_definitions_are_refused(parent):
    with pytest.raises(SubmodelError) as raised:
        apply_child_edit(SimpleNamespace(read_only=True, project=parent.project), parent.definition_id,
                         detach_child(stored(parent)))
    assert raised.value.code == "CHILD_READ_ONLY"
    with pytest.raises(SubmodelError) as raised:
        apply_child_edit(parent.session, "no-such-id", detach_child(stored(parent)))
    assert raised.value.code == "CHILD_DEFINITION_GONE"


def test_an_edited_definition_survives_save_and_reopen(parent):
    previous = stored(parent)
    edited = detach_child(previous)
    move(edited, interior_vertex(previous), (0.01, 0.01, 0.0))
    replacement = build_replacement(previous, edited)
    reopened = SubmodelDefinition.from_dict(replacement.to_dict())
    assert definition_fingerprint(reopened) == definition_fingerprint(replacement)
    assert reopened.source_faces == replacement.source_faces


# ----------------------------------------------------------------------
# Qt editor (real widgets)
# ----------------------------------------------------------------------
qt = pytest.mark.skipif(os.environ.get("ANYFEM_RUN_QT_TESTS") != "1", reason="set ANYFEM_RUN_QT_TESTS=1")


@pytest.fixture
def window(parent, monkeypatch, tmp_path):
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    from anyfem.io import recovery
    from anyfem.ui.qt.app import QtFemWindow

    monkeypatch.setenv("ANYFEM_JOB_EXECUTOR", "thread")
    monkeypatch.setattr(recovery, "default_recovery_root", lambda: tmp_path / "recovery")
    app = QApplication.instance() or QApplication([])
    win = QtFemWindow(parent.project, viewer_backend="software")
    win._qt_app = app
    try:
        yield win
    finally:
        from anyfem.ui.qt.submodel_child_editor import close_child_editors
        from anyfem.ui.qt.submodel_task import shutdown_submodel

        close_child_editors(win)
        shutdown_submodel(win)
        win.session.mark_saved()
        win.close()


@qt
def test_the_editor_applies_repeatedly_and_cannot_save_or_switch_document(window, parent):
    from anyfem.ui.qt.submodel_child_editor import child_editors, open_shell_child_editor

    definition_id = next(iter(window.project.submodels))
    editor = open_shell_child_editor(window, definition_id)
    assert open_shell_child_editor(window, definition_id) is editor and child_editors(window) == {definition_id: editor}
    assert editor.project is not window.project.submodels[definition_id].child
    project_before = editor.project
    editor.save_project()
    editor.new_project()
    assert editor.project is project_before
    previous = window.project.submodels[definition_id]
    move(editor.project, interior_vertex(previous), (0.01, 0.01, 0.0))
    assert editor.has_unapplied_changes()
    first = editor.apply_to_submodel()
    assert first is not None and not editor.has_unapplied_changes()
    move(editor.project, interior_vertex(first), (0.0, 0.01, 0.0))
    assert editor.apply_to_submodel() is not None  # a second edit/apply from the same window
    assert definition_fingerprint(window.project.submodels[definition_id]) != definition_fingerprint(first)


@qt
def test_the_editor_refuses_apply_after_the_submodel_changed_and_keeps_its_draft(window, parent):
    from anyfem.ui.qt.submodel_child_editor import open_shell_child_editor

    definition_id = next(iter(window.project.submodels))
    editor = open_shell_child_editor(window, definition_id)
    previous = window.project.submodels[definition_id]
    other = detach_child(previous)
    move(other, interior_vertex(previous), (0.01, 0.0, 0.0))
    apply_child_edit(window.session, definition_id, other)
    move(editor.project, interior_vertex(previous), (0.0, 0.01, 0.0))
    fingerprint = definition_fingerprint(window.project.submodels[definition_id])
    assert editor.stale_reason() is not None and editor.apply_to_submodel() is None
    assert definition_fingerprint(window.project.submodels[definition_id]) == fingerprint
    assert editor.has_unapplied_changes()


@qt
def test_the_editor_cannot_solve_or_publish_a_mesh_and_does_not_poll(window, parent):
    from anyfem.ui.qt.submodel_child_editor import open_shell_child_editor

    editor = open_shell_child_editor(window, next(iter(window.project.submodels)))
    before = (dict(editor.project.mesh_records), editor.project.mesh_numbering)
    for name in ("solve", "solve_async", "generate_mesh", "generate_mesh_async"):
        getattr(editor, name)()
    assert (dict(editor.project.mesh_records), editor.project.mesh_numbering) == before
    assert not hasattr(editor, "_poll")


@qt
def test_reopening_a_stale_editor_opens_the_current_draft_and_keeps_the_old_one(window, parent):
    from anyfem.ui.qt.submodel_child_editor import child_editors, open_shell_child_editor

    definition_id = next(iter(window.project.submodels))
    old = open_shell_child_editor(window, definition_id)
    previous = window.project.submodels[definition_id]
    other = detach_child(previous)
    move(other, interior_vertex(previous), (0.01, 0.0, 0.0))
    apply_child_edit(window.session, definition_id, other)
    move(old.project, interior_vertex(previous), (0.0, 0.01, 0.0))
    fresh = open_shell_child_editor(window, definition_id)
    assert fresh is not old and child_editors(window) == {definition_id: fresh}
    assert fresh.stale_reason(full=True) is None and old.has_unapplied_changes()
    assert old.apply_to_submodel() is None  # the outdated draft can be reviewed but never applied
    assert old.close_editor(force=True) and child_editors(window) == {definition_id: fresh}


@qt
def test_closing_the_editor_unregisters_it(window, parent):
    from anyfem.ui.qt.submodel_child_editor import child_editors, open_shell_child_editor

    definition_id = next(iter(window.project.submodels))
    editor = open_shell_child_editor(window, definition_id)
    assert editor.close_editor(force=True) and child_editors(window) == {}
