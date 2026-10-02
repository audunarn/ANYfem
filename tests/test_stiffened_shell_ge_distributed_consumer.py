"""Public GE distributed-load input persistence and owner dispatch, without copied mechanics."""
import copy
from dataclasses import replace

import pytest
from anysolver import stiffened_shell

from anyfem.io.project_file import load_project, save_project
from anyfem.model.project import Project
from anyfem.model.records import AnalysisDefinition
from anyfem.solve.stiffened_shell import (
    ExperimentalGEPointPanel, ExperimentalGENodalRigidFootprintPanel,
    SCHEMA, SCHEMA_DISTRIBUTED_LOAD, load_case, save_case,
)
from anyfem.solve.stiffened_shell_project import point_panel_from_analysis, solve_point_panel
from test_stiffened_shell_project import _ge
from test_stiffened_shell_staged_load_consumer import Owner, MODEL, _panel as _staged_panel


def _panel(identity=MODEL):
    return replace(_ge(), actions=(),
                   distributed_load=stiffened_shell.GEBeamDistributedLoad(identity, (
                       stiffened_shell.GEBeamLineAction(71, [0., 0., -9.81], [0., 2., 0.]),)))


def test_distributed_save_reopen_project_and_standalone_owns_inputs(tmp_path, monkeypatch):
    def forbidden(**options):
        pytest.fail("input persistence constructed a mechanical owner")
    monkeypatch.setattr(stiffened_shell, "create_analysis", forbidden)
    force = [0., 0., -9.81]
    couple = [0., 2., 0.]
    body = stiffened_shell.GEBeamDistributedLoad(MODEL, (
        stiffened_shell.GEBeamLineAction(71, force, couple),))
    panel = replace(_ge(), actions=(), distributed_load=body)
    force[2] = -1.
    couple[1] = 99.
    payload = panel.to_dict()
    assert payload["schema"] == SCHEMA_DISTRIBUTED_LOAD
    assert payload["panel"]["schema"] == SCHEMA
    assert panel.distributed_load is not body
    standalone = load_case(save_case(panel, tmp_path / "distributed.anyfem-ge.json"))
    project = Project(name="explicit distributed")
    record = AnalysisDefinition.experimental_point_panel("distributed", panel)
    project.analyses[record.id] = record
    reopened = load_project(save_project(project, tmp_path / "distributed.anyfem"))
    restored = point_panel_from_analysis(reopened.analyses[record.id])
    assert restored.to_dict() == standalone.to_dict() == payload
    assert restored.load_input.serialize() == body.serialize()
    assert restored.actions == () and restored.status == "EXPERIMENTAL_NOT_QUALIFIED"
    payload["distributed_load"]["actions"][0]["force_N_per_reference_m"][2] = 999.
    assert restored.distributed_load.actions[0].force_N_per_reference_m == (0., 0., -9.81)
    assert restored.distributed_load.actions[0].couple_Nm_per_reference_m == (0., 2., 0.)
    assert record.beam_formulation_options["panel"]["distributed_load"]["actions"][0]["force_N_per_reference_m"][2] == -9.81


@pytest.mark.parametrize("change", ["extra", "missing", "nested", "implicit", "identity", "actions", "status", "profile",
                                    "row-extra", "couple-missing", "duplicate", "density-size", "staged-inner"])
def test_distributed_strict_schema_and_unsupported_mix_rejected(change):
    payload = _panel().to_dict()
    if change == "extra":
        payload["unexpected"] = None
    elif change == "missing":
        del payload["distributed_load"]
    elif change == "nested":
        payload["panel"] = copy.deepcopy(payload)
    elif change == "implicit":
        payload = payload["panel"] | {"distributed_load": payload["distributed_load"]}
    elif change == "identity":
        payload["distributed_load"]["actions"][0]["force_N_per_reference_m"][2] = -1.
    elif change == "actions":
        payload["panel"]["actions"] = _ge().to_dict()["actions"]
    elif change == "row-extra":
        payload["distributed_load"]["actions"][0]["unexpected"] = None
    elif change == "couple-missing":
        del payload["distributed_load"]["actions"][0]["couple_Nm_per_reference_m"]
    elif change == "duplicate":
        payload["distributed_load"]["actions"] *= 2
    elif change == "density-size":
        payload["distributed_load"]["actions"][0]["couple_Nm_per_reference_m"] = [1., 2.]
    elif change == "staged-inner":
        payload["panel"] = _staged_panel().to_dict()
    else:
        payload[change] = "unsupported"
    with pytest.raises(ValueError):
        ExperimentalGEPointPanel.from_dict(payload)


def test_distributed_excludes_actions_staging_and_untyped_input():
    with pytest.raises(ValueError, match="empty legacy actions"):
        replace(_ge(), distributed_load=_panel().distributed_load)
    with pytest.raises(ValueError, match="no staged programme"):
        replace(_staged_panel(), distributed_load=_panel().distributed_load)
    with pytest.raises(ValueError, match="public experimental GE distributed load"):
        replace(_ge(), actions=(), distributed_load=object())
    body = stiffened_shell.GEBodyLoad(MODEL, (0., 0., -9.81))
    with pytest.raises(ValueError, match="no staged programme or body load"):
        replace(_panel(), body_load=body)


