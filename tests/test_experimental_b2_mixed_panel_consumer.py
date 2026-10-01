"""Explicit input persistence and public static solver delegation, not qualification."""
from dataclasses import replace
import json
from types import SimpleNamespace

import numpy as np
import pytest
from scipy import sparse

from anysolver import stiffened_shell as api
from anysolver._b2_physical_linear_definition import GeometricMetadata
from anysolver.control import CancellationToken
from anyfem.io.project_file import load_project, save_project
from anyfem.model.project import Project
from anyfem.model.records import AnalysisDefinition
from anyfem.solve.stiffened_shell_b2 import (
    ExperimentalB2MixedPointPanel, ExperimentalB2PointPanel,
    SCHEMA, SCHEMA_MIXED, STATUS, load_case, save_case,
)
from anyfem.solve.stiffened_shell_project import (
    B2_TYPE, B2_FORMULATION, point_panel_from_analysis, solve_point_panel,
)


def _inputs(kind="mixed", count=1):
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
    fixed = set(range(6)) | set(range(18, 24)) | set(range(36, 42))
    fixed.update(6*i+5 for i in (1, 2, 4, 5))
    free = [i for i in range(54) if i not in fixed]
    definition = api.B2MixedPanelDefinition(api.B2_E4_PL_FAST_LINEAR_V1,
        nodes, left+right, members, points, sparse.eye(54, format="csr")[:, free], np.zeros(54))
    load = np.zeros(54)
    load[-6:] = (.04, .08, -.03, .01, -.02, .015)
    cases = tuple((f"case-{i}", (1.+i/count)*load) for i in range(count))
    return definition, cases


def _panel(definition, cases):
    return ExperimentalB2MixedPointPanel.from_definition(definition, cases,
        selected_dofs=(49, 50, 52), selected_points=("joint-right",),
        recover_members={"beam-right": (.25, .75)})


@pytest.mark.parametrize("kind", ("s3", "mixed"))
def test_roundtrip_rebuilds_detached_public_mixed_definition_without_owner(tmp_path, kind, monkeypatch):
    definition, cases = _inputs(kind)
    def forbid(**_kwargs):
        raise AssertionError("input persistence created a numerical owner")
    monkeypatch.setattr(api, "create_analysis", forbid)
    panel = _panel(definition, cases)
    reopened = load_case(save_case(panel, tmp_path/"mixed.anyfem-b2.json"))
    assert type(reopened) is ExperimentalB2MixedPointPanel
    assert reopened.payload == panel.payload and reopened.schema == SCHEMA_MIXED
    rebuilt = reopened.to_definition()
    assert type(rebuilt) is api.B2MixedPanelDefinition
    assert tuple(type(s) for s in rebuilt.shells) == tuple(type(s) for s in definition.shells)
    assert rebuilt.shells == definition.shells and rebuilt.members == definition.members
    np.testing.assert_array_equal(rebuilt.transformation.toarray(), definition.transformation.toarray())
    np.testing.assert_array_equal(rebuilt.prescribed_offset, definition.prescribed_offset)
    canonical = panel.payload
    definition.shell_nodes_m[2] = (99., 99., 99.)
    definition.transformation.data[:] = 7.
    definition.prescribed_offset[:] = 8.
    cases[0][1][:] = 9.
    detached = reopened.to_dict()
    detached["shells"][0]["E"] = 999.
    rebuilt.shell_nodes_m[2] = (8., 8., 8.)
    rebuilt.transformation.data[:] = 11.
    assert panel.payload == canonical == reopened.payload
    assert reopened.to_definition().shell_nodes_m[2] == (1., 0., 0.)
    assert panel.status == STATUS


