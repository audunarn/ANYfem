"""Requested views preserve frozen scope, native data and real frame identity."""

import csv
import io
from dataclasses import replace

import numpy as np
import pytest

from anyfem.io.artifacts import ArtifactStore
from anyfem.io.output_views import add_output_request_views
from anyfem.io.result_artifact import ResultArtifactPayload
from anyfem.model.records import OutputRequest, ResultQuantityDescriptor
from anyfem.ui.result_export import lazy_field_to_csv


def payload():
    descriptor = ResultQuantityDescriptor(
        "displacement", "Movement", "node", components=("ux", "uz"),
        unit="m", frames=(2.0, 7.0),
    )
    return ResultArtifactPayload(
        fields={"displacement": (descriptor, np.array([
            [[1., 2.], [3., -4.], [5., 6.]],
            [[-9., 1.], [8., 2.], [7., -10.]],
        ]))}, frames=descriptor.frames,
        tables={"displacement_node_ids": np.array([11, 42, 93])},
    )


def scoped(request, nodes=(42,)):
    return {"request": request.to_dict(), "mesh_id": "submitted-mesh",
            "node_ids": list(nodes), "element_ids": []}


def repeated_coordinate_payload():
    descriptor = ResultQuantityDescriptor("stress_patch_global_xx_top", "Patch stress", "node",
        unit="Pa", components=("global_xx_top",), recovery="patch", frames=(0.5, 0.5),
        provenance={"load_cases": ["ascending", "descending"],
                    "node_recovery_status": [{"42": "qualified"}, {"42": "fallback"}]})
    return ResultArtifactPayload(fields={descriptor.key: (descriptor, np.array([[[1.]], [[-3.]]]))},
        frames=descriptor.frames, tables={descriptor.key + "_node_ids": np.array([42])})


@pytest.mark.parametrize("basis,available", [("local", True), ("element", True), ("material", False)])
def test_legacy_local_basis_spellings_preserve_intent_and_owner_values(tmp_path, basis, available):
    descriptor=ResultQuantityDescriptor("stress_sxx","Local stress","element",unit="Pa",
        components=("sxx",),basis="element_local",frames=(0.,),recovery="recovered")
    native=ResultArtifactPayload(fields={descriptor.key:(descriptor,np.array([[[123.]]]))},
        frames=descriptor.frames,tables={descriptor.key+"_element_ids":np.array([42])})
    request=OutputRequest(("stress.sxx",),"region","element",basis=basis)
    assert OutputRequest.from_dict(request.to_dict()).basis==basis
    result=add_output_request_views(native,({"request":request.to_dict(),"element_ids":[42]},))
    outcome=result.provenance["output_request_outcomes"][0]
    assert outcome["status"]==("available" if available else "unavailable")
    if not available:
        assert not outcome["fields"]
        return
    key=outcome["fields"][0];view,values=result.fields[key]
    assert view.basis=="element_local" and view.provenance["output_request"]["basis"]==basis
    np.testing.assert_array_equal(values,native.fields[descriptor.key][1])
    store=ArtifactStore(tmp_path/"local-basis.anyfem")
    artifact=store.write_result(job_id="job",document_id="document",mesh_id="mesh",model_hash="model",
        mesh_hash="mesh",analysis_hash="analysis",**result.write_result_inputs())
    dataset=store.open_result(artifact)
    assert dataset.field(key).descriptor.provenance["output_request"]["basis"]==basis
    rows=list(csv.DictReader(io.StringIO(lazy_field_to_csv(dataset,key))))
    assert [float(row["sxx [Pa]"]) for row in rows]==[123.]


@pytest.mark.parametrize("basis",["local","element"])
def test_local_basis_alias_does_not_rotate_global_values(basis):
    request=OutputRequest(("displacement.uz",),"region","node",basis=basis)
    result=add_output_request_views(payload(),(scoped(request),))
    outcome=result.provenance["output_request_outcomes"][0]
    assert outcome["status"]=="unavailable" and not outcome["fields"]


