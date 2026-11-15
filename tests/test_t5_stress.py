"""CI job T5 (nightly): thread-safety stress repeat of the T3 identity check.

The T3 check (sector60 through a REAL bundled free-threaded child, route ON against route OFF) is repeated ``ANYFEM_T5_REPEATS`` times
(default 20), the thread count cycling through ``ANYFEM_T5_THREADS`` (default ``1,2,4,8``; each 1..8).  Route OFF is computed ONCE, in the
same process; every repeat must equal it (model, report and bindings digests, face count, edge paths, proof flag) AND must have really
adopted the child's plan.  Races are probabilistic, so nothing here retries, tolerates or skips a mismatch: each repeat is its own test
(id ``r<index>-t<threads>``), a failure names the repeat, the thread count and each digest that differed, and
``test_every_repeat_was_recorded`` fails if any repeat did not run to a comparison.

If ``ANYFEM_T5_DIGESTS`` names a file, one JSON line per run is appended to it (the OFF reference first, then every repeat, also the
failing ones): the nightly job uploads it as an artifact.  Absolute digests are not stable across processes (S2 finding), so only
ON-against-OFF of this one run is compared.  Needs ``ANYGEOMETRY_THREAD_RUNTIME`` naming a built bundle (otherwise everything skips, and the
workflow's junit floor, which allows no skips, fails the job).  A smoke bundle proves the ROUTE, not the shipped interpreter payload.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from anyfem import runtime_lock as rl
from anyfem import thread_route as tr
from t3_identity_helpers import EXPECTED_EDGE_PATHS, load_closure, run, system_only_path

ROOT = Path(os.environ.get("ANYGEOMETRY_THREAD_RUNTIME", ""))
BUILT = bool(os.environ.get("ANYGEOMETRY_THREAD_RUNTIME")) and (ROOT / "python.exe").is_file() and (ROOT / rl.LOCK_NAME).is_file()
pytestmark = [pytest.mark.skipif(not BUILT, reason="no built runtime bundle in ANYGEOMETRY_THREAD_RUNTIME"),
              pytest.mark.skipif(os.name != "nt", reason="the thread route is Windows only")]

COMPARED = ("model", "report", "bindings", "faces", "edge_paths", "proof")


def settings() -> tuple[int, tuple[int, ...]]:
    """Repeat count and thread-count cycle from the environment; anything malformed is an ERROR at collection (fail closed)."""
    raw = os.environ.get("ANYFEM_T5_REPEATS", "20").strip()
    if not raw.isdigit() or int(raw) < 1:
        raise pytest.UsageError("ANYFEM_T5_REPEATS must be a positive integer, got %r" % raw)
    cycle = tuple(int(part) for part in os.environ.get("ANYFEM_T5_THREADS", "1,2,4,8").replace(" ", "").split(",") if part)
    if not cycle or any(not 1 <= count <= 8 for count in cycle):
        raise pytest.UsageError("ANYFEM_T5_THREADS must list thread counts 1..8, got %r" % os.environ.get("ANYFEM_T5_THREADS"))
    return int(raw), cycle


REPEATS, CYCLE = settings()
CASES = [(index, CYCLE[(index - 1) % len(CYCLE)]) for index in range(1, REPEATS + 1)]
RECORDED: dict[int, dict] = {}


def record(entry: dict) -> None:
    path = os.environ.get("ANYFEM_T5_DIGESTS")
    if path:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, sort_keys=True) + "\n")


@pytest.fixture(scope="module")
def stage():
    mp = pytest.MonkeyPatch()
    system_only_path(mp)
    mp.setenv(tr.ENV_RUNTIME, str(ROOT))
    tr._health_cache.clear()
    closure = load_closure()
    try:
        off = run(closure, "off", enabled=False)
        record(dict(repeat=0, threads=0, label="route-off reference", **{key: off[key] for key in COMPARED}, route=off["route"]))
        yield closure, off
    finally:
        tr.configure(reset=True)
        mp.undo()


def test_the_off_reference(stage):
    _, off = stage
    assert off["route"] == "off" and not off["adopted"]
    assert off["proof"] is True and off["edge_paths"] == EXPECTED_EDGE_PATHS


@pytest.mark.parametrize("index,threads", CASES, ids=["r%02d-t%d" % case for case in CASES])
def test_repeat_equals_route_off(stage, index, threads):
    closure, off = stage
    where = "repeat %d of %d, %d thread(s)" % (index, REPEATS, threads)
    entry = dict(repeat=index, threads=threads, label=where, route="error", adopted=False, plan_used=False, differing=["did not run to a comparison"])
    RECORDED[index] = entry
    try:
        on = run(closure, where, enabled=True, threads=threads)
    except BaseException as exc:  # recorded, then re-raised: never swallowed
        entry["error"] = "%s: %s" % (type(exc).__name__, exc)
        record(entry)
        raise
    differing = [key for key in COMPARED if on[key] != off[key]]
    if not (on["route"] == tr.OK and on["adopted"] and on["plan_used"]):
        differing.append("child plan not adopted (route=%s adopted=%s plan_used=%s reason=%s)" % (on["route"], on["adopted"], on["plan_used"], on["reason"]))
    entry.update({key: on[key] for key in COMPARED}, route=on["route"], adopted=on["adopted"], plan_used=on["plan_used"], differing=differing)
    record(entry)
    detail = "; ".join("%s: ON %r != OFF %r" % (key, on[key], off[key]) if key in COMPARED else key for key in differing)
    assert not differing, "T5 MISMATCH at %s: %s" % (where, detail)


def test_every_repeat_was_recorded(stage):
    """Fails when a repeat was deselected, aborted or never compared: the green count alone must not be able to hide a missing repeat."""
    missing = [index for index, _ in CASES if index not in RECORDED]
    bad = ["r%02d-t%d" % (index, RECORDED[index]["threads"]) for index, _ in CASES if index in RECORDED and RECORDED[index]["differing"]]
    assert not missing, "repeats never run: %s" % missing
    assert not bad, "repeats with differences: %s" % bad
    assert len(RECORDED) == REPEATS