@pytest.mark.parametrize("kind", ("s3", "mixed"))
@pytest.mark.parametrize("count", (1, 10, 100))
def test_selected_batches_match_public_owner_and_cancel_complete_prefix(kind, count):
    definition, cases = _inputs(kind, count)
    panel = _panel(definition, cases)
    owner = api.create_analysis(definition=definition)
    try:
        expected = owner.solve_linear_many(cases, selected_dofs=(49, 50, 52),
            selected_points=("joint-right",), recover_members={"beam-right": (.25, .75)})
    finally:
        owner.close()
    result = panel.solve_linear_many()
    assert result.status == expected.status == "completed", (result.failure_reason, expected.failure_reason)
    assert result == expected
    assert result.factorization_count == 1 and result.solve_count == count
    assert len(result.cases) == count and not result.production_qualified
    token = CancellationToken()
    names = []
    def stop(name):
        names.append(name)
        token.cancel("consumer complete-prefix cancellation")
    cancelled = _panel(definition, cases+(("never", cases[-1][1]),)).solve_linear_many(
        cancellation_token=token, on_case=stop)
    assert cancelled.status == "cancelled" and names == ["case-0"]
    assert len(cancelled.cases) == cancelled.solve_count == cancelled.factorization_count == 1
    assert cancelled.cases[0] == expected.cases[0]


@pytest.mark.parametrize("kind", ("s3", "mixed"))
def test_native_project_reopens_exact_mixed_consumer_without_solve(tmp_path, kind, monkeypatch):
    definition, cases = _inputs(kind)
    panel = _panel(definition, cases)
    project = Project("explicit static mixed panel", mesh_only=True)
    record = AnalysisDefinition.experimental_point_panel("mixed", panel)
    project.add_analysis(record)
    def forbid(**_kwargs):
        raise AssertionError("Project save/reopen created a numerical owner")
    with monkeypatch.context() as guard:
        guard.setattr(api, "create_analysis", forbid)
        restored = load_project(save_project(project, tmp_path/"mixed.anyfem"))
        analysis = restored.analyses[record.id]
        assert analysis.to_dict() == record.to_dict()
        reopened = point_panel_from_analysis(analysis)
        assert type(reopened) is ExperimentalB2MixedPointPanel
        assert type(reopened.to_definition()) is api.B2MixedPanelDefinition
        assert reopened.payload == panel.payload
        assert analysis.type == B2_TYPE and analysis.beam_formulation == B2_FORMULATION
    result = solve_point_panel(analysis)
    assert result == panel.solve_linear_many()
    assert result.status == "completed" and result.factorization_count == 1


def test_failed_status_is_forwarded_and_owner_always_closed(monkeypatch):
    definition, cases = _inputs()
    panel = _panel(definition, cases)
    failed = SimpleNamespace(status="failed", failure_reason="declared backend failure", cases=())
    closed, calls = [], []
    class Owner:
        def solve_linear_many(self, received, **kwargs):
            calls.append((received, kwargs))
            return failed
        def close(self):
            closed.append(True)
    def factory(*, definition):
        assert type(definition) is api.B2MixedPanelDefinition
        return Owner()
    monkeypatch.setattr(api, "create_analysis", factory)
    assert panel.solve_linear_many() is failed
    assert closed == [True] and len(calls) == 1
    assert calls[0][1]["selected_points"] == ("joint-right",)
    with pytest.raises(ValueError, match="static batches only"):
        panel.modes()
    assert closed == [True]


@pytest.mark.parametrize("bad", ("unknown-schema", "unknown-formulation", "legacy-s3",
    "wrong-count", "unknown-node", "missing-normal", "zero-normal", "nonfinite",
    "density", "mass", "geometric", "footprints", "request"))
