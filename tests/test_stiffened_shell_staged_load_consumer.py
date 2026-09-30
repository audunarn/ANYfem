"""Input persistence and dispatch contracts; mechanical owners are explicit stubs."""
import copy
from dataclasses import replace

import pytest
from anysolver import stiffened_shell

from anyfem.io.project_file import load_project, save_project
from anyfem.model.project import Project
from anyfem.model.records import AnalysisDefinition
from anyfem.solve.stiffened_shell import (
    ExperimentalGEPointPanel, SCHEMA, SCHEMA_STAGED, load_case, save_case,
)
from anyfem.solve.stiffened_shell_project import point_panel_from_analysis, solve_point_panel
from test_stiffened_shell_project import _ge


MODEL = "ab" * 32


def _panel():
    base = replace(_ge(), actions=())
    programme = stiffened_shell.StagedDeadLoadProgramme(MODEL, (
        (stiffened_shell.NodalAction("beam", 13, (0., 0., 10., 0., 0., 0.)),),
        (stiffened_shell.NodalAction("beam", 13, (0., 5., 10., 0., 0., 0.)),),
    ))
    return replace(base, load_programme=programme)


class Owner:
    def __init__(self, identity=MODEL, failure=None):
        self.identity, self.failure, self.closed = identity, failure, False
        self.calls = []

    def preflight(self):
        return {"identity": self.identity}

    def solve(self, programme, targets, **options):
        self.calls.append(("finite", programme, targets, options))
        if self.failure:
            raise self.failure
        return {"status": "accepted_prefix", "targets": targets}

    def solve_linear(self, actions, **options):
        self.calls.append(("linear", actions, options))
        return {"status": "completed"}

    def close(self):
        self.closed = True


def test_staged_save_reopen_native_project_and_standalone_without_assembly(tmp_path, monkeypatch):
    def forbidden(**options):
        pytest.fail("input persistence assembled a mechanical owner")
    monkeypatch.setattr(stiffened_shell, "create_analysis", forbidden)
    panel = _panel()
    payload = panel.to_dict()
    assert payload["schema"] == SCHEMA_STAGED and payload["panel"]["schema"] == SCHEMA
    assert payload["panel"]["actions"] == []
    standalone = load_case(save_case(panel, tmp_path/"staged.anyfem-ge.json"))
    assert standalone.to_dict() == payload
    project = Project(name="explicit staged inputs")
    record = AnalysisDefinition.experimental_point_panel("staged", panel)
    project.analyses[record.id] = record
    reopened = load_project(save_project(project, tmp_path/"staged.anyfem"))
    restored = point_panel_from_analysis(reopened.analyses[record.id])
    assert restored.to_dict() == payload
    assert restored.load_input.serialize() == panel.load_programme.serialize()
    assert restored.actions == () and restored.status == "EXPERIMENTAL_NOT_QUALIFIED"
    # Future endpoints and framing are deep-owned by both project and consumer.
    payload["load_programme"]["endpoints"][1][0]["wrench"][1] = 999.
    assert restored.load_programme.endpoints[1][0].wrench[1] == 5.
    assert record.beam_formulation_options["panel"]["load_programme"]["endpoints"][1][0]["wrench"][1] == 5.


def test_explicit_finite_dispatch_forwards_controls_and_keeps_legacy_linear(monkeypatch):
    owner = Owner()
    monkeypatch.setattr(stiffened_shell, "create_analysis", lambda **options: owner)
    record = AnalysisDefinition.experimental_point_panel("staged", _panel())
    token, observer, control = object(), lambda result: None, object()
    result = solve_point_panel(record, targets=(.5, 1.), cancellation_token=token,
        on_accepted=observer, increment_control=control, max_iterations=20, max_backtracks=12)
    assert result["status"] == "accepted_prefix" and owner.closed
    kind, programme, targets, options = owner.calls[0]
    assert kind == "finite" and targets == (.5, 1.) and programme.serialize() == _panel().load_programme.serialize()
    assert options == dict(cancellation_token=token, on_accepted=observer,
                          increment_control=control, max_iterations=20, max_backtracks=12)
    legacy_owner = Owner()
    monkeypatch.setattr(stiffened_shell, "create_analysis", lambda **options: legacy_owner)
    legacy = _ge()
    assert legacy.load_input is legacy.actions and "load_programme" not in legacy.to_dict()
    legacy_record = AnalysisDefinition.experimental_point_panel("legacy", legacy)
    assert solve_point_panel(legacy_record)["status"] == "completed"
    assert legacy_owner.calls[0][0] == "linear" and legacy_owner.closed


