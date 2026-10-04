"""Public reference-only consumer boundary; inputs and accepted state stay separate."""
from copy import deepcopy
from dataclasses import replace
import importlib.util
import json
from pathlib import Path

import numpy as np
import pytest
from anysolver import stiffened_shell as ss
from anysolver.control import CancellationToken, SolveCancelled
from anyfem.io.project_file import save_project, load_project
from anyfem.model.project import Project
from anyfem.model.records import AnalysisDefinition
from anyfem.solve import stiffened_shell as consumer
from anyfem.solve.stiffened_shell import (
    ExperimentalGEReferenceDistributingPanel, ExperimentalGEPointPanel,
    SCHEMA_REFERENCE_DISTRIBUTING, REFERENCE_DISTRIBUTING_LAW, save_case, load_case,
)
from anyfem.solve.stiffened_shell_project import (
    GE_TYPE, GE_FORMULATION, point_panel_from_analysis, solve_point_panel,
)


@pytest.fixture(scope="module")
def inputs():
    path = Path(__file__).resolve().parents[1]/"examples"/"stiffened_shell_ge_distributing_consumer.py"
    spec = importlib.util.spec_from_file_location("distributing_consumer_example", path)
    example = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(example)
    definition, actions = example.definition_and_actions()
    panel = ExperimentalGEReferenceDistributingPanel.from_definition(definition, actions)
    return definition, actions, panel


@pytest.fixture(scope="module")
def solved(inputs):
    panel = inputs[2]
    owner = panel.create_analysis()
    try:
        first = owner.solve_linear(panel.actions)
        twice = tuple(replace(action, wrench=tuple(2*v for v in action.wrench)) for action in panel.actions)
        second = owner.solve_linear(twice)
        yield owner, first, second
    finally:
        owner.close()


def _q(result):
    return np.r_[result.shell_displacement, result.beam_coordinates]


def _project(panel):
    project = Project("explicit reference-distributing consumer", mesh_only=True)
    analysis = AnalysisDefinition.experimental_point_panel("area-pad", panel)
    project.add_analysis(analysis)
    return project, analysis


def test_explicit_definition_no_fake_points_and_legacy_type_behavior(inputs):
    definition, actions, panel = inputs
    assert "ExperimentalGEReferenceDistributingPanel" in consumer.__all__
    assert panel.points == panel.definition().points == ()
    assert panel.definition().distributing_footprints == definition.distributing_footprints
    assert panel.load_input == actions and panel.status == "EXPERIMENTAL_NOT_QUALIFIED"
    assert panel.to_dict()["schema"] == SCHEMA_REFERENCE_DISTRIBUTING
    assert panel.to_dict()["law"] == REFERENCE_DISTRIBUTING_LAW
    assert type(panel.recovery_selection) is ss.GEReferenceDistributingFootprintSelection
    with pytest.raises(ValueError):
        ExperimentalGEPointPanel(panel.shell, panel.beams, (), panel.fixed)
    legacy = ExperimentalGEPointPanel(panel.shell, panel.beams, (ss.PointConnection(1, 101),), panel.fixed)
    assert type(ExperimentalGEPointPanel.from_dict(legacy.to_dict())) is ExperimentalGEPointPanel
    with pytest.raises(ValueError): ExperimentalGEPointPanel.from_dict(panel.to_dict())


