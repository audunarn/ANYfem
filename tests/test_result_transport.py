"""Regression tests for the plain-data transport of completed results.

The reported defect: a 15 m barge linear-static run completed, then the
application failed with ``ResultTransferError`` because the result's live S3
V2D elements refuse pickle by contract.  These tests pin the repair and its
fail-closed boundaries.

Everything numerical here is a controlled fake: no solve, no recovery kernel,
no mesher ran.  The S3 element is the real admitted class, constructed through
its public constructor, and its ``to_dict``/``from_dict`` round trip is the
solver's own validated API.  Postprocessing is exercised through a fake
recovery function, never the real kernel.
"""

from __future__ import annotations

import dataclasses
import pickle
import time

import numpy as np
import pytest

import result_transport_helpers as helpers
from anyfem.document import DocumentSession
from anyfem.job_process import dumps, loads, run_in_worker_process
from anyfem.jobs import JobManager
from anyfem.model.project import Project
from anyfem.model.records import AnalysisDefinition, JobStatus
from anyfem.post.results import LinearBatchSolution, LinearSolution
from anyfem.result_transport import (
    ResultTransportError,
    decode_result,
    encode_result,
)
from anyfem.solve.build import BuiltModel
from anysolver.e4_pl_s3_v2d_element import NativeParityE4PLS3V2DShellElement
from anysolver.fe_core import FEModel, FEMesh


def run(function, *, kwargs=None, timeout=60.0):
    project = Project("worker process")
    manager = JobManager(project, executor="process")
    record = manager.submit(
        AnalysisDefinition("Job"),
        DocumentSession(project).snapshot(),
        function,
        kwargs=kwargs or {},
    )
    manager.wait(record.id, timeout=timeout)
    return manager, record, manager.poll()


def _quiet(message=None):
    return None


def _solution():
    return helpers.s3_linear_result(project=None, progress=_quiet)


def _root(envelope):
    return envelope["objects"][envelope["root"]]


def _target(envelope, encoded):
    return envelope["objects"][encoded["ref"]]


def _mesh_values(envelope):
    built = _target(envelope, _root(envelope)["values"]["built"])
    fe_model = _target(envelope, built["values"]["fe_model"])
    return _target(envelope, fe_model["values"]["mesh"])["values"]


def _wire(envelope):
    """The envelope as the receiver sees it: through one transport pickle."""

    return loads(dumps(envelope))


# ----------------------------------------------------------------------
# in-process codec contracts
# ----------------------------------------------------------------------
def test_encode_decode_round_trip_preserves_the_completed_s3_result():
    solution = _solution()
    envelope = encode_result(solution)
    # Intentional codec-v4 contract: raw ordinary values (mappingproxy views)
    # need the private transport pickler, so the standard pickle shortcut is
    # no longer promised; the transport dumps/loads round trip is the contract.
    loads(dumps(envelope))

    decoded = decode_result(_wire(envelope))

    assert type(decoded) is LinearSolution
    np.testing.assert_array_equal(decoded.displacements, solution.displacements)
    assert decoded.label == solution.label
    assert decoded.value == solution.value
    assert decoded.info == solution.info
    assert decoded._stress is None

    assert type(decoded.built) is BuiltModel
    assert decoded.built.project.name == solution.built.project.name
    assert decoded.built.combination == solution.built.combination
    assert decoded.built.load_case is None
    # init=False policy identity must survive, not just default back.
    assert decoded.built.shell_formulation_policy is not None
    assert decoded.built.shell_formulation_policy == solution.built.shell_formulation_policy
    assert decoded.built.mesh.tris == solution.built.mesh.tris
    for node_id, position in solution.built.mesh.nodes.items():
        np.testing.assert_array_equal(decoded.built.mesh.nodes[node_id], position)

    original_model = solution.built.fe_model
    decoded_model = decoded.built.fe_model
    assert type(decoded_model) is FEModel
    assert decoded_model.name == original_model.name
    assert decoded_model.current_material == original_model.current_material
    assert set(decoded_model.materials) == set(original_model.materials)
    assert dumps(decoded_model.materials) == dumps(original_model.materials)
    assert len(decoded_model.boundary_conditions) == 1
    assert dumps(decoded_model.boundary_conditions) == dumps(original_model.boundary_conditions)
    assert len(decoded_model.constraint_equations) == 1
    assert dumps(decoded_model.constraint_equations) == dumps(
        original_model.constraint_equations
    )
    assert [case.name for case in decoded_model.load_cases] == ["dead"]
    assert decoded_model.mesh.point_masses == {2: 3.5}
    assert decoded_model.mesh.element_activity.element_ids.tolist() == [101]
    assert decoded_model.mesh.revisions == original_model.mesh.revisions
    assert decoded_model.mesh.revisions["mesh"] == 4

    element = decoded_model.mesh.elements[101]
    assert type(element) is NativeParityE4PLS3V2DShellElement
    assert element.to_dict() == original_model.mesh.elements[101].to_dict()

    # The displacement vector is indexed by the decoded DOF mapping.
    np.testing.assert_array_equal(
        decoded.node_displacement(2), solution.node_displacement(2)
    )


