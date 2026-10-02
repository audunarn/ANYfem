"""Explicit modal input persistence and public delegation, not qualification."""
from dataclasses import replace
import json
from types import SimpleNamespace

import numpy as np
import pytest
from scipy import sparse
from anysolver import stiffened_shell as api
from anysolver.control import CancellationToken
from anyfem.io.project_file import load_project, save_project
from anyfem.model.project import Project
from anyfem.model.records import AnalysisDefinition
from anyfem.solve.stiffened_shell_b2 import (
    ExperimentalB2MixedReferenceModalPanel, ExperimentalB2MixedPointPanel,
    ExperimentalB2PointPanel, SCHEMA, SCHEMA_MIXED, SCHEMA_MIXED_REFERENCE_MODAL,
    load_case, save_case,
)
from anyfem.solve.stiffened_shell_project import (
    B2_TYPE, B2_FORMULATION, point_panel_from_analysis, solve_point_panel,
)


def _static_inputs(kind="mixed"):
    # Reuse the existing mixed-panel fixture geometry through public records only.
    nodes = {1: (0., 0., 0.), 2: (1., 0., 0.), 3: (2., 0., 0.),
             4: (0., 1., 0.), 5: (1., 1., 0.), 6: (2., 1., 0.)}
    def s3(name, ids):
        return api.S3V2DPanelShell(name, ids, 1000., .3, .15, (0., 0., 1.))
    left = ((api.Q4PanelShell("left", (1, 2, 5, 4), 1000., .3, .15),)
            if kind != "s3" else (s3("left", (1, 2, 5)), s3("left-upper", (1, 5, 4))))
    right = ((api.Q4PanelShell("right", (2, 3, 6, 5), 1000., .3, .15),)
             if kind == "q4" else (s3("right", (2, 3, 6)), s3("right-upper", (2, 6, 5))))
    section = api.LinearSection.isotropic(E=2000., nu=.25, area=.04,
        Iy=.00015, Iz=.0003, J=.0004, ky=.8, kz=.7)
    members = tuple(api.PhysicalB2Member(name, name+"-source", (10+i, 11+i),
        ((.2+i, .5, .2), (1.2+i, .5, .2)), (0., 1., 0.), "section", section)
        for i, name in enumerate(("beam-left", "beam-right")))
    points = (api.Q4B2Point("joint-left", "left", "beam-left", .5, (0., .08, -.03)),
              api.Q4B2Point("joint-right", "right", "beam-right", .5, (0., .08, -.03)))
    fixed = set(range(6)) | set(range(18, 24)) | set(range(36, 42)) | {11, 17, 29, 35}
    free = [i for i in range(54) if i not in fixed]
    definition = api.B2MixedPanelDefinition(api.B2_E4_PL_FAST_LINEAR_V1, nodes,
        left+right, members, points, sparse.eye(54, format="csr")[:, free], np.zeros(54))
    load = np.zeros(54); load[-6:] = (.04, .08, -.03, .01, -.02, .015)
    return definition, (("case", load),)


def _definition(kind="mixed"):
    definition, _ = _static_inputs(kind)
    mass = api.MassMetadata("declared-section-inertia", (.02, -.03, .01),
        tuple(map(tuple, np.diag((.3, .3, .3, .003, .001, .002)))))
    fixed = set(range(6)) | set(range(18, 24)) | set(range(36, 42))
    free = [i for i in range(54) if i not in fixed]
    # Interior rz remains algebraic and free; only the physical roots are fixed.
    return api.B2MixedReferenceModalDefinition(**{**definition.__dict__,
        "shells": tuple(replace(s, density_kg_m3=2.) for s in definition.shells),
        "members": tuple(replace(m, mass=mass) for m in definition.members),
        "transformation": sparse.eye(54, format="csr")[:, free],
        "inertia_policy": api.S3_V2D_TRANSLATIONAL_LUMPED_SECTION_AREAL_MASS_V1})


def _panel(kind="mixed"):
    return ExperimentalB2MixedReferenceModalPanel.from_definition(
        _definition(kind), api.B2ReferenceModesRequest(count=2, shift_hz=.5))


def _reopen(tmp_path, panel, route):
    if route == "standalone":
        return load_case(save_case(panel, tmp_path/"mixed-modal.anyfem-b2.json")), None
    project = Project("mixed reference modes", mesh_only=True)
    record = AnalysisDefinition.experimental_point_panel("modes", panel)
    project.add_analysis(record)
    restored = load_project(save_project(project, tmp_path/"mixed-modal.anyfem")).analyses[record.id]
    assert (restored.type, restored.beam_formulation) == (B2_TYPE, B2_FORMULATION)
    return point_panel_from_analysis(restored), restored


