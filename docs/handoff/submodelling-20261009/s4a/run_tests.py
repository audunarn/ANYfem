"""Run the submodelling tests of this ANYfem checkout against the sibling repositories.

ANYfem: the checkout this file is in (pytest adds ``src``).  The other packages come from
``C:/Github/<repo>/src``: ANYfeSolver (formerly ANYsolver), ANYmesh, ANYgeometry,
ANYmaterial, ANYfileIO and ANYloads.  Each can be overridden with an environment variable
``S4A_<REPO>`` in upper case, for example ``S4A_ANYGEOMETRY`` for a clean ANYgeometry
worktree.  ``S4A_ANYSOLVER`` is still accepted for ANYfeSolver.

The sibling checkouts must be clean: a dirty ANYgeometry main checkout once made three
submodel tests fail that pass on origin/main.

Without a test path among the arguments it runs every ``tests/test_submodel*.py`` file;
pytest options such as ``--basetemp`` can be given either way.
"""

from __future__ import annotations

import os
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
for path in reversed(paths):
    sys.path.insert(0, path)
os.environ["PYTHONPATH"] = os.pathsep.join(paths + [os.environ.get("PYTHONPATH", "")])
os.chdir(WORKTREE)

import pytest  # noqa: E402

default = sorted(str(p.relative_to(WORKTREE).as_posix()) for p in (WORKTREE / "tests").glob("test_submodel*.py"))
args = sys.argv[1:]
named = any((WORKTREE / a.split("::")[0]).exists() for a in args if not a.startswith("-"))
sys.exit(pytest.main(["-q", *args, *([] if named else default)]))