def test_constructor_snapshots_caller_geometry_actions_and_footprints_without_assembly(inputs, monkeypatch):
    definition, _, _ = inputs
    def forbidden(**kwargs): raise AssertionError("input framing assembled a solver owner")
    monkeypatch.setattr(ss, "create_analysis", forbidden)
    xyz = [list(v) for v in definition.shell.coordinates_m]
    cells = [list(v) for v in definition.shell.q4_elements]
    node_ids = list(definition.shell.node_ids)
    ids, bounds = [2, 3], [1., 3., -.5, .5]
    pad = ss.GEReferenceDistributingFootprint("owned-pad", ids, 102, bounds)
    wrench = np.array((0., 20., 0., 0., 0., 3.))
    source = replace(definition, shell=replace(definition.shell, node_ids=node_ids,
        coordinates_m=xyz, q4_elements=cells), distributing_footprints=(pad,))
    panel = ExperimentalGEReferenceDistributingPanel.from_definition(source,
        (ss.NodalAction("beam", 103, wrench),))
    before = panel.to_dict()
    xyz[0][0] = 999.; cells[1][0] = 99; node_ids[0] = 99
    ids[0] = 99; bounds[0] = 999.; wrench[:] = np.nan
    assert panel.to_dict() == before
    assert panel.definition().shell.coordinates_m[0] == (0., -.5, 0.)
    assert panel.beams[0].raw == definition.beams[0].raw
    assert panel.beams[0].sha256 == definition.beams[0].sha256


def test_standalone_atomic_and_native_project_input_roundtrip_without_numeric_work(inputs, tmp_path, monkeypatch):
    panel = inputs[2]
    def forbidden(**kwargs): raise AssertionError("input persistence assembled a solver owner")
    monkeypatch.setattr(ss, "create_analysis", forbidden)
    original_replace = consumer.os.replace
    replacements = []
    def observe(source, destination):
        replacements.append((Path(source), Path(destination)))
        return original_replace(source, destination)
    monkeypatch.setattr(consumer.os, "replace", observe)
    path = save_case(panel, tmp_path/"pad.anyfem-ge.json")
    reopened = load_case(path)
    assert type(reopened) is ExperimentalGEReferenceDistributingPanel
    assert reopened.to_dict() == panel.to_dict()
    assert any(destination == path for _, destination in replacements)
    project, analysis = _project(reopened)
    filename = save_project(project, tmp_path/"pad.anyfem")
    saved = filename.read_bytes()
    restored = load_project(filename).analyses[analysis.id]
    assert restored.type == GE_TYPE and restored.beam_formulation == GE_FORMULATION
    assert restored.to_dict() == analysis.to_dict()
    assert point_panel_from_analysis(restored).to_dict() == panel.to_dict()
    assert filename.read_bytes() == saved and b'"schema"' in saved


def test_public_physical_recovery_and_one_cached_factor(solved, inputs):
    owner, first, second = solved
    panel = inputs[2]
    assert owner.preflight()["retained_dofs"] == 24
    assert owner.preflight()["reference_linear_factorizations"] == 1
    assert (first.generation, second.generation) == (1, 2)
    np.testing.assert_allclose(_q(second), 2*_q(first), rtol=1e-10, atol=1e-12)
    record, = owner.recover(selection=panel.recovery_selection)
    assert type(record) is ss.GEReferenceDistributingFootprintWrench
    assert record.configuration == "reference_linear" and record.law == REFERENCE_DISTRIBUTING_LAW
    assert not record.production_qualified and record.host_element_ids == (2, 3)
    assert record.beam_node_id == 102 and record.model_identity == owner.preflight()["identity"]
    np.testing.assert_allclose(record.shell_action[:3]+record.beam_action[:3], 0., atol=1e-10)
    np.testing.assert_allclose(record.shell_action[3:]+record.beam_action[3:]+
                              np.cross(record.eccentricity_m, record.beam_action[:3]), 0., atol=1e-10)
    np.testing.assert_array_equal(record.shell_node_actions[:, 5], np.zeros(6))
    xyz = dict(zip(panel.shell.node_ids, panel.shell.coordinates_m))
    t, omega = np.array((.2, -.3, .4)), np.array((.5, .2, -.1))
    shell_virtual = np.array([np.r_[t+np.cross(omega, xyz[node]), omega] for node in record.shell_node_ids])
    beam_virtual = np.r_[t+np.cross(omega, record.beam_position_m), omega]
    physical_work = np.sum(shell_virtual*record.shell_node_actions)+beam_virtual@record.beam_action
    assert physical_work == pytest.approx(0., abs=1e-9)
    for values in (record.shell_node_actions, record.shell_action, record.beam_action, record.centroid_m, record.gap):
        with pytest.raises(ValueError): values.setflags(write=True)


