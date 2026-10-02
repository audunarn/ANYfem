"""Public GE gravity input persistence and owner dispatch, without copied mechanics."""
import copy
from dataclasses import replace

import pytest
from anysolver import stiffened_shell

from anyfem.io.project_file import load_project, save_project
from anyfem.model.project import Project
from anyfem.model.records import AnalysisDefinition
from anyfem.solve.stiffened_shell import (
    ExperimentalGEPointPanel, ExperimentalGENodalRigidFootprintPanel,
    SCHEMA, SCHEMA_BODY_LOAD, load_case, save_case,
)
from anyfem.solve.stiffened_shell_project import point_panel_from_analysis, solve_point_panel
from test_stiffened_shell_project import _ge
from test_stiffened_shell_staged_load_consumer import Owner, MODEL, _panel as _staged_panel


def _panel(identity=MODEL):
    return replace(_ge(), actions=(),
                   body_load=stiffened_shell.GEBodyLoad(identity, [0., 0., -9.81]))


def test_gravity_save_reopen_project_and_standalone_owns_inputs(tmp_path, monkeypatch):
    def forbidden(**options):
        pytest.fail("input persistence constructed a mechanical owner")
    monkeypatch.setattr(stiffened_shell, "create_analysis", forbidden)
    acceleration = [0., 0., -9.81]
    body = stiffened_shell.GEBodyLoad(MODEL, acceleration)
    panel = replace(_ge(), actions=(), body_load=body)
    acceleration[2] = -1.
    payload = panel.to_dict()
    assert payload["schema"] == SCHEMA_BODY_LOAD
    assert payload["panel"]["schema"] == SCHEMA
    assert panel.body_load is not body
    standalone = load_case(save_case(panel, tmp_path / "gravity.anyfem-ge.json"))
    project = Project(name="explicit gravity")
    record = AnalysisDefinition.experimental_point_panel("gravity", panel)
    project.analyses[record.id] = record
    reopened = load_project(save_project(project, tmp_path / "gravity.anyfem"))
    restored = point_panel_from_analysis(reopened.analyses[record.id])
    assert restored.to_dict() == standalone.to_dict() == payload
    assert restored.load_input.serialize() == body.serialize()
    assert restored.actions == () and restored.status == "EXPERIMENTAL_NOT_QUALIFIED"
    payload["body_load"]["acceleration_m_s2"][2] = 999.
    assert restored.body_load.acceleration_m_s2 == (0., 0., -9.81)
    assert record.beam_formulation_options["panel"]["body_load"]["acceleration_m_s2"][2] == -9.81


@pytest.mark.parametrize("change", ["extra", "missing", "nested", "implicit", "identity", "actions", "status", "profile"])
def test_gravity_strict_schema_and_unsupported_mix_rejected(change):
    payload = _panel().to_dict()
    if change == "extra":
        payload["unexpected"] = None
    elif change == "missing":
        del payload["body_load"]
    elif change == "nested":
        payload["panel"] = copy.deepcopy(payload)
    elif change == "implicit":
        payload = payload["panel"] | {"body_load": payload["body_load"]}
    elif change == "identity":
        payload["body_load"]["acceleration_m_s2"][2] = -1.
    elif change == "actions":
        payload["panel"]["actions"] = _ge().to_dict()["actions"]
    else:
        payload[change] = "unsupported"
    with pytest.raises(ValueError):
        ExperimentalGEPointPanel.from_dict(payload)


def test_gravity_excludes_actions_staging_and_untyped_input():
    with pytest.raises(ValueError, match="empty legacy actions"):
        replace(_ge(), body_load=_panel().body_load)
    with pytest.raises(ValueError, match="no staged programme"):
        replace(_staged_panel(), body_load=_panel().body_load)
    with pytest.raises(ValueError, match="public experimental GE body load"):
        replace(_ge(), actions=(), body_load=object())


def test_gravity_unsupported_footprint_wrapper_rejected():
    with pytest.raises(ValueError, match="without body load"):
        ExperimentalGENodalRigidFootprintPanel(_panel(), ())