def test_distributed_unsupported_footprint_wrapper_rejected():
    with pytest.raises(ValueError, match="without body load or distributed load"):
        ExperimentalGENodalRigidFootprintPanel(_panel(), ())


def test_distributed_missing_public_solver_contract_fails_closed(monkeypatch):
    panel = _panel()
    payload = panel.to_dict()
    monkeypatch.delattr(stiffened_shell, "GEBeamDistributedLoad")
    def forbidden(**options):
        pytest.fail("unsupported installed solver constructed an owner")
    monkeypatch.setattr(stiffened_shell, "create_analysis", forbidden)
    for operation in (lambda: panel.create_analysis(),
                      lambda: ExperimentalGEPointPanel.from_dict(payload),
                      lambda: replace(panel)):
        with pytest.raises(ValueError, match="public experimental GE distributed load"):
            operation()


def test_distributed_wrong_model_rejected_and_owner_closed(monkeypatch):
    owner = Owner(identity="cd" * 32)
    monkeypatch.setattr(stiffened_shell, "create_analysis", lambda **options: owner)
    with pytest.raises(ValueError, match="different panel model"):
        _panel().create_analysis()
    assert owner.closed and owner.calls == []


def test_distributed_dispatch_forwards_linear_and_explicit_finite_controls(monkeypatch):
    record = AnalysisDefinition.experimental_point_panel("distributed", _panel())
    linear = Owner()
    monkeypatch.setattr(stiffened_shell, "create_analysis", lambda **options: linear)
    token, observer, control = object(), lambda result: None, object()
    assert solve_point_panel(record, cancellation_token=token) == {"status": "completed"}
    kind, load, options = linear.calls[0]
    assert kind == "linear" and load.serialize() == _panel().distributed_load.serialize()
    assert options == {"cancellation_token": token} and linear.closed
    finite = Owner()
    monkeypatch.setattr(stiffened_shell, "create_analysis", lambda **options: finite)
    result = solve_point_panel(record, targets=(.5, 1.), cancellation_token=token,
                              on_accepted=observer, increment_control=control,
                              max_iterations=20, max_backtracks=12)
    assert result == {"status": "accepted_prefix", "targets": (.5, 1.)}
    kind, load, targets, options = finite.calls[0]
    assert kind == "finite" and targets == (.5, 1.) and finite.closed
    assert load.serialize() == _panel().distributed_load.serialize()
    assert options == dict(cancellation_token=token, on_accepted=observer,
                           increment_control=control, max_iterations=20, max_backtracks=12)
    with pytest.raises(ValueError, match="finite solve controls"):
        solve_point_panel(record, increment_control=control)
    with pytest.raises(ValueError, match="case callback"):
        solve_point_panel(record, on_case=observer)


def test_distributed_dispatch_preserves_owner_failure_and_closes(monkeypatch):
    failure = MemoryError("distributed owner failure")
    owner = Owner(failure=failure)
    monkeypatch.setattr(stiffened_shell, "create_analysis", lambda **options: owner)
    record = AnalysisDefinition.experimental_point_panel("distributed", _panel())
    with pytest.raises(MemoryError) as caught:
        solve_point_panel(record, targets=(1.,))
    assert caught.value is failure and owner.closed


def test_distributed_mutated_project_payload_revalidated_on_save(tmp_path):
    project = Project(name="distributed mutation")
    record = AnalysisDefinition.experimental_point_panel("distributed", _panel())
    project.analyses[record.id] = record
    record.beam_formulation_options["panel"]["distributed_load"]["actions"][0]["force_N_per_reference_m"][2] = -1.
    with pytest.raises(ValueError):
        save_project(project, tmp_path / "invalid.anyfem")


@pytest.mark.parametrize("finite", [False, True])
def test_distributed_preflight_failure_preserved_and_owner_closed(monkeypatch, finite):
    failure = RuntimeError("model admission failed")
    class FailingOwner(Owner):
        def preflight(self):
            raise failure
    owner = FailingOwner()
    monkeypatch.setattr(stiffened_shell, "create_analysis", lambda **options: owner)
    record = AnalysisDefinition.experimental_point_panel("distributed", _panel())
    with pytest.raises(RuntimeError) as caught:
        solve_point_panel(record, targets=(1.,) if finite else None)
    assert caught.value is failure and owner.closed and owner.calls == []


def test_distributed_linear_failure_preserved_and_owner_closed(monkeypatch):
    failure = RuntimeError("linear distributed failure")
    class FailingOwner(Owner):
        def solve_linear(self, load, **options):
            raise failure
    owner = FailingOwner()
    monkeypatch.setattr(stiffened_shell, "create_analysis", lambda **options: owner)
    record = AnalysisDefinition.experimental_point_panel("distributed", _panel())
    with pytest.raises(RuntimeError) as caught:
        solve_point_panel(record)
    assert caught.value is failure and owner.closed
