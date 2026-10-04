"""Explicit free-reference input persistence and public owner delegation."""
from dataclasses import replace
from types import SimpleNamespace

import numpy as np
import pytest
from scipy import sparse
from anysolver import stiffened_shell as api
from anysolver.control import CancellationToken
from anyfem.solve.stiffened_shell_b2 import (
    ExperimentalB2MixedReferenceModalPanel, SCHEMA_MIXED_REFERENCE_MODAL,
    SCHEMA_MIXED_FREE_REFERENCE_MODAL,
)
from anyfem.solve.stiffened_shell_project import solve_point_panel
from test_experimental_b2_mixed_reference_modal_consumer import (
    _definition as _supported_definition, _panel as _supported_panel, _reopen,
)


def _definition(kind="mixed"):
    definition = _supported_definition(kind)
    return replace(definition, transformation=sparse.eye(54, format="csr"),
                   boundary_policy=api.B2_FREE_FREE_MODAL_ONLY_V1)


def _panel(kind="mixed"):
    return ExperimentalB2MixedReferenceModalPanel.from_definition(
        _definition(kind), api.B2ReferenceModesRequest(count=2, shift_hz=.5))


@pytest.mark.parametrize("kind", ("q4", "mixed", "s3"))
@pytest.mark.parametrize("route", ("standalone", "project"))
def test_free_input_roundtrip_is_owned_without_creating_owner(tmp_path, monkeypatch, kind, route):
    monkeypatch.setattr(api, "create_analysis", lambda **kw: pytest.fail("persistence created owner"))
    definition = _definition(kind)
    request = api.B2ReferenceModesRequest(count=2, shift_hz=.5)
    panel = ExperimentalB2MixedReferenceModalPanel.from_definition(definition, request)
    original = panel.to_dict()
    definition.shell_nodes_m[2] = (99., 99., 99.)
    definition.transformation.data[:] = 7.
    definition.prescribed_offset[:] = 8.
    object.__setattr__(request, "count", 99)
    restored, _ = _reopen(tmp_path, panel, route)
    assert type(restored) is ExperimentalB2MixedReferenceModalPanel
    assert restored.schema == SCHEMA_MIXED_FREE_REFERENCE_MODAL
    assert restored.status == "EXPERIMENTAL_NOT_QUALIFIED"
    assert restored.payload == panel.payload
    assert restored.to_dict() == original
    rebuilt = restored.definition()
    assert type(rebuilt) is api.B2MixedReferenceModalDefinition
    assert rebuilt.boundary_policy == api.B2_FREE_FREE_MODAL_ONLY_V1
    assert restored.request == api.B2ReferenceModesRequest(count=2, shift_hz=.5)
    rebuilt.shell_nodes_m[2] = (13., 13., 13.)
    rebuilt.transformation.data[:] = 12.
    rebuilt.prescribed_offset[:] = 11.
    assert restored.to_dict() == original


def _immutable_tuples(value):
    assert type(value) is tuple
    for item in value:
        if isinstance(item, (tuple, list, np.ndarray)):
            _immutable_tuples(item)
        else:
            assert isinstance(item, (int, float))


@pytest.mark.parametrize("kind", ("q4", "mixed", "s3"))
@pytest.mark.parametrize("route", ("standalone", "project"))
def test_actual_free_rigid_and_flexible_result_matches_public_owner(tmp_path, kind, route):
    panel = _panel(kind)
    owner = api.create_analysis(definition=_definition(kind))
    try:
        assert type(owner.capabilities) is api.ExperimentalB2MixedReferenceModalCapabilities
        assert owner.capabilities.boundary_policy == api.B2_FREE_FREE_MODAL_ONLY_V1
        assert owner.capabilities.operations == ("modes", "close")
        expected = owner.modes(panel.request)
    finally:
        owner.close()
    restored, record = _reopen(tmp_path, panel, route)
    actual = restored.modes() if record is None else solve_point_panel(record)
    assert type(actual) is api.B2FreeReferenceModesResult
    assert actual.status == expected.status == "ok", (actual.failure_reason, expected.failure_reason)
    assert actual.flexible.status == "ok"
    assert actual.policy == api.B2_FREE_FREE_MODAL_ONLY_V1
    assert actual.production_qualified is actual.flexible.production_qualified is False
    for name in ("rigid_origin_m", "canonical_rigid_modes", "rigid_modes",
                 "rigid_multiplier_modes", "rigid_kinetic_gram",
                 "rigid_equation_residuals", "rigid_constraint_residuals"):
        value = getattr(actual, name)
        _immutable_tuples(value)
        np.testing.assert_allclose(value, getattr(expected, name), rtol=1e-9, atol=1e-12)
    assert len(actual.rigid_modes) == len(actual.canonical_rigid_modes) == 6
    assert len(actual.flexible.frequencies_hz) == 2
    for name in ("frequencies_hz", "physical_modes", "point_multiplier_modes",
                 "equation_residuals", "constraint_residuals"):
        _immutable_tuples(getattr(actual.flexible, name))
    np.testing.assert_allclose(actual.flexible.frequencies_hz,
                               expected.flexible.frequencies_hz, rtol=1e-9, atol=0.)
    assert np.linalg.eigvalsh(np.asarray(actual.rigid_kinetic_gram)).min() > 0.
    assert max(actual.rigid_equation_residuals) <= 1e-8
    assert max(actual.rigid_constraint_residuals) <= 1e-9
    assert max(actual.flexible.equation_residuals) <= 1e-8
    assert max(actual.flexible.constraint_residuals) <= 1e-9
    assert actual.rigid_flexible_mass_overlap <= 1e-8
    assert actual.spectral_shift_rad2_s2 == expected.spectral_shift_rad2_s2


