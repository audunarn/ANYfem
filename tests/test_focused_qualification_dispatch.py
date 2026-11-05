"""Pure manual CI selector/source receipts; never imports the scientific fixture."""
import importlib.util
from pathlib import Path
import unittest
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
PIN = '8cddc735022c23d540c7c1a7299ba0c9455558b1'
NODE = 'tests/test_spline_mesh_ownership_acceptance.py::test_unassigned_reported_spline_mesh_has_exact_boundary_and_shared_nodes'


def helper():
    spec = importlib.util.spec_from_file_location('focused_qualification_unit', ROOT / 'tools/ci_focused_qualification.py')
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


class FocusedQualificationTests(unittest.TestCase):
    def test_default_and_frozen_selection(self):
        h = helper()
        self.assertEqual(h.selection('full', 'all', PIN)['mesher_ref'], PIN)
        plan = h.selection('qualification', 'reported-spline-four-face', 'a' * 40)
        self.assertEqual(plan['nodeid'], NODE)
        self.assertEqual(plan['scope'], 'partial qualification only')
        self.assertEqual(plan['acceptance'], 'not established')

    def test_preconditions_and_refs_fail_closed(self):
        h = helper()
        for scope in ('unit', 'integration', 'full'):
            with self.subTest(scope=scope), self.assertRaises(ValueError):
                h.selection(scope, 'reported-spline-four-face', PIN)
        for scope, case, ref in [('typo', 'all', PIN), ('qualification', 'typo', PIN), ('qualification', 'all', 'main'), ('qualification', 'all', 'x; echo injected')]:
            with self.subTest(value=(scope, case, ref)), self.assertRaises(ValueError):
                h.selection(scope, case, ref)

    def artifacts(self):
        case = ET.Element('testcase', classname='tests.test_spline_mesh_ownership_acceptance', name=NODE.split('::')[1])
        return case, {'assignments': {NODE: 'qualification'}}, {'profile': 'focused', 'scope': 'partial development only', 'status': 'passed', 'exit_code': 0, 'counts': {'tests': 1, 'failures': 0, 'errors': 0, 'skipped': 0}, 'acceptance': 'not established'}, {'mesher_ref': PIN, 'source_refs': {'ANYmesh': PIN}, 'nodeid': NODE, 'scope': 'partial qualification only', 'acceptance': 'not established'}

    def test_exact_original_pass_receipt(self):
        h = helper()
        case, inventory, run, source = self.artifacts()
        result = h.verify([case], inventory, run, source, PIN)
        self.assertEqual(result['tests_passed'], 1)
        self.assertEqual(result['acceptance'], 'not established')
        self.assertEqual(result['scope'], 'partial qualification only')

    def test_failure_skip_wrong_identity_and_duplicates_refused(self):
        h = helper()
        for kind in ('failure', 'error', 'skipped', 'wrong', 'duplicate'):
            case, inventory, run, source = self.artifacts()
            cases = [case]
            if kind == 'wrong': case.set('name', 'invented')
            elif kind == 'duplicate': cases.append(case)
            else: ET.SubElement(case, kind)
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                h.verify(cases, inventory, run, source, PIN)

    def test_source_and_partial_receipt_identity_required(self):
        h = helper()
        for mutation in ('actual_ref', 'source_ref', 'inventory', 'run', 'counts'):
            case, inventory, run, source = self.artifacts()
            actual = PIN
            if mutation == 'actual_ref': actual = 'a' * 40
            elif mutation == 'source_ref': source['source_refs']['ANYmesh'] = 'b' * 40
            elif mutation == 'inventory': inventory['assignments'] = {}
            elif mutation == 'counts': run['counts']['tests'] = 2
            else: run['profile'] = 'full'
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                h.verify([case], inventory, run, source, actual)

    def test_workflow_exact_selector_and_three_equal_source_refs(self):
        text = (ROOT / '.github/workflows/tests.yml').read_text()
        self.assertIn('qualification_case:', text)
        self.assertIn('options: [all, reported-spline-four-face]', text)
        self.assertIn('mesher_ref:', text)
        expected = "ref: ${{ inputs.mesher_ref || '" + PIN + "' }}"
        self.assertEqual(text.count(expected), 3)
        self.assertIn('--profile focused --ci-diagnostics --fail-fast --out reports/qualification-spline4 ' + NODE, text)
        self.assertIn('ci_focused_qualification.py verify', text)
        self.assertIn('ci_focused_qualification.py validate', text)
        self.assertIn('default: full', text)


if __name__ == '__main__':
    unittest.main()

