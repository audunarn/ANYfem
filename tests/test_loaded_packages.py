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
    url.mkdir(parents=True, exist_ok=True)
    if not (url / "pyproject.toml").exists():  # a real editable project root; version from metadata
        (url / "pyproject.toml").write_text(f'[project]\nname = "{name}"\ndynamic = ["version"]\n', encoding="utf-8")


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


# Review round 1 regressions (each failed on 203b17c).

def test_unc_editable_url_does_not_claim_the_same_path_on_the_local_drive(tmp_path, monkeypatch):
    site = tmp_path / "site"
    local = tmp_path / "project"
    drive_relative = local.resolve().as_posix().split(":", 1)[-1]  # /ANYworkspaceWork/...
    info = site / "anyunc-5.0.0.dist-info"
    info.mkdir(parents=True)
    (info / "METADATA").write_text("Metadata-Version: 2.1\nName: anyunc\nVersion: 5.0.0\n", encoding="utf-8")
    url = "file://anyfem-no-such-host.invalid" + drive_relative
    (info / "direct_url.json").write_text(json.dumps({"url": url, "dir_info": {"editable": True}}), encoding="utf-8")
    monkeypatch.syspath_prepend(str(site))
    module = _package(local / "src", "anyunc", version_attr="4.0.0")
    row = _row(lp.collect_loaded_packages({"anyunc": module}), "anyunc")
    assert (row.version, row.version_source) == ("4.0.0", "module __version__")
    assert any("describes another copy" in note for note in row.notes)
    assert lp._file_url_path("file://server/share/project") == "//server/share/project"
    assert lp._file_url_path("file:///C:/Github/ANYgeometry") == "C:/Github/ANYgeometry"


def test_symbolic_loose_ref_wins_over_an_older_packed_entry(tmp_path):
    repo = tmp_path / "repo"
    _git(repo, branch="main", sha=SHA)
    (repo / ".git" / "refs" / "heads" / "alias").write_text("ref: refs/heads/main\n", encoding="utf-8")
    (repo / ".git" / "packed-refs").write_text(f"{SHA2} refs/heads/alias\n", encoding="utf-8")
    (repo / ".git" / "HEAD").write_text("ref: refs/heads/alias\n", encoding="utf-8")
    module = _package(repo / "src", "anymaterial")
    assert _row(lp.collect_loaded_packages({"anymaterial": module}), "anymaterial").commit == f"{SHA[:12]} (alias) in {repo.resolve()}"
    (repo / ".git" / "refs" / "heads" / "main").write_text("ref: refs/heads/alias\n", encoding="utf-8")  # cycle
    row = _row(lp.collect_loaded_packages({"anymaterial": module}), "anymaterial")
    assert row.commit == f"{lp.UNKNOWN} (alias, unresolved) in {repo.resolve()}"


@pytest.mark.parametrize("damage", ["metadata-bytes", "project-not-table", "direct-url-list"])
def test_malformed_provenance_becomes_a_note_not_an_exception(tmp_path, monkeypatch, damage):
    site = tmp_path / "site"
    project = tmp_path / "project"
    info = site / "anybroken-1.0.0.dist-info"
    info.mkdir(parents=True)
    if damage == "metadata-bytes":
        (info / "METADATA").write_bytes(b"Metadata-Version: 2.1\nName: anybroken\nVersion: \xff\xfe\n")
    else:
        (info / "METADATA").write_text("Metadata-Version: 2.1\nName: anybroken\nVersion: 1.0.0\n", encoding="utf-8")
    direct = "[]" if damage == "direct-url-list" else json.dumps({"url": project.resolve().as_uri(), "dir_info": {"editable": True}})
    (info / "direct_url.json").write_text(direct, encoding="utf-8")
    project.mkdir()
    if damage == "project-not-table":
        (project / "pyproject.toml").write_text('project = "invalid"\n', encoding="utf-8")
    monkeypatch.syspath_prepend(str(site))
    module = _package(project / "src", "anybroken", version_attr="0.9.0")
    report = lp.collect_loaded_packages({"anybroken": module})
    row = _row(report, "anybroken")
    expected = {"metadata-bytes": "metadata for anybroken", "project-not-table": "pyproject.toml",
                "direct-url-list": "metadata for anybroken"}[damage]
    assert any(note.startswith(expected) and "could not be read" in note for note in row.notes), row.notes
    if damage == "project-not-table":
        assert (row.version, row.version_source) == ("1.0.0", "metadata (anybroken)")
    else:
        assert (row.version, row.version_source) == ("0.9.0", "module __version__")
    assert "anybroken" in report.text()


