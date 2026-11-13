"""Pure runner metadata tests; no caller/model qualification."""
import copy
import importlib.util
from pathlib import Path
import xml.etree.ElementTree as ET
import pytest
ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('caller_host_runner',ROOT/'tools/test_anyfem.py')
r=importlib.util.module_from_spec(spec);spec.loader.exec_module(r)
from tools.ci_caller_planning_profile import REQUIRED_CASES,CONTRACT

def inventory():
    nodes=['tests/'+k.split('::',1)[0]+'.py::'+k.split('::',1)[1] for k in sorted(REQUIRED_CASES)]
    nodes+=['tests/test_other.py::test_same[original]']
    return {n:[n.split('::',1)[0][:-3].replace('/','.'),n.split('::',1)[1]] for n in nodes}

def test_partition_preserves_exact_union_and_original_order():
    data=inventory();groups=r.caller_phase_assignments(data)
    assert len(groups)==5 and len(groups[0])==1 and all(len(g)==1 for g in groups[1:])
    union=[n for g in groups for n in g]
    assert len(union)==len(set(union))==len(data) and set(union)==set(data)
    assert groups[0]==[list(data)[-1]]

def test_partition_refuses_missing_required_positive():
    data=inventory();data.pop(next(iter(data)))
    with pytest.raises(RuntimeError,match='required caller'):r.caller_phase_assignments(data)

def make_xml(path,data):
    root=ET.Element('testsuites');suite=ET.SubElement(root,'testsuite')
    for node,(classname,name) in data.items():
        case=ET.SubElement(suite,'testcase',classname=classname,name=name)
        props=ET.SubElement(case,'properties')
        ET.SubElement(props,'property',name='anyfem_original_nodeid',value=node)
        if 'test_preparation_evidence.py' in node:
            for key,value in [('caller_planning_profile',CONTRACT),('execution_scope','original-caller-profile-assertions'),('original_test_body_executed','True'),('postcapture_qualification','True')]:
                ET.SubElement(props,'property',name=key,value=value)
    ET.ElementTree(root).write(path)
    return root

def test_merge_keeps_all_actual_cases_and_properties(tmp_path):
    data=inventory();path=tmp_path/'actual.xml';make_xml(path,data)
    target=tmp_path/'merged.xml';r.merge_caller_junit([path],target,data)
    cases=list(ET.parse(target).iter('testcase'))
    assert len(cases)==len(data) and all(c.find('properties') is not None for c in cases)

@pytest.mark.parametrize('fault',['missing','duplicate','unknown','wrong_identity','skip','failure','body','duplicate_property'])
def test_merge_refuses_union_or_positive_contract_faults(tmp_path,fault):
    data=inventory();path=tmp_path/'actual.xml';root=make_xml(path,data);suite=root[0];case=suite[0]
    if fault=='missing':suite.remove(case)
    elif fault=='duplicate':suite.append(copy.deepcopy(case))
    elif fault=='unknown':case.find('properties/property').set('value','tests/unknown.py::test_unknown')
    elif fault=='wrong_identity':case.set('name','test_wrong')
    elif fault in ('skip','failure'):ET.SubElement(case,'skipped' if fault=='skip' else 'failure')
    elif fault=='body':case.find("properties/property[@name='original_test_body_executed']").set('value','False')
    else:case.find('properties').append(copy.deepcopy(case.find('properties/property')))
    ET.ElementTree(root).write(path)
    with pytest.raises((RuntimeError,ValueError)):r.merge_caller_junit([path],tmp_path/'merged.xml',data)

@pytest.mark.parametrize('fault',['valid','skipped','body','identity','duplicate','wrong_positive'])
def test_single_positive_requires_actual_original_body_before_next_phase(tmp_path,fault):
    data=inventory();node=next(iter(data));selected={node:data[node]};path=tmp_path/'single.xml';root=make_xml(path,selected);case=root[0][0]
    if fault=='skipped':ET.SubElement(case,'skipped')
    elif fault=='body':case.find("properties/property[@name='original_test_body_executed']").set('value','False')
    elif fault=='identity':case.set('name','other')
    elif fault=='duplicate':root[0].append(copy.deepcopy(case))
    elif fault=='wrong_positive':
        other=list(data)[1];root=make_xml(path,{other:data[other]})
    ET.ElementTree(root).write(path)
    if fault=='valid':r._verify_caller_positive_phase(path,node,data[node])
    else:
        with pytest.raises((RuntimeError,ValueError)):r._verify_caller_positive_phase(path,node,data[node])


