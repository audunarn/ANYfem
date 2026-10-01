"""Input persistence and public modal dispatch; no mechanical qualification claim."""
from dataclasses import replace
from types import SimpleNamespace

import numpy as np
import pytest
from scipy import sparse
from anysolver import stiffened_shell as api
from anysolver.control import CancellationToken
from anyfem.io.project_file import save_project, load_project
from anyfem.model.project import Project
from anyfem.model.records import AnalysisDefinition
from anyfem.solve.stiffened_shell_b2 import (
    ExperimentalB2ReferenceModalPanel, ExperimentalB2NodalRigidFootprintPanel,
    SCHEMA_REFERENCE_MODAL, SCHEMA_NODAL_FOOTPRINT, save_case, load_case,
)
from anyfem.solve.stiffened_shell_project import (
    B2_TYPE, B2_FORMULATION, point_panel_from_analysis, solve_point_panel,
)
from test_stiffened_shell_b2_nodal_footprint_consumer import _definition as _static_definition


def _definition():
    definition = _static_definition()
    mass = api.MassMetadata("explicit-physical-inertia", (0., 0., 0.),
                           tuple(map(tuple, np.diag((.3, .3, .3, .003, .001, .002)))))
    return replace(definition,
        shells=tuple(replace(s, density_kg_m3=2.) for s in definition.shells),
        members=tuple(replace(m, mass=mass) for m in definition.members))


def _input():
    return ExperimentalB2ReferenceModalPanel.from_definition(
        _definition(), api.B2ReferenceModesRequest(count=2, shift_hz=.5))


def _project(panel):
    project = Project("experimental B2 footprint reference modes", mesh_only=True)
    record = AnalysisDefinition.experimental_point_panel("modes", panel)
    project.add_analysis(record)
    return project, record


def _reopen(tmp_path, panel, route):
    if route == "standalone":
        return load_case(save_case(panel, tmp_path/"modal.anyfem-b2.json")), None
    project, record = _project(panel)
    restored = load_project(save_project(project, tmp_path/"modal.anyfem")).analyses[record.id]
    assert (restored.type, restored.beam_formulation) == (B2_TYPE, B2_FORMULATION)
    return point_panel_from_analysis(restored), restored


@pytest.mark.parametrize("route", ("standalone", "project"))
def test_construct_save_reopen_never_creates_owner_and_owns_inputs(tmp_path, monkeypatch, route):
    monkeypatch.setattr(api, "create_analysis", lambda **kw: pytest.fail("input persistence assembled an owner"))
    definition = _definition()
    panel = ExperimentalB2ReferenceModalPanel.from_definition(definition, api.B2ReferenceModesRequest(count=2))
    expected = panel.to_dict()
    definition.transformation.data[:] = 3.
    definition.prescribed_offset[:] = 9.
    reopened, _ = _reopen(tmp_path, panel, route)
    assert type(reopened) is ExperimentalB2ReferenceModalPanel
    assert reopened.to_dict() == expected
    assert reopened.schema == SCHEMA_REFERENCE_MODAL
    assert reopened.status == "EXPERIMENTAL_NOT_QUALIFIED"
    assert reopened.request == api.B2ReferenceModesRequest(count=2)
    assert reopened.definition().members[0].mass.source_id == "explicit-physical-inertia"
    assert expected["footprints"][0]["normal_spin"] == "locked"
    assert "cases" not in expected and "selection" not in expected
    detached = reopened.to_dict(); detached["request"]["count"] = 99
    assert reopened.to_dict() == expected


@pytest.mark.parametrize("route", ("standalone", "project"))
def test_roundtrip_preserves_full_public_modal_result(tmp_path, route):
    panel = _input()
    owner = api.create_analysis(definition=panel.definition())
    try:
        expected = owner.modes(panel.request)
    finally:
        owner.close()
    reopened, record = _reopen(tmp_path, panel, route)
    result = reopened.modes() if record is None else solve_point_panel(record)
    assert type(result) is api.B2FootprintReferenceModesResult
    assert result.status == expected.status == "ok", result.failure_reason
    np.testing.assert_allclose(result.frequencies_hz, expected.frequencies_hz, rtol=1e-9, atol=0.)
    assert result.constraint_rows == expected.constraint_rows
    assert result.footprint_ids == ("right-patch",)
    assert result.footprint_law_id == reopened.to_dict()["footprints"][0]["law"]
    assert result.normal_spin == "locked" and result.mechanical_qualification == "incomplete"
    assert result.production_qualified is False
    assert result.factorization_count == 1 and result.solve_count > 0
    assert len(result.physical_modes) == len(result.constraint_multiplier_modes) == 2
    assert len(result.physical_modes[0]) == 54
    assert len(result.constraint_multiplier_modes[0]) == len(result.constraint_rows) == 24
    assert not result.point_multiplier_modes
    assert max(result.equation_residuals) <= 1e-8
    assert max(result.constraint_residuals) <= 1e-9


def test_project_preserves_public_cancelled_status(tmp_path):
    _, record = _reopen(tmp_path, _input(), "project")
    token = CancellationToken(); token.cancel("consumer modal cancellation")
    result = solve_point_panel(record, cancellation_token=token)
    assert type(result) is api.B2FootprintReferenceModesResult
    assert result.status == "cancelled" and result.factorization_count == 0
    assert not result.physical_modes and not result.constraint_multiplier_modes
    assert len(result.constraint_rows) == 24