def _fake_owner(panel, result=None, error=None, **cap_changes):
    definition = panel.definition()
    policies = tuple((s.shell_id, api.S3_V2D_TRANSLATIONAL_LUMPED_SECTION_AREAL_MASS_V1
        if type(s) is api.S3V2DPanelShell else api.Q4_DIRECTOR_MASS_POLICY) for s in definition.shells)
    cap = api.ExperimentalB2MixedReferenceModalCapabilities(
        profile_id=definition.profile, operations=("modes", "close"),
        shell_mass_policies=policies, boundary_policy=api.B2_FREE_FREE_MODAL_ONLY_V1)
    owner = SimpleNamespace(capabilities=replace(cap, **cap_changes), closed=False, calls=[])
    def modes(request, *, cancellation=None):
        owner.calls.append((request, cancellation))
        if error is not None:
            raise error
        return result
    owner.modes = modes
    owner.close = lambda: setattr(owner, "closed", True)
    return owner


def _assert_empty_failure(result):
    assert result.flexible.frequencies_hz == result.flexible.physical_modes == ()
    assert result.flexible.point_multiplier_modes == ()
    for name in ("rigid_origin_m", "canonical_rigid_modes", "rigid_modes",
                 "rigid_multiplier_modes", "rigid_kinetic_gram",
                 "rigid_equation_residuals", "rigid_constraint_residuals"):
        assert getattr(result, name) == ()


@pytest.mark.parametrize("status", ("ok", "failed", "cancelled"))
def test_backend_wrapper_is_preserved_by_identity_and_owner_closed(tmp_path, monkeypatch, status):
    panel, record = _reopen(tmp_path, _panel(), "project")
    expected = api.B2FreeReferenceModesResult(status,
        api.B2ReferenceModesResult(status, failure_reason="backend outcome"),
        failure_reason="backend outcome")
    owner = _fake_owner(panel, expected)
    monkeypatch.setattr(api, "create_analysis", lambda **kw: owner)
    token = CancellationToken()
    actual = solve_point_panel(record, cancellation_token=token)
    assert actual is expected
    _assert_empty_failure(actual)
    assert owner.calls == [(panel.request, token)] and owner.closed


@pytest.mark.parametrize("defect", ("old_result", "wrong_policy", "outer_qualified",
    "inner_qualified", "bad_flexible_type"))
def test_incompatible_free_result_fails_typed_and_closes_owner(monkeypatch, defect):
    panel = _panel()
    flexible = api.B2ReferenceModesResult("failed", failure_reason="backend outcome")
    result = api.B2FreeReferenceModesResult("failed", flexible,
        failure_reason="backend outcome")
    if defect == "old_result": result = flexible
    elif defect == "wrong_policy": result = replace(result, policy="unknown")
    elif defect == "outer_qualified": result = replace(result, production_qualified=True)
    elif defect == "inner_qualified":
        result = replace(result, flexible=replace(flexible, production_qualified=True))
    elif defect == "bad_flexible_type":
        result = replace(result, flexible=SimpleNamespace(**flexible.__dict__))
    owner = _fake_owner(panel, result)
    monkeypatch.setattr(api, "create_analysis", lambda **kw: owner)
    with pytest.raises(ValueError, match="incompatible public B2 free"):
        panel.modes()
    assert owner.calls == [(panel.request, None)] and owner.closed


def test_actual_precancellation_has_no_partial_output_and_closes_owner(monkeypatch):
    create = api.create_analysis
    closed = []
    def tracked(**kwargs):
        owner = create(**kwargs)
        close = owner.close
        def tracked_close():
            closed.append(True)
            close()
        owner.close = tracked_close
        return owner
    monkeypatch.setattr(api, "create_analysis", tracked)
    token = CancellationToken(); token.cancel("consumer cancellation")
    result = _panel("s3").modes(cancellation_token=token)
    assert result.status == result.flexible.status == "cancelled"
    assert result.flexible.factorization_count == 0
    _assert_empty_failure(result)
    assert closed == [True]


@pytest.mark.parametrize("changes", (
    {"boundary_policy": None}, {"boundary_policy": "unknown"},
    {"operations": ("modes", "solve_linear_many", "close")}, {"operations": ("modes",)},
    {"inertia_policy": "unknown"}, {"shell_mass_policies": ()},
    {"production_qualified": True}, {"status": "qualified"}, {"profile_id": "wrong"},
    {"s3_rotary_inertia": "invented"}, {"beam_mass_policy": "invented"}))
