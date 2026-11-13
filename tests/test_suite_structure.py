"""Pure suite-planning contracts: no test collection or owner imports."""
import importlib.util
import json
from pathlib import Path
import unittest
import os
from test_test_runner import _completed_receipt

ROOT = Path(__file__).resolve().parents[1]


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class SuitePlanTests(unittest.TestCase):
    def suite(self):
        return load("suite_plan_under_test", "tools/test_suite_plan.py")

    def test_unknown_defaults_to_integration(self):
        self.assertEqual(self.suite().classify("tests/test_new_unknown.py::test_case[x]"), "integration")

    def test_disjoint_exhaustive_node_manifest(self):
        plan = self.suite().manifest([
            "tests/test_license_policy.py::test_one",
            "tests/test_verification.py::test_science",
            "tests/test_new.py::test_other[x]",
        ], "integration")
        self.assertEqual(plan["counts"], {"unit": 1, "integration": 1, "qualification": 1})
        self.assertEqual(plan["selected"], ["tests/test_new.py::test_other[x]"])
        self.assertEqual(plan["acceptance"], "not established")
        self.assertEqual(len(plan["assignments"]), 3)

    def test_full_selects_original_order_without_loss(self):
        nodes = ["tests/test_verification.py::test_b", "tests/test_new.py::test_a"]
        plan = self.suite().manifest(nodes, "full")
        self.assertEqual(plan["selected"], nodes)
        self.assertEqual(plan["excluded"], [])

    def test_duplicates_and_unknown_profiles_fail_closed(self):
        module = self.suite()
        with self.assertRaises(ValueError):
            module.manifest(["tests/test_new.py::test_a"] * 2, "full")
        with self.assertRaises(ValueError):
            module.manifest([], "typo")

    def test_module_planning_no_test_imports(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "tests").mkdir()
            (root / "tests/test_new.py").write_text("raise RuntimeError('do not import')")
            plan = self.suite().module_plan(root, "integration")
            self.assertEqual(plan["selected"], ["tests/test_new.py"])
            self.assertEqual(plan["counts"]["integration"], 1)

    def test_qualification_contains_science_and_original_callers(self):
        module = self.suite()
        for path in ("tests/test_verification.py", "tests/test_preparation_evidence.py"):
            self.assertEqual(module.classify(path + "::test_any"), "qualification")

    def test_runner_profiles_and_default_reuse_existing_quick(self):
        runner = load("suite_runner_under_test", "tools/test_anyfem.py")
        self.assertEqual(set(runner.selectors("unit", [])), set(self.suite().UNIT_MODULES))
        self.assertEqual(runner.selectors("full", []), ["tests"])
        self.assertIn("diagnostics/test_verification.py", runner.selectors("qualification", []))
        self.assertNotIn("tests/test_verification.py", runner.selectors("integration", []))

    def test_fast_alias_and_all_module_partition(self):
        module = self.suite()
        unit = module.module_plan(ROOT, "unit")
        self.assertEqual(module.module_plan(ROOT, "fast")["selected"], unit["selected"])
        plans = [module.module_plan(ROOT, tier) for tier in module.TIERS]
        selected = [path for plan in plans for path in plan["selected"]]
        self.assertEqual(len(selected), len(set(selected)))
        self.assertEqual(set(selected), set(module.module_plan(ROOT, "full")["assignments"]))
        self.assertEqual(module.classify("diagnostics/test_spline_mesh_ownership_acceptance.py::test_four_face"), "qualification")

    def test_partial_receipt_and_ignored_inherited_selection(self):
        from unittest.mock import patch
        import os
        import tempfile
        runner = load("suite_runner_receipt", "tools/test_anyfem.py")
        seen = []
        def execute(command, log, env):
            seen.append((command, env))
            log.write_text("synthetic passed")
            log.with_name("pytest.xml").write_text('<testsuites><testsuite tests="1" failures="0" errors="0" skipped="0"/></testsuites>')
            _completed_receipt(log, env)
            return 0
        with tempfile.TemporaryDirectory() as tmp, patch.object(runner, "execute", execute), patch.object(runner, "owner_capabilities", side_effect=AssertionError("unexpected owner import")), patch.dict(os.environ, {"PYTEST_ADDOPTS": "--collect-only -k hidden"}):
            out = Path(tmp) / "receipt"
            self.assertEqual(runner.main(["--profile", "unit", "--out", str(out)]), 0)
            receipt = json.loads((out / "run.json").read_text())
            self.assertEqual(receipt["scope"], "partial development only")
            self.assertEqual(receipt["acceptance"], "not established")
            self.assertEqual(receipt["ignored_pytest_addopts"], "--collect-only -k hidden")
            self.assertNotIn("PYTEST_ADDOPTS", seen[0][1])
            self.assertNotIn("tests", seen[0][0][3:])
            self.assertEqual(set(json.loads((out / "suite-plan.json").read_text())["selected"]), set(runner.selectors("unit", [])))

    def test_node_receipt_never_filters_or_claims_partial_full(self):
        from unittest.mock import patch
        from types import SimpleNamespace
        import os
        import tempfile
        plugin = load("suite_receipt_plugin", "tools/pytest_anyfem_suite.py")
        nodes = [SimpleNamespace(nodeid="tests/test_new.py::test_one"), SimpleNamespace(nodeid="tests/test_verification.py::test_two")]
        session = SimpleNamespace(items=nodes[:], testsfailed=0, config=SimpleNamespace(rootpath=ROOT))
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "nodes.json"
            with patch.dict(os.environ, {"ANYFEM_SUITE_PROFILE": "integration", "ANYFEM_SUITE_MANIFEST": str(out), "ANYFEM_SUITE_ROOT": str(ROOT)}):
                plugin.pytest_collection_finish(session)
            receipt = json.loads(out.read_text())
            self.assertEqual(session.items, nodes)
            self.assertEqual(receipt["scope"], "partial development only")
            self.assertEqual(receipt["profile"], "integration")
            self.assertEqual(set(receipt["assignments"]), {node.nodeid for node in nodes})
            self.assertEqual(receipt["acceptance"], "not established")

    def test_required_positive_profile_refuses_partial_execution(self):
        from unittest.mock import patch
        import os
        import tempfile
        runner = load("suite_runner_refusal", "tools/test_anyfem.py")
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"ANYGEOMETRY_REQUIRE_QUALIFIED_PLANNING_TESTS": "1"}), patch.object(runner, "execute", side_effect=AssertionError("must not share caller H")):
            out = Path(tmp) / "refused"
            self.assertEqual(runner.main(["--profile", "qualification", "--out", str(out)]), 2)
            receipt = json.loads((out / "run.json").read_text())
            self.assertEqual(receipt["status"], "blocked")
            self.assertIn("fresh-H", receipt["error"])
            self.assertEqual(receipt["acceptance"], "not established")

    def test_inventory_refuses_malformed_and_binds_hashes(self):
        module = self.suite()
        for value in (None, "../test_x.py", "tests/../test_x.py", "tests//test_x.py", "tests/test_x.py::", "C:/tests/test_x.py", "tests/test_x.txt"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                module.manifest([value], "full")
        first = module.manifest(["tests/test_x.py::test_one"], "full")
        second = module.manifest(["tests/test_x.py::test_two"], "full")
        self.assertNotEqual(first["inventory_sha256"], second["inventory_sha256"])
        self.assertNotEqual(first["assignments_sha256"], second["assignments_sha256"])
        self.assertEqual(first["rules_sha256"], second["rules_sha256"])
        self.assertEqual(first["inventory_sha256"], module.manifest(["tests/test_x.py::test_one"], "full")["inventory_sha256"])

    def test_full_required_profile_uses_only_authoritative_driver(self):
        from unittest.mock import patch
        import os
        import tempfile
        runner = load("suite_runner_full_dispatch", "tools/test_anyfem.py")
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"ANYGEOMETRY_REQUIRE_QUALIFIED_PLANNING_TESTS": "1"}), patch.object(runner, "owner_capabilities"), patch.object(runner, "_execute_caller_phases", return_value=7) as driver, patch.object(runner, "execute", side_effect=AssertionError("must use full fresh H driver")):
            out = Path(tmp) / "caller"
            self.assertEqual(runner.main(["--profile", "caller", "--out", str(out)]), 7)
            driver.assert_called_once()
            self.assertEqual(driver.call_args.args[0][3], runner.selectors("caller", [])[0])
            self.assertEqual(driver.call_args.args[2]["ANYFEM_SUITE_PROFILE"], "caller")
            self.assertEqual(json.loads((out / "run.json").read_text())["acceptance"], "not established")

    def test_full_collection_receipt_is_not_overwritten_by_core_subset(self):
        import tempfile
        import xml.etree.ElementTree as ET
        from unittest.mock import patch
        runner = load("suite_runner_full_manifest", "tools/test_anyfem.py")
        inventory = {"tests/" + key.split("::", 1)[0] + ".py::" + key.split("::", 1)[1]: ["tests.test_preparation_evidence", key.split("::", 1)[1]] for key in runner._caller_profile().REQUIRED_CASES}
        inventory["tests/test_core.py::test_one"] = ["tests.test_core", "test_one"]
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            receipt = out / "suite-collected.json"
            seen = []
            def execute(command, log, environment):
                phase = Path(command[-1])
                data = json.loads(phase.read_text())
                seen.append(data["kind"])
                if data["kind"] == "collect":
                    data["inventory"] = inventory
                    phase.write_text(json.dumps(data))
                    ET.ElementTree(ET.Element("testsuites")).write(log.parent / "pytest.xml")
                    Path(environment["ANYFEM_SUITE_MANIFEST"]).write_text("complete-original-universe")
                    _completed_receipt(log, environment)
                    return 0
                self.assertNotIn("ANYFEM_SUITE_MANIFEST", environment)
                return 7
            with patch.object(runner, "execute", execute):
                self.assertEqual(runner._execute_caller_phases(["python", "-m", "pytest", "tests", "-q"], out, {"ANYFEM_SUITE_MANIFEST": str(receipt)}, {}), 7)
            self.assertEqual(seen, ["collect", "core"])
            self.assertEqual(receipt.read_text(), "complete-original-universe")

    def test_nested_pytest_root_cannot_write_or_overwrite_parent_receipt(self):
        from unittest.mock import patch
        from types import SimpleNamespace
        import os
        import tempfile
        plugin = load("suite_nested_root_plugin", "tools/pytest_anyfem_suite.py")
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "nodes.json"
            session = SimpleNamespace(items=[SimpleNamespace(nodeid="test_scalar_child.py::test_one")], testsfailed=0, config=SimpleNamespace(rootpath=Path(tmp)))
            with patch.dict(os.environ, {"ANYFEM_SUITE_PROFILE": "unit", "ANYFEM_SUITE_MANIFEST": str(out), "ANYFEM_SUITE_ROOT": str(ROOT)}):
                plugin.pytest_collection_finish(session)
                self.assertFalse(out.exists())
                session.config.rootpath = ROOT
                session.items = [SimpleNamespace(nodeid="tests/test_suite_structure.py::test_original")]
                plugin.pytest_collection_finish(session)
                original = out.read_bytes()
                session.items = [SimpleNamespace(nodeid="tests/test_suite_structure.py::test_nested")]
                plugin.pytest_collection_finish(session)
                self.assertEqual(out.read_bytes(), original)
                out.write_text('{"schema":"unowned-stale"}')
                with self.assertRaisesRegex(RuntimeError, "not an owned original"):
                    plugin.pytest_collection_finish(session)

    def test_workflow_default_quick_and_release_compatibility(self):
        text=(ROOT/'.github/workflows/tests.yml').read_text()
        self.assertIn('profile: quick',text)
        self.assertIn('profile: full',text)
        self.assertIn('profile: compatibility',text)
        self.assertIn('profile: caller',text)
        self.assertIn('os: [ubuntu-latest, windows-latest, macos-latest]',text)
        self.assertIn("python: ['3.11', '3.12', '3.13', '3.14']",text)
        self.assertIn("python: '3.14.2'",text)


if __name__ == "__main__":
    unittest.main()






