"""S0 spike environment: run the ANY stack from git-archive snapshots of HEAD.

Every S0 script imports this module first.  It puts the snapshot ``src``
directories at the front of ``sys.path`` and refuses to continue if any stack
module resolves from somewhere else (site-packages ``.pth`` files point at the
live, partly dirty ``C:/Github`` trees).

Snapshot root: environment variable ``S0_SNAP`` (default: the session
scratchpad path used on 2026-10-09).  The snapshot identities are recorded in
``<S0_SNAP>/IDENTITY.txt``.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

REPOS = ("ANYfem", "ANYgeometry", "ANYmesh", "ANYsolver", "ANYmaterial", "ANYfileIO", "ANYloads")
MODULES = {
    "anyfem": "ANYfem",
    "anygeometry": "ANYgeometry",
    "anymesher": "ANYmesh",
    "anysolver": "ANYsolver",
    "anymaterial": "ANYmaterial",
    "anyfileio": "ANYfileIO",
    "anyloads": "ANYloads",
}

_DEFAULT = (
    r"C:\Users\AUDUNA~1\AppData\Local\Temp\claude\C--Github"
    r"\59147d03-c123-4596-a0d0-f6053be6be25\scratchpad\s0-snap"
)
SNAP = Path(os.environ.get("S0_SNAP", _DEFAULT)).resolve()

for repo in reversed(REPOS):
    path = str(SNAP / repo / "src")
    if path not in sys.path:
        sys.path.insert(0, path)


def verify() -> dict[str, str]:
    """Import every stack module and prove it came from the snapshot."""

    import importlib

    origins: dict[str, str] = {}
    for module_name, repo in MODULES.items():
        module = importlib.import_module(module_name)
        origin = Path(module.__file__).resolve()
        expected = SNAP / repo / "src"
        if expected not in origin.parents:
            raise RuntimeError(f"{module_name} imported from {origin}, not the snapshot {expected}")
        origins[module_name] = f"{getattr(module, '__version__', '?')} {origin}"
    from anymesher import native_cpp

    origins["NATIVE_CPP_AVAILABLE"] = str(native_cpp.NATIVE_CPP_AVAILABLE)
    origins["python"] = sys.version.split()[0]
    identity = SNAP / "IDENTITY.txt"
    origins["identity"] = identity.read_text().strip() if identity.exists() else "missing"
    return origins


if __name__ == "__main__":
    for key, value in verify().items():
        print(f"{key:22s} {value}")