@pytest.mark.parametrize("kind", ("s3", "mixed"))
@pytest.mark.parametrize("route", ("standalone", "project"))
def test_input_roundtrip_owns_definition_request_payload_without_owner(tmp_path, monkeypatch, kind, route):
    monkeypatch.setattr(api, "create_analysis", lambda **kw: pytest.fail("persistence created owner"))
    definition = _definition(kind)
    request = api.B2ReferenceModesRequest(count=2, shift_hz=.5)
    panel = ExperimentalB2MixedReferenceModalPanel.from_definition(definition, request)
    expected = panel.to_dict()
    definition.shell_nodes_m[2] = (99., 99., 99.)
    definition.transformation.data[:] = 7.
    definition.prescribed_offset[:] = 8.
    object.__setattr__(request, "count", 99)
    reopened, _ = _reopen(tmp_path, panel, route)
    assert type(reopened) is ExperimentalB2MixedReferenceModalPanel
    assert reopened.payload == panel.payload
    assert reopened.schema == SCHEMA_MIXED_REFERENCE_MODAL
    assert reopened.status == "EXPERIMENTAL_NOT_QUALIFIED"
    rebuilt = reopened.definition()
    assert type(rebuilt) is api.B2MixedReferenceModalDefinition
    assert rebuilt.inertia_policy == api.S3_V2D_TRANSLATIONAL_LUMPED_SECTION_AREAL_MASS_V1
    assert tuple(type(s) for s in rebuilt.shells) == tuple(type(s) for s in definition.shells)
    assert all(s.density_kg_m3 == 2. for s in rebuilt.shells)
    assert all(m.mass.source_id == "declared-section-inertia" for m in rebuilt.members)
    assert all(rebuilt.transformation.getrow(i).nnz == 1 for i in (11, 17, 29, 35))
    assert reopened.request == api.B2ReferenceModesRequest(count=2, shift_hz=.5)
    rebuilt.shell_nodes_m[2] = (9., 9., 9.)
    rebuilt.transformation.data[:] = 11.
    detached = reopened.to_dict(); detached["request"]["count"] = 7
    assert reopened.to_dict() == expected
    assert "cases" not in expected and "selection" not in expected and "footprints" not in expected


@pytest.mark.parametrize("kind", ("s3", "mixed"))
@pytest.mark.parametrize("route", ("standalone", "project"))
def test_public_modal_result_matches_direct_owner_after_reopen(tmp_path, kind, route):
    panel = _panel(kind)
    owner = api.create_analysis(definition=panel.definition())
    try:
        expected = owner.modes(panel.request)
        policies = tuple((s.shell_id, api.S3_V2D_TRANSLATIONAL_LUMPED_SECTION_AREAL_MASS_V1
            if type(s) is api.S3V2DPanelShell else api.Q4_DIRECTOR_MASS_POLICY)
            for s in panel.definition().shells)
        assert type(owner.capabilities) is api.ExperimentalB2MixedReferenceModalCapabilities
        assert owner.capabilities.shell_mass_policies == policies
    finally:
        owner.close()
    reopened, record = _reopen(tmp_path, panel, route)
    actual = reopened.modes() if record is None else solve_point_panel(record)
    assert type(actual) is api.B2ReferenceModesResult
    assert actual.status == expected.status == "ok", (actual.failure_reason, expected.failure_reason)
    np.testing.assert_allclose(actual.frequencies_hz, expected.frequencies_hz, rtol=1e-9, atol=0.)
    assert actual.factorization_count == 1 and actual.solve_count > 0
    assert len(actual.physical_modes) == len(actual.point_multiplier_modes) == 2
    assert len(actual.physical_modes[0]) == 54
    assert max(actual.equation_residuals) <= 1e-8
    assert max(actual.constraint_residuals) <= 1e-9
    assert actual.production_qualified is False


@pytest.mark.parametrize("defect", ("unknown", "schema", "policy", "policy_none", "policy_missing",
    "density", "density_missing", "mass", "mass_missing", "formulation", "normal", "nodes",
    "duplicate_shell", "duplicate_member", "duplicate_point", "host", "offset", "mpc", "freefree",
    "cases", "selection", "footprints", "prestress", "geometric", "request", "request_extra", "boundary"))
