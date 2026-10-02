"""Public staged distributed input transport; no copied mechanics or numerical jobs."""
import copy
from dataclasses import replace

import pytest
import numpy as np
from anysolver import stiffened_shell
from anyfem.io.project_file import load_project, save_project
from anyfem.model.project import Project
from anyfem.model.records import AnalysisDefinition
from anyfem.solve.stiffened_shell import (
    ExperimentalGEPointPanel, ExperimentalGENodalRigidFootprintPanel,
    SCHEMA, SCHEMA_DISTRIBUTED_PROGRAMME, load_case, save_case,
)
from anyfem.solve.stiffened_shell_project import point_panel_from_analysis, solve_point_panel
from test_stiffened_shell_project import _ge
from test_stiffened_shell_staged_load_consumer import Owner, MODEL, _panel as staged_panel
from test_stiffened_shell_ge_distributed_consumer import _panel as distributed_panel


def panel():
    programme = stiffened_shell.GEBeamDistributedProgramme(MODEL, (
        (stiffened_shell.GEBeamLineAction(71, (0., 0., 10.), (1., 2., 3.)),),
        (),
        (stiffened_shell.GEBeamLineAction(71, (0., 0., -10.), (-1., -2., -3.)),),
    ))
    return replace(_ge(), actions=(), distributed_programme=programme)


def test_project_and_standalone_preserve_public_descriptor(tmp_path, monkeypatch):
    monkeypatch.setattr(stiffened_shell, 'create_analysis', lambda **kw: pytest.fail('persistence assembled owner'))
    p = panel(); payload = p.to_dict()
    assert payload['schema'] == SCHEMA_DISTRIBUTED_PROGRAMME
    assert payload['panel']['schema'] == SCHEMA
    standalone = load_case(save_case(p, tmp_path/'stages.anyfem-ge.json'))
    project = Project(name='distributed stages')
    record = AnalysisDefinition.experimental_point_panel('stages', p)
    project.analyses[record.id] = record
    reopened = load_project(save_project(project, tmp_path/'stages.anyfem'))
    restored = point_panel_from_analysis(reopened.analyses[record.id])
    assert standalone.to_dict() == restored.to_dict() == payload
    assert restored.load_input.serialize() == p.distributed_programme.serialize()
    assert restored.status == 'EXPERIMENTAL_NOT_QUALIFIED'
    payload['distributed_programme']['endpoints'][2][0]['force_N_per_reference_m'][2] = 99.
    assert restored.distributed_programme.endpoints[2][0].force_N_per_reference_m[2] == -10.
    record.beam_formulation_options['panel']['distributed_programme']['endpoints'][2][0]['force_N_per_reference_m'][2] = 99.
    with pytest.raises(ValueError): save_project(project, tmp_path/'bad.anyfem')


def test_finite_dispatch_controls_and_cleanup(monkeypatch):
    o = Owner(); monkeypatch.setattr(stiffened_shell, 'create_analysis', lambda **kw: o)
    p = panel(); record = AnalysisDefinition.experimental_point_panel('stages', p)
    token, control, observer = object(), object(), lambda r: None
    solve_point_panel(record, targets=(1., 2., 3.), cancellation_token=token,
                      increment_control=control, on_accepted=observer,
                      max_iterations=20, max_backtracks=12)
    kind, descriptor, targets, options = o.calls[0]
    assert kind == 'finite' and targets == (1., 2., 3.) and o.closed
    assert descriptor.serialize() == p.distributed_programme.serialize()
    assert options == dict(cancellation_token=token, increment_control=control,
                           on_accepted=observer, max_iterations=20, max_backtracks=12)


def test_explicit_positions_required_before_owner(monkeypatch):
    monkeypatch.setattr(stiffened_shell, 'create_analysis', lambda **kw: pytest.fail('missing targets assembled owner'))
    record = AnalysisDefinition.experimental_point_panel('stages', panel())
    with pytest.raises(ValueError): solve_point_panel(record)


def test_solver_owns_position_types_and_validation(monkeypatch):
    o = Owner(); monkeypatch.setattr(stiffened_shell, 'create_analysis', lambda **kw: o)
    targets = (np.float64(.5), np.int64(1))
    solve_point_panel(AnalysisDefinition.experimental_point_panel('stages', panel()), targets=targets)
    assert o.calls[0][2] is targets and o.closed


@pytest.mark.parametrize('other', ['actions', 'load_programme', 'body_load', 'distributed_load', 'pressure_load'])
def test_excludes_other_load_routes(other):
    values = dict(actions=_ge().actions, load_programme=staged_panel().load_programme,
        body_load=stiffened_shell.GEBodyLoad(MODEL, (0., 0., -9.81)),
        distributed_load=distributed_panel().distributed_load,
        pressure_load=stiffened_shell.GEShellPressureLoad(MODEL,
            (stiffened_shell.GEShellPressureAction(1, 10.),)))
    with pytest.raises(ValueError, match='no other load'):
        replace(panel(), **{other: values[other]})


@pytest.mark.parametrize('change', ['extra', 'missing', 'nested', 'implicit', 'future', 'status', 'profile', 'actions'])
def test_strict_envelope_and_descriptor(change):
    payload = panel().to_dict()
    if change == 'extra': payload['extra'] = None
    elif change == 'missing': del payload['distributed_programme']
    elif change == 'nested': payload['panel'] = copy.deepcopy(payload)
    elif change == 'implicit': payload = payload['panel'] | {'distributed_programme': payload['distributed_programme']}
    elif change == 'future': payload['distributed_programme']['endpoints'][2][0]['couple_Nm_per_reference_m'][1] = 4.
    elif change == 'status': payload['status'] = 'qualified'
    elif change == 'profile': payload['profile'] = 'other'
    else: payload['panel']['actions'] = _ge().to_dict()['actions']
    with pytest.raises(ValueError): ExperimentalGEPointPanel.from_dict(payload)


def test_wrong_model_preflight_and_solver_failure_close_owner(monkeypatch):
    p = panel()
    wrong = Owner(identity='cd'*32)
    monkeypatch.setattr(stiffened_shell, 'create_analysis', lambda **kw: wrong)
    with pytest.raises(ValueError, match='different panel model'): p.create_analysis()
    assert wrong.closed and not wrong.calls
    failure = RuntimeError('owner failure')
    class FailedPreflight(Owner):
        def preflight(self): raise failure
    failed = FailedPreflight()
    monkeypatch.setattr(stiffened_shell, 'create_analysis', lambda **kw: failed)
    with pytest.raises(RuntimeError) as caught: p.create_analysis()
    assert caught.value is failure and failed.closed
    failed = Owner(failure=failure)
    monkeypatch.setattr(stiffened_shell, 'create_analysis', lambda **kw: failed)
    with pytest.raises(RuntimeError) as caught:
        solve_point_panel(AnalysisDefinition.experimental_point_panel('stages', p), targets=(1.,))
    assert caught.value is failure and failed.closed


def test_unavailable_owner_descriptor_and_footprint_rejected(monkeypatch):
    p = panel()
    with pytest.raises(ValueError): ExperimentalGENodalRigidFootprintPanel(p, ())
    with pytest.raises(ValueError): replace(_ge(), actions=(), distributed_programme=object())
    payload = p.to_dict()
    monkeypatch.delattr(stiffened_shell, 'GEBeamDistributedProgramme')
    with pytest.raises(ValueError, match='lacks the public'): ExperimentalGEPointPanel.from_dict(payload)
    with pytest.raises(ValueError, match='public experimental'): p.create_analysis()
