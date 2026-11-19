"""Help > Loaded packages collector: versions, import paths and commits, fail closed."""
import json
import os
import sys
import types
from importlib import metadata
from pathlib import Path

import pytest

from anyfem.ui.qt import loaded_packages as lp

SHA = "0123456789abcdef0123456789abcdef01234567"
SHA2 = "fedcba9876543210fedcba9876543210fedcba98"


def _package(root: Path, name: str, version_attr=None) -> types.ModuleType:
    package = root / name
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    module = types.ModuleType(name)
    module.__file__ = str(package / "__init__.py")
    module.__path__ = [str(package)]
    if version_attr is not None:
        module.__version__ = version_attr
    return module


def _pyproject(root: Path, name: str, version: str):
    root.mkdir(parents=True, exist_ok=True)
    (root / "pyproject.toml").write_text(f'[project]\nname = "{name}"\nversion = "{version}"\n', encoding="utf-8")


def _git(root: Path, branch="main", sha=SHA):
    (root / ".git" / "refs" / "heads").mkdir(parents=True)
    (root / ".git" / "HEAD").write_text(f"ref: refs/heads/{branch}\n", encoding="utf-8")
    (root / ".git" / "refs" / "heads" / branch).write_text(sha + "\n", encoding="utf-8")


def _row(report, module):
    return next(row for row in report.rows if row.module == module)


def test_source_checkout_reports_pyproject_path_commit_and_disagreeing_version(tmp_path):
    repo = tmp_path / "ANYgeometry"
    _git(repo)
    _pyproject(repo, "ANYgeometry", "9.9.9")
    module = _package(repo / "src", "anygeometry", version_attr="9.9.8")
    row = _row(lp.collect_loaded_packages({"anygeometry": module}), "anygeometry")
    assert row.loaded and row.version == "9.9.9"
    assert row.version_source == "pyproject.toml of the source checkout"
    assert row.import_path == str(repo / "src" / "anygeometry")
    assert row.commit == f"{SHA[:12]} (main) in {repo.resolve()}"
    assert any("module __version__ says 9.9.8" in note for note in row.notes)


def test_import_path_through_a_junction_shows_the_resolved_target(tmp_path):
    winapi = pytest.importorskip("_winapi") if os.name == "nt" else None
    target = tmp_path / "owners" / "ANYmesh"
    _git(target, branch="runtime", sha=SHA2)
    _pyproject(target, "ANYmesher", "1.2.3")
    _package(target / "src", "anymesher")
    link = tmp_path / ".ecosystem" / "ANYmesh"
    link.parent.mkdir()
    if winapi is not None:
        winapi.CreateJunction(str(target), str(link))
    else:
        os.symlink(target, link, target_is_directory=True)
    module = types.ModuleType("anymesher")
    module.__file__ = str(link / "src" / "anymesher" / "__init__.py")
    module.__path__ = [str(link / "src" / "anymesher")]
    row = _row(lp.collect_loaded_packages({"anymesher": module}), "anymesher")
    assert row.import_path == str(link / "src" / "anymesher")
    assert Path(row.real_path) == (target / "src" / "anymesher").resolve()
    assert row.version == "1.2.3"
    assert row.commit == f"{SHA2[:12]} (runtime) in {target.resolve()}"
    assert "resolves to: " in lp.collect_loaded_packages({"anymesher": module}).text()


def test_worktree_packed_refs_and_detached_heads(tmp_path):
    common = tmp_path / "main" / ".git"
    (common / "worktrees" / "wt").mkdir(parents=True)
    (common / "packed-refs").write_text(f"# pack-refs\n{SHA} refs/heads/feature\n", encoding="utf-8")
    gitdir = common / "worktrees" / "wt"
    (gitdir / "HEAD").write_text("ref: refs/heads/feature\n", encoding="utf-8")
    (gitdir / "commondir").write_text("../..\n", encoding="utf-8")
    worktree = tmp_path / "wt"
    worktree.mkdir()
    (worktree / ".git").write_text(f"gitdir: {gitdir}\n", encoding="utf-8")
    module = _package(worktree / "src", "anyloads")
    assert _row(lp.collect_loaded_packages({"anyloads": module}), "anyloads").commit == f"{SHA[:12]} (feature) in {worktree.resolve()}"
    detached = tmp_path / "detached"
    (detached / ".git").mkdir(parents=True)
    (detached / ".git" / "HEAD").write_text(SHA2 + "\n", encoding="utf-8")
    module = _package(detached / "src", "anypost")
    assert _row(lp.collect_loaded_packages({"anypost": module}), "anypost").commit == f"{SHA2[:12]} (detached) in {detached.resolve()}"