def test_checkpoint_resume_and_cancellation_preserve_accepted_prefix(solved, inputs):
    owner, _, second = solved
    panel = inputs[2]
    prefix = owner.checkpoint()
    class AtPublish(CancellationToken):
        def raise_if_cancelled(self, stage=""):
            if stage == "stiffened-shell.linear.publish": self.cancel("consumer owned test boundary")
            super().raise_if_cancelled(stage)
    with pytest.raises(SolveCancelled): owner.solve_linear(panel.actions, cancellation_token=AtPublish())
    assert owner.checkpoint() == prefix and owner.recover().generation == second.generation
    fresh = panel.create_analysis()
    try:
        restored = fresh.restore(prefix)
        np.testing.assert_array_equal(_q(restored), _q(second))
        assert fresh.checkpoint() == prefix
        assert type(fresh.recover(selection=panel.recovery_selection)[0]) is ss.GEReferenceDistributingFootprintWrench
        resumed = fresh.solve_linear(panel.actions)
        assert resumed.generation == restored.generation+1
        assert fresh.preflight()["reference_linear_factorizations"] == 1
        with pytest.raises(ValueError): fresh.solve(panel.actions)
    finally:
        fresh.close()


def test_native_project_public_create_solve_and_selected_recovery(inputs, solved, tmp_path):
    panel = inputs[2]
    project, record = _project(panel)
    reopened = load_project(save_project(project, tmp_path/"project.anyfem")).analyses[record.id]
    restored = point_panel_from_analysis(reopened)
    result = solve_point_panel(reopened)
    np.testing.assert_array_equal(_q(result), _q(solved[1]))
    assert type(result.distributing_footprint_wrenches[0]) is ss.GEReferenceDistributingFootprintWrench
    owner = reopened.create_point_panel_analysis()
    try:
        owner.solve_linear(restored.actions)
        assert type(owner.recover(selection=restored.recovery_selection)[0]) is ss.GEReferenceDistributingFootprintWrench
    finally:
        owner.close()


def test_independent_point_prefix_remains_explicit(inputs):
    definition, actions, _ = inputs
    definition = replace(definition, points=(ss.PointConnection(1, 101),))
    panel = ExperimentalGEReferenceDistributingPanel.from_definition(definition, actions)
    assert panel.points == (ss.PointConnection(1, 101),)
    assert type(ExperimentalGEReferenceDistributingPanel.from_dict(panel.to_dict())) is type(panel)
    owner = panel.create_analysis()
    try:
        result = owner.solve_linear(panel.actions)
        assert len(result.point_wrenches) == len(result.distributing_footprint_wrenches) == 1
        assert owner.preflight()["constraints"] == 12
        assert (result.point_wrenches[0].shell_node_id, result.point_wrenches[0].beam_node_id) == (1, 101)
    finally:
        owner.close()


@pytest.mark.parametrize("options", ({"targets": (1.,)}, {"increment_control": object()},
    {"on_accepted": lambda result: None}, {"on_case": lambda result: None},
    {"max_iterations": 13}, {"max_backtracks": 9}))
def test_project_unsupported_controls_reject_before_owner_creation(inputs, monkeypatch, options):
    _, record = _project(inputs[2])
    def forbidden(self): raise AssertionError("unsupported controls created an owner")
    monkeypatch.setattr(ExperimentalGEReferenceDistributingPanel, "create_analysis", forbidden)
    with pytest.raises(ValueError, match="reference-linear"): solve_point_panel(record, **options)