def test_staged_exclusivity_and_explicit_load_selection_fail_before_owner(monkeypatch):
    def forbidden(**options):
        pytest.fail("ambiguous load selection constructed an owner")
    monkeypatch.setattr(stiffened_shell, "create_analysis", forbidden)
    with pytest.raises(ValueError, match="public experimental GE staged"):
        replace(_ge(), actions=(), load_programme=object())
    with pytest.raises(ValueError, match="empty legacy actions"):
        replace(_ge(), load_programme=_panel().load_programme)
    staged = AnalysisDefinition.experimental_point_panel("staged", _panel())
    with pytest.raises(ValueError, match="explicit programme-position"):
        solve_point_panel(staged)
    with pytest.raises(ValueError, match="case callback"):
        solve_point_panel(staged, targets=(1.,), on_case=lambda result: None)
    legacy = AnalysisDefinition.experimental_point_panel("legacy", _ge())
    with pytest.raises(ValueError, match="explicit staged"):
        solve_point_panel(legacy, targets=(1.,))


@pytest.mark.parametrize("field,value", [("profile", "other"), ("status", "qualified"), ("extra", 1)])
def test_corrupt_staged_envelope_rejected(field, value):
    payload = _panel().to_dict()
    payload[field] = value
    with pytest.raises(ValueError):
        ExperimentalGEPointPanel.from_dict(payload)


@pytest.mark.parametrize("change", ("future", "moment", "nested", "legacy"))
def test_future_identity_unsupported_moments_and_nested_or_implicit_downgrade_rejected(change):
    payload = _panel().to_dict()
    if change == "future":
        payload["load_programme"]["endpoints"][1][0]["wrench"][1] += 1.
    elif change == "moment":
        # Consumer parsing must delegate rejection, not drop moments on reopen.
        payload["load_programme"]["endpoints"][0][0]["wrench"][3] = 1.
    elif change == "nested":
        payload["panel"] = copy.deepcopy(payload)
    else:
        payload = payload["panel"] | {"load_programme": payload["load_programme"]}
    with pytest.raises(ValueError):
        ExperimentalGEPointPanel.from_dict(payload)


def test_mutated_project_future_payload_revalidated_on_save(tmp_path):
    project = Project(name="staged mutation")
    record = AnalysisDefinition.experimental_point_panel("staged", _panel())
    project.analyses[record.id] = record
    record.beam_formulation_options["panel"]["load_programme"]["endpoints"][1][0]["wrench"][1] += 1.
    with pytest.raises(ValueError):
        save_project(project, tmp_path/"bad.anyfem")


def test_actual_model_binding_rejected_when_owner_created_and_owner_closed(monkeypatch):
    owner = Owner(identity="cd"*32)
    monkeypatch.setattr(stiffened_shell, "create_analysis", lambda **options: owner)
    panel = ExperimentalGEPointPanel.from_dict(_panel().to_dict())
    with pytest.raises(ValueError, match="different panel model"):
        panel.create_analysis()
    assert owner.closed and owner.calls == []


def test_original_solver_failure_preserved_and_owner_closed(monkeypatch):
    failure = MemoryError("public finite owner failure")
    owner = Owner(failure=failure)
    monkeypatch.setattr(stiffened_shell, "create_analysis", lambda **options: owner)
    record = AnalysisDefinition.experimental_point_panel("staged", _panel())
    with pytest.raises(MemoryError) as caught:
        solve_point_panel(record, targets=(1.,))
    assert caught.value is failure and owner.closed
