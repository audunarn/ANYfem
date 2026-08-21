"""Typed, persistent output-request contracts."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import FrozenInstanceError, replace

import pytest

from anyfem import AnalysisDefinition, DocumentSession, OutputRequest, Project
from anyfem import commands as cmd
from anyfem.io.project_file import load_project, project_from_dict, project_to_dict, save_project
from anyfem.jobs import analysis_hash
from anyfem.model import ManualRegion, Region
from anyfem.model.project import ProjectError
from anyfem.ui.definitions import output_request_from_values
from anyfem.ui.tree import ModelTree


def scoped_project(name: str = "outputs"):
    project = Project(name)
    point_id = project.geometry.add_point(0.0, 0.0, 0.0)
    region = project.regions.add(
        Region(
            "Inspection point",
            "geometry",
            "vertex",
            ManualRegion((project.point(point_id),)),
        )
    )
    return project, region


def displacement_request(region, **changes):
    values = {
        "quantity_keys": ("displacement",),
        "region": region,
        "location": "node",
        "label": "Deck movement",
    }
    values.update(changes)
    return OutputRequest(**values)


def test_output_request_is_immutable_and_requires_canonical_scope():
    project, region = scoped_project()
    request = displacement_request(region=region.id)
    assert request.region.id == region.id
    with pytest.raises(FrozenInstanceError):
        request.label = "mutated"  # type: ignore[misc]
    with pytest.raises(ValueError, match="explicit result location"):
        OutputRequest.from_dict(
            {
                "quantity_keys": ["displacement"],
                "region": region.id,
            }
        )
    with pytest.raises(ProjectError, match="missing region"):
        project.add_output_request(
            displacement_request(region="missing-region")
        )


def test_v4_round_trip_preserves_request_and_analysis_uuid_links(tmp_path):
    project, region = scoped_project()
    request = project.add_output_request(
        displacement_request(
            region=region.id,
            recovery="patch",
            reduction="average",
            frame_policy="last",
        )
    )
    analysis = project.add_analysis(
        AnalysisDefinition(
            "Service check",
            output_request_ids=(request.id,),
        )
    )

    restored = load_project(save_project(project, tmp_path / "outputs.anyfem"))
    assert restored.output_requests[request.id] == request
    assert restored.analyses[analysis.id].output_request_ids == (request.id,)
    assert restored.output_request_provenance(analysis.id) == (request.to_dict(),)
    serialized = project_to_dict(restored)
    assert serialized["output_requests"][0]["region"] == region.id


def test_legacy_migration_is_deterministic_and_never_invents_missing_intent():
    project, region = scoped_project("legacy outputs")
    analysis = project.add_analysis(AnalysisDefinition("Legacy static"))
    legacy = project_to_dict(project)
    legacy["anyfem"]["format"] = 3
    legacy["anyfem"].pop("document_id")
    legacy.pop("ownership")
    legacy["meshing"].pop("native_backend")
    legacy.pop("output_requests")
    entry = legacy["analyses"][0]
    entry["output_request_ids"] = []
    entry["output_requests"] = {
        "displacement": {
            "region_id": region.id,
            "location": "node",
            "recovery": "native",
        },
        # No scope/location: preserving this unresolved legacy instruction is
        # safer than silently assuming every node in the model.
        "stress": True,
    }

    first = project_from_dict(deepcopy(legacy))
    second = project_from_dict(deepcopy(legacy))
    assert tuple(first.output_requests) == tuple(second.output_requests)
    request_id = next(iter(first.output_requests))
    assert first.output_requests[request_id].quantity_keys == ("displacement",)
    migrated = first.analyses[analysis.id]
    assert migrated.output_request_ids == (request_id,)
    assert migrated.output_requests == {"stress": True}
    with pytest.raises(ProjectError, match="legacy output request data"):
        first.validate(require_loads=False, require_supports=False)

    no_intent = deepcopy(legacy)
    no_intent["analyses"][0]["output_requests"] = {}
    plain = project_from_dict(no_intent)
    assert plain.output_requests == {}
    assert plain.analyses[analysis.id].output_request_ids == ()


def test_request_semantics_change_analysis_hash_only_and_labels_do_not():
    project, region = scoped_project("hashes")
    request = project.add_output_request(displacement_request(region=region.id))
    analysis = project.add_analysis(
        AnalysisDefinition("Named result", output_request_ids=(request.id,))
    )
    session = DocumentSession(project)
    baseline_model = session.revision.model_hash
    baseline_analysis = analysis_hash(
        analysis,
        project.output_requests,
        document=project_to_dict(project),
    )

    renamed = replace(request, label="Engineer-facing label")
    with session.transaction("rename request"):
        project.update_output_request(renamed)
    assert session.revision.model_hash == baseline_model
    assert analysis_hash(
        analysis,
        project.output_requests,
        document=project_to_dict(project),
    ) == baseline_analysis

    reduced = replace(renamed, reduction="envelope")
    with session.transaction("change reduction"):
        project.update_output_request(reduced)
    assert session.revision.model_hash == baseline_model
    assert analysis_hash(
        analysis,
        project.output_requests,
        document=project_to_dict(project),
    ) != baseline_analysis


def test_add_edit_delete_commands_restore_uuid_and_analysis_links():
    project, region = scoped_project("commands")
    analysis = project.add_analysis(AnalysisDefinition("Static"))
    request = displacement_request(region=region.id)
    stack = cmd.CommandStack(project)

    stack.run(cmd.AddOutputRequest(request, (analysis.id,)))
    assert project.analyses[analysis.id].output_request_ids == (request.id,)
    stack.undo()
    assert request.id not in project.output_requests
    assert project.analyses[analysis.id].output_request_ids == ()
    stack.redo()
    assert project.output_requests[request.id] is request

    edited = replace(request, label="Edited", frame_policy="last")
    stack.run(cmd.EditOutputRequest(request.id, edited))
    assert project.output_requests[request.id] == edited
    stack.undo()
    assert project.output_requests[request.id] is request
    stack.redo()

    stack.run(cmd.DeleteOutputRequest(request.id))
    assert request.id not in project.output_requests
    assert project.analyses[analysis.id].output_request_ids == ()
    stack.undo()
    assert project.output_requests[request.id] == edited
    assert project.analyses[analysis.id].output_request_ids == (request.id,)


def test_unresolved_scope_and_unavailable_analysis_quantity_block_validation():
    project, region = scoped_project("validation")
    request = project.add_output_request(
        OutputRequest(
            ("stress.von_mises",),
            region.id,
            "element",
            label="Stress",
        )
    )
    analysis = project.add_analysis(
        AnalysisDefinition(
            "Free-free modes",
            type="modal",
            target_kind="none",
            output_request_ids=(request.id,),
        )
    )
    with pytest.raises(ProjectError, match="unavailable for analysis 'modal'"):
        project.validate(require_loads=False, require_supports=False)

    project.analyses[analysis.id] = replace(
        analysis, type="linear_static"
    )
    project.regions.remove(region.id)
    with pytest.raises(ProjectError, match="unresolved region"):
        project.validate(require_loads=False, require_supports=False)

    tree = object.__new__(ModelTree)
    tree.project = project
    tree._region_candidate_cache = {}
    assert tree._output_request_status(request) == "unresolved"


def test_details_parser_and_add_command_fail_closed_for_wrong_family():
    project, region = scoped_project("details")
    request = output_request_from_values(
        "Motion histories",
        "velocity, acceleration",
        region.id,
        "node",
        frame_policy="all",
    )
    modal = project.add_analysis(
        AnalysisDefinition("Modes", type="modal", target_kind="none")
    )
    with pytest.raises(ProjectError, match="cannot be attached"):
        cmd.AddOutputRequest(request, (modal.id,)).do(project)
    assert request.id not in project.output_requests
