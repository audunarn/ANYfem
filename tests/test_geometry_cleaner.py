"""Project attachment, history, document lifecycle and refusal coverage."""
from copy import deepcopy

import numpy as np
import pytest

from anyfem.commands import AddPoint, CommandStack
from anyfem.document import DocumentSession
from anyfem.geometry_cleaner import (review_project, preview_project_repair,
    apply_project_repair, command_for_project_repair, repair_disabled_reason)
from anyfem.model.project import Project
from anyfem.model.attributes import Support
from anyfem.io.project_file import project_to_dict, project_from_dict
from anygeometry.entities import EntityRef
from anygeometry.errors import GeometryError


def content(document):
    result = deepcopy(document)
    result["geometry"].pop("revision", None)
    result["geometry"].pop("checksum", None)
    return result


def removal(project, vertex):
    report = review_project(project)
    item = next(f for f in report.findings if f.kind == 'unused_vertex' and f.refs[0].id == vertex)
    return preview_project_repair(project, report, finding_ids=[item.id])


def test_one_command_exact_undo_redo_and_save_reload():
    project = Project()
    a = project.geometry.add_point(.12, .23)
    b = project.geometry.add_point(1, 2)
    stack = CommandStack(project)
    before = project_to_dict(project)
    plan = removal(project, a)
    assert a in project.geometry.vertices
    changes = apply_project_repair(stack, plan)
    assert len(changes) == 1 and stack.history() == ['clean geometry']
    after = project_to_dict(project)
    assert a not in project.geometry.vertices and b in project.geometry.vertices
    assert stack.undo()
    assert content(project_to_dict(project)) == content(before)
    assert stack.redo()
    assert content(project_to_dict(project)) == content(after)
    restored = project_from_dict(deepcopy(after))
    assert project_to_dict(restored) == after


def test_document_session_lifecycle_and_noop_bypasses_history():
    project = Project()
    vertex = project.geometry.add_point(.12, .23)
    session = DocumentSession(project)
    initial_revision = session.revision
    report = review_project(project)
    assert session.revision == initial_revision and not session.dirty
    empty = preview_project_repair(project, report)
    assert command_for_project_repair(project, empty) is None
    assert apply_project_repair(session.commands, empty) == ()
    assert not session.commands.can_undo
    plan = removal(project, vertex)
    session.execute(command_for_project_repair(project, plan))
    assert session.dirty and session.revision != initial_revision
    session.undo()
    assert vertex in project.geometry.vertices
    session.redo()
    assert vertex not in project.geometry.vertices


def test_attached_unused_point_refuses_removal_and_keeps_everything():
    project = Project()
    vertex = project.geometry.add_point(0, 0)
    support = Support('fixed', EntityRef('vertex', vertex), {'ux': 0.})
    project.supports.append(support)
    before = deepcopy(project.supports)
    revision = project.geometry.revision
    with pytest.raises(GeometryError, match='attachments'):
        removal(project, vertex)
    assert project.supports == before and project.geometry.revision == revision


def test_new_attachment_after_preview_refuses_before_mutation():
    project = Project()
    vertex = project.geometry.add_point(0, 0)
    plan = removal(project, vertex)
    project.supports.append(Support('fixed', EntityRef('vertex', vertex), {'ux': 0.}))
    stack = CommandStack(project)
    before = project.geometry.revision
    with pytest.raises(GeometryError, match='attachments'):
        apply_project_repair(stack, plan)
    assert vertex in project.geometry.vertices and project.geometry.revision == before
    assert not stack.can_undo


def test_snapping_retains_attachment_identity_and_attributes():
    project = Project()
    vertex = project.geometry.add_point(.11, .21)
    support = Support('fixed', EntityRef('vertex', vertex), {'ux': 0.})
    project.supports.append(support)
    report = review_project(project)
    plan = preview_project_repair(project, report, snap_vertices=[vertex], grid_size=.1, max_displacement=.02)
    stack = CommandStack(project)
    apply_project_repair(stack, plan)
    assert project.supports == [support]
    assert np.allclose(project.geometry.vertex_position(vertex), [.1, .2, 0])
    stack.undo()
    assert np.allclose(project.geometry.vertex_position(vertex), [.11, .21, 0])
    assert project.supports == [support]
    stack.redo()
    assert project.supports == [support]


@pytest.mark.parametrize('flag,value', [('mesh_only', True), ('read_only_reason', 'locked'),
                                      ('geometry_editing_disabled_reason', 'recovered')])
