"""Private qualification observation never replaces the geometry owner call."""
from types import SimpleNamespace
import pytest
from anyfem import _preparation_context as context


def test_inactive_observation_does_not_read_plan():
    class Unreadable:
        @property
        def content_checksum(self):pytest.fail('inactive observer read plan')
    context._record_preparation_plan(Unreadable())


def test_nested_observation_and_exception_restore():
    first=SimpleNamespace(content_checksum='first');second=SimpleNamespace(content_checksum='second')
    with context._collect_preparation_evidence() as outer:
        assert type(outer) is list
        context._record_preparation_plan(first)
        with pytest.raises(RuntimeError):
            with context._collect_preparation_evidence() as inner:
                context._record_preparation_plan(second)
                raise RuntimeError('caller failure')
        context._record_preparation_plan(first)
    assert [row['plan_checksum'] for row in outer]==['first','second','first']
    assert inner==[{'plan_checksum':'second','plan':second}]
    assert outer[0]['plan'] is first
    context._record_preparation_plan(second)
    assert len(outer)==3 and len(inner)==1


def test_actual_serial_preparation_observes_one_original_plan(monkeypatch):
    from anygeometry import GeometryModel
    from anyfem.structural_preparation import prepare_structural_connectivity
    monkeypatch.setenv('ANYGEOMETRY_EXPERIMENTAL_PARALLEL_PLANNING','0')
    model=GeometryModel()
    for points in (((-1,-1,0),(1,-1,0),(1,1,0),(-1,1,0)),
                   ((-1,0,-1),(1,0,-1),(1,0,1),(-1,0,1))):
        model.add_plate(model.add_points(points))
    with context._collect_preparation_evidence() as rows:
        report=prepare_structural_connectivity(model)
    assert report.connections and len(rows)==1
    assert rows[0]['plan_checksum']==rows[0]['plan'].content_checksum
    assert not any(name.startswith('_anyfem') for name in vars(model))


def test_actual_parallel_preparation_without_invented_callback(monkeypatch,tmp_path):
    import json
    from anygeometry import GeometryModel
    from anygeometry.serialization import to_dict,from_dict
    from anygeometry._planning_code import exact_numerical_record
    from anygeometry.runtime_diagnostics import collect_runtime_records
    from anyfem.structural_preparation import prepare_structural_connectivity
    monkeypatch.setenv('ANYGEOMETRY_EXPERIMENTAL_PARALLEL_PLANNING','1')
    monkeypatch.setenv('ANYGEOMETRY_PLANNING_SCRATCH_ROOT',str(tmp_path/'production'))
    model=GeometryModel()
    for points in (((-1,-1,0),(1,-1,0),(1,1,0),(-1,1,0)),
                   ((-1,0,-1),(1,0,-1),(1,0,1),(-1,0,1))):
        model.add_plate(model.add_points(points))
    initial=to_dict(model)
    with context._collect_preparation_evidence() as rows,collect_runtime_records() as records:
        report=prepare_structural_connectivity(model)
    receipt=records['parallel_planning'][-1]
    assert receipt['disposition']=='parallel',receipt
    assert report.connections and len(rows)==1
    assert receipt['costs']['callback_calls']==0
    assert [stage['stage'] for stage in receipt['stages']]==['pair','face']
    raw=exact_numerical_record(rows[0]['plan'])
    application=json.dumps(to_dict(model),sort_keys=True,separators=(',',':'),allow_nan=False)
    monkeypatch.setenv('ANYGEOMETRY_EXPERIMENTAL_PARALLEL_PLANNING','0')
    reference=from_dict(initial)
    with context._collect_preparation_evidence() as control:
        serial_report=prepare_structural_connectivity(reference)
    assert len(control)==1
    assert raw==exact_numerical_record(control[0]['plan'])
    assert rows[0]['plan_checksum']==control[0]['plan_checksum']
    assert application==json.dumps(to_dict(reference),sort_keys=True,separators=(',',':'),allow_nan=False)
    assert report.to_dict()==serial_report.to_dict()


@pytest.mark.parametrize('invalid',[False,True])
def test_real_callback_phase_and_original_exception_preserved(monkeypatch,invalid):
    from anygeometry import GeometryModel
    from anyfem.structural_preparation import prepare_structural_connectivity
    monkeypatch.setenv('ANYGEOMETRY_EXPERIMENTAL_PARALLEL_PLANNING','0')
    model=GeometryModel()
    for points in (((-1,-1,0),(1,-1,0),(1,1,0),(-1,1,0)),
                   ((-1,0,-1),(1,0,-1),(1,0,1),(-1,0,1))):
        model.add_plate(model.add_points(points))
    phases=[];failure=RuntimeError('original caller failure')
    def callback(phase):
        phases.append(phase)
        if phase=='structural intersection batch':raise failure
    with context._collect_preparation_evidence() as rows:
        with pytest.raises(TypeError if invalid else RuntimeError) as caught:
            prepare_structural_connectivity(model,cancellation_check=0 if invalid else callback)
    assert rows==[]
    if not invalid:
        assert caught.value is failure
        assert phases[-1]=='structural intersection batch'


@pytest.mark.parametrize('action',['model','exception','cancel'])
def test_actual_parallel_caller_callback_terminal_after_capture(monkeypatch,tmp_path,action):
    from anygeometry import GeometryModel
    from anygeometry._planning_executor import PlannerCancelled,PlannerSourceChanged
    from anygeometry.runtime_diagnostics import collect_runtime_records
    from anyfem.structural_preparation import prepare_structural_connectivity,StructuralPreparationError
    monkeypatch.setenv('ANYGEOMETRY_EXPERIMENTAL_PARALLEL_PLANNING','1')
    monkeypatch.setenv('ANYGEOMETRY_PLANNING_SCRATCH_ROOT',str(tmp_path/'production'))
    model=GeometryModel()
    for points in (((-1,-1,0),(1,-1,0),(1,1,0),(-1,1,0)),
                   ((-1,0,-1),(1,0,-1),(1,0,1),(-1,0,1))):
        model.add_plate(model.add_points(points))
    phases=[];batch=[]
    failure=PlannerCancelled('caller cancelled') if action=='cancel' else RuntimeError('caller original error')
    def callback(phase):
        phases.append(phase)
        if phase!='structural intersection batch':return
        batch.append(phase)
        if len(batch)<2:return
        if action=='model':model.unknown_callback_authority=True
        else:raise failure
    with context._collect_preparation_evidence() as rows,collect_runtime_records() as records:
        with pytest.raises(RuntimeError if action=='exception' else StructuralPreparationError) as caught:
            prepare_structural_connectivity(model,cancellation_check=callback)
    if action=='exception':assert caught.value is failure
    elif action=='cancel':assert caught.value.__cause__ is failure
    else:assert type(caught.value.__cause__) is PlannerSourceChanged
    assert rows==[] and len(batch)==2 and phases[-1]=='structural intersection batch'
    receipt=records['parallel_planning'][-1]
    assert receipt['manifest_sha256'] and receipt['disposition']=='terminal'
    assert receipt['retired'] is True and receipt['stages']==[]
