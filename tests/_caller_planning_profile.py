"""Explicit caller positive-profile versus real initial serial fallback coverage."""
from functools import wraps
import inspect
import json
import os
import sys

CONTRACT='caller-planning-windows-3142-np246-scipy1171-v1'


def qualified_profile():
    import numpy
    import scipy
    return (sys.platform=='win32' and sys.version_info[:3]==(3,14,2)
            and numpy.__version__=='2.4.6' and scipy.__version__=='1.17.1')


def require_positive_profile(qualified):
    if os.environ.get('ANYGEOMETRY_REQUIRE_QUALIFIED_PLANNING_TESTS')=='1':
        assert qualified,'mandatory caller planning lane is outside its declared positive profile'


def validate_initial_receipt(receipt):
    """R5 encodes QualificationDecline as serial; completed capture has a digest.

    This validates initial scope only. The reason is the real controller's typed
    decline text, not a prediction that a legacy route is necessarily unsupported.
    """
    assert receipt['schema']=='R5-production-v1'
    assert receipt['disposition']=='serial',receipt
    assert receipt.get('manifest_sha256') is None,receipt
    assert receipt['stages']==[] and receipt['attempted_stages']==[],receipt
    assert receipt['costs']['abandoned']=={},receipt
    assert receipt['retired'] is True,receipt
    reason=receipt['reason']
    assert type(reason) is str and 0<len(reason)<=1024,receipt
    return reason


def actual_initial_serial_fallback(monkeypatch,tmp_path):
    """Run the public caller ON and compare exact OFF plan/application/report."""
    from anygeometry import GeometryModel
    from anygeometry.serialization import from_dict,to_dict
    from anygeometry._planning_code import exact_numerical_record
    from anygeometry.runtime_diagnostics import collect_runtime_records
    from anyfem import _preparation_context as context
    from anyfem.structural_preparation import prepare_structural_connectivity
    model=GeometryModel()
    for points in (((-1,-1,0),(1,-1,0),(1,1,0),(-1,1,0)),
                   ((-1,0,-1),(1,0,-1),(1,0,1),(-1,0,1))):
        model.add_plate(model.add_points(points))
    initial=to_dict(model)
    monkeypatch.setenv('ANYGEOMETRY_EXPERIMENTAL_PARALLEL_PLANNING','1')
    monkeypatch.setenv('ANYGEOMETRY_PLANNING_SCRATCH_ROOT',str(tmp_path/'initial-profile'))
    with context._collect_preparation_evidence() as rows,collect_runtime_records() as records:
        report=prepare_structural_connectivity(model)
    assert len(records['parallel_planning'])==1
    reason=validate_initial_receipt(records['parallel_planning'][0])
    assert report.connections and len(rows)==1
    raw=exact_numerical_record(rows[0]['plan'])
    application=json.dumps(to_dict(model),sort_keys=True,separators=(',',':'),allow_nan=False)
    monkeypatch.setenv('ANYGEOMETRY_EXPERIMENTAL_PARALLEL_PLANNING','0')
    reference=from_dict(initial)
    with context._collect_preparation_evidence() as control,collect_runtime_records() as off_records:
        serial_report=prepare_structural_connectivity(reference)
    assert not off_records.get('parallel_planning')
    assert len(control)==1
    assert raw==exact_numerical_record(control[0]['plan'])
    assert rows[0]['plan_checksum']==control[0]['plan_checksum']
    assert application==json.dumps(to_dict(reference),sort_keys=True,separators=(',',':'),allow_nan=False)
    assert report.to_dict()==serial_report.to_dict()
    return reason


def caller_planning_profile(function):
    signature=inspect.signature(function)
    assert {'monkeypatch','tmp_path'}<=set(signature.parameters)
    parameters=list(signature.parameters.values())+[inspect.Parameter('request',inspect.Parameter.KEYWORD_ONLY)]

    @wraps(function)
    def invoke(*args,**kwargs):
        request=kwargs.pop('request')
        properties=request.node.user_properties
        properties.append(('caller_planning_profile',CONTRACT))
        qualified=qualified_profile()
        require_positive_profile(qualified)
        if qualified:
            # Mark completion only after every original assertion has passed.
            result=function(*args,**kwargs)
            properties.extend((('execution_scope','original-caller-profile-assertions'),
                ('original_test_body_executed',True),('postcapture_qualification',True)))
            return result
        bound=signature.bind(*args,**kwargs)
        reason=actual_initial_serial_fallback(bound.arguments['monkeypatch'],bound.arguments['tmp_path'])
        properties.extend((('execution_scope','initial-serial-fallback'),
            ('initial_decline_reason',reason),('initial_serial_fallback_executed',True),
            ('original_test_body_executed',False),('postcapture_qualification',False),
            ('numerical_worker_execution',False),('planning_switch_control','exact-OFF')))

    invoke.__signature__=signature.replace(parameters=parameters)
    return invoke
