"""Bounded headless qualification of the latest v7 compatibility boundary."""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from types import SimpleNamespace

import pytest

from anygeometry import EntityRef, GeometryModel, extract_model_closure
from anygeometry.serialization import to_dict as geometry_to_dict
from anymesher.hybrid import generate_hybrid_mesh
from anysolver import audit_constraints

from anyfem import Project, ProjectError, steel
from anyfem.document import DocumentSession
from anyfem.io.artifacts import ArtifactStore
from anyfem.io.project_file import FORMAT_VERSION, project_from_dict, project_to_dict
from anyfem.mesh_jobs import (
    MeshJobResult,
    MeshSettings,
    MeshTaskManager,
    mesh_semantic_hash,
)
from anyfem.model.records import MeshRecord
from anyfem.model.sections import BeamSection
from anyfem.native_meshing import CertificationMode, MeshBackend, NativeMeshSettings
from anyfem.native_meshing_backend import NativeProjectMeshingSession
from anyfem.solve.build import build_fe_model
from anyfem.structural_preparation import (
    StructuralPreparationError,
    prepare_structural_connectivity,
    remap_mesh_to_source,
    source_work_mapping,
)


def _resign_geometry(document: dict) -> None:
    """Re-sign a deliberately manufactured legacy geometry document."""

    payload = {key: value for key, value in document.items() if key != "checksum"}
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    document["checksum"] = {
        "algorithm": "sha256",
        "value": hashlib.sha256(canonical).hexdigest(),
    }


def _legacy_corrupt_payload(*, unresolved_scope: bool = False) -> tuple[dict, dict]:
    project = Project("legacy pending feature")
    face = project.geometry.add_plate(
        project.geometry.add_points(
            ((0.0, 0.0, 0.0), (2.0, 0.0, 0.0), (2.0, 1.0, 0.0), (0.0, 1.0, 0.0))
        )
    )
    project.add_material(steel())
    project.add_plate_section("plate", 0.01, "S355")
    project.assign_plate(face, "plate")
    payload = project_to_dict(project)
    payload["anyfem"]["format"] = 6
    payload.pop("ownership")
    corrupt_history = {
        "version": 1,
        "next_id": 8,
        "baseline": None,
        "records": [
            {
                "id": 7,
                "kind": "geometry.plate",
                "kind_version": 1,
                "name": "Detached Add Plate",
                # These exact inputs describe the materialized face, but a
                # recovery must still not infer the missing persistent output.
                "parameters": {},
                "inputs": {
                    "vertices": [
                        {"entity": ["vertex", identifier]}
                        for identifier in (1, 2, 3, 4)
                    ]
                },
                "dependencies": [],
                "suppressed": False,
                "outputs": {},
                "state": "pending",
                "diagnostic": None,
                "materialization_checksum": None,
            }
        ],
    }
    payload["geometry"]["features"] = deepcopy(corrupt_history)
    _resign_geometry(payload["geometry"])
    if unresolved_scope:
        # This was the intended plate output, but it was never committed to
        # the old detached record.  The v7 frozen Base Geometry receives a new
        # monotonic feature ID and must not capture this target implicitly.
        payload["regions"][0]["definition"]["anchors"][0] = {
            "type": "feature_output",
            "feature_id": 7,
            "output_key": "face",
            "kind": "face",
        }
    return payload, corrupt_history


def _entity_materialization(geometry: GeometryModel) -> dict[str, object]:
    document = geometry_to_dict(geometry, include_features=False)
    return {
        key: value
        for key, value in document.items()
        if key not in {"checksum", "id_state", "model_id", "revision"}
    }


