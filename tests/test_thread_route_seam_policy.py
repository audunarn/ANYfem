"""The thread route must honour the parent's seam policy: with ``ANYGEOMETRY_SEAM_POLICY=ring_split`` in the application the
child plans with the same configuration, so the route is not refused with "planning configuration differs" (measured on
ANYgeometry 169ce53: child ['exact','baseline'] vs parent ['exact','ring_split'], because the child environment drops every
ANYGEOMETRY_* variable except the thread count).  Real bundled child on sector60 (91 faces); skipped without a built bundle.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from anyfem import runtime_lock as rl
from anyfem import thread_route as tr
from t3_identity_helpers import load_closure, run as _run, system_only_path

ROOT = Path(os.environ.get("ANYGEOMETRY_THREAD_RUNTIME", ""))
BUILT = bool(os.environ.get("ANYGEOMETRY_THREAD_RUNTIME")) and (ROOT / "python.exe").is_file() and (ROOT / rl.LOCK_NAME).is_file()
pytestmark = [pytest.mark.skipif(not BUILT, reason="no built runtime bundle in ANYGEOMETRY_THREAD_RUNTIME"),
              pytest.mark.skipif(os.name != "nt", reason="the thread route is Windows only")]


def test_parent_ring_split_policy_is_not_refused_as_a_planning_configuration_mismatch():
    mp = pytest.MonkeyPatch()
    system_only_path(mp)
    mp.setenv(tr.ENV_RUNTIME, str(ROOT))
    mp.setenv("ANYGEOMETRY_SEAM_POLICY", "ring_split")
    tr._health_cache.clear()
    try:
        serial = _run(load_closure(), "off", enabled=False)
        row = _run(load_closure(), "on", enabled=True)
    finally:
        tr.configure(reset=True)
        mp.undo()
    assert "planning configuration differs" not in row["reason"], row["reason"]
    assert row["route"] == tr.OK, (row["route"], row["reason"])
    assert row["adopted"] and row["plan_used"]
    assert row["model"] == serial["model"] and row["report"] == serial["report"]