@pytest.mark.parametrize("policy,indices,expected_indices", [
    ("all", (), [0, 1]), ("first", (), [0]), ("last", (), [1]),
    ("selected", (1, 0), [1, 0]), ("envelope", (), []),
])
def test_repeated_frame_coordinates_preserve_metadata_identity(tmp_path, policy, indices, expected_indices):
    native = repeated_coordinate_payload()
    request = OutputRequest(("stress.global_xx_top",), "region", "node", recovery="patch",
                            frame_policy=policy, frame_indices=indices)
    result = add_output_request_views(native, (scoped(request),))
    outcome = result.provenance["output_request_outcomes"][0]
    key = outcome["fields"][0]
    view, values = result.fields[key]
    expected_values = [-3.] if policy == "envelope" else [1. if index == 0 else -3. for index in expected_indices]
    expected_statuses = ["fallback"] if policy == "envelope" else ["qualified" if index == 0 else "fallback" for index in expected_indices]
    assert view.provenance["node_recovery_status"] == [{"42": status} for status in expected_statuses]
    assert view.provenance["frame_labels"] == [["ascending", "descending"][index] for index in expected_indices]
    np.testing.assert_array_equal(values[:, 0, 0], expected_values)
    assert outcome["status"] == ("available" if expected_statuses == ["qualified"] else "partial")
    if policy == "envelope":
        assert "source_frame_indices" not in view.provenance
        assert view.provenance["envelope_source_frame_indices"] == [0, 1]
    else:
        assert view.provenance["source_frame_indices"] == expected_indices
    store = ArtifactStore(tmp_path / "repeated.anyfem")
    artifact = store.write_result(job_id="job", document_id="document", mesh_id="mesh",
        model_hash="model", mesh_hash="mesh", analysis_hash="analysis", **result.write_result_inputs())
    rows = list(csv.DictReader(io.StringIO(lazy_field_to_csv(store.open_result(artifact), key))))
    assert [row["recovery_status"] for row in rows] == expected_statuses
    assert [float(row["global_xx_top [Pa]"]) for row in rows] == expected_values
    if policy != "envelope":
        assert [int(row["source_frame_index"]) for row in rows] == expected_indices
        assert [row["frame_label"] for row in rows] == view.provenance["frame_labels"]


@pytest.mark.parametrize("reduction,canonical", [
    ("mean", "mean"), ("average", "mean"), ("min", "min"), ("max", "max"),
    ("max_abs", "max_abs"), ("abs_max", "max_abs"),
])
def test_sample_reduction_matches_existing_postprocessor_and_csv(tmp_path, reduction, canonical):
    from anyfem.post.fields import _reduce
    raw = np.array([[[[-9., 9.], [2., 6.]], [[40., 50.], [60., 70.]]],
                    [[[3., -12.], [8., 1.]], [[10., 20.], [30., 40.]]]])
    descriptor = ResultQuantityDescriptor(
        "stress_sxx", "Stress XX", "integration_point", unit="Pa", components=("sxx",),
        basis="element_local", frames=(2., 7.), recovery="recovered",
        provenance={"scalar_sample_axes": [2, 3]},
    )
    native = ResultArtifactPayload(fields={"stress_sxx": (descriptor, raw)},
        frames=descriptor.frames, tables={"stress_sxx_element_ids": np.array([11, 42])})
    request = OutputRequest(("stress.sxx",), "region", "element", basis="element_local",
                            reduction=reduction, frame_policy="selected", frame_indices=(1, 0))
    scope = {"request": request.to_dict(), "node_ids": [], "element_ids": [11]}
    result = add_output_request_views(native, (scope,))
    outcome = result.provenance["output_request_outcomes"][0]
    assert outcome["status"] == "available"
    key = outcome["fields"][0]
    view, values = result.fields[key]
    expected = [_reduce(raw[index, 0], canonical) for index in (1, 0)]
    np.testing.assert_array_equal(values[:, 0, 0], expected)
    assert view.location == "element" and view.reduction == canonical
    assert view.frames == (7., 2.) and "scalar_sample_axes" not in view.provenance
    assert result.fields["stress_sxx"] is native.fields["stress_sxx"]
    store = ArtifactStore(tmp_path / "reduced.anyfem")
    artifact = store.write_result(job_id="job", document_id="document", mesh_id="mesh",
        model_hash="model", mesh_hash="mesh", analysis_hash="analysis", **result.write_result_inputs())
    dataset = store.open_result(artifact)
    rows = list(csv.DictReader(io.StringIO(lazy_field_to_csv(dataset, key))))
    assert [float(row["sxx [Pa]"]) for row in rows] == expected
    assert {int(row["element_id"]) for row in rows} == {11}
    native_rows = list(csv.DictReader(io.StringIO(lazy_field_to_csv(dataset, "stress_sxx"))))
    assert len(native_rows) == raw.size
    assert [float(row["sxx [Pa]"]) for row in native_rows] == raw.reshape(-1).tolist()


