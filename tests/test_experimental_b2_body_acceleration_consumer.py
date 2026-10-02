"""Input persistence and typed public body-load delegation; not qualification."""
from dataclasses import replace
from types import SimpleNamespace

import numpy as np
import pytest

from anysolver import stiffened_shell as api
from anysolver.control import CancellationToken
from anyfem.io.project_file import load_project, save_project
from anyfem.model.project import Project
from anyfem.model.records import AnalysisDefinition
from anyfem.solve.stiffened_shell_b2 import (
    ExperimentalB2BodyAccelerationPanel, ExperimentalB2PointPanel,
    ExperimentalB2MixedPointPanel, ExperimentalB2NodalRigidFootprintPanel,
    ExperimentalB2ReferenceModalPanel, ExperimentalB2MixedReferenceModalPanel,
    SCHEMA_BODY_ACCELERATION, SCHEMA_MIXED_BODY_ACCELERATION,
    SCHEMA_FOOTPRINT_BODY_ACCELERATION, load_case, save_case,
)
from anyfem.solve.stiffened_shell_project import (
    point_panel_from_analysis, solve_point_panel,
)
from test_experimental_b2_stiffened_shell_consumer import _panel as _q4
from test_experimental_b2_mixed_panel_consumer import _inputs as _mixed
from test_stiffened_shell_b2_nodal_footprint_consumer import _definition as _footprint


def _definition(mesh="q4"):
    definition = (_q4() if mesh == "q4" else _footprint() if mesh == "footprint"
                  else _mixed(mesh)[0])
    mass = api.MassMetadata("declared-physical-inertia", (0., .02, -.015),
        tuple(map(tuple, np.diag((.3, .3, .3, .003, .001, .002)))))
    return replace(definition,
        shells=tuple(replace(s, density_kg_m3=2.) for s in definition.shells),
        members=tuple(replace(m, mass=mass) for m in definition.members))


def _input(mesh="q4", count=3):
    nodal = mesh == "footprint"
    return ExperimentalB2BodyAccelerationPanel.from_definition(
        _definition(mesh), tuple((f"body-{i:03d}", (.2*(1.+i/count), -.1, -.3))
                                for i in range(count)),
        selected_dofs=(49, 50, 53), selected_points=() if nodal else ("joint-right",),
        recover_members={"beam-1" if nodal else "beam-right": (.25, .5, .75)},
        selected_footprints=(api.B2NodalRigidFootprintSelection(("right-patch",))
                             if nodal else None))


def _reopen(tmp_path, panel, route):
    if route == "standalone":
        return load_case(save_case(panel, tmp_path/"body.anyfem-b2.json")), None
    project = Project("experimental B2 reference body acceleration", mesh_only=True)
    analysis = AnalysisDefinition.experimental_point_panel("body", panel)
    project.add_analysis(analysis)
    restored = load_project(save_project(project, tmp_path/"body.anyfem")).analyses[analysis.id]
    return point_panel_from_analysis(restored), restored


@pytest.mark.parametrize("mesh", ("q4", "mixed", "s3", "footprint"))
@pytest.mark.parametrize("route", ("standalone", "project"))
def test_reopen_is_detached_syntax_only(tmp_path, monkeypatch, mesh, route):
    monkeypatch.setattr(api, "create_analysis", lambda **kw: pytest.fail("persistence assembled an owner"))
    panel = _input(mesh)
    expected = panel.to_dict()
    rebuilt = panel.to_definition()
    rebuilt.transformation.data[:] = 7.
    rebuilt.prescribed_offset[:] = 8.
    rebuilt.shell_nodes_m[2] = (99., 99., 99.)
    reopened, _ = _reopen(tmp_path, panel, route)
    assert type(reopened) is ExperimentalB2BodyAccelerationPanel
    assert reopened.payload == panel.payload and reopened.to_dict() == expected
    assert reopened.status == "EXPERIMENTAL_NOT_QUALIFIED"
    assert reopened.schema == (SCHEMA_FOOTPRINT_BODY_ACCELERATION if mesh == "footprint" else
                               SCHEMA_BODY_ACCELERATION if mesh == "q4" else SCHEMA_MIXED_BODY_ACCELERATION)
    assert reopened.acceleration_cases == panel.acceleration_cases
    mass = reopened.to_definition().members[0].mass
    assert mass.source_id == "declared-physical-inertia" and mass.origin_local_m == (0., .02, -.015)
    assert "load" not in expected["cases"][0] and "model_identity" not in expected
    detached = reopened.to_dict(); detached["cases"][0]["acceleration_m_s2"][0] = 99.
    assert reopened.to_dict() == expected


