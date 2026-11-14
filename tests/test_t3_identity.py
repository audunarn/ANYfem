"""CI job T3: end-to-end ON/OFF identity with a REAL child process (a built runtime bundle).

sector60 (a 91-face closure of the tripod elbow, ``tests/data/sector60_closure.json.gz``, produced by ``ModelClosure.to_transport``) is
prepared once with the route OFF and then through the bundled free-threaded child with the route ON, in this ONE process, each time on a
fresh identity-preserving clone of the same loaded closure.  PATH holds system directories only, so no Python can be found by name.
Required: the child really planned and its plan was adopted (otherwise ON == OFF would hold vacuously through the serial fallback),
and model / report / bindings digests, the proof flag and the source-edge-path count equal the route-OFF values.  Absolute digests are not
stable across processes (S2 finding), so nothing is compared with a constant digest; only ON against OFF of the same run.

Needs ``ANYGEOMETRY_THREAD_RUNTIME`` to name a built bundle; skips otherwise (the T2 job).  In CI the bundle is a smoke bundle
(unpinned interpreter): this proves identity of the ROUTE, not of the shipped interpreter payload.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import os
import shutil
from dataclasses import replace
from pathlib import Path

import pytest

from anyfem import runtime_lock as rl
from anyfem import thread_route as tr

ROOT = Path(os.environ.get("ANYGEOMETRY_THREAD_RUNTIME", ""))
BUILT = bool(os.environ.get("ANYGEOMETRY_THREAD_RUNTIME")) and (ROOT / "python.exe").is_file() and (ROOT / rl.LOCK_NAME).is_file()
pytestmark = [pytest.mark.skipif(not BUILT, reason="no built runtime bundle in ANYGEOMETRY_THREAD_RUNTIME"),
              pytest.mark.skipif(os.name != "nt", reason="the thread route is Windows only")]

DATA = Path(__file__).parent / "data" / "sector60_closure.json.gz"
EXPECTED_EDGE_PATHS = 223
THREAD_COUNTS = (1, 4)


def _digest(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _run(closure, label, **cfg) -> dict:
    import anygeometry
    from anygeometry import to_dict
    from anyfem.structural_preparation import prepare_structural_connectivity, source_edge_paths
    tr.configure(reset=True, **cfg)
    tr.last_outcome = None
    tr.last_adoption = {}
    work = closure.working_model.clone(preserve_identity=True)
    fresh = replace(closure, working_model=work)
    # Spy: what the plan source handed to the apply phase.  An adopted flag alone would not show that the child's plan was USED.
    handed = []
    original = tr._batch_prepare.plan_apply_phase

    def spy(geometry, operands, *, plan_source=None, **kwargs):
        if plan_source is None:
            return original(geometry, operands, **kwargs)

        def wrapped(policy):
            plan = plan_source(policy)
            handed.append(plan)
            return plan
        return original(geometry, operands, plan_source=wrapped, **kwargs)

    tr._batch_prepare.plan_apply_phase = spy
    try:
        report = prepare_structural_connectivity(work, source_model_id=str(fresh.source_model_id), source_revision=fresh.source_revision,
                                                 member_ids=tuple(work.members))
    finally:
        tr._batch_prepare.plan_apply_phase = original
    outcome = tr.last_outcome
    return dict(label=label, route=(outcome.status if outcome else "off"), reason=(outcome.reason if outcome else ""),
                adopted=bool(tr.last_adoption.get("adopted")), plan_used=bool(handed) and all(plan is not None for plan in handed), model=_digest(to_dict(work)), report=_digest(report.to_dict()),
                faces=len(work.faces), proof=bool(anygeometry.has_current_intersection_preparation(work)),
                edge_paths=len(source_edge_paths(fresh)), bindings=tr.binding_fingerprint(work))


@pytest.fixture(scope="module")
def rows():
    import anygeometry
    mp = pytest.MonkeyPatch()
    system = os.environ.get("SystemRoot", r"C:\Windows")
    mp.setenv("PATH", os.pathsep.join([os.path.join(system, "System32"), system]))
    for name in ("python", "python3", "py", "python.exe"):
        assert shutil.which(name) is None, "PATH must hold no Python for T3 (%s found)" % name
    for name in (tr.ENV_ENABLE, tr.ENV_THREADS, tr.ENV_MIN_FACES, tr.ENV_APP_DIR):
        mp.delenv(name, raising=False)
    mp.setenv(tr.ENV_RUNTIME, str(ROOT))
    tr._health_cache.clear()
    with gzip.open(DATA, "rb") as handle:
        closure = anygeometry.model_closure_from_dict(json.loads(handle.read().decode("utf-8")))
    try:
        # A smoke bundle is refused unless conftest set the in-process opt-in (ANYFEM_TEST_ALLOW_CI_SMOKE_BUNDLE=1): then every ON row fails.
        out = {"off": _run(closure, "off", enabled=False), "on": _run(closure, "on", enabled=True)}
        for count in THREAD_COUNTS:
            out["on%d" % count] = _run(closure, "on, %d thread(s)" % count, enabled=True, threads=count)
    finally:
        tr.configure(reset=True)
        mp.undo()
    return out


def test_the_off_run_is_the_serial_reference(rows):
    off = rows["off"]
    assert off["route"] == "off" and not off["adopted"]
    assert off["proof"] is True
    assert off["edge_paths"] == EXPECTED_EDGE_PATHS


@pytest.mark.parametrize("key", ["on", *("on%d" % n for n in THREAD_COUNTS)])
def test_a_real_child_planned_and_its_plan_was_adopted(rows, key):
    row = rows[key]
    assert row["route"] == tr.OK, (row["label"], row["route"], row["reason"])
    assert row["adopted"] is True, (row["label"], "the child result was not adopted: %s" % row["reason"])
    assert row["plan_used"] is True, (row["label"], "the apply phase did not receive the child's plan")
    assert row["proof"] is True


@pytest.mark.parametrize("key", ["on", *("on%d" % n for n in THREAD_COUNTS)])
def test_route_on_equals_route_off_in_the_same_run(rows, key):
    off, on = rows["off"], rows[key]
    for field in ("model", "report", "bindings", "faces", "edge_paths", "proof"):
        assert on[field] == off[field], (on["label"], field, on[field], off[field])
    assert on["edge_paths"] == EXPECTED_EDGE_PATHS