def test_the_decoded_mesh_owns_a_fresh_constructor_epoch_and_subscriptions():
    solution = _solution()
    decoded = decode_result(_wire(encode_result(solution)))
    mesh = decoded.built.fe_model.mesh
    token = mesh._qualified_direct_state_token

    # The public FEMesh constructor owns a fresh runtime epoch for the
    # reconstructed mesh; the worker's private token is not transported.
    assert token is not solution.built.fe_model.mesh._qualified_direct_state_token
    for node in mesh.nodes.values():
        assert any(bound is token for bound in node._qualified_direct_state_tokens)
    for element in mesh.elements.values():
        assert any(bound is token for bound in element._qualified_direct_state_tokens)

    # Public node coordinate mutation must advance the decoded mesh's tracked
    # epoch, so qualified cache invalidation observes the reconstructed mesh.
    before = token[0]
    mesh.nodes[1].x += 0.1
    assert token[0] > before


def test_the_envelope_records_the_current_dataclass_field_sets():
    envelope = encode_result(_solution())
    record = _root(envelope)
    assert envelope["version"] == 4
    assert record["fields"] == [f.name for f in dataclasses.fields(LinearSolution)]
    built = _target(envelope, record["values"]["built"])
    assert built["fields"] == [f.name for f in dataclasses.fields(BuiltModel)]
    fe_model = _target(envelope, built["values"]["fe_model"])
    assert fe_model["fields"] == [f.name for f in dataclasses.fields(FEModel)]
    mesh = _target(envelope, fe_model["values"]["mesh"])
    assert mesh["fields"] == [f.name for f in dataclasses.fields(FEMesh)]


def test_a_picklable_stress_cache_is_carried_exactly():
    solution = helpers.s3_linear_result_with_stress(project=None, progress=_quiet)
    decoded = decode_result(_wire(encode_result(solution)))
    assert decoded._stress.provenance == solution._stress.provenance
    assert (
        decoded._stress.element_stresses[101]["von_mises"].tolist()
        == solution._stress.element_stresses[101]["von_mises"].tolist()
        == [1.0, 2.0]
    )


def test_an_unpicklable_stress_cache_fails_the_transfer_instead_of_dropping():
    solution = helpers.s3_linear_result_with_unpicklable_stress(
        project=None, progress=_quiet
    )
    with pytest.raises(ResultTransportError, match="_stress"):
        encode_result(solution)


def test_result_subclasses_with_extra_fields_are_refused_not_reduced():
    solution = helpers.s3_linear_subclass_result(project=None, progress=_quiet)
    with pytest.raises(ResultTransportError, match="subclass"):
        encode_result(solution)


def test_built_model_subclasses_are_refused_not_reduced():
    solution = helpers.s3_built_subclass_result(project=None, progress=_quiet)
    with pytest.raises(ResultTransportError, match="exact BuiltModel"):
        encode_result(solution)


def test_nonlinear_results_are_refused_by_design():
    solution = helpers.s3_nonlinear_result(project=None, progress=_quiet)
    with pytest.raises(ResultTransportError, match="no supported plain-data transfer"):
        encode_result(solution)


def test_unsupported_result_families_are_refused():
    with pytest.raises(ResultTransportError, match="no supported plain-data transfer"):
        encode_result({"not": "a result"})