def test_collection_imports_nothing_in_a_fresh_process(tmp_path):
    import subprocess
    import zipfile
    archive = tmp_path / "plain.zip"  # an ordinary zip on sys.path makes discovery open it
    with zipfile.ZipFile(archive, "w") as handle:
        handle.writestr("readme.txt", "x")
    code = ("import sys, types, anyfem.ui.qt.loaded_packages as lp; "
            f"sys.path.insert(0, {str(archive)!r}); "
            "lazy = types.ModuleType('anylazy'); lazy.__file__ = __import__('os').path.abspath('anylazy.py'); "
            "lazy.__getattr__ = lambda name: __import__('fractions'); sys.modules['anylazy'] = lazy; "
            "before=set(sys.modules); lp.collect_loaded_packages(); print(sorted(set(sys.modules)-before))")
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120,
                            cwd=tmp_path, env={**os.environ, "PYTHONPATH": os.pathsep.join(sys.path)})
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "[]"


def test_every_owned_metadata_source_is_compared(tmp_path, monkeypatch):
    site = tmp_path / "site"
    project = tmp_path / "project"
    _editable_dist(site, "anymulti", "6.10.1", project)
    _editable_dist(site, "anymulti_essentials", "6.10.2", project)
    monkeypatch.syspath_prepend(str(site))
    module = _package(project / "src", "anymulti", version_attr="6.10.1")
    row = lp._row("Third party", "anymulti", "anymulti", ("anymulti", "anymulti-essentials"),
                  {"anymulti": module}, {}, False)
    assert (row.version, row.version_source) == ("6.10.1", "metadata (anymulti)")
    assert any("metadata (anymulti_essentials) says 6.10.2" in note for note in row.notes), row.notes


# Review round 2 regressions (each failed on 13f6f53).

def test_module_version_is_read_without_invoking_a_lazy_getattr(tmp_path):
    calls = []
    module = _package(tmp_path / "lazy", "anylazyattr")
    module.__getattr__ = lambda name: calls.append(name) or "6.6.6"
    row = _row(lp.collect_loaded_packages({"anylazyattr": module}), "anylazyattr")
    assert calls == [] and row.version == lp.UNKNOWN


@pytest.mark.parametrize("target", ["C:/other/.git/refs/heads/main", "/other/refs/heads/main",
                                    "//server/share/refs/heads/main", "refs/heads/../../../other"])
def test_symbolic_refs_outside_the_repository_are_refused(tmp_path, target):
    other = tmp_path / "other"
    _git(other, sha=SHA2)
    repo = tmp_path / "repo"
    _git(repo, sha=SHA)
    target = target.replace("C:/other", other.resolve().as_posix())
    (repo / ".git" / "refs" / "heads" / "main").write_text(f"ref: {target}\n", encoding="utf-8")
    module = _package(repo / "src", "anyloads")
    commit = _row(lp.collect_loaded_packages({"anyloads": module}), "anyloads").commit
    assert SHA2[:12] not in commit
    assert commit == f"{lp.UNKNOWN} (main, unresolved) in {repo.resolve()}"
    (repo / ".git" / "HEAD").write_text(f"ref: {target}\n", encoding="utf-8")
    commit = _row(lp.collect_loaded_packages({"anyloads": module}), "anyloads").commit
    assert SHA2[:12] not in commit and commit.startswith(lp.UNKNOWN)


