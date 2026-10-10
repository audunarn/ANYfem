"""Run the S4a workflow tests against the S1/S3a branches and HEAD snapshots.

ANYfem: this worktree (pytest adds ``src``).  ANYsolver and ANYmesher: the S1 and
S3a worktrees.  ANYgeometry, ANYmaterial, ANYfileIO, ANYloads: the S0 git-archive
snapshots of HEAD (the live ANYgeometry checkout may carry the owner's S2a work).
Paths can be overridden with S4A_ANYSOLVER, S4A_ANYMESH and S0_SNAP.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

WORKTREE = Path(__file__).resolve().parents[4]
SNAP = Path(os.environ.get("S0_SNAP", r"C:\Users\AUDUNA~1\AppData\Local\Temp\claude\C--Github"
                                      r"\59147d03-c123-4596-a0d0-f6053be6be25\scratchpad\s0-snap"))
paths = [
    os.environ.get("S4A_ANYSOLVER", r"C:\ANYworkspaceWork\sm1\src"),
    os.environ.get("S4A_ANYMESH", r"C:\ANYworkspaceWork\sm3\src"),
    *(str(SNAP / repo / "src") for repo in ("ANYgeometry", "ANYmaterial", "ANYfileIO", "ANYloads")),
]
for path in reversed(paths):
    sys.path.insert(0, path)
os.environ["PYTHONPATH"] = os.pathsep.join(paths + [os.environ.get("PYTHONPATH", "")])
os.chdir(WORKTREE)

import pytest  # noqa: E402

sys.exit(pytest.main(["-q", "tests/test_submodel_workflow.py", *sys.argv[1:]]))