def test_a_batch_with_distinct_built_models_keeps_each_shape_model():
    batch = helpers.s3_batch_distinct_built_result(project=None, progress=_quiet)
    decoded = decode_result(_wire(encode_result(batch)))
    assert type(decoded) is LinearBatchSolution
    assert decoded.case_names == ("alpha", "beta")
    assert decoded.shapes[0].built.fe_model is not decoded.shapes[1].built.fe_model
    assert decoded.shapes[0].built.fe_model is not decoded.built.fe_model
    assert decoded.built.project.name == "result transport batch"
    assert [shape.built.project.name for shape in decoded.shapes] == [
        "result transport alpha",
        "result transport beta",
    ]
    for shape in decoded.shapes:
        assert type(shape) is LinearSolution
        assert type(shape.built.fe_model.mesh.elements[101]) is (
            NativeParityE4PLS3V2DShellElement
        )


# ----------------------------------------------------------------------
# tampering and fail-closed decode
# ----------------------------------------------------------------------
def test_unknown_codec_version_and_kind_are_refused():
    envelope = encode_result(_solution())
    for mutation in (
        {"codec": "other.codec"},
        {"version": 99},
        {"kind": "NonlinearSolution"},
    ):
        tampered = dict(envelope)
        tampered.update(mutation)
        with pytest.raises(ResultTransportError):
            decode_result(tampered)


def test_a_non_mapping_envelope_is_refused():
    with pytest.raises(ResultTransportError):
        decode_result(["not", "a", "mapping"])


def test_field_set_drift_is_refused():
    envelope = encode_result(_solution())
    record = _root(envelope)
    record["fields"] = list(record["fields"]) + ["future_field"]
    with pytest.raises(ResultTransportError, match="field set"):
        decode_result(envelope)


def test_class_marker_drift_is_refused():
    envelope = encode_result(_solution())
    _root(envelope)["class"] = "fake.module.Thing"
    with pytest.raises(ResultTransportError, match="was encoded as"):
        decode_result(envelope)


def test_a_tampered_element_identity_fingerprint_is_refused():
    envelope = encode_result(_solution())
    record = _mesh_values(envelope)["elements"][101]
    payload = dict(record["payload"])
    payload["formulation_id"] = "FORGED_FORMULATION_ID"
    record["payload"] = payload
    with pytest.raises(ResultTransportError):
        decode_result(envelope)


def test_an_element_whose_identity_differs_from_its_key_is_refused():
    envelope = encode_result(_solution())
    record = _mesh_values(envelope)["elements"][101]
    payload = dict(record["payload"])
    payload["element_id"] = 999
    record["payload"] = payload
    with pytest.raises(ResultTransportError):
        decode_result(envelope)


def test_an_element_advertising_a_subclass_is_refused():
    class AdvertisedShell(NativeParityE4PLS3V2DShellElement):
        pass

    envelope = encode_result(_solution())
    record = _mesh_values(envelope)["elements"][101]
    record["module"] = "test_result_transport"
    record["qualname"] = "AdvertisedShell"
    with pytest.raises(ResultTransportError):
        decode_result(envelope)


def test_an_unimportable_element_class_is_refused():
    envelope = encode_result(_solution())
    record = _mesh_values(envelope)["elements"][101]
    record["qualname"] = "DoesNotExistAnywhere"
    with pytest.raises(ResultTransportError, match="cannot be loaded"):
        decode_result(envelope)


def test_an_unknown_element_transport_codec_is_refused():
    envelope = encode_result(_solution())
    record = _mesh_values(envelope)["elements"][101]
    record["codec"] = "quantum"
    with pytest.raises(ResultTransportError, match="unknown transport codec"):
        decode_result(envelope)


def test_a_node_whose_id_differs_from_its_key_is_refused():
    envelope = encode_result(_solution())
    values = _mesh_values(envelope)
    envelope = _wire(envelope)  # tamper with a copy, never the live result
    values = _mesh_values(envelope)
    values["nodes"]["value"][1].id = 999
    with pytest.raises(ResultTransportError, match="node ids do not match"):
        decode_result(envelope)


