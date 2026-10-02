"""Public GE pressure-load input persistence and owner dispatch, without copied mechanics."""
import copy
from dataclasses import replace

import pytest
from anysolver import stiffened_shell

from anyfem.io.project_file import load_project, save_project
from anyfem.model.project import Project
from anyfem.model.records import AnalysisDefinition
from anyfem.solve.stiffened_shell import (
    ExperimentalGEPointPanel, ExperimentalGENodalRigidFootprintPanel,
    SCHEMA, SCHEMA_PRESSURE_LOAD, load_case, save_case,
)
from anyfem.solve.stiffened_shell_project import point_panel_from_analysis, solve_point_panel
from test_stiffened_shell_project import _ge
from test_stiffened_shell_staged_load_consumer import Owner, MODEL, _panel as _staged_panel


def _panel(identity=MODEL):
    return replace(_ge(), actions=(),
                   pressure_load=stiffened_shell.GEShellPressureLoad(identity, (
                       stiffened_shell.GEShellPressureAction(1, 100., "current_surface"),)))


def test_pressure_save_reopen_project_and_standalone_owns_inputs(tmp_path, monkeypatch):
    def forbidden(**options):
        pytest.fail("input persistence constructed a mechanical owner")
    monkeypatch.setattr(stiffened_shell, "create_analysis", forbidden)
    body = stiffened_shell.GEShellPressureLoad(MODEL, (
        stiffened_shell.GEShellPressureAction(1, 100., "current_surface"),))
    panel = replace(_ge(), actions=(), pressure_load=body)
    payload = panel.to_dict()
    assert payload["schema"] == SCHEMA_PRESSURE_LOAD
    assert payload["panel"]["schema"] == SCHEMA
    assert panel.pressure_load is not body
    standalone = load_case(save_case(panel, tmp_path / "pressure.anyfem-ge.json"))
    project = Project(name="explicit pressure")
    record = AnalysisDefinition.experimental_point_panel("pressure", panel)
    project.analyses[record.id] = record
    reopened = load_project(save_project(project, tmp_path / "pressure.anyfem"))
    restored = point_panel_from_analysis(reopened.analyses[record.id])
    assert restored.to_dict() == standalone.to_dict() == payload
    assert restored.load_input.serialize() == body.serialize()
    assert restored.actions == () and restored.status == "EXPERIMENTAL_NOT_QUALIFIED"
    payload["pressure_load"]["actions"][0]["pressure_pa"] = 999.
    assert restored.pressure_load.actions[0].pressure_pa == 100.
    assert restored.pressure_load.actions[0].configuration == "current_surface"
    assert record.beam_formulation_options["panel"]["pressure_load"]["actions"][0]["pressure_pa"] == 100.



@pytest.mark.parametrize("change", ["extra", "missing", "nested", "implicit", "identity", "actions", "status", "profile",
                                    "row-extra", "mode-missing", "duplicate", "bad-mode", "staged-inner"])
def test_pressure_strict_schema_and_unsupported_mix_rejected(change):
    payload = _panel().to_dict()
    if change == "extra":
        payload["unexpected"] = None
    elif change == "missing":
        del payload["pressure_load"]
    elif change == "nested":
        payload["panel"] = copy.deepcopy(payload)
    elif change == "implicit":
        payload = payload["panel"] | {"pressure_load": payload["pressure_load"]}
    elif change == "identity":
        payload["pressure_load"]["actions"][0]["pressure_pa"] = -1.
    elif change == "actions":
        payload["panel"]["actions"] = _ge().to_dict()["actions"]
    elif change == "row-extra":
        payload["pressure_load"]["actions"][0]["unexpected"] = None
    elif change == "mode-missing":
        del payload["pressure_load"]["actions"][0]["configuration"]
    elif change == "duplicate":
        payload["pressure_load"]["actions"] *= 2
    elif change == "bad-mode":
        payload["pressure_load"]["actions"][0]["configuration"] = "unsupported"
    elif change == "staged-inner":
        payload["panel"] = _staged_panel().to_dict()
    else:
        payload[change] = "unsupported"
    with pytest.raises(ValueError):
        ExperimentalGEPointPanel.from_dict(payload)


