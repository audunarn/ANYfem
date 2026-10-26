"""Explicit renderer routing must fail clearly before opening the workbench."""
from pathlib import Path
import runpy
import sys
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]


def launcher():
    return runpy.run_path(str(ROOT / "run_gui.py"), run_name="renderer_test")


@pytest.mark.parametrize("renderer", ["gpu", "webgpu", "software"])
def test_explicit_renderer_reaches_qt(monkeypatch, renderer):
    main = launcher()["main"]
    calls = []
    monkeypatch.setitem(main.__globals__, "require_renderer_dependencies", lambda value: calls.append(value))
    monkeypatch.setitem(main.__globals__, "require_compatible_ecosystem", lambda **kw: calls.append(kw))
    monkeypatch.setitem(sys.modules, "anyfem.ui.qt", SimpleNamespace(main=lambda **kw: calls.append(kw)))
    monkeypatch.setattr(sys, "argv", ["run_gui.py", "--renderer", renderer])
    main()
    assert calls == [renderer, {"frontend": "qt"}, {"viewer_backend": renderer}]


def test_tk_rejects_explicit_renderer_before_preflight(monkeypatch):
    main = launcher()["main"]
    monkeypatch.setattr(sys, "argv", ["run_gui.py", "--tk", "--renderer", "webgpu"])
    with pytest.raises(SystemExit, match="Qt viewport"):
        main()


def test_missing_webgpu_reports_selected_environment(monkeypatch):
    check = launcher()["require_renderer_dependencies"]
    monkeypatch.setattr(check.__globals__["util"], "find_spec", lambda module: None)
    with pytest.raises(RuntimeError, match="WebGPU is unavailable") as error:
        check("webgpu")
    assert sys.executable in str(error.value)
    assert "[webgpu]" in str(error.value)


def test_other_renderers_do_not_require_webgpu(monkeypatch):
    check = launcher()["require_renderer_dependencies"]
    def unexpected(module):
        raise AssertionError(module)
    monkeypatch.setattr(check.__globals__["util"], "find_spec", unexpected)
    check("auto")
    check("gpu")
    check("software")