def test_stale_egg_info_metadata_is_flagged_not_trusted(tmp_path, monkeypatch):
    project = tmp_path / "ANYfake"
    _pyproject(project, "anyfakeegg", "1.0.1")
    module = _package(project / "src", "anyfakeegg", version_attr="1.0.1")
    egg = project / "src" / "anyfakeegg.egg-info"
    egg.mkdir()
    (egg / "PKG-INFO").write_text("Metadata-Version: 2.1\nName: anyfakeegg\nVersion: 1.0.0\n", encoding="utf-8")
    monkeypatch.syspath_prepend(str(project / "src"))
    row = _row(lp.collect_loaded_packages({"anyfakeegg": module}), "anyfakeegg")
    assert row.version == "1.0.1" and row.version_source.startswith("pyproject.toml")
    assert any("metadata (anyfakeegg) says 1.0.0" in note for note in row.notes)


def _editable_dist(site: Path, name: str, version: str, url: Path):
    info = site / f"{name}-{version}.dist-info"
    info.mkdir(parents=True)
    (info / "METADATA").write_text(f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n", encoding="utf-8")
    (info / "direct_url.json").write_text(json.dumps({"url": url.resolve().as_uri(), "dir_info": {"editable": True}}), encoding="utf-8")


def test_editable_metadata_counts_only_for_its_own_project(tmp_path, monkeypatch):
    site = tmp_path / "site"
    owned_project = tmp_path / "owned"
    _editable_dist(site, "anyowned", "2.0.0", owned_project)
    _editable_dist(site, "anyelsewhere", "3.0.0", tmp_path / "another-copy")
    monkeypatch.syspath_prepend(str(site))
    owned = _package(owned_project / "src", "anyowned")
    elsewhere = _package(tmp_path / "loaded-copy" / "src", "anyelsewhere")
    report = lp.collect_loaded_packages({"anyowned": owned, "anyelsewhere": elsewhere})
    row = _row(report, "anyowned")
    assert (row.version, row.version_source) == ("2.0.0", "metadata (anyowned)")
    row = _row(report, "anyelsewhere")
    assert row.version == lp.UNKNOWN
    assert any("describes another copy" in note for note in row.notes)


def test_module_version_is_the_fallback_and_site_packages_any_modules_are_not_ecosystem(tmp_path):
    loose = _package(tmp_path / "loose", "anyloose", version_attr="0.0.7")
    anyio = _package(tmp_path / "Lib" / "site-packages", "anyio", version_attr="4.0")
    report = lp.collect_loaded_packages({"anyloose": loose, "anyio": anyio})
    row = _row(report, "anyloose")
    assert (row.group, row.version, row.version_source) == ("ANY ecosystem", "0.0.7", "module __version__")
    assert all(row.module != "anyio" for row in report.rows)


def test_installed_wheel_uses_owned_metadata_and_nothing_is_imported():
    import numpy
    before = set(sys.modules)
    report = lp.collect_loaded_packages()
    assert set(sys.modules) == before
    row = _row(report, "numpy")
    assert (row.version, row.version_source) == (metadata.version("numpy"), "metadata (numpy)")
    assert row.import_path == str(Path(numpy.__file__).parent)
    for module_name, _display, _candidates in lp.ECOSYSTEM_MODULES + lp.THIRD_PARTY_MODULES:
        assert _row(report, module_name).loaded == (module_name in sys.modules)
    text = report.text()
    assert text.startswith("ANYfem loaded packages\n") and f"Python: {sys.version.split()[0]}" in text
    for row in report.rows:
        assert (f"{row.name}: not loaded in this session" in text) != row.loaded
    assert report.elapsed_ms < 2000  # measured ~70 ms warm with the GUI loaded