def test_strict_modal_record_rejects_unsupported_or_corrupt_fields(defect):
    data = _panel().to_dict()
    if defect == "unknown": data["extra"] = True
    elif defect == "schema": data["schema"] = SCHEMA_MIXED
    elif defect == "policy": data["inertia_policy"] = api.Q4_DIRECTOR_MASS_POLICY
    elif defect == "policy_none": data["inertia_policy"] = None
    elif defect == "policy_missing": data.pop("inertia_policy")
    elif defect == "density": data["shells"][0]["density_kg_m3"] = 0.
    elif defect == "density_missing": data["shells"][0].pop("density_kg_m3")
    elif defect == "mass": data["members"][0]["mass"] = None
    elif defect == "mass_missing": data["members"][0].pop("mass")
    elif defect == "formulation": data["shells"][1]["formulation"] = "legacy-s3"
    elif defect == "normal": data["shells"][1]["reference_normal"] = [0., 0., 0.]
    elif defect == "nodes": data["shells"][1]["node_ids"].append(5)
    elif defect == "duplicate_shell": data["shells"][1]["shell_id"] = data["shells"][0]["shell_id"]
    elif defect == "duplicate_member": data["members"][1]["member_id"] = data["members"][0]["member_id"]
    elif defect == "duplicate_point": data["points"][1]["point_id"] = data["points"][0]["point_id"]
    elif defect == "host": data["points"][0]["shell_id"] = "absent"
    elif defect == "offset": data["affine"]["offset"][0] = .01
    elif defect == "mpc":
        affine = data["affine"]
        row = next(i for i in range(54) if affine["indptr"][i+1] > affine["indptr"][i])
        position = affine["indptr"][row+1]
        affine["indices"].insert(position, 1)
        affine["data"].insert(position, .25)
        affine["indptr"][row+1:] = [i+1 for i in affine["indptr"][row+1:]]
    elif defect == "freefree":
        t = sparse.eye(54, format="csr")
        data["affine"].update(shape=list(t.shape), indptr=t.indptr.tolist(), indices=t.indices.tolist(), data=t.data.tolist())
    elif defect in ("cases", "footprints", "prestress"): data[defect] = []
    elif defect == "selection": data["selection"] = {}
    elif defect == "geometric": data["members"][0]["geometric"] = {}
    elif defect == "request": data["request"]["count"] = True
    elif defect == "boundary": data["modal_boundary_policy"] = "free-free"
    else: data["request"]["prestress"] = True
    with pytest.raises(ValueError): ExperimentalB2MixedReferenceModalPanel.from_dict(data)


@pytest.mark.parametrize("value", (float("nan"), float("inf"), -float("inf")))
def test_nonfinite_mass_density_controls_rejected(value):
    for field in ("density", "mass", "request"):
        data = _panel().to_dict()
        if field == "density": data["shells"][1]["density_kg_m3"] = value
        elif field == "mass": data["members"][0]["mass"]["matrix_per_m"][0][0] = value
        else: data["request"]["shift_hz"] = value
        with pytest.raises(ValueError): ExperimentalB2MixedReferenceModalPanel.from_dict(data)


def test_duplicate_json_key_and_unknown_policy_from_definition_rejected():
    panel = _panel()
    duplicate = panel.payload.replace(b'"inertia_policy":', b'"inertia_policy":"unknown","inertia_policy":', 1)
    with pytest.raises(ValueError): ExperimentalB2MixedReferenceModalPanel(duplicate)
    for value in (None, "unknown", True):
        with pytest.raises(ValueError):
            ExperimentalB2MixedReferenceModalPanel.from_definition(
                replace(_definition(), inertia_policy=value), panel.request)
    for request in (None, {}, object()):
        with pytest.raises(ValueError):
            ExperimentalB2MixedReferenceModalPanel.from_definition(_definition(), request)


def test_native_analysis_payload_detaches_input_and_reconstructs_owned_definition(tmp_path):
    original = _panel()
    reopened, record = _reopen(tmp_path, original, "project")
    canonical = original.payload
    record.beam_formulation_options["panel"]["request"]["count"] = 1
    modified = point_panel_from_analysis(record)
    assert modified.request.count == 1 and reopened.request.count == original.request.count == 2
    modified.definition().transformation.data[:] = 12.
    assert original.payload == reopened.payload == canonical


@pytest.mark.parametrize("name", ("B2MixedReferenceModalDefinition", "B2ReferenceModesRequest",
    "ExperimentalB2MixedReferenceModalCapabilities", "S3_V2D_TRANSLATIONAL_LUMPED_SECTION_AREAL_MASS_V1"))
def test_missing_public_api_rejected_without_fallback(monkeypatch, name):
    payload = _panel().payload
    monkeypatch.delattr(api, name)
    with pytest.raises(ValueError, match="lacks public"):
        ExperimentalB2MixedReferenceModalPanel(payload)


