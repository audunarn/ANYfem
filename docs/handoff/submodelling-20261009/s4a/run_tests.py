"""Run the S4a workflow tests against the S1/S3a branches and HEAD snapshots.

ANYfem: this worktree (pytest adds ``src``).  ANYsolver and ANYmesher: detached
checkout of main 8d6e64d2 (S1) and the ANYmesh worktree sm7 (main 5e6d97c, which has S3a,
plus the scalar-offset side fix).  ANYgeometry:
the S2b worktree (main 9db03bb plus seam-member partitioning) when present, else the
snapshot.  ANYmaterial, ANYfileIO, ANYloads: the S0 git-archive snapshots of HEAD.
Paths can be overridden with S4A_ANYSOLVER, S4A_ANYMESH, S4A_ANYGEOMETRY and S0_SNAP.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

WORKTREE = Path(__file__).resolve().parents[4]
SNAP = Path(os.environ.get("S0_SNAP", r"C:\Users\AUDUNA~1\AppData\Local\Temp\claude\C--Github"
                                      r"\59147d03-c123-4596-a0d0-f6053be6be25\scratchpad\s0-snap"))
paths = [
    os.environ.get("S4A_ANYSOLVER", r"C:\ANYworkspaceWork\sv-8d6e64d2\src"),
    # ANYmesh: main 5e6d97c plus the scalar-offset side fix (branch claude/offset-normal-sign).
    os.environ.get("S4A_ANYMESH", r"C:\ANYworkspaceWork\sm7\src"),
    # ANYgeometry: the S2b worktree when present, else the S0 snapshot.
    os.environ.get("S4A_ANYGEOMETRY", r"C:\ANYworkspaceWork\sm5\src"
                   if Path(r"C:\ANYworkspaceWork\sm5\src").is_dir() else str(SNAP / "ANYgeometry" / "src")),
    *(str(SNAP / repo / "src") for repo in ("ANYmaterial", "ANYfileIO", "ANYloads")),
]
for path in reversed(paths):
    sys.path.insert(0, path)
os.environ["PYTHONPATH"] = os.pathsep.join(paths + [os.environ.get("PYTHONPATH", "")])
os.chdir(WORKTREE)

import pytest  # noqa: E402

sys.exit(pytest.main(["-q", *(sys.argv[1:] or ["tests/test_submodel_workflow.py", "tests/test_submodel_jobs.py",
                                         "tests/test_submodel_cut.py", "tests/test_submodel_cylinder.py"])]))