def test_sample_reduction_refuses_ambiguous_legacy_layout():
    descriptor = ResultQuantityDescriptor("stress_sxx", "XX", "integration_point",
        components=("sxx",), basis="element_local", frames=(0.,))
    native = ResultArtifactPayload(fields={"stress_sxx": (descriptor, np.ones((1, 1, 4)))},
                                   tables={"stress_sxx_element_ids": np.array([11])})
    request = OutputRequest(("stress.sxx",), "region", "element", basis="element_local", reduction="mean")
    result = add_output_request_views(native, ({"request": request.to_dict(), "element_ids": [11]},))
    outcome = result.provenance["output_request_outcomes"][0]
    assert outcome["status"] == "unavailable" and not outcome["fields"]
    assert tuple(result.fields) == ("stress_sxx",)


@pytest.mark.parametrize("policy,labels", [("first", ["dead"]), ("last", ["live"]),
                                        ("envelope", [])])
def test_case_labels_follow_quantity_frames_and_do_not_label_envelopes_as_cases(tmp_path, policy, labels):
    native = payload()
    descriptor, values = native.fields["displacement"]
    native = replace(native, fields={"displacement": (
        replace(descriptor, provenance={"load_cases": ["dead", "live"]}), values)})
    request = OutputRequest(("displacement.uz",), "region", "node", frame_policy=policy)
    result = add_output_request_views(native, (scoped(request),))
    key = result.provenance["output_request_outcomes"][0]["fields"][0]
    assert result.fields[key][0].provenance["frame_labels"] == labels
    store = ArtifactStore(tmp_path / "cases.anyfem")
    artifact = store.write_result(job_id="job", document_id="document", mesh_id="mesh",
        model_hash="model", mesh_hash="mesh", analysis_hash="analysis", **result.write_result_inputs())
    rows = list(csv.DictReader(io.StringIO(lazy_field_to_csv(store.open_result(artifact), key))))
    if labels:
        assert [row["frame_label"] for row in rows] == labels
    else:
        assert "frame_label" not in rows[0]


@pytest.mark.parametrize("policy,expected,frames", [
    ("all", [-4., 2.], (2., 7.)),
    ("first", [-4.], (2.,)),
    ("last", [2.], (7.,)),
    ("envelope", [-4.], (0.,)),
])
def test_subset_component_frames_and_csv_roundtrip(tmp_path, policy, expected, frames):
    native = payload()
    request = OutputRequest(("displacement.uz",), "tip-region", "node", frame_policy=policy)
    result = add_output_request_views(native, (scoped(request),))
    outcome = result.provenance["output_request_outcomes"][0]
    assert outcome["status"] == "available"
    key = outcome["fields"][0]
    descriptor, values = result.fields[key]
    assert descriptor.components == ("uz",) and descriptor.frames == frames
    np.testing.assert_array_equal(values[:, 0, 0], expected)
    np.testing.assert_array_equal(result.tables[f"{key}_node_ids"], [42])
    assert result.fields["displacement"] is native.fields["displacement"]
    assert set(result.fields) == {"displacement", key}
    store = ArtifactStore(tmp_path / "views.anyfem")
    artifact = store.write_result(
        job_id="job", document_id="document", mesh_id="mesh", model_hash="model",
        mesh_hash="mesh", analysis_hash="analysis", **result.write_result_inputs(),
    )
    dataset = store.open_result(artifact)
    rows = list(csv.DictReader(io.StringIO(lazy_field_to_csv(dataset, key))))
    assert [float(row["frame_value"]) for row in rows] == list(frames)
    assert {int(row["node_id"]) for row in rows} == {42}
    assert [float(row["uz [m]"]) for row in rows] == expected