def test_gravity_missing_public_solver_contract_fails_closed(monkeypatch):
    panel = _panel()
    payload = panel.to_dict()
    monkeypatch.delattr(stiffened_shell, "GEBodyLoad")
    def forbidden(**options):
        pytest.fail("unsupported installed solver constructed an owner")
    monkeypatch.setattr(stiffened_shell, "create_analysis", forbidden)
    for operation in (lambda: panel.create_analysis(),
                      lambda: ExperimentalGEPointPanel.from_dict(payload),
                      lambda: replace(panel)):
        with pytest.raises(ValueError, match="public experimental GE body load"):
            operation()


def test_gravity_wrong_model_rejected_and_owner_closed(monkeypatch):
    owner = Owner(identity="cd" * 32)
    monkeypatch.setattr(stiffened_shell, "create_analysis", lambda **options: owner)
    with pytest.raises(ValueError, match="different panel model"):
        _panel().create_analysis()
    assert owner.closed and owner.calls == []


def test_gravity_dispatch_forwards_linear_and_explicit_finite_controls(monkeypatch):
    record = AnalysisDefinition.experimental_point_panel("gravity", _panel())
    linear = Owner()
    monkeypatch.setattr(stiffened_shell, "create_analysis", lambda **options: linear)
    token, observer, control = object(), lambda result: None, object()
    assert solve_point_panel(record, cancellation_token=token) == {"status": "completed"}
    kind, load, options = linear.calls[0]
    assert kind == "linear" and load.serialize() == _panel().body_load.serialize()
    assert options == {"cancellation_token": token} and linear.closed
    finite = Owner()
    monkeypatch.setattr(stiffened_shell, "create_analysis", lambda **options: finite)
    result = solve_point_panel(record, targets=(.5, 1.), cancellation_token=token,
                              on_accepted=observer, increment_control=control,
                              max_iterations=20, max_backtracks=12)
    assert result == {"status": "accepted_prefix", "targets": (.5, 1.)}
    kind, load, targets, options = finite.calls[0]
    assert kind == "finite" and targets == (.5, 1.) and finite.closed
    assert load.serialize() == _panel().body_load.serialize()
    assert options == dict(cancellation_token=token, on_accepted=observer,
                           increment_control=control, max_iterations=20, max_backtracks=12)
    with pytest.raises(ValueError, match="finite solve controls"):
        solve_point_panel(record, increment_control=control)
    with pytest.raises(ValueError, match="case callback"):
        solve_point_panel(record, on_case=observer)


def test_gravity_dispatch_preserves_owner_failure_and_closes(monkeypatch):
    failure = MemoryError("gravity owner failure")
    owner = Owner(failure=failure)
    monkeypatch.setattr(stiffened_shell, "create_analysis", lambda **options: owner)
    record = AnalysisDefinition.experimental_point_panel("gravity", _panel())
    with pytest.raises(MemoryError) as caught:
        solve_point_panel(record, targets=(1.,))
    assert caught.value is failure and owner.closed


def test_gravity_mutated_project_payload_revalidated_on_save(tmp_path):
    project = Project(name="gravity mutation")
    record = AnalysisDefinition.experimental_point_panel("gravity", _panel())
    project.analyses[record.id] = record
    record.beam_formulation_options["panel"]["body_load"]["acceleration_m_s2"][2] = -1.
    with pytest.raises(ValueError):
        save_project(project, tmp_path / "invalid.anyfem")


def test_gravity_actual_public_model_binding_survives_project_roundtrip(tmp_path):
    base = replace(_ge(), actions=())
    owner = base.create_analysis()
    try:
        identity = owner.preflight()["identity"]
    finally:
        owner.close()
    panel = replace(base, body_load=stiffened_shell.GEBodyLoad(identity, (0., 0., -9.81)))
    project = Project(name="public gravity binding")
    record = AnalysisDefinition.experimental_point_panel("gravity", panel)
    project.analyses[record.id] = record
    reopened = load_project(save_project(project, tmp_path / "bound.anyfem"))
    restored = point_panel_from_analysis(reopened.analyses[record.id])
    owner = restored.create_analysis()
    try:
        assert owner.preflight()["identity"] == identity
        assert restored.load_input.serialize() == panel.body_load.serialize()
    finally:
        owner.close()
