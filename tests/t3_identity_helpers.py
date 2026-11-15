"""Shared helpers of the T3 identity test and the T5 stress repeat (run sector60 through the route, digest the result).

Not a test module (no ``test_`` prefix).  ``pythonpath = ["src", "tests"]`` makes it importable by both.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import os
import shutil
from dataclasses import replace
from pathlib import Path

from anyfem import thread_route as tr

DATA = Path(__file__).parent / "data" / "sector60_closure.json.gz"
EXPECTED_EDGE_PATHS = 223


_PYTHON_NAMES = ("python", "python3", "py", "python.exe")


def system_only_path(mp) -> None:
    """PATH = system directories without any Python, the route variables cleared, via the MonkeyPatch ``mp``.

    A system folder that itself holds a launcher (the GitHub windows runners have ``py.exe`` in the Windows folder) is left out of PATH:
    the route never looks at PATH, so the proof "no Python can be found by name" stays true and the folder does not matter.
    """
    system = os.environ.get("SystemRoot", r"C:\Windows")
    candidates = [os.path.join(system, "System32"), system]
    kept = [folder for folder in candidates if not any(shutil.which(name, path=folder) for name in _PYTHON_NAMES)]
    assert kept, "no system folder is free of a Python launcher"
    mp.setenv("PATH", os.pathsep.join(kept))
    for name in _PYTHON_NAMES:
        assert shutil.which(name) is None, "PATH must hold no Python (%s found)" % name
    for name in (tr.ENV_ENABLE, tr.ENV_THREADS, tr.ENV_MIN_FACES, tr.ENV_APP_DIR):
        mp.delenv(name, raising=False)


def load_closure():
    import anygeometry
    with gzip.open(DATA, "rb") as handle:
        return anygeometry.model_closure_from_dict(json.loads(handle.read().decode("utf-8")))


def digest(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def run(closure, label, **cfg) -> dict:
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
                adopted=bool(tr.last_adoption.get("adopted")), plan_used=bool(handed) and all(plan is not None for plan in handed), model=digest(to_dict(work)), report=digest(report.to_dict()),
                faces=len(work.faces), proof=bool(anygeometry.has_current_intersection_preparation(work)),
                edge_paths=len(source_edge_paths(fresh)), bindings=tr.binding_fingerprint(work))
