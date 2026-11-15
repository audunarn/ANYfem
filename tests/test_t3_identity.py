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

import os
from pathlib import Path

import pytest

from anyfem import runtime_lock as rl
from anyfem import thread_route as tr
from t3_identity_helpers import EXPECTED_EDGE_PATHS, load_closure, run as _run, system_only_path

ROOT = Path(os.environ.get("ANYGEOMETRY_THREAD_RUNTIME", ""))
BUILT = bool(os.environ.get("ANYGEOMETRY_THREAD_RUNTIME")) and (ROOT / "python.exe").is_file() and (ROOT / rl.LOCK_NAME).is_file()
pytestmark = [pytest.mark.skipif(not BUILT, reason="no built runtime bundle in ANYGEOMETRY_THREAD_RUNTIME"),
              pytest.mark.skipif(os.name != "nt", reason="the thread route is Windows only")]

THREAD_COUNTS = (1, 4)


@pytest.fixture(scope="module")
def rows():
    mp = pytest.MonkeyPatch()
    system_only_path(mp)
    mp.setenv(tr.ENV_RUNTIME, str(ROOT))
    tr._health_cache.clear()
    closure = load_closure()
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
