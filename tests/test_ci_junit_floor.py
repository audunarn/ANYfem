"""The CI floor checker must turn a skipped, missing or failing test into a red job."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location("ci_junit_floor", Path(__file__).resolve().parents[1] / "tools" / "ci_junit_floor.py")
floor = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(floor)


def report(tmp_path, tests, skipped=0, failures=0, errors=0, wrapped=True):
    suite = '<testsuite tests="%d" skipped="%d" failures="%d" errors="%d"/>' % (tests, skipped, failures, errors)
    path = tmp_path / "r.xml"
    path.write_text("<testsuites>%s</testsuites>" % suite if wrapped else suite, encoding="utf-8")
    return str(path)


@pytest.mark.parametrize("wrapped", [True, False])
def test_exact_floor_passes(tmp_path, wrapped):
    assert floor.main([report(tmp_path, 7, wrapped=wrapped), "--min-tests", "7"]) == 0


@pytest.mark.parametrize("kwargs, extra", [
    (dict(tests=6), ["--min-tests", "7"]),                 # fewer tests than the floor
    (dict(tests=7, skipped=1), ["--min-tests", "7"]),      # a skipped canary: executed 6 < 7
    (dict(tests=9, skipped=2), ["--min-tests", "7"]),      # skips above the allowance
    (dict(tests=7, failures=1), ["--min-tests", "7"]),
    (dict(tests=7, errors=1), ["--min-tests", "7"]),
])
def test_violations_are_red(tmp_path, kwargs, extra):
    assert floor.main([report(tmp_path, **kwargs)] + extra) == 1