def test_a_tampered_dof_mapping_is_refused():
    envelope = encode_result(_solution())
    values = _mesh_values(envelope)
    envelope = _wire(envelope)
    nodes = _mesh_values(envelope)["nodes"]["value"]
    nodes[1].dofs, nodes[2].dofs = nodes[2].dofs, nodes[1].dofs
    with pytest.raises(ResultTransportError, match="DOF mapping"):
        decode_result(envelope)


def test_the_retired_stress_drop_state_is_refused():
    envelope = encode_result(_solution())
    _root(envelope)["values"]["_stress"] = {"state": "dropped_unpicklable"}
    with pytest.raises(ResultTransportError, match="unknown stress cache state"):
        decode_result(envelope)


# ----------------------------------------------------------------------
# real worker transport
# ----------------------------------------------------------------------
def test_completed_s3_result_transfers_through_a_real_worker_process():
    manager, record, events = run(helpers.s3_linear_result)
    assert record.status == JobStatus.COMPLETED
    result = manager.result(record.id)
    assert type(result) is LinearSolution
    np.testing.assert_array_equal(result.displacements, helpers.DISPLACEMENTS)
    element = result.built.fe_model.mesh.elements[101]
    assert type(element) is NativeParityE4PLS3V2DShellElement
    assert result.built.fe_model.mesh.revisions["mesh"] == 4
    assert result.built.fe_model.mesh.revisions.get("result_state") == 1
    assert result.built.fe_model.mesh.point_masses == {2: 3.5}
    assert [case.name for case in result.built.fe_model.load_cases] == ["dead"]
    assert result.built.shell_formulation_policy is not None
    np.testing.assert_array_equal(
        result.node_displacement(3), helpers.DISPLACEMENTS[12:18]
    )

    # The decoded mesh's qualified-direct-state epoch is owned by the public
    # FEMesh constructor: public node mutation advances it and the decoded
    # nodes/elements stay subscribed to it.
    mesh = result.built.fe_model.mesh
    token = mesh._qualified_direct_state_token
    before = token[0]
    mesh.nodes[2].x += 0.1
    assert token[0] > before
    assert any(bound is token for bound in mesh.nodes[2]._qualified_direct_state_tokens)
    assert any(bound is token for bound in element._qualified_direct_state_tokens)
    messages = [event.message for event in events if event.kind == "progress"]
    assert "linear_static.complete" in messages
    assert "preparing the completed result for transfer" in messages
    assert "transferring the completed result" in messages


def test_worker_tree_is_retired_before_the_transferred_result_is_returned():
    captured = {}
    project = Project("retire")
    result = run_in_worker_process(
        function=helpers.s3_linear_result,
        kwargs={},
        snapshot=DocumentSession(project).snapshot(),
        project_override=None,
        progress=_quiet,
        token=None,
        on_process=lambda process: captured.__setitem__("process", process),
    )
    assert type(result) is LinearSolution
    process = captured["process"]
    assert not process.is_alive()
    assert process.exitcode is not None


def test_a_batch_result_transfers_with_each_load_case_intact():
    manager, record, _ = run(helpers.s3_batch_result)
    assert record.status == JobStatus.COMPLETED
    result = manager.result(record.id)
    assert type(result) is LinearBatchSolution
    assert result.case_names == ("dead", "live")
    assert [shape.built.load_case.name for shape in result.shapes] == ["dead", "live"]
    np.testing.assert_array_equal(result.shapes[1].displacements, 2 * helpers.DISPLACEMENTS)


def test_a_batch_with_distinct_built_models_transfers_through_a_worker():
    manager, record, _ = run(helpers.s3_batch_distinct_built_result)
    assert record.status == JobStatus.COMPLETED
    result = manager.result(record.id)
    assert [shape.built.project.name for shape in result.shapes] == [
        "result transport alpha",
        "result transport beta",
    ]


def test_a_carried_stress_cache_survives_the_worker_transfer():
    manager, record, _ = run(helpers.s3_linear_result_with_stress)
    assert record.status == JobStatus.COMPLETED
    result = manager.result(record.id)
    expected = helpers.controlled_stress_cache()
    assert result._stress.provenance == expected.provenance
    assert set(result._stress.element_stresses) == set(expected.element_stresses)
    decoded_stress = result._stress.element_stresses[101]
    expected_stress = expected.element_stresses[101]
    assert set(decoded_stress) == set(expected_stress)
    np.testing.assert_array_equal(
        decoded_stress["von_mises"], expected_stress["von_mises"]
    )


