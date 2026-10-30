"""Write the exact tests required for Mac acceptance (no pytest execution)."""
import argparse
import json
from pathlib import Path
import runpy
import sys

ROOT = Path(__file__).resolve().parents[1]
CALLERS = [
    "tests/test_qt_workbench.py::test_quick_selection_buttons_follow_all_selection_routes",
    "tests/test_qt_mesh_parallel_integration.py::test_real_qt_parallel_job_attaches_mesh_and_records_route",
    "tests/test_qt_mesh_parallel_integration.py::test_real_qt_parallel_cancel_does_not_attach_partial_mesh",
    "tests/test_qt_mesh_parallel_integration.py::test_real_qt_worker_crash_fails_without_retry",
    "tests/test_qt_geometry_cleaner.py::test_cancel_and_close_retire_actual_worker_without_mutation",
]


def required_ids():
    # The module defines spawned jobs and skip markers but runs no test bodies.
    sys.path.insert(0, str(ROOT / "tests"))
    try:
        identifiers = runpy.run_path(str(ROOT / "tests/test_macos_containment.py"))["REQUIRED_TEST_IDS"]
    finally:
        sys.path.pop(0)
    return list(dict.fromkeys([*identifiers, *CALLERS]))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, type=Path)
    options = parser.parse_args(argv)
    options.out.parent.mkdir(parents=True, exist_ok=True)
    options.out.write_text(json.dumps(required_ids(), indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
