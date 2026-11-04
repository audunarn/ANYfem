"""Relocation must never turn missing/skipped integration into a green gate."""
import importlib.util
from pathlib import Path
import xml.etree.ElementTree as ET
import pytest


def validator():
    p=Path(__file__).resolve().parents[1]/'tools/ci_mesher_caller_integration.py'
    spec=importlib.util.spec_from_file_location('relocated_caller_gate',p)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def document(module):
    root=ET.Element('testsuite')
    for key in sorted(module.REQUIRED_CASES):
        stem,name=key.split('::',1)
        ET.SubElement(root,'testcase',classname='tests.'+stem,name=name)
    return root


def test_all_original_cases_must_pass(tmp_path):
    module=validator();p=tmp_path/'cases.xml';ET.ElementTree(document(module)).write(p)
    assert module.verify_junit([p])==11


@pytest.mark.parametrize('fault',['missing','duplicate','failure','error','skipped','renamed','module'])
def test_missing_or_nonexecuted_relocated_cases_fail_closed(tmp_path,fault):
    module=validator();root=document(module);case=root[0]
    if fault=='missing':root.remove(case)
    elif fault=='duplicate':root.append(case)
    elif fault in ('failure','error','skipped'):ET.SubElement(case,fault)
    elif fault=='renamed':case.set('name','unrelated_pass')
    else:case.set('classname','unrelated_module')
    p=tmp_path/'cases.xml';ET.ElementTree(root).write(p)
    with pytest.raises(ValueError):module.verify_junit([p])
