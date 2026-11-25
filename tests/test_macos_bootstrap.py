"""Portable checks for the Mac installation's refusal and provenance contracts."""
import json
from pathlib import Path
import re

import pytest

from tools.bootstrap_macos import fresh_work_directory, source_pins


def test_bootstrap_refuses_reusing_outputs_and_building_inside_source(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    with pytest.raises(ValueError, match="outside"):
        fresh_work_directory(source / "build", source)
    work = tmp_path / "work"
    assert fresh_work_directory(work, source) == work
    with pytest.raises(FileExistsError):
        fresh_work_directory(work, source)


def test_macos_pins_are_the_shared_lanes_pins():
    root = Path(__file__).resolve().parents[1]
    lane = (root / ".github/workflows/application-lane.yml").read_text(encoding="utf-8")
    pins = source_pins()
    assert pins["ANYmesh"] == "e889e62331d980ceb7042ff5c338a5117dca0162"
    assert pins["ANYgeometry"] == "5f68177e42fe0a53341dceb80cc8e4fb73482745"
    for name, sha in pins.items():
        assert f"repository: audunarn/{name}" in lane and sha in lane
    assert not (root / "tools" / "macos-ecosystem.json").exists(), "no second pin list"


def test_lane_pins_refuse_unpinned_or_conflicting_refs(tmp_path):
    good = (
        "      - uses: actions/checkout@v4\n"
        "        with:\n"
        "          repository: audunarn/{n}\n"
        "          ref: {r}\n"
    )
    path = tmp_path / "lane.yml"
    path.write_text(good.format(n="ANYmesh", r="main"))
    with pytest.raises(ValueError, match="invalid ecosystem pin"):
        source_pins(path)
    path.write_text(good.format(n="ANYmesh", r="a" * 40) + good.format(n="ANYmesh", r="b" * 40))
    with pytest.raises(ValueError, match="conflicting"):
        source_pins(path)
    path.write_text("jobs: {}\n")
    with pytest.raises(ValueError, match="no ecosystem pins"):
        source_pins(path)


def test_installed_license_gate_ignores_inactive_platform_requirement(monkeypatch):
    from tools import check_licenses
    calls = []
    monkeypatch.setattr(check_licenses.importlib_metadata, "distribution", lambda name: calls.append(name))
    check_licenses._check_installed([{"scope": "runtime", "name": "absent-platform-only-package",
        "requirement": "absent-platform-only-package; python_version < '1'"}])
    assert calls == []


def test_macos_workflow_is_one_bounded_manual_or_scheduled_lane_call():
    root = Path(__file__).resolve().parents[1]
    raw = (root / ".github/workflows/macos.yml").read_text(encoding="utf-8")
    text = "\n".join(line for line in raw.splitlines() if not line.lstrip().startswith("#"))
    triggers = text.split("jobs:", 1)[0]
    assert "workflow_dispatch:" in triggers and "schedule:" in triggers
    assert "push" not in triggers and "pull_request" not in triggers
    body = text.split("jobs:", 1)[1]
    assert body.count("uses: ./.github/workflows/application-lane.yml") == 1
    assert body.count("os: macos-15") == 1 and "matrix" not in text
    assert "repository:" not in text and "ref:" not in text, "pins belong to the shared lane only"
    lane = (root / ".github/workflows/application-lane.yml").read_text(encoding="utf-8")
    assert re.search(r"timeout-minutes: \d+", lane), "the lane bounds every job it runs"