def test_a_normal_picklable_result_keeps_the_direct_path():
    manager, record, events = run(helpers.picklable_linear_result)
    assert record.status == JobStatus.COMPLETED
    result = manager.result(record.id)
    assert type(result) is LinearSolution
    messages = [event.message for event in events if event.kind == "progress"]
    assert "preparing the completed result for transfer" not in messages
    assert "transferring the completed result" not in messages


def test_a_nonlinear_result_is_refused_by_the_worker_transfer():
    manager, record, _ = run(helpers.s3_nonlinear_result)
    assert record.status == JobStatus.FAILED
    diagnostic = record.diagnostics[-1]
    assert diagnostic["type"] == "ResultTransferError"
    assert "no supported plain-data transfer" in diagnostic["message"]


def test_an_unpicklable_stress_cache_fails_the_worker_transfer():
    manager, record, _ = run(helpers.s3_linear_result_with_unpicklable_stress)
    assert record.status == JobStatus.FAILED
    diagnostic = record.diagnostics[-1]
    assert diagnostic["type"] == "ResultTransferError"
    assert "_stress" in diagnostic["message"]


def test_result_subclasses_are_refused_by_the_worker_transfer():
    for function, fragment in (
        (helpers.s3_linear_subclass_result, "subclass"),
        (helpers.s3_built_subclass_result, "exact BuiltModel"),
    ):
        manager, record, _ = run(function)
        assert record.status == JobStatus.FAILED
        diagnostic = record.diagnostics[-1]
        assert diagnostic["type"] == "ResultTransferError"
        assert fragment in diagnostic["message"]
        with pytest.raises(KeyError):
            manager.result(record.id)


def test_cancellation_during_transfer_ends_cancelled_and_attaches_nothing():
    project = Project("cancel")
    manager = JobManager(project, executor="process")
    record = manager.submit(
        AnalysisDefinition("Job"),
        DocumentSession(project).snapshot(),
        helpers.s3_linear_result_after_ready,
    )
    deadline = time.monotonic() + 60
    while not any(event.message == "ready" for event in manager.poll()):
        assert time.monotonic() < deadline, "worker never reported ready"
        time.sleep(0.02)
    assert manager.cancel(record.id)
    assert manager.wait(record.id, timeout=30).status == JobStatus.CANCELLED
    with pytest.raises(KeyError):
        manager.result(record.id)
    assert record.diagnostics[-1]["type"] == "CancelledDuringTransfer"


def test_cancellation_during_parent_decode_ends_cancelled_and_attaches_nothing(
    monkeypatch,
):
    import anyfem.result_transport as transport

    real_decode = transport.decode_result
    project = Project("cancel-decode")
    manager = JobManager(project, executor="process")
    submitted = {}

    def cancelling_decode(envelope):
        # Deterministic parent-side cancellation: the caller cancels while
        # the decode itself is running in the waiting job thread, so the
        # post-decode cancellation check must refuse the attachment without
        # relying on transfer timing.
        deadline = time.monotonic() + 30
        while "record" not in submitted:
            assert time.monotonic() < deadline, "decode ran before submit returned"
            time.sleep(0.001)
        assert manager.cancel(submitted["record"].id)
        return real_decode(envelope)

    monkeypatch.setattr(transport, "decode_result", cancelling_decode)
    record = manager.submit(
        AnalysisDefinition("Job"),
        DocumentSession(project).snapshot(),
        helpers.s3_linear_result,
    )
    submitted["record"] = record
    assert manager.wait(record.id, timeout=60).status == JobStatus.CANCELLED
    with pytest.raises(KeyError):
        manager.result(record.id)
    assert record.diagnostics[-1]["type"] == "CancelledDuringTransfer"


def test_a_decode_failure_is_structured_and_attaches_nothing(monkeypatch):
    import anyfem.result_transport as transport

    def broken_decode(envelope):
        raise ResultTransportError("fake decode failure")

    monkeypatch.setattr(transport, "decode_result", broken_decode)
    manager, record, _ = run(helpers.s3_linear_result)
    assert record.status == JobStatus.FAILED
    diagnostic = record.diagnostics[-1]
    assert diagnostic["type"] == "ResultTransferError"
    assert "fake decode failure" in diagnostic["message"]
    with pytest.raises(KeyError):
        manager.result(record.id)


