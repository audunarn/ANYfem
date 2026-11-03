"""Require the four real caller planner positives in the declared CI lane."""
import argparse
import json
import os
from pathlib import Path
import sys
import xml.etree.ElementTree as ET

CONTRACT='caller-planning-windows-3142-np246-scipy1171-v1'
REQUIRED_CASES=frozenset([
    'test_preparation_evidence::test_actual_parallel_preparation_without_invented_callback',
    *('test_preparation_evidence::test_actual_parallel_caller_callback_terminal_after_capture['+action+']'
      for action in ('model','exception','cancel'))])


def verify_junit(paths):
    seen=set()
    for path in paths:
        for case in ET.parse(path).iter('testcase'):
            key=case.get('classname','').rsplit('.',1)[-1]+'::'+case.get('name','')
            if key not in REQUIRED_CASES:continue
            if key in seen:raise ValueError('duplicate required caller profile case: '+key)
            props=[(p.get('name'),p.get('value')) for p in case.findall('properties/property')]
            required=[('caller_planning_profile',CONTRACT),('execution_scope','original-caller-profile-assertions'),
                      ('original_test_body_executed','True'),('postcapture_qualification','True')]
            if (any(case.find(kind) is not None for kind in ('failure','error','skipped')) or
                    any(props.count(row)!=1 for row in required) or
                    any(name=='execution_scope' and value!='original-caller-profile-assertions' for name,value in props) or
                    any(name=='initial_serial_fallback_executed' for name,_ in props)):
                raise ValueError('required caller case did not complete original positive assertions: '+key)
            seen.add(key)
    if seen!=REQUIRED_CASES:
        raise ValueError('missing required caller profile cases: '+', '.join(sorted(REQUIRED_CASES-seen)))
    return len(seen)


def preflight(geometry_root):
    if sys.platform!='win32' or sys.version_info[:3]!=(3,14,2):
        raise ValueError('caller positive profile requires Windows CPython3.14.2')
    if os.environ.get('ANYGEOMETRY_REQUIRE_QUALIFIED_PLANNING_TESTS')!='1':
        raise ValueError('caller positive profile must require original assertions')
    import numpy
    import scipy
    import anygeometry
    if numpy.__version__!='2.4.6' or scipy.__version__!='1.17.1':
        raise ValueError('caller positive numerical versions differ')
    origin=Path(anygeometry.__file__).resolve()
    if geometry_root is not None and origin!=(geometry_root/'src/anygeometry/__init__.py').resolve():
        raise ValueError('caller positive geometry checkout differs')
    from anygeometry._planning_numpy_context import capture_numpy_context
    from anygeometry._planning_native import identity
    context=capture_numpy_context(package_only=True);context.verify()
    return dict(contract=CONTRACT,geometry_origin=str(origin),python=list(sys.version_info[:3]),
                numpy=numpy.__version__,scipy=scipy.__version__,native=identity(),
                required_cases=len(REQUIRED_CASES),original_assertions_required=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--preflight',action='store_true')
    parser.add_argument('--geometry-root',type=Path)
    parser.add_argument('--report',type=Path)
    parser.add_argument('junit',nargs='*',type=Path)
    args=parser.parse_args()
    data=preflight(args.geometry_root) if args.preflight else {'positive_cases':verify_junit(args.junit)}
    if args.report:
        args.report.parent.mkdir(parents=True,exist_ok=True)
        args.report.write_text(json.dumps(data,indent=2))
    print(json.dumps(data))


if __name__=='__main__':main()