def test_v6_detached_record_recovers_exact_frozen_topology_and_archive() -> None:
    payload, corrupt_history = _legacy_corrupt_payload()
    clean_v7 = deepcopy(payload)
    clean_v7["anyfem"]["format"] = FORMAT_VERSION
    clean_v7["ownership"] = {"sheet_joins": []}
    clean_v7["geometry"]["features"] = {
        "version": 1,
        "next_id": 1,
        "baseline": None,
        "records": [],
    }
    _resign_geometry(clean_v7["geometry"])
    expected = _entity_materialization(project_from_dict(clean_v7).geometry)

    recovered = project_from_dict(payload)

    assert _entity_materialization(recovered.geometry) == expected
    assert recovered.archived_feature_histories[-1]["history"] == corrupt_history
    assert recovered.geometry_editing_disabled_reason
    assert recovered.read_only_reason is None
    records = recovered.geometry.features.records
    assert len(records) == 1
    record = records[0]
    assert record.feature_id == 8
    assert record.kind == "anyfem.frozen.base_geometry"
    assert record.state == "frozen"
    assert record.materialization_checksum
    assert set(record.outputs.values()) == {
        EntityRef(kind, identifier)
        for kind, store in (
            ("vertex", recovered.geometry.vertices),
            ("edge", recovered.geometry.edges),
            ("face", recovered.geometry.faces),
        )
        for identifier in store
    }
    recovered.validate(require_loads=False, require_supports=False)

    persisted = project_to_dict(recovered)
    reopened = project_from_dict(deepcopy(persisted))
    assert persisted["anyfem"]["format"] == FORMAT_VERSION == 7
    assert reopened.archived_feature_histories == recovered.archived_feature_histories
    assert _entity_materialization(reopened.geometry) == expected
    assert reopened.geometry.features.records[0].outputs == record.outputs
    assert (
        reopened.geometry.features.records[0].materialization_checksum
        == record.materialization_checksum
    )
    assert reopened.read_only_reason is None


def test_recovered_valid_scope_is_usable_but_missing_exact_scope_fails_closed() -> None:
    valid_payload, _ = _legacy_corrupt_payload()
    valid = project_from_dict(valid_payload)
    assert valid.read_only_reason is None
    valid.validate(require_loads=False, require_supports=False)
    valid_mesh = valid.generate_mesh(0.5)
    assert valid_mesh.elements_of_face[1]

    unresolved_payload, _ = _legacy_corrupt_payload(unresolved_scope=True)
    unresolved = project_from_dict(unresolved_payload)

    assert unresolved.read_only_reason is not None
    assert "feature 7" in unresolved.read_only_reason
    # The exact face described by the corrupt feature's inputs exists, but its
    # absent output identity is never repaired by topology or proximity.
    assert set(unresolved.geometry.faces) == {1}
    assert unresolved.face_sections == {}
    with pytest.raises(ValueError, match="compatibility recovery is blocking"):
        unresolved.validate(require_loads=False, require_supports=False)
    with pytest.raises(ProjectError, match="compatibility recovery is blocked"):
        unresolved.generate_mesh(0.5)


def _crossing_sheets() -> tuple[GeometryModel, int, int, int, int]:
    geometry = GeometryModel()
    horizontal = geometry.add_plate(
        geometry.add_points(
            ((-1.0, -1.0, 0.0), (1.0, -1.0, 0.0), (1.0, 1.0, 0.0), (-1.0, 1.0, 0.0))
        )
    )
    vertical = geometry.add_plate(
        geometry.add_points(
            ((-1.0, 0.0, -1.0), (1.0, 0.0, -1.0), (1.0, 0.0, 1.0), (-1.0, 0.0, 1.0))
        )
    )
    first_sheet = geometry.add_sheet((horizontal,), name="horizontal")
    second_sheet = geometry.add_sheet((vertical,), name="vertical")
    return geometry, horizontal, vertical, first_sheet, second_sheet


def _crossing_project() -> tuple[Project, int, int]:
    geometry, first_face, second_face, _first_sheet, _second_sheet = _crossing_sheets()
    project = Project("crossing plates", geometry=geometry)
    project.add_material(steel())
    project.add_plate_section("plate", 0.01, "S355")
    project.assign_plates((first_face, second_face), "plate")
    return project, first_face, second_face