def test_postprocessing_on_the_transferred_result_uses_fake_recovery(monkeypatch):
    import anysolver

    captured = {}

    def fake_recover(fe_model, displacements):
        captured["fe_model"] = fe_model
        captured["displacements"] = displacements
        return {"element_stresses": {101: {"von_mises": np.array([3.0, 4.0])}}}

    monkeypatch.setattr(anysolver, "recover_stress_result", fake_recover)
    manager, record, _ = run(helpers.s3_linear_result)
    result = manager.result(record.id)
    stresses = result.stresses()
    assert stresses["element_stresses"][101]["von_mises"].tolist() == [3.0, 4.0]
    assert captured["fe_model"] is result.built.fe_model
    np.testing.assert_array_equal(captured["displacements"], helpers.DISPLACEMENTS)


def test_the_transferred_result_adapts_into_a_result_artifact_payload():
    from anyfem.io.result_artifact import result_artifact_payload

    # Controlled cached fields only: no solve, no recovery kernel.
    solution = helpers.s3_linear_result_with_stress(project=None, progress=_quiet)
    decoded = decode_result(encode_result(solution))
    payload = result_artifact_payload(decoded)

    assert payload.frame_kind == "static"
    displacement = payload.fields["displacement"]
    np.testing.assert_array_equal(
        np.asarray(displacement[1]).reshape(-1), helpers.DISPLACEMENTS
    )
    stress = payload.fields["stress_von_mises"]
    np.testing.assert_array_equal(np.asarray(stress[1]), [[[1.0, 2.0]]])
    assert payload.tables["stress_von_mises_element_ids"].tolist() == [101]


def test_requested_stresses_survive_the_worker_and_artifact_adapter():
    from anyfem.io.result_artifact import result_artifact_payload

    manager, record, _ = run(helpers.s3_linear_result_with_requested_stresses)
    assert record.status == JobStatus.COMPLETED
    result = manager.result(record.id)
    assert result._requested_global_stress.provenance == helpers.controlled_global_stress().provenance
    assert result._requested_patch_stress.provenance == helpers.controlled_patch_stress().provenance
    payload = result_artifact_payload(result)
    np.testing.assert_array_equal(payload.fields["stress_global_xx_top"][1], [[[3.0]]])
    np.testing.assert_array_equal(payload.fields["stress_patch_von_mises"][1], [[[5.0], [6.0], [7.0]]])
    assert payload.tables["stress_patch_von_mises_node_ids"].tolist() == [1, 2, 3]


def test_requested_stress_presence_including_none_survives():
    solution = _solution()
    decoded = decode_result(_wire(encode_result(solution)))
    assert not hasattr(decoded, "_requested_global_stress")
    assert not hasattr(decoded, "_requested_patch_stress")
    solution._requested_global_stress = None
    decoded = decode_result(_wire(encode_result(solution)))
    assert decoded._requested_global_stress is None
    assert not hasattr(decoded, "_requested_patch_stress")


@pytest.mark.parametrize("name", ["_requested_global_stress", "_requested_patch_stress"])
def test_unpicklable_requested_stress_is_refused(name):
    solution = _solution()
    setattr(solution, name, helpers.UnpicklableCache())
    with pytest.raises(ResultTransportError, match=name):
        encode_result(solution)


def test_unknown_dynamic_state_is_refused():
    solution = _solution()
    solution.unsupported_state = {"accepted": True}
    with pytest.raises(ResultTransportError, match="unsupported_state"):
        encode_result(solution)


def test_unknown_application_extra_record_is_refused():
    envelope = encode_result(_solution())
    _root(envelope)["application_extras"]["unsupported_state"] = 1
    with pytest.raises(ResultTransportError, match="unknown application extras"):
        decode_result(envelope)


# ----------------------------------------------------------------------
# codec v4: identity-indexed graph
# ----------------------------------------------------------------------
def _objects_of(envelope, name):
    suffix = "." + name
    return [r for r in envelope["objects"] if r["class"].endswith(suffix)]


