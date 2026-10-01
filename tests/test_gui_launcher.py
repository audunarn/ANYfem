"""The repository GUI launcher must stay usable from an IDE or shell."""

from __future__ import annotations

import runpy
from pathlib import Path
from types import SimpleNamespace
import sys
import pytest


ROOT = Path(__file__).resolve().parents[1]


def test_run_gui_exposes_and_calls_main() -> None:
    script = ROOT / "run_gui.py"
    namespace = runpy.run_path(str(script), run_name="launcher_test")
    source = script.read_text(encoding="utf-8")

    assert callable(namespace["main"])
    assert 'if __name__ == "__main__":\n    main()' in source


@pytest.mark.parametrize("flags,frontend",[([],"qt"),(["--qt"],"qt"),(["--tk"],"tk")])
def test_default_and_explicit_frontend_routes(monkeypatch,flags,frontend):
    main=runpy.run_path(str(ROOT/"run_gui.py"),run_name="launcher_test")["main"]
    calls=[]
    monkeypatch.setitem(main.__globals__,"require_compatible_ecosystem",lambda **kwargs:calls.append(kwargs["frontend"]))
    monkeypatch.setitem(sys.modules,f"anyfem.ui.{frontend}",SimpleNamespace(main=lambda:calls.append("launched")))
    monkeypatch.setattr(sys,"argv",["run_gui.py",*flags])
    main();assert calls==[frontend,"launched"]


def test_conflicting_frontends_fail_before_preflight(monkeypatch):
    main=runpy.run_path(str(ROOT/"run_gui.py"),run_name="launcher_test")["main"]
    monkeypatch.setattr(sys,"argv",["run_gui.py","--qt","--tk"])
    with pytest.raises(SystemExit,match="only one frontend"):main()
