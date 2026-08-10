"""Transactional contracts for the trusted embedded scripting workflow."""

from __future__ import annotations

import json
import time

import pytest

from anyfem import commands as cmd
from anyfem.document import DocumentSession
from anyfem.io.project_file import project_to_dict
from anyfem.model.project import Project
from anyfem.scripting import (
    ScriptCancelled,
    ScriptConflictError,
    ScriptExecutionError,
    ScriptRunner,
    ScriptValidationError,
)
from anyfem.selection import Selection
from anygeometry.entities import EntityRef


def _bytes(project: Project) -> bytes:
    return json.dumps(
        project_to_dict(project),
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def test_script_executes_on_a_clone_then_commits_as_one_undo_item():
    project = Project("script")
    project.load_case("LC")
    selection = Selection("vertex")
    session = DocumentSession(project, selection=selection)
    before = _bytes(project)

    with ScriptRunner(session) as runner:
        task = runner.submit(
            """
print(project.name)
print('diagnostic', file=__import__('sys').stderr)
point = commands.run(commands.AddPoint(1.0, 2.0, 3.0))
selection.select(project.point(point))
analyses['check'] = __import__('anyfem').AnalysisDefinition('Script check', id='check')
result = {'point': point, 'jobs': len(jobs), 'meshes': len(meshes)}
"""
        )
        proposal = task.result(timeout=5.0)

        # Worker completion alone never leaks a partial edit into the GUI.
        assert _bytes(project) == before
        assert selection.items == []
        assert proposal.stdout == "script\n"
        assert proposal.stderr == "diagnostic\n"
        assert proposal.return_value == {"point": 1, "jobs": 0, "meshes": 0}

        outcome = runner.commit(proposal, label="parametric point")

    assert outcome.committed
    assert outcome.project_changed
    assert project.geometry.vertices[1].position == pytest.approx((1, 2, 3))
    assert selection.items == [EntityRef("vertex", 1)]
    assert project.analyses["check"].name == "Script check"
    assert project.load_cases["LC"]._region_factory.__self__ is project
    assert session.commands.history() == ["parametric point"]

    assert session.undo()
    assert _bytes(project) == before
    assert selection.items == []
    assert project.load_cases["LC"]._region_factory.__self__ is project
    assert session.redo()
    assert project.geometry.vertices[1].position == pytest.approx((1, 2, 3))
    assert selection.items == [EntityRef("vertex", 1)]
    assert project.load_cases["LC"]._region_factory.__self__ is project


def test_script_failure_keeps_project_selection_and_history_byte_exact():
    project = Project("failure")
    first = project.geometry.add_point(0, 0, 0)
    selection = Selection("vertex")
    selection.select(project.point(first))
    session = DocumentSession(project, selection=selection)
    before = _bytes(project)
    selected_before = selection.ordered_items

    with ScriptRunner(session) as runner:
        with pytest.raises(ScriptExecutionError) as caught:
            runner.run(
                "commands.run(commands.AddPoint(9, 9, 9))\n"
                "selection.clear()\n"
                "print('made working edit')\n"
                "raise RuntimeError('reject me')",
            )

    assert "reject me" in str(caught.value)
    assert caught.value.stdout == "made working edit\n"
    assert "RuntimeError" in caught.value.traceback_text
    assert _bytes(project) == before
    assert selection.ordered_items == selected_before
    assert session.commands.history() == []
    assert session.revision.sequence == 0


def test_invalid_direct_topology_is_rejected_before_commit():
    project = Project("invalid")
    first = project.geometry.add_point(0, 0, 0)
    second = project.geometry.add_point(1, 0, 0)
    project.geometry.add_line(first, second)
    session = DocumentSession(project)
    before = _bytes(project)

    with ScriptRunner(session) as runner:
        with pytest.raises(ScriptValidationError) as caught:
            runner.run("del project.geometry.vertices[1]")

    assert "topology" in str(caught.value).lower()
    assert _bytes(project) == before
    assert session.commands.history() == []


def test_script_cannot_commit_a_dangling_geometry_selection():
    project = Project("selection validation")
    point = project.geometry.add_point(0, 0, 0)
    selection = Selection("vertex")
    selection.select(project.point(point))
    session = DocumentSession(project, selection=selection)
    before = _bytes(project)

    with ScriptRunner(session) as runner:
        with pytest.raises(ScriptValidationError) as caught:
            runner.run("project.geometry.remove_vertex(1)")

    assert "selection references missing" in str(caught.value)
    assert _bytes(project) == before
    assert selection.items == [EntityRef("vertex", 1)]


def test_cancellation_interrupts_python_code_and_never_commits():
    project = Project("cancel")
    session = DocumentSession(project)
    before = _bytes(project)

    with ScriptRunner(session) as runner:
        task = runner.submit(
            "commands.run(commands.AddPoint(1, 2, 3))\n"
            "while True:\n"
            "    pass\n"
        )
        deadline = time.monotonic() + 2.0
        while not task.running() and time.monotonic() < deadline:
            time.sleep(0.001)
        task.cancel()
        with pytest.raises(ScriptCancelled):
            task.result(timeout=5.0)

    assert _bytes(project) == before
    assert session.commands.history() == []


def test_intervening_model_edit_causes_fail_closed_conflict():
    project = Project("conflict")
    session = DocumentSession(project)

    with ScriptRunner(session) as runner:
        proposal = runner.run(
            "commands.run(commands.AddPoint(1, 0, 0))", commit=False
        )
        session.execute(cmd.AddPoint(2, 0, 0))
        intervening = _bytes(project)

        with pytest.raises(ScriptConflictError):
            runner.commit(proposal)

    assert _bytes(project) == intervening
    assert len(project.geometry.vertices) == 1
    assert project.geometry.vertices[1].position == pytest.approx((2, 0, 0))
    assert session.commands.history() == ["add point"]


def test_read_only_sessions_can_inspect_but_cannot_commit_changes():
    project = Project("read only")
    session = DocumentSession(project)
    session.read_only = True

    with ScriptRunner(session) as runner:
        # Inspection is safe because it only touches the worker clone.
        proposal = runner.run("print(project.name)", commit=False)
        assert proposal.stdout == "read only\n"
        with pytest.raises(PermissionError):
            runner.commit(proposal)


def test_noop_script_has_output_but_creates_no_revision_or_undo_item():
    session = DocumentSession(Project("inspect"))
    with ScriptRunner(session) as runner:
        outcome = runner.run("print(len(project.geometry.vertices))")

    assert outcome.stdout == "0\n"
    assert not outcome.committed
    assert session.revision.sequence == 0
    assert session.commands.history() == []


def test_mesh_working_copy_is_aggregate_undoable_state():
    session = DocumentSession(Project("mesh script"))
    session.mesh_cache["active"] = {"quality": [0.9]}

    with ScriptRunner(session) as runner:
        outcome = runner.run(
            "meshes['active']['quality'].append(0.95)\n"
            "result = tuple(meshes['active']['quality'])"
        )

    assert outcome.committed
    assert not outcome.project_changed
    assert outcome.meshes_changed
    assert outcome.return_value == (0.9, 0.95)
    assert session.mesh_cache["active"]["quality"] == [0.9, 0.95]
    assert session.commands.history() == ["run script"]
    assert session.undo()
    assert session.mesh_cache["active"]["quality"] == [0.9]