@pytest.mark.parametrize("cases", [1, 10, 50, 200])
def test_one_model_record_per_distinct_model_at_any_batch_size(cases):
    batch = helpers.shared_model_batch(cases)
    envelope = encode_result(batch)
    assert len(_objects_of(envelope, "FEModel")) == 1
    assert len(_objects_of(envelope, "FEMesh")) == 1
    # one wrapper per shape + the batch; one BuiltModel per replace() copy + base
    assert len(_objects_of(envelope, "LinearSolution")) == cases
    assert len(_objects_of(envelope, "BuiltModel")) == cases + 1


def test_wire_size_is_linear_not_quadratic_in_cases():
    sizes = {n: len(dumps(("encoded_result", encode_result(helpers.shared_model_batch(n)))))
             for n in (50, 100, 200)}
    # Doubling the cases must roughly double the bytes (quadratic would be ~4x).
    assert sizes[200] < 2.6 * sizes[100]
    assert sizes[100] < 2.6 * sizes[50]


def test_a_shared_64kib_project_marker_is_carried_once():
    base = len(dumps(("encoded_result", encode_result(helpers.shared_model_batch(200, 0)))))
    marked = len(dumps(("encoded_result", encode_result(helpers.shared_model_batch(200, 65536)))))
    assert marked - base <= 70 * 1024


def test_decoded_batch_keeps_every_shared_alias():
    batch = helpers.shared_model_batch(10)
    decoded = decode_result(_wire(encode_result(batch)))
    model = decoded.built.fe_model
    for shape in decoded.shapes:
        assert shape.built.fe_model is model
        assert shape.built.project is decoded.built.project
        assert shape.built.mesh is decoded.built.mesh
        assert shape.built.shell_formulation_policy == decoded.built.shell_formulation_policy
    for index, shape in enumerate(decoded.shapes):
        # shape.built.load_case is the very object registered in the model
        assert any(shape.built.load_case is case for case in model.load_cases)
        assert shape.built.load_case.name == f"case-{index}"
    assert decoded.shapes[0].built.mesh.nodes is decoded.shapes[3].built.mesh.nodes


def test_decoded_batch_values_are_exact():
    batch = helpers.shared_model_batch(10)
    decoded = decode_result(_wire(encode_result(batch)))
    for old, new in zip(batch.shapes, decoded.shapes):
        assert np.array_equal(old.displacements, new.displacements)
        assert old.info == new.info and old.label == new.label and old.value == new.value
        for old_case, new_case in zip(old.built.fe_model.load_cases,
                                      new.built.fe_model.load_cases):
            assert dumps(old_case.nodal_loads) == dumps(new_case.nodal_loads)


def test_the_shared_decoded_mesh_advances_one_epoch_on_node_mutation():
    decoded = decode_result(_wire(encode_result(helpers.shared_model_batch(5))))
    mesh = decoded.built.fe_model.mesh
    token = mesh._qualified_direct_state_token
    before = token[0]
    mesh.nodes[1].x += 0.1
    assert token[0] > before


def test_each_distinct_mesh_is_reconstructed_once(monkeypatch):
    built = []
    real = FEMesh.__init__

    def counting(self, *args, **kwargs):
        built.append(self)
        real(self, *args, **kwargs)

    batch = helpers.shared_model_batch(20)
    wire = _wire(encode_result(batch))
    monkeypatch.setattr(FEMesh, "__init__", counting)
    decode_result(wire)
    assert len(built) == 1


def test_batch_application_extras_are_carried_and_unknown_state_refused():
    batch = helpers.shared_model_batch(2)
    batch._requested_global_stress = helpers.controlled_global_stress()
    decoded = decode_result(_wire(encode_result(batch)))
    assert decoded._requested_global_stress.provenance == (
        helpers.controlled_global_stress().provenance
    )
    assert not hasattr(decoded, "_requested_patch_stress")
    batch.mystery = 1
    with pytest.raises(ResultTransportError, match="mystery"):
        encode_result(batch)


def test_an_unsupported_envelope_version_is_refused_explicitly():
    envelope = encode_result(helpers.shared_model_batch(2))
    envelope["version"] = 3
    with pytest.raises(ResultTransportError, match="unsupported result transport codec"):
        decode_result(envelope)