def test_pressure_excludes_actions_staging_and_untyped_input():
    with pytest.raises(ValueError, match="empty legacy actions"):
        replace(_ge(), pressure_load=_panel().pressure_load)
    with pytest.raises(ValueError, match="no staged programme"):
        replace(_staged_panel(), pressure_load=_panel().pressure_load)
    with pytest.raises(ValueError, match="public experimental GE pressure load"):
        replace(_ge(), actions=(), pressure_load=object())
    body = stiffened_shell.GEBodyLoad(MODEL, (0., 0., -9.81))
    with pytest.raises(ValueError, match="no staged programme or body/distributed load"):
        replace(_panel(), body_load=body)
    distributed = stiffened_shell.GEBeamDistributedLoad(MODEL, (
        stiffened_shell.GEBeamLineAction(71, [0., 0., -1.], [0., 0., 0.]),))
    with pytest.raises(ValueError, match="body/distributed load"):
        replace(_panel(), distributed_load=distributed)


def test_pressure_unsupported_footprint_wrapper_rejected():
    with pytest.raises(ValueError, match="pressure load"):
        ExperimentalGENodalRigidFootprintPanel(_panel(), ())


def test_pressure_missing_public_solver_contract_fails_closed(monkeypatch):
    panel = _panel()
    payload = panel.to_dict()
    monkeypatch.delattr(stiffened_shell, "GEShellPressureLoad")
    def forbidden(**options):
        pytest.fail("unsupported installed solver constructed an owner")
    monkeypatch.setattr(stiffened_shell, "create_analysis", forbidden)
    for operation in (lambda: panel.create_analysis(),
                      lambda: ExperimentalGEPointPanel.from_dict(payload),
                      lambda: replace(panel)):
        with pytest.raises(ValueError, match="public experimental GE pressure load"):
            operation()


def test_pressure_wrong_model_rejected_and_owner_closed(monkeypatch):
    owner = Owner(identity="cd" * 32)
    monkeypatch.setattr(stiffened_shell, "create_analysis", lambda **options: owner)
    with pytest.raises(ValueError, match="different panel model"):
        _panel().create_analysis()
    assert owner.closed and owner.calls == []


def test_pressure_dispatch_forwards_linear_and_explicit_finite_controls(tmp_path, monkeypatch):
    project = Project(name="pressure dispatch")
    record = AnalysisDefinition.experimental_point_panel("pressure", _panel())
    project.analyses[record.id] = record
    reopened = load_project(save_project(project, tmp_path / "dispatch.anyfem"))
    record = reopened.analyses[record.id]
    linear = Owner()
    monkeypatch.setattr(stiffened_shell, "create_analysis", lambda **options: linear)
    token, observer, control = object(), lambda result: None, object()
    assert solve_point_panel(record, cancellation_token=token) == {"status": "completed"}
    kind, load, options = linear.calls[0]
    assert kind == "linear" and load.serialize() == _panel().pressure_load.serialize()
    assert options == {"cancellation_token": token} and linear.closed
    finite = Owner()
    monkeypatch.setattr(stiffened_shell, "create_analysis", lambda **options: finite)
    result = solve_point_panel(record, targets=(.5, 1.), cancellation_token=token,
                              on_accepted=observer, increment_control=control,
                              max_iterations=20, max_backtracks=12)
    assert result == {"status": "accepted_prefix", "targets": (.5, 1.)}
    kind, load, targets, options = finite.calls[0]
    assert kind == "finite" and targets == (.5, 1.) and finite.closed
    assert load.serialize() == _panel().pressure_load.serialize()
    assert options == dict(cancellation_token=token, on_accepted=observer,
                           increment_control=control, max_iterations=20, max_backtracks=12)
    with pytest.raises(ValueError, match="finite solve controls"):
        solve_point_panel(record, increment_control=control)
    with pytest.raises(ValueError, match="case callback"):
        solve_point_panel(record, on_case=observer)


def test_pressure_dispatch_preserves_owner_failure_and_closes(monkeypatch):
    failure = MemoryError("pressure owner failure")
    owner = Owner(failure=failure)
    monkeypatch.setattr(stiffened_shell, "create_analysis", lambda **options: owner)
    record = AnalysisDefinition.experimental_point_panel("pressure", _panel())
    with pytest.raises(MemoryError) as caught:
        solve_point_panel(record, targets=(1.,))
    assert caught.value is failure and owner.closed


