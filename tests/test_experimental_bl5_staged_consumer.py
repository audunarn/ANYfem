"""Strict experimental BL5 input selection; solver mechanics remain public-owned."""

from dataclasses import replace
import json

import pytest
from anysolver import stiffened_shell

from anyfem.solve.stiffened_shell import (
    ExperimentalGEBL5Panel, SCHEMA_BL5, SCHEMA_BL5_STAGED_V2,
    load_case, save_case,
)
from test_experimental_stiffened_shell_consumer import _bl5_case


def _selected_case():
    return replace(_bl5_case(),
                   q4_pullback_policy=stiffened_shell.Q4_ALGORITHMIC_PULLBACK_V1)


def _programme(case):
    owner = case.create_analysis()
    try:
        identity = owner.preflight()["identity"]
    finally:
        owner.close()
    return stiffened_shell.StagedDeadLoadProgramme(identity, (
        (stiffened_shell.NodalAction("beam", 105, (0., 0., 100., 0., 0., 0.)),),
        (stiffened_shell.NodalAction("beam", 105, (0., 0., 150., 0., 0., 0.)),),
    ))


def test_selected_bl5_without_programme_round_trips_separately_from_v1(tmp_path):
    legacy = _bl5_case()
    assert legacy.to_dict()["schema"] == SCHEMA_BL5
    assert "q4_pullback_policy" not in legacy.to_dict()
    assert "load_programme" not in legacy.to_dict()
    assert legacy.load_input == legacy.actions
    selected = _selected_case()
    record = selected.to_dict()
    assert record["schema"] == SCHEMA_BL5_STAGED_V2
    assert record["q4_pullback_policy"] == stiffened_shell.Q4_ALGORITHMIC_PULLBACK_V1
    assert record["load_programme"] is None
    assert selected.definition().q4_pullback_policy == record["q4_pullback_policy"]
    reopened = load_case(save_case(selected, tmp_path / "selected-bl5.anyfem-ge.json"))
    assert type(reopened) is ExperimentalGEBL5Panel
    assert reopened.to_dict() == record
    assert reopened.load_input == reopened.actions == selected.actions
    assert reopened.status == "EXPERIMENTAL_NOT_QUALIFIED"


def test_staged_bl5_owns_public_programme_and_reopens(tmp_path):
    selected = _selected_case()
    programme = _programme(selected)
    case = replace(selected, actions=(), load_programme=programme)
    assert case.load_programme is not programme
    assert case.load_programme.serialize() == programme.serialize()
    record = case.to_dict()
    assert record["schema"] == SCHEMA_BL5_STAGED_V2
    assert record["actions"] == []
    assert record["load_programme"] == programme.to_payload()
    reopened = load_case(save_case(case, tmp_path / "staged-bl5.anyfem-ge.json"))
    assert reopened.to_dict() == record
    assert reopened.load_input == reopened.load_programme
    owner = reopened.create_analysis()
    try:
        assert owner.preflight()["identity"] == reopened.load_programme.model_identity
    finally:
        owner.close()


def test_bl5_v2_strict_schema_and_unsupported_inputs(tmp_path):
    legacy = _bl5_case()
    selected = _selected_case()
    programme = _programme(selected)
    with pytest.raises(ValueError, match="selected Q4 pullback and empty actions"):
        replace(selected, load_programme=programme)
    with pytest.raises(ValueError, match="selected Q4 pullback"):
        replace(legacy, actions=(), load_programme=programme)
    with pytest.raises(ValueError, match="Q4 pullback policy"):
        replace(selected, q4_pullback_policy="unknown")
    with pytest.raises(ValueError, match="staged load programme"):
        replace(selected, actions=(), load_programme=object())

    staged = replace(selected, actions=(), load_programme=programme).to_dict()
    mutations = (
        (lambda row: row.update(schema=SCHEMA_BL5), "strict experimental BL5 panel"),
        (lambda row: row.pop("q4_pullback_policy"), "strict experimental BL5 panel"),
        (lambda row: row.pop("load_programme"), "strict experimental BL5 panel"),
        (lambda row: row.update(q4_pullback_policy="historical_corotational"),
         "selected Q4 pullback"),
        (lambda row: row["load_programme"].update(identity="0" * 64),
         "identity"),
        (lambda row: row["actions"].append({"domain": "beam", "node": 105,
                                              "wrench": [0., 0., 1., 0., 0., 0.]}),
         "empty actions"),
    )
    path = tmp_path / "invalid-staged-bl5.anyfem-ge.json"
    for mutate, message in mutations:
        record = json.loads(json.dumps(staged))
        mutate(record)
        path.write_text(json.dumps(record), encoding="ascii")
        with pytest.raises(ValueError, match=message):
            load_case(path)


def test_staged_bl5_mismatched_model_closes_new_owner(monkeypatch):
    selected = _selected_case()
    case = replace(selected, actions=(), load_programme=_programme(selected))

    class WrongOwner:
        closed = False

        def preflight(self):
            return {"identity": "0" * 64}

        def close(self):
            self.closed = True

    wrong = WrongOwner()
    monkeypatch.setattr(stiffened_shell, "create_analysis", lambda *, definition: wrong)
    with pytest.raises(ValueError, match="different panel model"):
        case.create_analysis()
    assert wrong.closed