def test_crossing_plates_prepare_only_on_detached_closure() -> None:
    source, first_face, second_face, first_sheet, second_sheet = _crossing_sheets()
    before = geometry_to_dict(source)
    closure = extract_model_closure(
        source,
        (source.handle("sheet", first_sheet), source.handle("sheet", second_sheet)),
        include_structural_closure=True,
        include_features=False,
    )

    report = prepare_structural_connectivity(
        closure.working_model,
        source_model_id=str(closure.source_model_id),
        source_revision=closure.source_revision,
    )
    mapping = source_work_mapping(closure)

    assert geometry_to_dict(source) == before
    assert report.created_count >= 1
    assert any(item.intersection == "cross" for item in report.connections)
    assert len(mapping[f"face:{first_face}"]) == 2
    assert len(mapping[f"face:{second_face}"]) == 2
    work = closure.working_model
    assert set(work.sheets) == {first_sheet, second_sheet}
    shared = {
        edge_id
        for edge_id in work.edges
        if {
            work.face_uses[item].sheet_id
            for item in work.face_uses_using_edge(edge_id)
        }
        == {first_sheet, second_sheet}
    }
    assert len(shared) == 1
    assert work.validate_topology() == ()


def test_crossing_beams_prepare_one_declared_junction_on_detached_closure() -> None:
    source = GeometryModel()
    first = source.add_member(
        (source.add_line(*source.add_points(((-1, 0, 0), (1, 0, 0)))),),
        name="first",
    )
    second = source.add_member(
        (source.add_line(*source.add_points(((0, -1, 0), (0, 1, 0)))),),
        name="second",
    )
    before = geometry_to_dict(source)
    closure = extract_model_closure(
        source,
        (source.handle("member", first), source.handle("member", second)),
        include_structural_closure=True,
        include_features=False,
    )

    report = prepare_structural_connectivity(closure.working_model)

    assert geometry_to_dict(source) == before
    assert report.created_count == 1
    assert len(closure.working_model.junctions) == 1
    assert all(
        len(closure.working_model.members[item].edge_use_ids) == 2
        for item in (first, second)
    )
    assert closure.working_model.validate_topology() == ()


def test_crossing_beam_project_remaps_axes_and_builds_without_mpc_cycles() -> None:
    geometry = GeometryModel()
    first_edge = geometry.add_line(
        *geometry.add_points(((-1.0, 0.0, 0.0), (1.0, 0.0, 0.0)))
    )
    second_edge = geometry.add_line(
        *geometry.add_points(((0.0, -1.0, 0.0), (0.0, 1.0, 0.0)))
    )
    project = Project("crossing beams", geometry=geometry)
    project.add_material(steel())
    project.add_beam_section(
        BeamSection(
            name="flat bar",
            profile="Flatbar",
            material="S355",
            flange_width=0.1,
            flange_thickness=0.01,
        )
    )
    project.assign_beams((first_edge, second_edge), "flat bar")
    before = geometry_to_dict(project.geometry)

    mesh = project.generate_mesh(0.5)
    built = build_fe_model(
        project,
        mesh,
        load_case=None,
        require_loads=False,
        require_supports=False,
    )
    audit = audit_constraints(built.fe_model)

    assert geometry_to_dict(project.geometry) == before
    assert {first_edge, second_edge} <= set(mesh.elements_of_edge)
    assert set(mesh.nodes_on(EntityRef("edge", first_edge))) & set(
        mesh.nodes_on(EntityRef("edge", second_edge))
    )
    assert project._last_mesh_preparation["created_count"] == 1
    assert not [issue for issue in audit.issues if issue.code == "CONSTRAINT003"]


