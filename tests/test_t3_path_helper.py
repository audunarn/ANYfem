"""The T3 PATH helper must cope with a system folder that itself holds a Python launcher (the GitHub windows runners have py.exe in the Windows folder)."""
from __future__ import annotations

import os
import shutil
import sys

import pytest

from t3_identity_helpers import system_only_path

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="PATH/PATHEXT semantics of the Windows runners")


def _fake_system(tmp_path, launcher_in_windir):
    root = tmp_path / "Windows"
    (root / "System32").mkdir(parents=True)
    (root / "System32" / "cmd.exe").write_bytes(b"")
    if launcher_in_windir:
        (root / "py.exe").write_bytes(b"")
    return root


@pytest.mark.parametrize("launcher_in_windir", [False, True])
def test_helper_leaves_no_python_on_the_path(tmp_path, monkeypatch, launcher_in_windir):
    root = _fake_system(tmp_path, launcher_in_windir)
    monkeypatch.setenv("SystemRoot", str(root))
    system_only_path(monkeypatch)
    for name in ("python", "python3", "py", "python.exe"):
        assert shutil.which(name) is None
    assert str(root / "System32") in os.environ["PATH"].split(os.pathsep)


def test_helper_still_refuses_when_no_folder_can_be_cleaned(tmp_path, monkeypatch):
    root = tmp_path / "Windows"
    (root / "System32").mkdir(parents=True)
    (root / "System32" / "py.exe").write_bytes(b"")
    (root / "py.exe").write_bytes(b"")
    monkeypatch.setenv("SystemRoot", str(root))
    with pytest.raises(AssertionError):
        system_only_path(monkeypatch)