def test_all_phases_execute_once_and_merge_exact_inventory(tmp_path,monkeypatch):
    import json
    data=inventory();seen=[];env={'ANYGEOMETRY_REQUIRE_QUALIFIED_PLANNING_TESTS':'1','ANYFEM_VERIFICATION_OUT':'original'}
    def execute(command,log,environment):
        assert environment==env
        manifest=Path(command[-1]);d=json.loads(manifest.read_text());seen.append(d)
        if d['kind']=='collect':
            d['inventory']=data;manifest.write_text(json.dumps(d));ET.ElementTree(ET.Element('testsuites')).write(log.parent/'pytest.xml')
        else:make_xml(log.parent/'pytest.xml',{n:data[n] for n in d['assigned']})
        return 0
    monkeypatch.setattr(r,'execute',execute);report={}
    assert r._execute_caller_phases(['python','-m','pytest','tests','-q','--junitxml=old.xml'],tmp_path,env,report)==0
    assert [x['kind'] for x in seen]==['collect','core','positive','positive','positive','positive']
    ids=[n for x in seen[1:] for n in x['assigned']]
    assert len(ids)==len(set(ids))==len(data) and set(ids)==set(data)
    assert len(list(ET.parse(tmp_path/'pytest.xml').iter('testcase')))==len(data)
    assert len(report['phases'])==6

@pytest.mark.parametrize('profile,flag',[('full','0'),('qt','1'),('quick','1'),('focused','1')])
def test_unselected_profiles_retain_original_execute_path(tmp_path,monkeypatch,profile,flag):
    import json
    monkeypatch.setenv('ANYGEOMETRY_REQUIRE_QUALIFIED_PLANNING_TESTS',flag)
    monkeypatch.setattr(r,'owner_capabilities',lambda:None)
    monkeypatch.setattr(r.importlib,'import_module',lambda *a:None)
    monkeypatch.setattr(r,'_execute_caller_phases',lambda *a:pytest.fail('unexpected isolation'))
    monkeypatch.setattr(r,'validate_verification',lambda *a:None)
    # Existing full verification catalog import is a separate unchanged gate.
    if profile=='full':
        monkeypatch.setitem(__import__('sys').modules,'anyfem.verification',type('Verification',(),{'cases':staticmethod(lambda:[])})())
    seen=[]
    def execute(command,log,env):
        seen.append(command);suite=ET.Element('testsuite',tests='1',failures='0',errors='0',skipped='0')
        ET.SubElement(suite,'testcase',classname='tests.test_qt_workbench',name='test_real')
        root=ET.Element('testsuites');root.append(suite);ET.ElementTree(root).write(log.parent/'pytest.xml');return 0
    monkeypatch.setattr(r,'execute',execute)
    args=['--profile',profile,'--out',str(tmp_path/'out')]
    if profile=='focused':args+=['tests/test_tiny.py::test_real']
    assert r.main(args)==0 and len(seen)==1
    assert seen[0][1:3]==['-m','pytest']


def test_default_verifier_still_refuses_partial_single_case(tmp_path):
    data=inventory();node=next(iter(data));path=tmp_path/'single.xml';make_xml(path,{node:data[node]})
    with pytest.raises(ValueError,match='missing required caller'):r._caller_profile().verify_junit([path])

@pytest.mark.parametrize('kind',['failure','error'])
def test_merge_rejects_ordinary_failed_xml_even_with_exit_zero(tmp_path,kind):
    data=inventory();path=tmp_path/'actual.xml';root=make_xml(path,data)
    ET.SubElement(root[0][-1],kind,message='actual ordinary test failure')
    ET.ElementTree(root).write(path)
    with pytest.raises(RuntimeError,match='ordinary test failure'):
        r.merge_caller_junit([path],tmp_path/'merged.xml',data)

@pytest.mark.parametrize('fault',['unknown','duplicate','missing','changed_reason'])
def test_exact_known_collection_skips_only(tmp_path,fault):
    data=inventory();path=tmp_path/'actual.xml';root=make_xml(path,data)
    case=ET.SubElement(root[0],'testcase',classname='tests.test_optional',name='tests/test_optional.py')
    ET.SubElement(case,'skipped',type='pytest.skip',message='declared optional').text='fixed source line'
    known=[r._collection_skip_key(case)]
    if fault=='unknown':known=[]
    elif fault=='duplicate':root[0].append(copy.deepcopy(case))
    elif fault=='missing':root[0].remove(case)
    else:case.find('skipped').set('message','unrelated skip')
    ET.ElementTree(root).write(path)
    with pytest.raises(RuntimeError):r.merge_caller_junit([path],tmp_path/'merged.xml',data,known)