def _fake_owner(panel, result=None, error=None, **cap_changes):
    definition = panel.definition()
    policies = tuple((s.shell_id, api.S3_V2D_TRANSLATIONAL_LUMPED_SECTION_AREAL_MASS_V1
        if type(s) is api.S3V2DPanelShell else api.Q4_DIRECTOR_MASS_POLICY) for s in definition.shells)
    cap = api.ExperimentalB2MixedReferenceModalCapabilities(
        profile_id=definition.profile, operations=("modes", "close"), shell_mass_policies=policies)
    owner = SimpleNamespace(capabilities=replace(cap, **cap_changes), closed=False, calls=[])
    def modes(request, *, cancellation=None):
        owner.calls.append((request, cancellation))
        if error: raise error
        return result
    owner.modes = modes
    owner.close = lambda: setattr(owner, "closed", True)
    return owner


@pytest.mark.parametrize("status", ("failed", "cancelled"))
def test_complete_failure_or_cancellation_delegated_without_partial_publication(monkeypatch, tmp_path, status):
    panel, record = _reopen(tmp_path, _panel(), "project")
    expected = api.B2ReferenceModesResult(status, failure_reason="declared backend outcome")
    owner = _fake_owner(panel, expected)
    monkeypatch.setattr(api, "create_analysis", lambda **kw: owner)
    token = CancellationToken()
    actual = solve_point_panel(record, cancellation_token=token)
    assert actual is expected and actual.status == status
    assert actual.physical_modes == actual.point_multiplier_modes == actual.frequencies_hz == ()
    assert owner.calls == [(panel.request, token)] and owner.closed


def test_actual_precancelled_modes_have_no_partial_results():
    token = CancellationToken(); token.cancel("consumer modal cancellation")
    actual = _panel("s3").modes(cancellation_token=token)
    assert actual.status == "cancelled" and actual.factorization_count == 0
    assert not actual.physical_modes and not actual.point_multiplier_modes and not actual.frequencies_hz


@pytest.mark.parametrize("changes", ({"inertia_policy": "unknown"}, {"shell_mass_policies": ()},
    {"s3_rotary_inertia": "fabricated rotary inertia"}, {"production_qualified": True},
    {"beam_mass_policy": "invented inertia"}, {"profile_id": "wrong"}, {"operations": ("close",)}))
def test_capability_mismatch_closes_before_modal_call(monkeypatch, changes):
    panel = _panel(); owner = _fake_owner(panel, **changes)
    monkeypatch.setattr(api, "create_analysis", lambda **kw: owner)
    with pytest.raises(ValueError, match="does not admit"): panel.modes()
    assert owner.closed and not owner.calls


def test_programming_error_propagates_and_owner_closes(monkeypatch):
    panel = _panel(); error = RuntimeError("unclassified programming failure")
    owner = _fake_owner(panel, error=error)
    monkeypatch.setattr(api, "create_analysis", lambda **kw: owner)
    with pytest.raises(RuntimeError, match="unclassified") as caught: panel.modes()
    assert caught.value is error and owner.closed


def test_missing_public_q4_mass_identity_and_untyped_capability_fail_closed(monkeypatch):
    panel = _panel(); owner = _fake_owner(panel)
    monkeypatch.setattr(api, "create_analysis", lambda **kw: owner)
    monkeypatch.delattr(api, "Q4_DIRECTOR_MASS_POLICY")
    with pytest.raises(ValueError, match="Q4 director mass-policy"): panel.modes()
    assert owner.closed and not owner.calls
    monkeypatch.undo()
    owner = _fake_owner(panel)
    owner.capabilities = SimpleNamespace(**owner.capabilities.__dict__)
    monkeypatch.setattr(api, "create_analysis", lambda **kw: owner)
    with pytest.raises(ValueError, match="does not admit"): panel.modes()
    assert owner.closed and not owner.calls


def test_old_static_schema_payloads_and_modal_only_dispatch_unchanged(tmp_path, monkeypatch):
    definition, cases = _static_inputs()
    old = ExperimentalB2MixedPointPanel.from_definition(definition, cases, selected_dofs=(49,))
    q4, q4_cases = _static_inputs("q4")
    q4 = api.B2Q4PanelDefinition(**q4.__dict__)
    legacy = ExperimentalB2PointPanel.from_definition(q4, q4_cases, selected_dofs=(49,))
    assert old.schema == SCHEMA_MIXED and legacy.schema == SCHEMA
    assert "inertia_policy" not in old.to_dict() and "inertia_policy" not in json.loads(legacy.payload)
    with pytest.raises(ValueError): ExperimentalB2MixedReferenceModalPanel(old.payload)
    panel, record = _reopen(tmp_path, _panel(), "project")
    monkeypatch.setattr(api, "create_analysis", lambda **kw: pytest.fail("invalid dispatch created owner"))
    with pytest.raises(ValueError, match="modes only"): panel.solve_linear_many()
    for kwargs in ({"targets": (1.,)}, {"on_case": lambda *_: None}):
        with pytest.raises(ValueError, match="modes only"): solve_point_panel(record, **kwargs)