def test_project_refusals(flag, value):
    project = Project()
    vertex = project.geometry.add_point(0, 0)
    plan = removal(project, vertex)
    setattr(project, flag, value)
    assert repair_disabled_reason(project)
    with pytest.raises(PermissionError):
        command_for_project_repair(project, plan)
    assert vertex in project.geometry.vertices


def test_session_readonly_flag_and_stale_plan_refuse():
    project = Project()
    vertex = project.geometry.add_point(0, 0)
    plan = removal(project, vertex)
    with pytest.raises(PermissionError):
        command_for_project_repair(project, plan, read_only=True)
    project.geometry.add_point(1, 0)
    with pytest.raises(GeometryError, match='stale'):
        command_for_project_repair(project, plan)


def test_real_authored_features_review_but_refuse_cleanup():
    project = Project()
    stack = CommandStack(project)
    stack.run(AddPoint(.11, .21))
    before = project_to_dict(project)
    report = review_project(project)
    assert report.findings
    with pytest.raises(PermissionError, match='Feature'):
        preview_project_repair(project, report, snap_vertices=[1], grid_size=.1, max_displacement=.02)
    assert content(project_to_dict(project)) == content(before)
    assert len(stack.history()) == 1


def test_failed_command_rollback_keeps_attachments_and_history(monkeypatch):
    project = Project()
    a, b = project.geometry.add_point(0, 0), project.geometry.add_point(1, 0)
    report = review_project(project)
    plan = preview_project_repair(project, report, finding_ids=[f.id for f in report.findings if f.kind == 'unused_vertex'])
    original = project.geometry.remove_vertex
    calls = []
    def fail(identifier, **kwargs):
        calls.append(identifier)
        if len(calls) == 2:
            raise GeometryError('injected failure')
        return original(identifier, **kwargs)
    monkeypatch.setattr(project.geometry, 'remove_vertex', fail)
    stack = CommandStack(project)
    with pytest.raises(GeometryError, match='injected'):
        apply_project_repair(stack, plan)
    assert set(project.geometry.vertices) == {a, b}
    assert not stack.can_undo


def test_query_region_and_manual_region_prevent_destructive_cleanup():
    from anyfem.model.regions import Region, ManualRegion, QueryRegion, QueryGroup, QueryClause
    project = Project()
    vertex = project.geometry.add_point(0, 0)
    manual = Region('point', 'geometry', 'vertex', ManualRegion((EntityRef('vertex', vertex),)))
    project.regions.add(manual)
    with pytest.raises(GeometryError, match='anchors'):
        removal(project, vertex)
    project.regions.remove(manual.id)
    query = Region('all vertices', 'geometry', 'vertex',
                   QueryRegion(QueryGroup(clauses=(QueryClause('kind', 'eq', 'vertex'),))))
    project.regions.add(query)
    with pytest.raises(GeometryError, match='anchors'):
        removal(project, vertex)


def test_full_project_diagnosis_retains_missing_anchors_and_regions():
    from anyfem.geometry_cleaner import diagnose_project
    from anyfem.model.regions import Region, ManualRegion
    project = Project()
    project.supports.append(Support('missing', EntityRef('vertex', 999), {'ux': 0.}))
    for identifier in (10, 20):
        project.regions.add(Region(f'missing {identifier}', 'geometry', 'vertex', ManualRegion((EntityRef('vertex', identifier),))))
    report = diagnose_project(project)
    assert not report.complete and not report.clean
    assert len([item for item in report.findings if item.kind == 'missing_project_anchor']) == 3
    assert len([item for item in report.findings if item.kind == 'unresolved_project_region']) == 2


def test_mesh_only_full_diagnosis_is_structured_unsupported_report():
    from anyfem.geometry_cleaner import diagnose_project
    project = Project(mesh_only=True)
    report = diagnose_project(project)
    assert not report.complete and not report.clean
    assert any(item.kind == 'mesh_only' for item in report.findings)


def test_full_diagnosis_of_valid_authored_feature_keeps_history_readonly():
    from anyfem.geometry_cleaner import diagnose_project
    project = Project()
    stack = CommandStack(project)
    stack.run(AddPoint(1, 2))
    revision, history = project.geometry.revision, stack.history()
    report = diagnose_project(project)
    assert report.complete
    assert any(check.name == 'feature_intent' and check.status == 'completed' for check in report.checks)
    assert project.geometry.revision == revision and stack.history() == history