def test_beam_shell_prepares_declared_attachment_on_detached_closure() -> None:
    source = GeometryModel()
    face = source.add_plate(
        source.add_points(((-1, -1, 0), (1, -1, 0), (1, 1, 0), (-1, 1, 0)))
    )
    sheet = source.add_sheet((face,), name="plate")
    member = source.add_member(
        (source.add_line(*source.add_points(((0, 0, -1), (0, 0, 1)))),),
        name="through member",
    )
    before = geometry_to_dict(source)
    closure = extract_model_closure(
        source,
        (source.handle("sheet", sheet), source.handle("member", member)),
        include_structural_closure=True,
        include_features=False,
    )

    report = prepare_structural_connectivity(closure.working_model)

    assert geometry_to_dict(source) == before
    assert report.created_count >= 1
    assert len(closure.working_model.attachments) == 1
    assert len(closure.working_model.junctions) == 1
    attachment = next(iter(closure.working_model.attachments.values()))
    assert attachment.member_id == member
    assert attachment.target_id == sheet
    assert closure.working_model.validate_topology() == ()


def test_prepared_mesh_associations_are_remapped_to_source_faces() -> None:
    source, first_face, second_face, first_sheet, second_sheet = _crossing_sheets()
    closure = extract_model_closure(
        source,
        (source.handle("sheet", first_sheet), source.handle("sheet", second_sheet)),
        include_structural_closure=True,
        include_features=False,
    )
    prepare_structural_connectivity(closure.working_model)
    mesh = generate_hybrid_mesh(
        closure.working_model,
        target_size=0.5,
        strategy="auto",
        certification_mode="interactive",
    )

    assert not {first_face, second_face} <= set(mesh.elements_of_face)
    remap_mesh_to_source(mesh, closure)

    assert {first_face, second_face} <= set(mesh.elements_of_face)
    assert mesh.geometry_model_id == closure.source_model_id
    assert mesh.geometry_revision == closure.source_revision
    first_nodes = set(mesh.nodes_on(EntityRef("face", first_face)))
    second_nodes = set(mesh.nodes_on(EntityRef("face", second_face)))
    assert first_nodes & second_nodes


def test_background_snapshot_mesh_keeps_live_geometry_immutable_and_reports_mapping() -> None:
    project, first_face, second_face = _crossing_project()
    session = DocumentSession(project)
    source_before = geometry_to_dict(project.geometry)
    revision_before = project.geometry.revision
    snapshot = session.snapshot()
    manager = MeshTaskManager()

    try:
        future = manager.submit(
            "detached-crossing",
            snapshot,
            MeshSettings.create(0.5, element_order="linear"),
        )
        future.result(timeout=10.0)
        completed = [event for event in manager.poll() if event.kind == "completed"]
    finally:
        manager.shutdown()

    assert len(completed) == 1
    result = completed[0].payload
    assert isinstance(result, MeshJobResult)
    assert geometry_to_dict(project.geometry) == source_before
    assert project.geometry.revision == revision_before
    assert project._last_mesh_preparation == {}
    report = result.structural_preparation
    assert report["created_count"] >= 1
    assert len(report["source_to_working"][f"face:{first_face}"]) == 2
    assert len(report["source_to_working"][f"face:{second_face}"]) == 2
    assert {first_face, second_face} <= set(result.mesh.elements_of_face)
    assert set(result.mesh.nodes_on(EntityRef("face", first_face))) & set(
        result.mesh.nodes_on(EntityRef("face", second_face))
    )


def test_mesh_hash_excludes_ephemeral_working_identity_and_is_repeatable() -> None:
    project, _first_face, _second_face = _crossing_project()
    session = DocumentSession(project)
    source_before = geometry_to_dict(project.geometry)

    first_mesh = project.generate_mesh(0.5)
    first_report = deepcopy(project._last_mesh_preparation)
    first_hash = mesh_semantic_hash(
        first_mesh,
        model_hash=session.revision.model_hash,
        mesh_input_hash="qualified-input",
        structural_preparation=first_report,
    )
    second_mesh = project.generate_mesh(0.5)
    second_report = deepcopy(project._last_mesh_preparation)
    second_hash = mesh_semantic_hash(
        second_mesh,
        model_hash=session.revision.model_hash,
        mesh_input_hash="qualified-input",
        structural_preparation=second_report,
    )

    assert first_report["working_model_id"] != second_report["working_model_id"]
    assert first_report["working_revision"] == second_report["working_revision"]
    assert first_hash == second_hash
    assert geometry_to_dict(project.geometry) == source_before