def test_known_collection_skip_is_preserved_once(tmp_path):
    data=inventory();path=tmp_path/'actual.xml';root=make_xml(path,data)
    case=ET.SubElement(root[0],'testcase',classname='tests.test_optional',name='tests/test_optional.py')
    ET.SubElement(case,'skipped',type='pytest.skip',message='declared optional')
    known=[r._collection_skip_key(case)];ET.ElementTree(root).write(path)
    r.merge_caller_junit([path],tmp_path/'merged.xml',data,known)
    assert len(ET.parse(tmp_path/'merged.xml').findall('.//skipped'))==1


def test_actual_pytest_collection_skip_and_before_fixture_identity_check(tmp_path):
    import json,subprocess,sys,os
    tests=tmp_path/'tests';tests.mkdir()
    (tests/'test_optional.py').write_text("import pytest\npytest.skip('known optional',allow_module_level=True)\n")
    marker=tmp_path/'fixture-ran'
    (tests/'test_mini.py').write_text("from pathlib import Path\ndef test_real():\n Path("+repr(str(marker))+").write_text('ran')\n")
    config=tmp_path/'pytest.ini';config.write_text('[pytest]\n')
    manifest=tmp_path/'collect.json';xml=tmp_path/'collect.xml'
    manifest.write_text(json.dumps(dict(kind='collect',args=['-c',str(config),'tests','-q','--junitxml='+str(xml)])))
    script="import importlib.util,pathlib;spec=importlib.util.spec_from_file_location('unit_runner',"+repr(str(ROOT/'tools/test_anyfem.py'))+");r=importlib.util.module_from_spec(spec);spec.loader.exec_module(r);r.ROOT=pathlib.Path("+repr(str(tmp_path))+");r.owner_capabilities=lambda:None;raise SystemExit(r._caller_bootstrap("+repr(str(manifest))+"))"
    env=os.environ.copy()
    env.pop('PYTEST_ADDOPTS',None)
    # The temporary project has its own collection and must not load the outer suite plugin.
    env.pop('PYTEST_PLUGINS',None)
    env['PYTEST_DISABLE_PLUGIN_AUTOLOAD']='1'
    result=subprocess.run([sys.executable,'-c',script],cwd=tmp_path,env=env,capture_output=True,text=True,timeout=15)
    assert result.returncode==0,result.stdout+result.stderr
    data=json.loads(manifest.read_text());assert list(data['inventory'])==['tests/test_mini.py::test_real']
    cases=list(ET.parse(xml).iter('testcase'));assert len(cases)==1 and cases[0].find('skipped') is not None
    assert not marker.exists()
    data.update(kind='core',assigned=['tests/test_mini.py::test_wrong'],args=['-c',str(config),'tests','-q','--junitxml='+str(tmp_path/'core.xml')]);manifest.write_text(json.dumps(data))
    result=subprocess.run([sys.executable,'-c',script],cwd=tmp_path,env=env,capture_output=True,text=True,timeout=15)
    assert result.returncode!=0 and not marker.exists()


def test_sequential_phase_driver_preserves_order_env_and_stops_after_failure(tmp_path,monkeypatch):
    import json
    data=inventory();events=[]
    def execute(command,log,env):
        manifest=Path(command[-1]);d=json.loads(manifest.read_text());events.append(d)
        assert env['ANYGEOMETRY_REQUIRE_QUALIFIED_PLANNING_TESTS']=='1'
        if d['kind']=='collect':
            d['inventory']=data;manifest.write_text(json.dumps(d));ET.ElementTree(ET.Element('testsuites')).write(log.parent/'pytest.xml');return 0
        return 7
    monkeypatch.setattr(r,'execute',execute);report={}
    code=r._execute_caller_phases(['python','-m','pytest','tests','-q','--junitxml=old.xml'],tmp_path,{'ANYGEOMETRY_REQUIRE_QUALIFIED_PLANNING_TESTS':'1'},report)
    assert code==7 and [d['kind'] for d in events]==['collect','core']
    assert not (tmp_path/'pytest.xml').exists() and len(report['phases'])==2
    assert report['phases'][1]['cumulative_driver_seconds']>=report['phases'][0]['cumulative_driver_seconds']