@pytest.mark.parametrize("change,diagnostic", [
    ({"basis": "element_local"}, "basis"),
    ({"recovery": "averaged"}, "recovery"),
    ({"reduction": "mean"}, "reduction"),
    ({"frame_policy": "selected"}, "frame indices"),
    ({"quantity_keys": ("displacement.missing",)}, "unavailable"),
])
def test_unavailable_semantics_produce_diagnostics_not_full_field(change, diagnostic):
    request = replace(OutputRequest(("displacement",), "region", "node"), **change)
    result = add_output_request_views(payload(), (scoped(request),))
    outcome = result.provenance["output_request_outcomes"][0]
    assert outcome["status"] == "unavailable" and outcome["fields"] == []
    assert diagnostic in " ".join(outcome["diagnostics"])
    assert tuple(result.fields) == ("displacement",)


def test_absent_entity_data_stays_absent():
    request = OutputRequest(("displacement",), "region", "node")
    result = add_output_request_views(payload(), (scoped(request, (404,)),))
    outcome = result.provenance["output_request_outcomes"][0]
    assert outcome["status"] == "unavailable"
    assert "no entities" in outcome["diagnostics"][0]


def test_partial_entity_output_discloses_missing_members_without_zero_fill():
    request = OutputRequest(("displacement",), "region", "node")
    result = add_output_request_views(payload(), (scoped(request, (42, 404)),))
    outcome = result.provenance["output_request_outcomes"][0]
    assert outcome["status"] == "partial"
    assert "404" in outcome["diagnostics"][0]
    key = outcome["fields"][0]
    assert result.fields[key][1].shape == (2, 1, 2)
    assert result.tables[f"{key}_node_ids"].tolist() == [42]


def test_external_request_identity_does_not_create_nested_hdf_groups(tmp_path):
    request = OutputRequest(("displacement:uz",), "region", "node", id="external/request")
    result = add_output_request_views(payload(), (scoped(request),))
    key = result.provenance["output_request_outcomes"][0]["fields"][0]
    assert "/" not in key and "%2F" in key
    store = ArtifactStore(tmp_path / "external-id.anyfem")
    artifact = store.write_result(
        job_id="job", document_id="document", mesh_id="mesh", model_hash="model",
        mesh_hash="mesh", analysis_hash="analysis", **result.write_result_inputs(),
    )
    dataset = store.open_result(artifact)
    assert key in dataset.field_keys
    assert dataset.field(key).descriptor.provenance["output_request"]["id"] == request.id


def test_selected_frames_keep_declared_order_and_real_csv_coordinates(tmp_path):
    request = OutputRequest(("displacement.uz",), "region", "node",
                            frame_policy="selected", frame_indices=(1, 0))
    result = add_output_request_views(payload(), (scoped(request),))
    outcome = result.provenance["output_request_outcomes"][0]
    assert outcome["status"] == "available"
    key = outcome["fields"][0]
    descriptor, values = result.fields[key]
    assert descriptor.frames == (7., 2.)
    np.testing.assert_array_equal(values[:, 0, 0], [2., -4.])
    store = ArtifactStore(tmp_path / "selected-frames.anyfem")
    artifact = store.write_result(
        job_id="job", document_id="document", mesh_id="mesh", model_hash="model",
        mesh_hash="mesh", analysis_hash="analysis", **result.write_result_inputs(),
    )
    dataset = store.open_result(artifact)
    rows = list(csv.DictReader(io.StringIO(lazy_field_to_csv(dataset, key))))
    assert [float(row["frame_value"]) for row in rows] == [7., 2.]
    assert [float(row["uz [m]"]) for row in rows] == [2., -4.]