def test_editable_project_does_not_own_a_nested_separate_checkout(tmp_path, monkeypatch):
    site = tmp_path / "site"
    outer = tmp_path / "project"
    _editable_dist(site, "anynested", "9.0.0", outer)
    monkeypatch.syspath_prepend(str(site))
    _pyproject(outer, "anynested", "9.0.0")
    second = outer / "second-copy"
    second.mkdir()
    (second / "pyproject.toml").write_text('[project]\nname = "anynested"\ndynamic = ["version"]\n', encoding="utf-8")
    module = _package(second / "src", "anynested", version_attr="1.0.0")
    row = _row(lp.collect_loaded_packages({"anynested": module}), "anynested")
    assert (row.version, row.version_source) == ("1.0.0", "module __version__")
    assert any("describes another copy" in note for note in row.notes)


# Review round 3 regressions (each failed on 7e3aec9).

def test_module_location_never_invokes_a_lazy_getattr(tmp_path):
    calls = []
    module = types.ModuleType("anyfilelazy")
    module.__file__ = str(tmp_path / "anyfilelazy.py")  # a plain module: no __path__
    module.__getattr__ = lambda name: calls.append(name) or ["x"]
    row = _row(lp.collect_loaded_packages({"anyfilelazy": module}), "anyfilelazy")
    assert calls == [] and row.import_path == module.__file__


def test_ref_names_git_allows_still_resolve(tmp_path):
    repo = tmp_path / "repo"
    _git(repo, branch="feature=fix", sha=SHA)
    module = _package(repo / "src", "anysolid")
    assert _row(lp.collect_loaded_packages({"anysolid": module}), "anysolid").commit == f"{SHA[:12]} (feature=fix) in {repo.resolve()}"


# Review round 4 regression (failed on c080510/5a1feaa).

def test_lazyloader_module_is_never_executed(tmp_path):
    import builtins
    import importlib.util
    package = tmp_path / "anylazyload"
    package.mkdir()
    body = "import builtins\nbuiltins._anyfem_lazy_executed = True\n__version__ = '3.0'\n"
    (package / "__init__.py").write_text(body, encoding="utf-8")
    spec = importlib.util.spec_from_file_location("anylazyload", package / "__init__.py",
                                                  submodule_search_locations=[str(package)])
    spec.loader = importlib.util.LazyLoader(spec.loader)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # deferred: the body runs on first attribute access
    try:
        row = _row(lp.collect_loaded_packages({"anylazyload": module}), "anylazyload")
        assert not hasattr(builtins, "_anyfem_lazy_executed")
        assert row.loaded and row.import_path == str(package)
    finally:
        if hasattr(builtins, "_anyfem_lazy_executed"):
            del builtins._anyfem_lazy_executed


# Review round 5 regressions (failed on 23e5be8).

def test_proxies_and_module_subclass_descriptors_are_not_invoked(tmp_path):
    calls = []

    class Proxy:
        @property
        def __class__(self):
            calls.append("__class__")
            return types.ModuleType

        def __getattribute__(self, name):
            calls.append(name)
            raise RuntimeError("proxy touched")

    class Deferred(types.ModuleType):
        @property
        def __dict__(self):
            calls.append("__dict__")
            raise RuntimeError("descriptor touched")

    deferred = Deferred("anydeferred")
    types.ModuleType.__dict__["__dict__"].__get__(deferred)["__file__"] = str(tmp_path / "anydeferred.py")
    report = lp.collect_loaded_packages({"numpy": Proxy(), "PySide6.QtCore": Proxy(), "anydeferred": deferred})
    assert calls == []
    row = _row(report, "numpy")
    assert row.loaded and row.version == lp.UNKNOWN and row.import_path == ""
    assert report.qt_runtime == lp.UNKNOWN
    assert _row(report, "anydeferred").import_path == str(tmp_path / "anydeferred.py")


