"""Validate the fixed manual four-face gate; every receipt remains partial."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MESHER_REF = '946b9353924dbca6f326feb8cb0327bcae5c4ccc'
CASE = 'reported-spline-four-face'
NODE = 'diagnostics/test_spline_mesh_ownership_acceptance.py::test_unassigned_reported_spline_mesh_has_exact_boundary_and_shared_nodes'
OUTPUT = ROOT / 'reports/qualification-spline4'
SOURCE = ROOT / 'reports/qualification-spline4-source.json'
RECEIPT = ROOT / 'reports/qualification-spline4-receipt.json'


def selection(scope, case, mesher_ref):
    if scope not in {'unit', 'integration', 'qualification', 'full'} or case not in {'all', CASE}:
        raise ValueError('unknown manual scope or qualification case')
    if case != 'all' and scope != 'qualification':
        raise ValueError('qualification_case requires scope qualification')
    if case == CASE:
        raise ValueError('OWNER-HELD reported four-face spline meshing; see docs/REPORTED_SPLINE_MESH_RELEASE_HOLD.md')
    if not re.fullmatch('[0-9a-fA-F]{40}', mesher_ref):
        raise ValueError('mesher_ref must be a frozen 40-hex commit SHA')
    return dict(scope='partial qualification only', acceptance='not established',
                qualification_case=case, mesher_ref=mesher_ref.lower(),
                nodeid=NODE if case == CASE else None)


def verify(cases, inventory, run, source, actual_ref):
    if (source.get('nodeid') != NODE or source.get('scope') != 'partial qualification only'
            or source.get('acceptance') != 'not established'
            or source.get('mesher_ref') != actual_ref
            or source.get('source_refs', {}).get('ANYmesh') != actual_ref):
        raise ValueError('focused source identity does not match frozen mesher ref')
    if (len(cases) != 1 or cases[0].get('classname') != 'tests.test_spline_mesh_ownership_acceptance'
            or cases[0].get('name') != NODE.split('::', 1)[1]
            or any(cases[0].find(kind) is not None for kind in ('skipped', 'failure', 'error'))):
        raise ValueError('exactly one original unskipped passing four-face case required')
    if inventory.get('assignments') != {NODE: 'qualification'}:
        raise ValueError('collected original case inventory differs')
    if (run.get('profile') != 'focused' or run.get('status') != 'passed'
            or run.get('exit_code') != 0 or run.get('acceptance') != 'not established'
            or run.get('scope') != 'partial development only'
            or run.get('counts') != {'tests': 1, 'failures': 0, 'errors': 0, 'skipped': 0}):
        raise ValueError('focused partial run receipt did not pass')
    return dict(source, status='passed', tests_passed=1,
                scope='partial qualification only', acceptance='not established')


def git_head(path):
    return subprocess.check_output(['git', '-C', str(path), 'rev-parse', 'HEAD'], text=True).strip()


def fixture_hash():
    return hashlib.sha256((ROOT / NODE.split('::', 1)[0]).read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('validate', 'prepare', 'verify'))
    args = parser.parse_args()
    plan = selection(os.environ.get('QUALIFICATION_SCOPE', 'unit'),
                     os.environ.get('QUALIFICATION_CASE', 'all'),
                     os.environ.get('MESHER_REF', DEFAULT_MESHER_REF))
    if args.mode == 'validate':
        return 0
    if plan['qualification_case'] != CASE:
        raise ValueError('focused receipt operations require the fixed four-face case')
    actual = git_head(ROOT / '.ecosystem/ANYmesh')
    if actual != plan['mesher_ref']:
        raise ValueError('ANYmesh checkout differs from requested frozen ref')
    SOURCE.parent.mkdir(parents=True, exist_ok=True)
    if args.mode == 'prepare':
        plan.update(source_refs={'ANYmesh': actual, 'ANYfem': git_head(ROOT)},
                    fixture_sha256=fixture_hash(), status='not executed')
        with SOURCE.open('x', encoding='utf-8') as stream:
            json.dump(plan, stream, indent=2)
        return 0
    source = json.loads(SOURCE.read_text(encoding='utf-8'))
    try:
        if (source.get('fixture_sha256') != fixture_hash()
                or source.get('source_refs', {}).get('ANYfem') != git_head(ROOT)
                or source.get('mesher_ref') != plan['mesher_ref']):
            raise ValueError('fixture or application source differs from prepared receipt')
        result = verify(list(ET.parse(OUTPUT / 'pytest.xml').iter('testcase')),
                        json.loads((OUTPUT / 'suite-collected.json').read_text(encoding='utf-8')),
                        json.loads((OUTPUT / 'run.json').read_text(encoding='utf-8')), source, actual)
    except (ValueError, OSError, ET.ParseError) as error:
        RECEIPT.write_text(json.dumps(dict(source, status='failed', error=str(error)), indent=2), encoding='utf-8')
        raise
    RECEIPT.write_text(json.dumps(result, indent=2), encoding='utf-8')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

