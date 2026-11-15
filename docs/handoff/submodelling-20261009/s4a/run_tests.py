"""Run submodelling tests through ANYfem's bounded test route with chosen sibling sources.

This only puts the sibling packages on ``PYTHONPATH`` and then runs
``tools/test_anyfem.py --profile focused`` (``docs/test-scopes.md``), so the route's
600-second deadline, owned process tree and evidence directory apply unchanged.

The sibling packages come from ``C:/Github/<repo>/src``: ANYfeSolver (formerly
ANYsolver), ANYmesh, ANYgeometry, ANYmaterial, ANYfileIO and ANYloads.  Each can be
overridden with ``S4A_<REPO>`` in upper case, for example ``S4A_ANYGEOMETRY`` for a
clean ANYgeometry worktree, and ``S4A_ROOT`` overrides ``C:/Github``.
``S4A_ANYSOLVER`` is still accepted for ANYfeSolver.  The sibling checkouts must be
clean: a dirty ANYgeometry main checkout once made three submodel tests fail that pass
on origin/main.

Arguments go to ``tools/test_anyfem.py`` (``--out``, ``--fail-fast``, test paths).
Without a test path it selects every ``tests/test_submodel*.py``.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

WORKTREE = Path(__file__).resolve().parents[4]
ROOT = Path(os.environ.get("S4A_ROOT", r"C:\Github"))
REPOS = ("ANYfeSolver", "ANYmesh", "ANYgeometry", "ANYmaterial", "ANYfileIO", "ANYloads")


def _source(repo: str) -> str:
    override = os.environ.get(f"S4A_{repo.upper()}")
    if override is None and repo == "ANYfeSolver":
        override = os.environ.get("S4A_ANYSOLVER")
    path = Path(override) if override else ROOT / repo / "src"
    if not path.is_dir():
        sys.exit(f"run_tests: {repo} source folder {path} does not exist; set S4A_{repo.upper()}")
    return str(path)


paths = [_source(repo) for repo in REPOS]
environment = dict(os.environ)
environment["PYTHONPATH"] = os.pathsep.join([*paths, *filter(None, [os.environ.get("PYTHONPATH")])])
args = sys.argv[1:]
if not any(a.startswith("tests/") for a in args):
    args += sorted(p.relative_to(WORKTREE).as_posix() for p in (WORKTREE / "tests").glob("test_submodel*.py"))
command = [sys.executable, str(WORKTREE / "tools" / "test_anyfem.py"), "--profile", "focused", *args]
sys.exit(subprocess.call(command, cwd=WORKTREE, env=environment))