def test_streaming_encode_names_an_unpicklable_field_without_probing_first():
    from anyfem.result_transport import encode_result_stream

    solution = helpers.s3_linear_result_with_unpicklable_stress(project=None, progress=_quiet)

    def serialize(envelope):
        return dumps(("encoded_result", envelope))

    with pytest.raises(ResultTransportError, match="_stress"):
        encode_result_stream(solution, serialize)


def _two_mesh_batch():
    """Two distinct FEMesh/FEModel instances holding the SAME element object."""

    batch = helpers.shared_model_batch(2)
    common = batch.built.fe_model
    other_model = dataclasses.replace(common, mesh=dataclasses.replace(common.mesh))
    batch.shapes[1].built = dataclasses.replace(batch.shapes[1].built, fe_model=other_model)
    return batch


def test_an_element_shared_between_distinct_meshes_is_rebuilt_once(monkeypatch):
    batch = _two_mesh_batch()
    assert batch.shapes[0].built.fe_model.mesh is not batch.shapes[1].built.fe_model.mesh
    wire = _wire(encode_result(batch))
    real = NativeParityE4PLS3V2DShellElement.from_dict
    calls = []

    def counting(cls, payload):
        calls.append(1)
        return real(payload)

    monkeypatch.setattr(NativeParityE4PLS3V2DShellElement, "from_dict", classmethod(counting))
    decoded = decode_result(wire)
    first = decoded.shapes[0].built.fe_model.mesh
    second = decoded.shapes[1].built.fe_model.mesh
    assert first is not second
    assert first.elements[101] is second.elements[101]
    assert len(calls) == 1
    # fresh, independent epochs; the shared element is subscribed to both
    token_a, token_b = first._qualified_direct_state_token, second._qualified_direct_state_token
    assert token_a is not token_b
    bound = first.elements[101]._qualified_direct_state_tokens
    assert any(t is token_a for t in bound) and any(t is token_b for t in bound)


def test_large_shared_numpy_metadata_round_trips_through_both_encode_routes():
    from anyfem.result_transport import encode_result_stream

    batch = helpers.shared_model_batch(3)
    shared = np.arange(20000, dtype=float)  # > 64 KiB: pickled as a PickleBuffer
    for shape in batch.shapes:
        shape.info["shared"] = shared
    batch.info["shared"] = shared

    verified = decode_result(_wire(encode_result(batch)))  # verify=True probes
    assert np.array_equal(verified.info["shared"], shared)
    assert all(s.info["shared"] is verified.info["shared"] for s in verified.shapes)

    wire = encode_result_stream(batch, lambda envelope: dumps(("encoded_result", envelope)))
    streamed = decode_result(loads(wire)[1])
    assert np.array_equal(streamed.info["shared"], shared)
    assert all(s.info["shared"] is streamed.info["shared"] for s in streamed.shapes)
    assert streamed.shapes[0].info["shared"] is streamed.info["shared"]


def test_distinct_elements_with_equal_ids_in_distinct_meshes_are_not_merged():
    batch = helpers.s3_batch_distinct_built_result(project=None, progress=_quiet)
    decoded = decode_result(_wire(encode_result(batch)))
    first, second = (s.built.fe_model.mesh.elements[101] for s in decoded.shapes)
    assert first is not second


def test_cancellation_during_serialization_is_a_cancellation_not_a_transfer_error():
    from anyfem._transport_spool import TransportSpoolError
    from anyfem.job_process import dumps_spooled

    with pytest.raises(TransportSpoolError) as caught:
        dumps_spooled(("result", b"x" * 100), cancelled=lambda: True)
    assert caught.value.cancelled
    assert not TransportSpoolError("disk full").cancelled


def test_the_worker_carries_a_shared_batch_with_aliases_intact():
    manager, record, _ = run(helpers.s3_shared_batch_result)
    assert record.status == JobStatus.COMPLETED
    result = manager.result(record.id)
    model = result.built.fe_model
    assert all(shape.built.fe_model is model for shape in result.shapes)
    assert all(any(s.built.load_case is c for c in model.load_cases) for s in result.shapes)