def test_mixed_records_reject_unknown_or_unsupported_semantics(bad):
    record = _panel(*_inputs()).to_dict()
    shell = record["shells"][-1]
    if bad == "unknown-schema": record["schema"] = "unknown"
    elif bad == "unknown-formulation": shell["formulation"] = "unknown"
    elif bad == "legacy-s3": shell["formulation"] = "e4-pl-s3"
    elif bad == "wrong-count": shell["node_ids"].append(4)
    elif bad == "unknown-node": shell["node_ids"][0] = 999
    elif bad == "missing-normal": shell.pop("reference_normal")
    elif bad == "zero-normal": shell["reference_normal"] = [0., 0., 0.]
    elif bad == "nonfinite": shell["E"] = float("inf")
    elif bad == "density": shell["density_kg_m3"] = 1.
    elif bad in ("mass", "geometric"): record["members"][0][bad] = None
    elif bad == "footprints": record["footprints"] = []
    elif bad == "request": record["request"] = {"count": 1}
    with pytest.raises(ValueError):
        ExperimentalB2MixedPointPanel.from_dict(record)


@pytest.mark.parametrize("bad", ("q4-definition", "schema", "density", "mass", "geometric", "footprints"))
def test_from_definition_rejects_semantics_instead_of_discarding_them(bad):
    definition, cases = _inputs()
    kwargs = {}
    if bad == "q4-definition": definition = api.B2Q4PanelDefinition(**definition.__dict__)
    elif bad == "schema": kwargs["schema"] = SCHEMA
    elif bad == "density":
        definition = replace(definition, shells=(replace(definition.shells[0], density_kg_m3=1.),)+definition.shells[1:])
    elif bad == "mass":
        mass = api.MassMetadata("mass", (0., 0., 0.), tuple(tuple(row) for row in np.eye(6)))
        definition = replace(definition, members=(replace(definition.members[0], mass=mass),)+definition.members[1:])
    elif bad == "geometric":
        geometric = GeometricMetadata("geometry", (0., 0., 0.),
            (0., 0., 0.), (0., 0., 0.))
        definition = replace(definition, members=(replace(definition.members[0], geometric=geometric),)+definition.members[1:])
    elif bad == "footprints":
        definition = replace(definition, footprints=(api.B2NodalRigidFootprint("patch", (2, 3, 6), 12),))
    with pytest.raises(ValueError):
        ExperimentalB2MixedPointPanel.from_definition(definition, cases, selected_dofs=(49,), **kwargs)


def test_historical_entry_and_new_schema_cannot_be_interchanged():
    definition, cases = _inputs("q4")
    old = ExperimentalB2PointPanel.from_definition(api.B2Q4PanelDefinition(**definition.__dict__),
        cases, selected_dofs=(49,))
    assert old.schema == SCHEMA and all("formulation" not in s for s in json.loads(old.payload)["shells"])
    with pytest.raises(ValueError):
        ExperimentalB2MixedPointPanel(old.payload)
    with pytest.raises(ValueError):
        ExperimentalB2PointPanel(_panel(*_inputs()).payload)


def test_project_unknown_schema_has_no_historical_fallback():
    panel = _panel(*_inputs())
    analysis = AnalysisDefinition.experimental_point_panel("mixed", panel)
    analysis.beam_formulation_options["panel"]["schema"] = "unknown"
    with pytest.raises(ValueError):
        point_panel_from_analysis(analysis)


def test_exact_mixed_class_admission_and_missing_public_solver_seam(monkeypatch):
    definition, cases = _inputs()
    panel = _panel(definition, cases)
    class Derived(ExperimentalB2MixedPointPanel):
        pass
    with pytest.raises(ValueError, match="exact bounded"):
        Derived(panel.payload)
    monkeypatch.delattr(api, "B2MixedPanelDefinition")
    with pytest.raises(ValueError, match="installed solver lacks explicit public"):
        ExperimentalB2MixedPointPanel(panel.payload)


def test_nonfinite_json_is_rejected_by_parser_not_only_encoder():
    panel = _panel(*_inputs())
    raw = panel.payload.replace(b'"E":1000.0', b'"E":NaN', 1)
    assert raw != panel.payload
    with pytest.raises(ValueError, match="nonfinite experimental B2 JSON"):
        ExperimentalB2MixedPointPanel(raw)