def test_mesh_record_and_artifact_round_trip_structural_preparation(tmp_path) -> None:
    project, _first_face, _second_face = _crossing_project()
    session = DocumentSession(project)
    mesh = project.generate_mesh(0.5)
    report = deepcopy(project._last_mesh_preparation)
    mesh_hash = mesh_semantic_hash(
        mesh,
        model_hash=session.revision.model_hash,
        mesh_input_hash="qualified-input",
        structural_preparation=report,
    )
    record = MeshRecord(
        name="crossing mesh",
        source_model_hash=session.revision.model_hash,
        mesh_input_hash="qualified-input",
        mesh_hash=mesh_hash,
        structural_preparation=report,
    )
    restored_record = MeshRecord.from_dict(record.to_dict())
    store = ArtifactStore(tmp_path / "model.anyfem")
    artifact = store.write_mesh(
        mesh,
        mesh_id=record.id,
        document_id=project.document_id,
        model_hash=session.revision.model_hash,
        mesh_hash=mesh_hash,
        structural_preparation=report,
    )

    metadata = store.read_mesh_metadata(artifact)

    assert restored_record.structural_preparation == report
    assert metadata["structural_preparation"] == report
    assert metadata["model_hash"] == session.revision.model_hash
    assert metadata["mesh_hash"] == mesh_hash


class _NoCancellation:
    def raise_if_cancelled(self, _stage: str) -> None:
        return None


def test_native_component_prepares_full_neighbour_closure_without_source_mutation() -> None:
    project, first_face, second_face = _crossing_project()
    first_sheet, second_sheet = sorted(project.geometry.sheets)
    component = project.geometry.handle("sheet", first_sheet)
    settings = NativeMeshSettings(target_size=0.5, backend="automatic")
    before = geometry_to_dict(project.geometry)

    with NativeProjectMeshingSession(project, settings) as session:
        snapshot = session.capture_component(component)
        assert set(snapshot.geometry.sheets) == {first_sheet, second_sheet}
        request = SimpleNamespace(
            cancellation=_NoCancellation(),
            snapshot=snapshot,
            component=component,
            settings=settings,
            controls=(),
            backend=MeshBackend.AUTOMATIC,
            certification_mode=CertificationMode.INTERACTIVE,
            changes=None,
        )
        result = session.generate_component(request)

    assert geometry_to_dict(project.geometry) == before
    assert first_face in result.mesh.elements_of_face
    assert second_face not in result.mesh.elements_of_face
    assert result.mesh.geometry_model_id == project.geometry.model_id
    assert result.mesh.geometry_revision == project.geometry.revision
    assert any("structural preparation:" in item for item in result.diagnostics)


def test_coplanar_overlap_blocks_without_mutating_source_or_working_copy() -> None:
    source = GeometryModel()
    first = source.add_plate(
        source.add_points(((0, 0, 0), (2, 0, 0), (2, 2, 0), (0, 2, 0)))
    )
    second = source.add_plate(
        source.add_points(((1, 0, 0), (3, 0, 0), (3, 2, 0), (1, 2, 0)))
    )
    first_sheet = source.add_sheet((first,), name="first")
    second_sheet = source.add_sheet((second,), name="second")
    source_before = geometry_to_dict(source)
    closure = extract_model_closure(
        source,
        (source.handle("sheet", first_sheet), source.handle("sheet", second_sheet)),
        include_structural_closure=True,
        include_features=False,
    )
    working_before = geometry_to_dict(closure.working_model)

    with pytest.raises(StructuralPreparationError, match="Fragment Overlaps"):
        prepare_structural_connectivity(closure.working_model)

    assert geometry_to_dict(source) == source_before
    assert geometry_to_dict(closure.working_model) == working_before