@pytest.mark.parametrize("mesh", ("q4", "mixed", "s3", "footprint"))
@pytest.mark.parametrize("route", ("standalone", "project"))
def test_roundtrip_preserves_typed_public_owner_result(tmp_path, mesh, route):
    panel = _input(mesh)
    owner = api.create_analysis(definition=panel.to_definition())
    try:
        typed = tuple((name, owner.body_acceleration_load(acceleration))
                      for name, acceleration in panel.acceleration_cases)
        options = dict(selected_dofs=(49, 50, 53),
            selected_points=() if mesh == "footprint" else ("joint-right",),
            recover_members={"beam-1" if mesh == "footprint" else "beam-right": (.25, .5, .75)})
        if mesh == "footprint":
            options["selected_footprints"] = api.B2NodalRigidFootprintSelection(("right-patch",))
        expected = owner.solve_linear_many(typed, **options)
    finally:
        owner.close()
    reopened, analysis = _reopen(tmp_path, panel, route)
    result = reopened.solve_linear_many() if analysis is None else solve_point_panel(analysis)
    assert result.status == expected.status == "completed", result.failure_reason
    assert result.factorization_count == 1 and result.solve_count == 3
    assert result.selected_dofs == (49, 50, 53)
    for actual, direct, (_, acceleration) in zip(result.cases, expected.cases, panel.acceleration_cases):
        assert type(actual) is type(direct)
        assert actual.case_id == direct.case_id
        assert actual.body_acceleration_m_s2 == acceleration
        assert actual.body_model_identity == direct.body_model_identity and actual.body_model_identity
        for field in ("selected_displacements", "selected_affine_reactions"):
            np.testing.assert_allclose(getattr(actual, field), getattr(direct, field), rtol=1e-10, atol=1e-12)
        assert actual.point_multipliers == direct.point_multipliers
        assert actual.beam_resultants_local == direct.beam_resultants_local
        assert actual.equilibrated_sections == direct.equilibrated_sections
        fields, = actual.equilibrated_sections
        assert fields.stations == (.25, .5, .75)
        assert len(fields.resultants_left) == len(fields.resultants_right) == 3
        assert len(fields.strains_left) == len(fields.strains_right) == 3
        assert fields.production_qualified is False
        if mesh == "footprint":
            assert actual.footprint_wrenches == direct.footprint_wrenches
            assert result.selected_footprints == ("right-patch",)


@pytest.mark.parametrize("count", (1, 10, 100))
def test_ordered_body_batches_reuse_one_factorization(count):
    panel = _input(count=count)
    result = panel.solve_linear_many()
    assert result.status == "completed", result.failure_reason
    assert result.factorization_count == 1 and result.solve_count == count
    assert result.maximum_rhs_chunk == 1
    assert tuple(case.case_id for case in result.cases) == tuple(name for name, _ in panel.acceleration_cases)
    assert all(len(case.selected_displacements) == 3 and len(case.equilibrated_sections) == 1
               for case in result.cases)


@pytest.mark.parametrize("mesh", ("q4", "footprint"))
def test_project_cancelled_complete_prefix(tmp_path, mesh):
    _, analysis = _reopen(tmp_path, _input(mesh, count=10), "project")
    token = CancellationToken()
    seen = []
    def cancel_after_case(case_id):
        seen.append(case_id)
        token.cancel("consumer complete-prefix check")
    result = solve_point_panel(analysis, cancellation_token=token, on_case=cancel_after_case)
    assert result.status == "cancelled" and len(result.cases) == 1
    assert result.factorization_count == 1 and result.solve_count == 1
    assert tuple(case.case_id for case in result.cases) == tuple(seen) == ("body-000",)
    completed, = result.cases
    assert len(completed.equilibrated_sections) == 1
    assert completed.body_acceleration_m_s2 == (.2, -.1, -.3)
    assert completed.body_model_identity


