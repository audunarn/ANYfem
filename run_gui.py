#!/usr/bin/env python
"""Run the ANYfem desktop application from this checkout."""

from __future__ import annotations

import sys
from pathlib import Path


_ROOT = Path(__file__).resolve().parent
_SOURCE_TREES = (
    _ROOT / "src",
    _ROOT.parent / "ANYsolver" / "src",
    _ROOT.parent / "ANYmaterial" / "src",
    _ROOT.parent / "ANYgeometry" / "src",
    _ROOT.parent / "ANYmesh" / "src",
    _ROOT.parent / "ANYio" / "src",
    _ROOT.parent / "ANYtk3D" / "src",
)

# Add in reverse so ANYfem's own checkout remains first on sys.path.
for _source in reversed(_SOURCE_TREES):
    if _source.is_dir() and str(_source) not in sys.path:
        sys.path.insert(0, str(_source))


def main() -> None:
    """Launch the GUI using the application package's maintained entry point."""

    from anyfem.ui.app import main as gui_main

    gui_main()


if __name__ == "__main__":
    main()