@pytest.mark.parametrize("change", (
    lambda d: replace(d, lines=(object(),)), lambda d: replace(d, footprints=(object(),)),
    lambda d: replace(d, modal_boundary_policy=ss.GE_FREE_FREE_MODAL_ONLY_V1),
    lambda d: replace(d, footprint_chart_policy=ss.GE_NODAL_FOOTPRINT_INCREMENT_ORIGIN_CHART_V2),
    lambda d: replace(d, bl5_material_policy=ss.BL5_GENERALIZED_MATERIAL_V1),
    lambda d: replace(d, shell_reference_policy=ss.CURVED_Q4_POINT_REFERENCE_V1),
    lambda d: replace(d, retained_scale_policy="native_force_moment_roles_v1"),
    lambda d: replace(d, q4_pullback_policy="historical_corotational"),
    lambda d: replace(d, shell=replace(d.shell, s3_elements=((1, 2, 6),))),
    lambda d: replace(d, shell=replace(d.shell, j2_curve=object())),
))
def test_unsupported_definition_exports_fail_without_assembly(inputs, monkeypatch, change):
    def forbidden(**kwargs): raise AssertionError("unsupported export assembled an owner")
    monkeypatch.setattr(ss, "create_analysis", forbidden)
    with pytest.raises(ValueError): ExperimentalGEReferenceDistributingPanel.from_definition(change(inputs[0]), inputs[1])


def test_known_empty_finite_field_is_compatible_but_nonempty_is_rejected(inputs, monkeypatch):
    if "finite_distributing_footprints" not in ss.LinearDefinition.__dataclass_fields__:
        return  # Older solver graphs have no such option; their strict schema remains.
    model, actions = inputs[:2]
    exported=ExperimentalGEReferenceDistributingPanel.from_definition(model,actions)
    assert exported.definition().finite_distributing_footprints==()
    def forbidden(**kwargs):raise AssertionError("unsupported finite consumer assembled a model")
    monkeypatch.setattr(ss,"create_analysis",forbidden)
    for value in (None, False, [], (ss.GEFiniteDistributingFootprint(model.distributing_footprints[0]),)):
        bad=replace(model,finite_distributing_footprints=value)
        with pytest.raises(ValueError):ExperimentalGEReferenceDistributingPanel.from_definition(bad,actions)


@pytest.mark.parametrize("change", (
    lambda d: d.update(extra=True), lambda d: d.update(schema="unknown"),
    lambda d: d.update(law="unknown"), lambda d: d.update(admission="finite"),
    lambda d: d["distributing_footprints"][0].update(law="unknown"),
    lambda d: d["distributing_footprints"][0].update(host_element_ids=[2, 2]),
    lambda d: d["distributing_footprints"][0].update(bounds_xy_m=[1., 3., 0., float("nan")]),
    lambda d: d["distributing_footprints"][0].update(extra=True),
    lambda d: d["beams"][0].update(sha256="0"*64),
    lambda d: d["beams"][0].update(raw_base64="corrupt"),
    lambda d: d["shell"]["coordinates_m"][0].__setitem__(0, float("inf")),
    lambda d: d.pop("q4_pullback_policy"),
))
def test_strict_schema_law_and_native_input_corruption_reject(inputs, monkeypatch, change):
    def forbidden(**kwargs): raise AssertionError("malformed input assembled an owner")
    monkeypatch.setattr(ss, "create_analysis", forbidden)
    record = deepcopy(inputs[2].to_dict())
    change(record)
    with pytest.raises(ValueError): ExperimentalGEReferenceDistributingPanel.from_dict(record)


def test_create_analysis_closes_owner_when_public_admission_mismatches(inputs, monkeypatch):
    class Mismatch:
        capabilities = {}; closed = False
        def preflight(self): return {}
        def close(self): self.closed = True
    wrong = Mismatch()
    monkeypatch.setattr(ss, "create_analysis", lambda **kwargs: wrong)
    with pytest.raises(ValueError, match="public owner"): inputs[2].create_analysis()
    assert wrong.closed
