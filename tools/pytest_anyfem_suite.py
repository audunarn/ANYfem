"""Collected original-node receipt, without changing pytest selection or outcomes."""
import json
import os
from pathlib import Path

from tools.test_suite_plan import manifest


def pytest_collection_finish(session):
    output = os.environ.get("ANYFEM_SUITE_MANIFEST")
    if not output:
        return
    expected_root = os.environ.get("ANYFEM_SUITE_ROOT")
    if not expected_root or Path(session.config.rootpath).resolve() != Path(expected_root).resolve():
        return  # Nested scalar pytest must not consume its parent's receipt setting.
    target = Path(output)
    def preserve_original():
        previous = json.loads(target.read_text(encoding="utf-8"))
        if (previous.get("suite_root") != str(Path(expected_root).resolve())
                or previous.get("schema") != "anyfem-test-tiers-v1"
                or not isinstance(previous.get("collection_owner_pid"), int)):
            raise RuntimeError("existing suite receipt is not an owned original collection")
    if target.exists():
        preserve_original()
        return  # Original collection already recorded before any test body ran.
    profile = os.environ["ANYFEM_SUITE_PROFILE"]
    data = manifest([item.nodeid for item in session.items], "full")
    data.update(requested_profile=profile, inventory_kind="actual pytest collected nodes",
                suite_root=str(Path(expected_root).resolve()), collection_owner_pid=os.getpid(),
                collection_errors=session.testsfailed,
                collection_scope="complete original full collection" if profile == "full" else "selected development modules only")
    # The receipt describes actual collected nodes; no classification deselection.
    # Partial profiles have already selected module paths before imports occur.
    data["acceptance"] = "not established"
    data["profile"] = profile
    data["scope"] = "complete inventory" if profile == "full" else "partial development only"
    # The runner refuses existing output directories. Preserve that original
    # collection if a nested same-root pytest inherits this private environment.
    try:
        with target.open("x", encoding="utf-8") as stream:
            stream.write(json.dumps(data, indent=2) + "\n")
    except FileExistsError:
        preserve_original()
