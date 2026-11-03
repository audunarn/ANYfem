"""Receipt/JUnit validation units; simulated data is not runtime qualification."""
from copy import deepcopy
import importlib.util
from pathlib import Path
import xml.etree.ElementTree as ET
import pytest
from _caller_planning_profile import CONTRACT,require_positive_profile,validate_initial_receipt


def receipt():
    return dict(schema='R5-production-v1',disposition='serial',stages=[],attempted_stages=[],
                costs={'abandoned':{}},retired=True,reason='interpreter runtime adapter is Windows-only')


def test_initial_receipt_unit_accepts_only_initial_serial_scope():
    assert validate_initial_receipt(receipt())=='interpreter runtime adapter is Windows-only'


@pytest.mark.parametrize('field,value',[('disposition','parallel'),('manifest_sha256','captured'),
    ('stages',[{'stage':'pair'}]),('attempted_stages',['pair']),('costs',{'abandoned':{'pair':1}}),
    ('retired',False),('reason','')])
def test_initial_receipt_unit_rejects_postcapture_or_nonrefusal(field,value):
    data=receipt();data[field]=value
    with pytest.raises(AssertionError):validate_initial_receipt(data)


def test_required_positive_flag_forbids_initial_classification(monkeypatch):
    monkeypatch.setenv('ANYGEOMETRY_REQUIRE_QUALIFIED_PLANNING_TESTS','1')
    with pytest.raises(AssertionError,match='outside its declared positive profile'):
        require_positive_profile(False)
    require_positive_profile(True)


def validator():
    path=Path(__file__).resolve().parents[1]/'tools/ci_caller_planning_profile.py'
    spec=importlib.util.spec_from_file_location('caller_profile_validator_unit',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def junit(module):
    root=ET.Element('testsuite')
    for key in sorted(module.REQUIRED_CASES):
        name=key.split('::',1)[1]
        case=ET.SubElement(root,'testcase',classname='tests.test_preparation_evidence',name=name)
        properties=ET.SubElement(case,'properties')
        for key,value in [('caller_planning_profile',CONTRACT),('execution_scope','original-caller-profile-assertions'),
                          ('original_test_body_executed','True'),('postcapture_qualification','True')]:
            ET.SubElement(properties,'property',name=key,value=value)
    return root


def test_junit_unit_requires_exact_four_completed_original_cases(tmp_path):
    module=validator();path=tmp_path/'synthetic.xml';ET.ElementTree(junit(module)).write(path)
    assert module.verify_junit([path])==4


@pytest.mark.parametrize('invalid',['missing','duplicate','failed','skipped','initial','body','postcapture'])
def test_junit_unit_refuses_incomplete_or_initial_only_cases(tmp_path,invalid):
    module=validator();root=junit(module);case=root[0]
    if invalid=='missing':root.remove(case)
    elif invalid=='duplicate':root.append(deepcopy(case))
    elif invalid in ('failed','skipped'):ET.SubElement(case,'failure' if invalid=='failed' else 'skipped')
    else:
        key={'initial':'execution_scope','body':'original_test_body_executed','postcapture':'postcapture_qualification'}[invalid]
        for prop in case.findall('properties/property'):
            if prop.get('name')==key:prop.set('value','initial-serial-fallback' if invalid=='initial' else 'False')
    path=tmp_path/'synthetic.xml';ET.ElementTree(root).write(path)
    with pytest.raises(ValueError):module.verify_junit([path])