@pytest.mark.parametrize("defect", ("unknown", "schema", "density", "missing_density", "mass",
    "missing_mass", "law", "spin", "qualified", "policy", "prestress", "cases", "selection",
    "offset", "mpc", "freefree", "request", "request_unknown", "empty_footprints"))
def test_unsupported_or_corrupt_input_fails_closed(defect):
    data = _input().to_dict()
    if defect == "unknown": data["extra"] = True
    elif defect == "schema": data["schema"] = SCHEMA_NODAL_FOOTPRINT
    elif defect == "density": data["shells"][0]["density_kg_m3"] = 0.
    elif defect == "missing_density": data["shells"][0].pop("density_kg_m3")
    elif defect == "mass": data["members"][0]["mass"] = None
    elif defect == "missing_mass": data["members"][0].pop("mass")
    elif defect == "law": data["footprints"][0]["law"] = "GE"
    elif defect == "spin": data["footprints"][0]["normal_spin"] = "released"
    elif defect == "qualified": data["footprints"][0]["production_qualified"] = True
    elif defect == "policy": data["modal_boundary_policy"] = "free-free"
    elif defect == "prestress": data["prestress"] = []
    elif defect == "cases": data["cases"] = []
    elif defect == "selection": data["selection"] = {}
    elif defect == "offset": data["affine"]["offset"][0] = .01
    elif defect == "mpc": data["affine"]["indices"][1] = data["affine"]["indices"][0]
    elif defect == "freefree":
        t = sparse.eye(54, format="csr")
        data["affine"].update(shape=list(t.shape), indptr=t.indptr.tolist(),
            indices=t.indices.tolist(), data=t.data.tolist())
    elif defect == "request": data["request"]["count"] = True
    elif defect == "request_unknown": data["request"]["prestress"] = True
    else: data["footprints"] = []
    with pytest.raises(ValueError): ExperimentalB2ReferenceModalPanel.from_dict(data)


@pytest.mark.parametrize("bad_request", (None, {"count": 2}, object()))
def test_requires_public_typed_request(bad_request):
    with pytest.raises(ValueError, match="typed"):
        ExperimentalB2ReferenceModalPanel.from_definition(_definition(), bad_request)


def test_static_footprint_schema_keeps_its_mass_and_mode_rejection():
    definition = _definition()
    with pytest.raises(ValueError):
        ExperimentalB2NodalRigidFootprintPanel.from_definition(definition,
            (("case", np.zeros(54)),), selected_dofs=(49,))
    static = ExperimentalB2NodalRigidFootprintPanel.from_definition(_static_definition(),
        (("case", np.zeros(54)),), selected_dofs=(49,))
    with pytest.raises(ValueError, match="static only"):
        static.modes(api.B2ReferenceModesRequest())
    with pytest.raises(ValueError, match="modes only"):
        _input().solve_linear_many()


@pytest.mark.parametrize("kwargs", ({"targets": (1.,)}, {"increment_control": object()},
    {"on_accepted": lambda r: None}, {"on_case": lambda r: None},
    {"max_iterations": 13}, {"max_backtracks": 9}))
def test_project_rejects_finite_and_batch_controls_before_owner(monkeypatch, kwargs):
    _, record = _project(_input())
    monkeypatch.setattr(api, "create_analysis", lambda **kw: pytest.fail("controls admitted before owner"))
    with pytest.raises(ValueError, match="modes only"): solve_point_panel(record, **kwargs)


def test_mutable_project_is_revalidated_before_owner(monkeypatch):
    _, record = _project(_input())
    record.beam_formulation_options["panel"]["request"]["shift_hz"] = 0.
    monkeypatch.setattr(api, "create_analysis", lambda **kw: pytest.fail("invalid Project created owner"))
    with pytest.raises(ValueError): solve_point_panel(record)


@pytest.mark.parametrize("outcome", ("ok", "cancelled", "numerical_failure", "raised", "capability"))
def test_fresh_owner_closes_and_preserves_status_or_exception(monkeypatch, outcome):
    panel = _input()
    definition = panel.definition()
    cap = api.ExperimentalB2Q4FootprintCapabilities(footprints=definition.footprints,
        operations=("solve_linear", "solve_linear_many", "modes", "close"))
    sentinel = SimpleNamespace(status=outcome, failure_reason="owner detail")
    token = CancellationToken(); owners = []
    class Owner:
        capabilities = cap if outcome != "capability" else SimpleNamespace(operations=("modes",))
        def __init__(self): self.closed = False
        def close(self): self.closed = True
        def modes(self, request, *, cancellation):
            assert request == panel.request and cancellation is token
            if outcome == "raised": raise RuntimeError("owner error")
            return sentinel
    def factory(**kw):
        assert type(kw["definition"]) is api.B2Q4PanelDefinition
        owner = Owner(); owners.append(owner); return owner
    monkeypatch.setattr(api, "create_analysis", factory)
    for _ in range(2):
        if outcome in ("raised", "capability"):
            with pytest.raises(RuntimeError if outcome == "raised" else ValueError):
                panel.modes(cancellation_token=token)
        else:
            assert panel.modes(cancellation_token=token) is sentinel
    assert len(owners) == 2 and all(owner.closed for owner in owners)


def test_owner_rejects_under_supported_physical_rigid_fields():
    definition = _definition()
    # One translational support leaves physical rigid rotations unrestrained.
    definition = replace(definition, transformation=sparse.eye(54, format="csr")[:, 1:])
    panel = ExperimentalB2ReferenceModalPanel.from_definition(definition, api.B2ReferenceModesRequest(count=2))
    with pytest.raises(ValueError, match="admit supported"):
        panel.create_analysis()