@pytest.mark.parametrize("defect", (
    "unknown", "schema", "profile", "status", "case_unknown", "case_load", "case_duplicate",
    "boolean", "vector", "nonfinite", "density", "density_bool", "density_missing",
    "mass_missing", "mass_none", "mass_zero", "mass_origin", "member_duplicate", "shell_duplicate",
    "point_duplicate", "point_host", "dof_duplicate", "dof_range", "selected_unknown", "station_bool",
))
def test_bad_or_unsupported_input_fails_closed(defect):
    data = _input().to_dict()
    if defect == "unknown": data["gravity_policy"] = "finite"
    elif defect == "schema": data["schema"] = "anyfem.experimental-b2-body-v999"
    elif defect == "profile": data["profile"] = "default"
    elif defect == "status": data["status"] = "QUALIFIED"
    elif defect == "case_unknown": data["cases"][0]["extra"] = 1
    elif defect == "case_load": data["cases"][0]["load"] = [0.]*54
    elif defect == "case_duplicate": data["cases"].append(data["cases"][0])
    elif defect == "boolean": data["cases"][0]["acceleration_m_s2"][0] = True
    elif defect == "vector": data["cases"][0]["acceleration_m_s2"] = [1., 2.]
    elif defect == "nonfinite": data["cases"][0]["acceleration_m_s2"][0] = float("inf")
    elif defect == "density": data["shells"][0]["density_kg_m3"] = 0.
    elif defect == "density_bool": data["shells"][0]["density_kg_m3"] = True
    elif defect == "density_missing": data["shells"][0].pop("density_kg_m3")
    elif defect == "mass_missing": data["members"][0].pop("mass")
    elif defect == "mass_none": data["members"][0]["mass"] = None
    elif defect == "mass_zero": data["members"][0]["mass"]["matrix_per_m"] = [[0.]*6 for _ in range(6)]
    elif defect == "mass_origin": data["members"][0]["mass"]["origin_local_m"][1] = True
    elif defect == "member_duplicate": data["members"].append(data["members"][0])
    elif defect == "shell_duplicate": data["shells"].append(data["shells"][0])
    elif defect == "point_duplicate": data["points"].append(data["points"][0])
    elif defect == "point_host": data["points"][0]["shell_id"] = "unknown"
    elif defect == "dof_duplicate": data["selection"]["dofs"].append(data["selection"]["dofs"][0])
    elif defect == "dof_range": data["selection"]["dofs"][0] = 54
    elif defect == "selected_unknown": data["selection"]["members"] = {"unknown": [.5]}
    elif defect == "station_bool": data["selection"]["members"]["beam-right"][0] = True
    with pytest.raises(ValueError):
        ExperimentalB2BodyAccelerationPanel.from_dict(data)


def test_duplicate_json_field_and_nonfinite_payload_rejected():
    payload = _input().payload
    with pytest.raises(ValueError, match="duplicate"):
        ExperimentalB2BodyAccelerationPanel(payload.replace(b'"status":', b'"status":"EXPERIMENTAL_NOT_QUALIFIED","status":', 1))
    with pytest.raises(ValueError, match="nonfinite"):
        ExperimentalB2BodyAccelerationPanel(payload.replace(b'0.2', b'NaN', 1))


@pytest.mark.parametrize("acceleration", ((.2, True, -.3), (True, False, True)))
def test_constructor_does_not_coerce_boolean_acceleration(acceleration):
    with pytest.raises(ValueError, match="finite"):
        ExperimentalB2BodyAccelerationPanel.from_definition(_definition(),
            (("body", acceleration),), selected_dofs=(49,))


def test_old_solver_capability_fails_explicitly_and_closes_owner(monkeypatch):
    closed = []
    owner = SimpleNamespace(capabilities=SimpleNamespace(status="experimental",
        production_qualified=False, operations=("solve_linear_many", "close")),
        close=lambda: closed.append(True),
        solve_linear_many=lambda *a, **kw: pytest.fail("unsupported body load solved"))
    monkeypatch.setattr(api, "create_analysis", lambda **kw: owner)
    with pytest.raises(ValueError, match="body-load capability"):
        _input().solve_linear_many()
    assert closed == [True]


