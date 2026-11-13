"""Pure source contracts for the one explicit owner-held four-face capability."""
import ast
import hashlib
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / 'diagnostics/test_spline_mesh_ownership_acceptance.py'
TARGET = 'test_unassigned_reported_spline_mesh_has_exact_boundary_and_shared_nodes'
REASON = 'OWNER-HELD reported four-face spline meshing; see docs/REPORTED_SPLINE_MESH_RELEASE_HOLD.md'
ORIGINAL_SHA256 = '951e6c8f8e3b536a13dd91bf0c174fa7dab5d4d40ceaf12b3245dfc7cd6a5481'
DECORATOR = '@pytest.mark.skip(reason=' + repr(REASON) + ')'


class FourFaceHoldTests(unittest.TestCase):
    def test_exact_single_literal_owner_hold(self):
        tree = ast.parse(PATH.read_bytes())
        held = []
        for node in tree.body:
            if isinstance(node, ast.FunctionDef):
                for mark in node.decorator_list:
                    if isinstance(mark, ast.Call) and ast.unparse(mark.func) == 'pytest.mark.skip':
                        held.append((node.name, mark))
        self.assertEqual([name for name, _ in held], [TARGET])
        mark = held[0][1]
        self.assertEqual(mark.args, [])
        self.assertEqual([(kw.arg, ast.literal_eval(kw.value)) for kw in mark.keywords], [('reason', REASON)])
        self.assertFalse(any(isinstance(node, ast.Name) and node.id == 'pytestmark' and isinstance(node.ctx, ast.Store) for node in ast.walk(tree)))
        self.assertFalse(any(isinstance(node, ast.Attribute) and node.attr in ('skipif', 'xfail') for node in ast.walk(tree)))

    def test_entire_original_fixture_and_positive_source_unchanged(self):
        raw = PATH.read_bytes()
        normalized = raw.replace(b'\r\n', b'\n')
        self.assertEqual(normalized.count(b'import pytest\n'), 1)
        self.assertEqual(normalized.count(DECORATOR.encode() + b'\n'), 1)
        original = normalized.replace(b'import pytest\n', b'', 1).replace(DECORATOR.encode() + b'\n', b'', 1)
        # Original checkout uses CRLF. Restore it before binding byte identity.
        self.assertEqual(hashlib.sha256(original.replace(b'\n', b'\r\n')).hexdigest(), ORIGINAL_SHA256)

    def test_owner_note_preserves_failure_limits_and_future_authority(self):
        note = (ROOT / 'docs/REPORTED_SPLINE_MESH_RELEASE_HOLD.md').read_text(encoding='utf-8')
        for value in ('2026-11-06', '13549.020236332', '42103849028', '1 failed', '4096', '32', 'unsupported', 'Fresh owner-approved qualification', 'Consumed execution authority is not reusable', 'no environment'):
            self.assertIn(value, note)
        self.assertIn('source-controlled amendment', note)


if __name__ == '__main__':
    unittest.main()
