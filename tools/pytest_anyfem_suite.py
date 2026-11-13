"""Collected original-node receipt, without changing pytest selection or outcomes."""
import json
import os
from pathlib import Path

from tools.test_suite_plan import manifest

_collection_skips = []
_subtests = []


def pytest_runtest_logreport(report):
    context=getattr(report,'context',None)
    if context is not None and report.when=='call':
        description=report._sub_test_description()
        _subtests.append(dict(nodeid=report.nodeid, description=description,
                              ordinal=len(_subtests), outcome=report.outcome))


def pytest_sessionfinish(session):
    target=os.environ.get('ANYFEM_SUITE_MANIFEST')
    root=os.environ.get('ANYFEM_SUITE_ROOT')
    if target and root and Path(session.config.rootpath).resolve()==Path(root).resolve():
        Path(target).with_name('suite-subtests.json').write_text(json.dumps(_subtests,indent=2)+'\n',encoding='utf8')


def pytest_collectreport(report):
    if report.skipped:
        from _pytest.junitxml import mangle_test_address
        names = mangle_test_address(report.nodeid)
        _collection_skips.append(['.'.join(names[:-1]), names[-1], str(report.longrepr)])


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
                or previous.get("schema") != "anyfem-manager-tiers-v2"
                or not isinstance(previous.get("collection_owner_pid"), int)):
            raise RuntimeError("existing suite receipt is not an owned original collection")
    if target.exists():
        preserve_original()
        return  # Original collection already recorded before any test body ran.
    profile = os.environ["ANYFEM_SUITE_PROFILE"]
    nodes = [item.nodeid for item in session.items]
    data = manifest(nodes, "full")
    data.update(selected=nodes, excluded=[], excluded_reasons={}, collection_skips=list(_collection_skips))
    for item in session.items:
        if hasattr(item, 'user_properties') and not any(key == 'anyfem_original_nodeid' for key, _ in item.user_properties):
            item.user_properties.append(('anyfem_original_nodeid', item.nodeid))
    data.update(requested_profile=profile, inventory_kind="actual pytest collected nodes",
                suite_root=str(Path(expected_root).resolve()), collection_owner_pid=os.getpid(),
                collection_errors=session.testsfailed,
                collection_scope="required manager application collection" if profile == "full" else "selected development modules only")
    # The receipt describes actual collected nodes; no classification deselection.
    # Partial profiles have already selected module paths before imports occur.
    data["acceptance"] = "not established"
    data["profile"] = profile
    data["scope"] = "required manager application suite" if profile == "full" else "partial development only"
    # The runner refuses existing output directories. Preserve that original
    # collection if a nested same-root pytest inherits this private environment.
    try:
        with target.open("x", encoding="utf-8") as stream:
            stream.write(json.dumps(data, indent=2) + "\n")
    except FileExistsError:
        preserve_original()