@pytest.mark.parametrize("missing_export", (
    "B2LoadedMemberSections", "B2BodyLoadedPanelResult", "B2BodyLoadedFootprintResult",
    "B2BodyLoadedPanelSelectedCase", "B2BodyLoadedFootprintSelectedCase",
))
def test_preparation_only_solver_without_typed_recovery_is_rejected(monkeypatch, missing_export):
    panel = _input()
    closed = []
    owner = SimpleNamespace(capabilities=SimpleNamespace(status="experimental",
        production_qualified=False, operations=("body_acceleration_load", "solve_linear_many", "close")),
        body_acceleration_load=lambda *a, **kw: pytest.fail("body generated without typed recovery"),
        solve_linear_many=lambda *a, **kw: pytest.fail("body solved without typed recovery"),
        close=lambda: closed.append(True))
    assert api.B2ReferenceBodyLoad is not None
    monkeypatch.setattr(api, "create_analysis", lambda **kw: owner)
    monkeypatch.delattr(api, missing_export)
    with pytest.raises(ValueError, match=f"typed B2 body-load recovery exports: {missing_export}"):
        panel.solve_linear_many()
    assert closed == [True]


@pytest.mark.parametrize("failure", ("body", "solve"))
def test_owner_closes_when_load_generation_or_batch_execution_raises(monkeypatch, failure):
    closed = []
    def raise_error(*args, **kwargs):
        raise RuntimeError("owned operation failed")
    def make_load(acceleration):
        return api.B2ReferenceBodyLoad(acceleration, tuple(range(1, 7)), (10, 11, 12),
                                       np.zeros(54), "fake-model")
    def fail_solve(cases, **kwargs):
        assert all(type(load) is api.B2ReferenceBodyLoad for _, load in cases)
        return raise_error()
    owner = SimpleNamespace(capabilities=SimpleNamespace(status="experimental",
        production_qualified=False, operations=("body_acceleration_load", "solve_linear_many", "close")),
        body_acceleration_load=raise_error if failure == "body" else make_load,
        solve_linear_many=fail_solve, close=lambda: closed.append(True))
    monkeypatch.setattr(api, "create_analysis", lambda **kw: owner)
    with pytest.raises(RuntimeError, match="owned operation failed"):
        _input().solve_linear_many()
    assert closed == [True]


def test_legacy_schemas_and_modal_dispatch_remain_distinct(tmp_path):
    legacy = ExperimentalB2PointPanel.from_definition(_q4(), (("legacy", np.zeros(54)),), selected_dofs=(49,))
    reopened, _ = _reopen(tmp_path, legacy, "project")
    assert type(reopened) is ExperimentalB2PointPanel and reopened.payload == legacy.payload
    for consumer in (ExperimentalB2PointPanel, ExperimentalB2MixedPointPanel,
                     ExperimentalB2NodalRigidFootprintPanel, ExperimentalB2ReferenceModalPanel,
                     ExperimentalB2MixedReferenceModalPanel):
        with pytest.raises(ValueError):
            consumer(_input().payload)
    body = _input()
    with pytest.raises(ValueError, match="modes"):
        body.modes()
    _, analysis = _reopen(tmp_path, body, "project")
    with pytest.raises(ValueError, match="finite"):
        solve_point_panel(analysis, targets=(1.,))


@pytest.mark.parametrize("mesh", ("q4", "mixed", "s3", "footprint"))
def test_missing_physical_inputs_rejected_before_assembly(mesh):
    definition = _definition(mesh)
    with pytest.raises(ValueError, match="density"):
        ExperimentalB2BodyAccelerationPanel.from_definition(
            replace(definition, shells=tuple(replace(s, density_kg_m3=None) for s in definition.shells)),
            (("body", (0., 0., -1.)),), selected_dofs=(49,))
    with pytest.raises(ValueError, match="mass"):
        ExperimentalB2BodyAccelerationPanel.from_definition(
            replace(definition, members=tuple(replace(m, mass=None) for m in definition.members)),
            (("body", (0., 0., -1.)),), selected_dofs=(49,))