def test_unavailable_selected_frame_refuses_the_whole_selection():
    request = OutputRequest(("displacement",), "region", "node",
                            frame_policy="selected", frame_indices=(0, 2))
    result = add_output_request_views(payload(), (scoped(request),))
    outcome = result.provenance["output_request_outcomes"][0]
    assert outcome["status"] == "unavailable" and not outcome["fields"]
    assert "exceed stored range 0..1" in outcome["diagnostics"][0]


def test_mesh_scope_is_frozen_and_rejects_another_mesh():
    from anyfem import Project
    from anyfem.application.output_requests import freeze_output_requests
    from anyfem.mesh.mapped import Mesh
    from anyfem.model.regions import ManualRegion, MeshEntityRef, Region, RegionError

    project = Project()
    mesh = Mesh()
    mesh.nodes = {11: np.zeros(3), 42: np.ones(3)}
    region = project.regions.add(Region(
        "Selected node", "mesh", "node",
        ManualRegion((MeshEntityRef("submitted", "node", 42),)),
    ))
    request = project.add_output_request(OutputRequest(("displacement",), region.id, "node"))
    frozen = freeze_output_requests(project, (request.id,), mesh, mesh_id="submitted")
    region.definition = ManualRegion((MeshEntityRef("submitted", "node", 11),))
    assert frozen[0]["node_ids"] == [42]
    with pytest.raises(RegionError, match="belongs to mesh"):
        freeze_output_requests(project, (request.id,), mesh, mesh_id="replacement")


def test_element_face_scope_does_not_include_the_other_side():
    descriptor = ResultQuantityDescriptor(
        "stress", "Surface stress", "element_face", unit="Pa",
        components=("sxx",), frames=(0.,), basis="element_local",
    )
    native = ResultArtifactPayload(
        fields={"stress": (descriptor, np.array([[[100.], [200.], [300.]]]))},
        tables={"stress_element_ids": np.array([[9, 0], [9, 1], [12, 0]])},
    )
    request = OutputRequest(("stress.sxx",), "face-region", "element_face", basis="element_local")
    scope = {"request": request.to_dict(), "mesh_id": "mesh", "node_ids": [],
             "element_ids": [9], "element_faces": [[9, 1]]}
    result = add_output_request_views(native, (scope,))
    key = result.provenance["output_request_outcomes"][0]["fields"][0]
    np.testing.assert_array_equal(result.fields[key][1], [[[200.]]])
    np.testing.assert_array_equal(result.tables[f"{key}_element_ids"], [[9, 1]])


@pytest.mark.parametrize("kind,expected_nodes,expected_elements", [
    ("node", [11], []), ("element", [11, 42], [11]),
])
def test_mesh_query_keeps_node_and_element_id_namespaces_separate(kind, expected_nodes, expected_elements):
    from anyfem import Project
    from anyfem.application.output_requests import freeze_output_requests
    from anyfem.mesh.mapped import Mesh
    from anyfem.model.regions import BooleanRegion, QueryClause, QueryRegion, Region

    project = Project()
    mesh = Mesh()
    mesh.nodes = {11: np.zeros(3), 42: np.ones(3)}
    mesh.beams = {11: (11, 42)}
    query = project.regions.add(Region(
        "Matching ID", "mesh", kind, QueryRegion(QueryClause("id", "eq", 11)), mesh_id="mesh",
    ))
    combined = project.regions.add(Region(
        "Boolean scope", "mesh", kind, BooleanRegion("intersection", (query.id, query.id)), mesh_id="mesh",
    ))
    request = project.add_output_request(OutputRequest(("displacement",), combined.id, "node"))
    frozen = freeze_output_requests(project, (request.id,), mesh, mesh_id="mesh")[0]
    assert frozen["node_ids"] == expected_nodes
    assert frozen["element_ids"] == expected_elements