def test_package_paths_are_read_without_import_machinery(tmp_path, monkeypatch):
    import importlib
    calls = []

    class Recorder:
        def find_spec(self, *args, **kwargs):
            calls.append(("find_spec", args[0]))
            return None

        def find_distributions(self, *args, **kwargs):
            calls.append(("find_distributions",))
            return iter(())

    namespace_root = tmp_path / "ns"
    (namespace_root / "anynspkg").mkdir(parents=True)
    monkeypatch.syspath_prepend(str(namespace_root))
    importlib.invalidate_caches()
    package = importlib.import_module("anynspkg")
    try:
        assert type(package.__path__).__name__ == "_NamespacePath"
        monkeypatch.syspath_prepend(str(tmp_path / "later"))  # iteration would now recalculate
        monkeypatch.setattr(sys, "meta_path", [Recorder(), *sys.meta_path])

        class CustomPath:
            def __iter__(self):
                calls.append(("iter",))
                return iter([str(tmp_path)])

        custom = types.ModuleType("anycustompath")
        custom.__path__ = CustomPath()
        report = lp.collect_loaded_packages({"anynspkg": package, "anycustompath": custom, "numpy": sys.modules.get("numpy")})
    finally:
        sys.modules.pop("anynspkg", None)
    assert calls == []
    assert Path(_row(report, "anynspkg").import_path) == namespace_root / "anynspkg"
    assert all(row.module != "anycustompath" for row in report.rows)  # no inert location: not listed


# Review round 6 regressions (failed on 28659e6).

def test_path_types_are_compared_by_identity_and_impostors_rejected(tmp_path):
    calls = []

    class Meta(type):
        def __eq__(cls, other):
            calls.append("__eq__")
            return False

        __hash__ = type.__hash__

        def __getattribute__(cls, name):
            if name in ("__name__", "__module__"):
                calls.append(name)
            return super().__getattribute__(name)

    class Odd(metaclass=Meta):
        pass

    class _NamespacePath:  # look-alike of importlib's class
        @property
        def __dict__(self):
            calls.append("__dict__")
            return {"_path": [str(tmp_path)]}

    _NamespacePath.__module__ = "importlib._bootstrap_external"
    odd, impostor = types.ModuleType("anyodd"), types.ModuleType("anyimpostor")
    odd.__path__, impostor.__path__ = Odd(), _NamespacePath()
    report = lp.collect_loaded_packages({"anyodd": odd, "anyimpostor": impostor})
    assert calls == []
    assert all(row.module not in ("anyodd", "anyimpostor") for row in report.rows)


def test_only_exact_string_sys_path_entries_reach_metadata_discovery(monkeypatch):
    import numpy  # noqa: F401  (a real loaded row that needs metadata discovery)
    calls = []

    class Entry:
        def __hash__(self):
            calls.append("__hash__")
            return 1

        def __fspath__(self):
            calls.append("__fspath__")
            return "."

    class Text(str):
        def __hash__(self):
            calls.append("str.__hash__")
            return str.__hash__(self)

    monkeypatch.setattr(sys, "path", [Entry(), Text("."), *sys.path])
    row = _row(lp.collect_loaded_packages(), "numpy")
    assert calls == []
    assert row.version_source == "metadata (numpy)"


def test_qt_runtime_comes_only_from_the_real_builtin(tmp_path):
    calls = []
    fake = types.ModuleType("PySide6.QtCore")
    fake.qVersion = lambda: calls.append("qVersion") or "9.9.9"
    assert lp.collect_loaded_packages({"PySide6.QtCore": fake}).qt_runtime == lp.UNKNOWN
    assert calls == []
    from PySide6 import QtCore
    assert lp.collect_loaded_packages({"PySide6.QtCore": QtCore}).qt_runtime == QtCore.qVersion()
    assert lp.collect_loaded_packages({}, qt_runtime="6.1.2").qt_runtime == "6.1.2"