def test_wrong_capability_fails_before_modes_and_closes(monkeypatch, changes):
    panel = _panel(); owner = _fake_owner(panel, **changes)
    monkeypatch.setattr(api, "create_analysis", lambda **kw: owner)
    with pytest.raises(ValueError, match="does not admit"):
        panel.modes()
    assert owner.closed and not owner.calls


@pytest.mark.parametrize("defect", ("type", "modes", "close"))
def test_wrong_capability_type_or_missing_operation_fails_typed(monkeypatch, defect):
    panel = _panel(); owner = _fake_owner(panel)
    if defect == "type":
        owner.capabilities = SimpleNamespace(**owner.capabilities.__dict__)
    else:
        delattr(owner, defect)
    monkeypatch.setattr(api, "create_analysis", lambda **kw: owner)
    with pytest.raises(ValueError, match="does not admit"):
        panel.modes()
    assert not owner.calls
    if defect != "close":
        assert owner.closed


@pytest.mark.parametrize("name", ("B2_FREE_FREE_MODAL_ONLY_V1", "B2FreeReferenceModesResult"))
def test_missing_public_free_exports_fail_without_affecting_supported(monkeypatch, name):
    payload = _panel().payload
    supported = _supported_panel().payload
    monkeypatch.delattr(api, name)
    with pytest.raises(ValueError, match="lacks public"):
        ExperimentalB2MixedReferenceModalPanel(payload)
    assert ExperimentalB2MixedReferenceModalPanel(supported).payload == supported


@pytest.mark.parametrize("defect", ("old_schema", "supported", "unknown_policy", "missing_policy",
    "mpc", "scaled_identity", "offset", "unknown", "footprints", "density", "mass"))
def test_free_schema_rejects_wrong_policy_unsupported_inputs_and_unknown_fields(defect):
    data = _panel().to_dict()
    if defect == "old_schema": data["schema"] = SCHEMA_MIXED_REFERENCE_MODAL
    elif defect == "supported": data["modal_boundary_policy"] = "supported"
    elif defect == "unknown_policy": data["modal_boundary_policy"] = "unknown"
    elif defect == "missing_policy": data.pop("modal_boundary_policy")
    elif defect == "mpc": data["affine"]["indices"][0] = 1
    elif defect == "scaled_identity": data["affine"]["data"][0] = 2.
    elif defect == "offset": data["affine"]["offset"][0] = .01
    elif defect == "unknown": data["extra"] = True
    elif defect == "footprints": data["footprints"] = []
    elif defect == "density": data["shells"][0]["density_kg_m3"] = None
    elif defect == "mass": data["members"][0]["mass"] = None
    with pytest.raises(ValueError):
        ExperimentalB2MixedReferenceModalPanel.from_dict(data)


def test_old_supported_schema_does_not_acquire_free_semantics():
    supported = _supported_panel()
    assert supported.schema == SCHEMA_MIXED_REFERENCE_MODAL
    assert supported.to_dict()["modal_boundary_policy"] == "supported"
    assert "boundary_policy" not in supported.to_dict()
    assert supported.definition().boundary_policy is None
    data = supported.to_dict(); data["schema"] = SCHEMA_MIXED_FREE_REFERENCE_MODAL
    data["modal_boundary_policy"] = api.B2_FREE_FREE_MODAL_ONLY_V1
    with pytest.raises(ValueError, match="identity"):
        ExperimentalB2MixedReferenceModalPanel.from_dict(data)
    with pytest.raises(ValueError, match="supports"):
        ExperimentalB2MixedReferenceModalPanel.from_definition(
            replace(_definition(), boundary_policy=None), supported.request)
    for boundary in ("supported", "unknown", True):
        with pytest.raises(ValueError):
            ExperimentalB2MixedReferenceModalPanel.from_definition(
                replace(_definition(), boundary_policy=boundary), supported.request)


def test_programming_errors_propagate_and_owner_closes(monkeypatch):
    panel = _panel(); error = RuntimeError("unclassified programming failure")
    owner = _fake_owner(panel, error=error)
    monkeypatch.setattr(api, "create_analysis", lambda **kw: owner)
    with pytest.raises(RuntimeError) as caught:
        panel.modes()
    assert caught.value is error and owner.closed


def test_free_route_rejects_static_and_project_batch_controls_without_owner(tmp_path, monkeypatch):
    panel, record = _reopen(tmp_path, _panel(), "project")
    monkeypatch.setattr(api, "create_analysis", lambda **kw: pytest.fail("invalid route created owner"))
    with pytest.raises(ValueError, match="modes only"):
        panel.solve_linear_many()
    for kwargs in ({"targets": (1.,)}, {"on_case": lambda *_: None},
                   {"increment_control": object()}, {"max_iterations": 20}):
        with pytest.raises(ValueError, match="modes only"):
            solve_point_panel(record, **kwargs)
