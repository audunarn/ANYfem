"""Auditable manager tiers and explicitly retained scientific diagnostics.

Planning reads filenames only. Unknown modules always belong to integration.
Qualification labels allocate development work, never waive release assertions.
"""
import hashlib
import json
from pathlib import Path

TIERS = ("unit", "integration", "qualification")
UNIT_MODULES = (
    "tests/test_license_policy.py", "tests/test_application_frontend_boundary.py",
    "tests/test_output_requests.py", "tests/test_command_recording.py",
    "tests/test_document_geometry_snapshot.py", "tests/test_selection_state.py",
    "tests/test_test_runner.py", "tests/test_suite_structure.py",
    "tests/test_mesh_reference_update.py",
    "tests/test_reported_spline_mesh_release_hold.py", "tests/test_focused_qualification_dispatch.py",
)
# Legacy identities remain readable; retained diagnostics are classified by their explicit directory.
QUALIFICATION_MODULES = frozenset({
    "tests/test_verification.py", "tests/test_preparation_evidence.py",
    "tests/test_nurbs_cut_mesh_integration.py", "tests/test_nurbs_cut_mesh_release_hold.py",
    "tests/test_floating_pipeline.py", "tests/test_qt_tree_interaction_performance.py",
})
SCHEMA = "anyfem-manager-tiers-v2"


def classify(node):
    path = node.split("::", 1)[0].replace("\\", "/")
    if path.startswith("diagnostics/"):
        return "qualification"
    if path in UNIT_MODULES:
        return "unit"
    if path in QUALIFICATION_MODULES:
        return "qualification"
    return "integration"


def manifest(inventory, profile):
    """Partition every supplied original identity once; preserve original order."""
    if profile == "fast":
        profile = "unit"
    if profile not in (*TIERS, "full"):
        raise ValueError("unknown suite profile: " + profile)
    nodes = list(inventory)
    for node in nodes:
        if not isinstance(node, str):
            raise ValueError("original test identity must be a string")
        path, separator, name = node.partition("::")
        if (not path.startswith(("tests/", "diagnostics/")) or not path.endswith(".py") or "\\" in path
                or any(part in {"", ".", ".."} for part in path.split("/"))
                or (separator and not name)):
            raise ValueError("invalid repository test identity: " + node)
    if len(nodes) != len(set(nodes)):
        raise ValueError("duplicate original test identity")
    assignments = {node: classify(node) for node in nodes}
    selected = [node for node in nodes if (profile == "full" and not node.startswith("diagnostics/")) or (profile != "full" and assignments[node] == profile)]
    selected_set = set(selected)
    data = {"schema": SCHEMA, "profile": profile, "acceptance": "not established",
            "scope": "required manager application suite" if profile == "full" else "partial development only",
            "assignments": assignments,
            "counts": {tier: sum(value == tier for value in assignments.values()) for tier in TIERS},
            "selected": selected, "excluded": [node for node in nodes if node not in selected_set],
            "excluded_reasons": {node: ("optional scientific diagnostic; explicit qualification authority required" if node.startswith("diagnostics/") else "outside selected profile") for node in nodes if node not in selected_set},
            "rules": {"unit_modules": list(UNIT_MODULES),
                      "qualification_modules": sorted(QUALIFICATION_MODULES),
                      "qualification_filename_suffix": "_acceptance.py",
                      "unknown": "integration"}}
    def digest(value):
        return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    data.update(inventory_sha256=digest(nodes), rules_sha256=digest(data["rules"]),
                assignments_sha256=digest(assignments))
    return data


def module_plan(root, profile):
    plan = manifest((path.relative_to(root).as_posix()
                     for folder in ("tests", "diagnostics") for path in sorted((Path(root) / folder).rglob("test_*.py"))), profile)
    plan["inventory_kind"] = "static module paths; not pytest collection"
    return plan