def test_pressure_mutated_project_payload_revalidated_on_save(tmp_path):
    project = Project(name="pressure mutation")
    record = AnalysisDefinition.experimental_point_panel("pressure", _panel())
    project.analyses[record.id] = record
    record.beam_formulation_options["panel"]["pressure_load"]["actions"][0]["pressure_pa"] = -1.
    with pytest.raises(ValueError):
        save_project(project, tmp_path / "invalid.anyfem")


@pytest.mark.parametrize("finite", [False, True])
def test_pressure_preflight_failure_preserved_and_owner_closed(monkeypatch, finite):
    failure = RuntimeError("model admission failed")
    class FailingOwner(Owner):
        def preflight(self):
            raise failure
    owner = FailingOwner()
    monkeypatch.setattr(stiffened_shell, "create_analysis", lambda **options: owner)
    record = AnalysisDefinition.experimental_point_panel("pressure", _panel())
    with pytest.raises(RuntimeError) as caught:
        solve_point_panel(record, targets=(1.,) if finite else None)
    assert caught.value is failure and owner.closed and owner.calls == []


def test_pressure_linear_failure_preserved_and_owner_closed(monkeypatch):
    failure = RuntimeError("linear pressure failure")
    class FailingOwner(Owner):
        def solve_linear(self, load, **options):
            raise failure
    owner = FailingOwner()
    monkeypatch.setattr(stiffened_shell, "create_analysis", lambda **options: owner)
    record = AnalysisDefinition.experimental_point_panel("pressure", _panel())
    with pytest.raises(RuntimeError) as caught:
        solve_point_panel(record)
    assert caught.value is failure and owner.closed


@pytest.mark.parametrize("status", ["accepted_prefix", "incomplete", "failed"])
def test_pressure_result_status_and_prefix_preserved(monkeypatch, status):
    result = {"status": status, "accepted_factors": (.1,), "failure": "owner diagnostic"}
    class ResultOwner(Owner):
        def solve(self, load, targets, **options):
            return result
    owner = ResultOwner()
    monkeypatch.setattr(stiffened_shell, "create_analysis", lambda **options: owner)
    record = AnalysisDefinition.experimental_point_panel("pressure", _panel())
    assert solve_point_panel(record, targets=(.1, .2)) is result
    assert owner.closed


def test_pressure_unsupported_finite_mode_failure_passes_through(monkeypatch):
    failure = ValueError("finite reference_dead enhanced S3 pressure unsupported")
    owner = Owner(failure=failure)
    monkeypatch.setattr(stiffened_shell, "create_analysis", lambda **options: owner)
    panel = replace(_ge(), actions=(), pressure_load=stiffened_shell.GEShellPressureLoad(
        MODEL, (stiffened_shell.GEShellPressureAction(1, 100., "reference_dead"),)))
    record = AnalysisDefinition.experimental_point_panel("pressure", panel)
    with pytest.raises(ValueError) as caught:
        solve_point_panel(record, targets=(.1, .2))
    assert caught.value is failure and owner.closed
    assert owner.calls[0][1].actions[0].configuration == "reference_dead"


def test_pressure_real_q4_project_linear_and_finite_source_flow(tmp_path):
    """Integration evidence only; no mixed-panel or installed qualification claim."""
    base = replace(_ge(), actions=(),
                   q4_pullback_policy=stiffened_shell.Q4_ALGORITHMIC_PULLBACK_V1)
    owner = base.create_analysis()
    try:
        identity = owner.preflight()["identity"]
    finally:
        owner.close()
    panel = replace(base, pressure_load=stiffened_shell.GEShellPressureLoad(identity, (
        stiffened_shell.GEShellPressureAction(1, 100., "current_surface"),)))
    project = Project(name="Q4 pressure source flow")
    record = AnalysisDefinition.experimental_point_panel("pressure", panel)
    project.analyses[record.id] = record
    reopened = load_project(save_project(project, tmp_path / "q4-pressure.anyfem"))
    restored = reopened.analyses[record.id]
    assert point_panel_from_analysis(restored).to_dict() == panel.to_dict()
    linear = solve_point_panel(restored)
    assert linear.status == "completed"
    assert linear.pressure_load.applied_force == pytest.approx((0., 0., 100.))
    accepted = []
    finite = solve_point_panel(restored, targets=(.1, .2),
                               on_accepted=lambda result: accepted.append(result.accepted_factors[-1]))
    assert finite.status == "completed" and finite.accepted_factors == (.1, .2)
    assert accepted == [.1, .2]
    assert finite.pressure_load.potential is None
