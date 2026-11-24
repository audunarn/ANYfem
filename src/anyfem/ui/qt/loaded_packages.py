"""Help > Loaded packages: what this session actually imported, and from where.

ANYfem normally loads its ecosystem through ``.ecosystem`` junctions or sibling
source checkouts, so an installed distribution's metadata can describe a copy
other than the one in ``sys.modules``.  The collector therefore reports metadata
only when that distribution is shown to own the loaded path; otherwise it falls
back to the loaded module's own ``__version__`` and finally to ``unknown``.
Everything is collected only when the dialog opens, on a worker thread.

Inspection contract: collection never imports a package.  It reads module
namespaces through ``ModuleType``'s own descriptor (no ``__getattr__``,
LazyLoader or proxy hooks), package paths only from plain lists/tuples or
importlib's cached namespace path, distribution metadata only through the
standard file-system finder (no ``sys.meta_path`` dispatch), and ``.git`` /
``pyproject.toml`` as files.  Objects outside these standard structures are
reported as unknown rather than inspected, and any failure stays in its row.

Metadata ownership contract (owner, 2026-11-24: "standard layouts only"):
installed metadata is credited to the loaded copy only when a distribution of
that name has a RECORD whose three-field rows list the loaded module file
(relative to the distribution base, or absolute), or is a PEP 610 editable
install whose absolute ``file:`` URL is the loaded copy's own project root.
All same-name distributions on ``sys.path`` are examined and disagreements
flagged.  Out of scope, reported as not owned rather than guessed: egg-info
and zip-backed metadata, malformed RECORD rows, aliased paths (symlinks,
junctions, case-sensitive Windows folders), RECORD file names containing
line breaks, relative editable URLs, and projects nested in other checkouts
or virtual environments.
Packages this session has not imported are listed as not loaded.
"""
from __future__ import annotations

import email.message  # noqa: F401  importlib.metadata imports these lazily on its first read;
import email.parser  # noqa: F401   load them with this module so collection itself imports nothing
import base64
import csv
import hashlib
import io
import json
import os
import re
import sys
import types
import time
import tomllib
from dataclasses import dataclass, field
from importlib import metadata
from pathlib import Path, PurePath
from urllib.parse import unquote, urlparse

import encodings.cp437  # noqa: F401  zipfile decodes legacy names with it when sys.path holds a zip
import zipfile  # noqa: F401

for _lazy in ("importlib.metadata._adapters", "zipfile._path"):  # private, imported lazily by the stdlib
    try:
        __import__(_lazy)
    except ImportError:  # pragma: no cover - layout of another Python
        pass

UNKNOWN = "unknown"

# (top-level module, display name, distribution names to try).  The ANY*
# distribution names follow each repository's pyproject ``name``.
ECOSYSTEM_MODULES = (
    ("anyfem", "ANYfem", ("ANYfem",)),
    ("anygeometry", "ANYgeometry", ("ANYgeometry",)),
    ("anymesher", "ANYmesher (ANYmesh)", ("ANYmesher",)),
    ("anyfesolver", "ANYfeSolver", ("ANYfeSolver",)),
    ("anyfesolver_results", "ANYfeSolver results", ("ANYfeSolver-results",)),
    ("anysolver", "ANYsolver", ("ANYsolver",)),
    ("anysolver_results", "ANYsolver results", ("ANYsolver-results",)),
    ("any3dview", "ANY3dView", ("ANY3dView",)),
    ("anytk3d", "ANYtk3D", ("ANYtk3D",)),
    ("anyfileio", "ANYfileIO", ("ANYfileio",)),
    ("anypost", "ANYpost", ("ANYpost",)),
    ("anymaterial", "ANYmaterial", ("ANYmaterial",)),
    ("anyloads", "ANYloads", ("ANYloads",)),
    ("anysection", "ANYsection", ("anysection",)),
    ("anysolid", "ANYsolid", ("ANYsolid",)),
    ("anyhydro", "ANYhydro", ("anyhydro",)),
    ("anystability", "ANYstability", ("ANYstability",)),
    ("anyfatigue", "ANYfatigue", ("ANYfatigue",)),
    ("anymarine", "ANYmarine", ("ANYmarine",)),
)

THIRD_PARTY_MODULES = (
    ("numpy", "numpy", ("numpy",)),
    ("scipy", "scipy", ("scipy",)),
    ("PySide6", "PySide6", ("PySide6", "PySide6-Essentials")),
    ("shiboken6", "shiboken6", ("shiboken6",)),
    ("wgpu", "wgpu", ("wgpu",)),
    ("rendercanvas", "rendercanvas", ("rendercanvas",)),
    ("moderngl", "moderngl", ("moderngl",)),
    ("h5py", "h5py", ("h5py",)),
    ("matplotlib", "matplotlib", ("matplotlib",)),
    ("ezdxf", "ezdxf", ("ezdxf",)),
    ("gmsh", "gmsh", ("gmsh",)),
    ("meshio", "meshio", ("meshio",)),
    ("OCP", "OCP", ("cadquery-ocp", "OCP")),
    ("numba", "numba", ("numba",)),
    ("pandas", "pandas", ("pandas",)),
    ("psutil", "psutil", ("psutil",)),
    ("PIL", "Pillow", ("Pillow",)),
)

_INSTALL_DIRS = {"site-packages", "dist-packages"}


@dataclass(frozen=True)
class PackageRow:
    group: str
    name: str
    module: str
    loaded: bool
    version: str = ""
    version_source: str = ""
    import_path: str = ""
    real_path: str = ""
    commit: str = ""
    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class PackageReport:
    python: str
    executable: str
    qt_runtime: str
    rows: tuple[PackageRow, ...]
    elapsed_ms: float
    notes: tuple[str, ...] = field(default_factory=tuple)

    def text(self) -> str:
        """Plain text for the clipboard; one block per package."""
        lines = [
            "ANYfem loaded packages",
            f"Python: {self.python}",
            f"Executable: {self.executable}",
            f"Qt runtime: {self.qt_runtime}",
            f"Collected in {self.elapsed_ms:.1f} ms",
        ]
        lines.extend(self.notes)
        for group in ("ANY ecosystem", "Third party"):
            lines.append("")
            lines.append(f"[{group}]")
            for row in (r for r in self.rows if r.group == group):
                if not row.loaded:
                    lines.append(f"{row.name}: not loaded in this session")
                    continue
                lines.append(f"{row.name}: {row.version} ({row.version_source})")
                lines.append(f"    module: {row.module}")
                lines.append(f"    loaded from: {row.import_path}")
                if row.real_path and row.real_path != row.import_path:
                    lines.append(f"    resolves to: {row.real_path}")
                if row.commit:
                    lines.append(f"    commit: {row.commit}")
                for note in row.notes:
                    lines.append(f"    note: {note}")
        return "\n".join(lines) + "\n"


def _norm(path) -> str:
    return os.path.normcase(os.path.abspath(str(path)))


def _real(path) -> str:
    try:
        return str(Path(path).resolve())
    except (OSError, RuntimeError):
        return str(path)


def _namespace(module) -> dict:
    """The module's own ``__dict__``, read without any overridden lookup.

    ``getattr``/``hasattr``/``vars`` can run a module-level ``__getattr__`` or
    a ``importlib.util.LazyLoader`` module's ``__getattribute__``, which
    executes (imports) the deferred module.  The real type is checked with
    ``type()`` (never the instance's ``__class__``) and the dict is read through
    ModuleType's own ``__dict__`` descriptor, so subclass descriptors and
    proxies are bypassed; anything else in ``sys.modules`` yields ``{}``.
    """
    try:
        if not issubclass(type(module), types.ModuleType):
            return {}
        namespace = _MODULE_DICT.__get__(module, types.ModuleType)
    except Exception:
        return {}
    # Any dict subclass (PySide6 uses one) is read only via dict.get/__contains__,
    # never through its own possibly lazy lookup methods.
    return namespace if issubclass(type(namespace), dict) else {}


_MODULE_DICT = types.ModuleType.__dict__["__dict__"]


def _filesystem_distributions(name: str) -> list:
    """Every distribution called ``name`` on ``sys.path``, in path order, from
    the standard file-system/zip metadata finder only.

    ``metadata.distribution()`` dispatches to every ``sys.meta_path`` finder's
    ``find_distributions``, which may run arbitrary code; custom providers are
    treated as unavailable here.
    """
    entries = sys.path if type(sys.path) is list else []
    found = []
    for entry in list(entries):
        if type(entry) is not str or "\x00" in entry:  # exact strings only: no __hash__/__fspath__ hooks
            continue
        try:  # one unusable entry must not hide distributions found on the others
            context = metadata.DistributionFinder.Context(name=name, path=[entry])
            found.extend(metadata.MetadataPathFinder.find_distributions(context))
        except Exception:
            continue
    return found


try:  # the real class, so that look-alikes are not trusted
    from importlib._bootstrap_external import _NamespacePath as _NAMESPACE_PATH
except ImportError:  # pragma: no cover - layout of another Python
    _NAMESPACE_PATH = None


def _inert_paths(paths) -> list:
    """Entries of a package ``__path__`` without running import machinery.

    Lists/tuples are read directly; importlib's namespace path is read from
    its cached list (iterating it can recalculate through path hooks and
    finders).  Any other object is not inspected.
    """
    kind = type(paths)  # identity checks only: never ==, never the type's attributes
    if kind is list or kind is tuple:
        return [entry for entry in paths if type(entry) is str]
    if _NAMESPACE_PATH is not None and kind is _NAMESPACE_PATH:
        try:
            cached = dict.get(object.__getattribute__(paths, "__dict__"), "_path")
        except Exception:
            return []
        return [entry for entry in cached if type(entry) is str] if type(cached) is list else []
    return []


def _module_location(module) -> str:
    """The file or package directory the module was imported from, or ''."""
    namespace = _namespace(module)
    file = dict.get(namespace, "__file__")
    if type(file) is str and file:
        return str(Path(file).parent) if dict.__contains__(namespace, "__path__") else file
    paths = _inert_paths(dict.get(namespace, "__path__"))
    return paths[0] if paths else ""


def _file_url_path(url: str) -> str:
    """Local or UNC path of a ``file:`` URL; '' when it is not one."""
    parsed = urlparse(url)
    if parsed.scheme != "file":
        return ""
    path = unquote(parsed.path)
    host = parsed.netloc
    if host and host.lower() != "localhost":
        return "//" + host + path  # UNC: \\server\share\...
    if os.name == "nt":
        if re.match(r"^/[A-Za-z]:", path):
            path = path[1:]  # file:///C:/x -> C:/x (Windows only; on POSIX /C:/x is a real path)
        # Absolute means drive-qualified here; "/project" would resolve against the current drive.
        return path if re.match(r"^[A-Za-z]:[/\\]", path) else ""
    # Relative forms (e.g. ``file:.``) would resolve against the working directory.
    return path if path.startswith("/") else ""


def _editable_root(dist) -> str:
    """The project directory of a PEP 610 editable install, or ''."""
    raw = dist.read_text("direct_url.json")
    if not raw:
        return ""
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("direct_url.json is not an object")
    dir_info = data.get("dir_info")
    if not (isinstance(dir_info, dict) and dir_info.get("editable") is True):
        return ""
    url = data.get("url")
    return _file_url_path(url) if isinstance(url, str) else ""


_RECORD_SIZE = re.compile(r"[0-9]*")


def _record_hash_ok(field: str) -> bool:
    """Empty, or ``algorithm=digest`` as the PyPA RECORD spec writes it: an
    algorithm from ``hashlib.algorithms_guaranteed`` and the canonical unpadded
    urlsafe-base64 encoding of a digest of that algorithm's size (any non-empty
    size for the variable-length SHAKE algorithms; their digest is not
    constructed, so a runtime block on SHAKE does not affect them).  Anything
    else, including a fixed-size algorithm the runtime refuses, makes only this
    row non-evidence."""
    if not field:
        return True
    algorithm, separator, digest = field.partition("=")
    if not separator or algorithm not in hashlib.algorithms_guaranteed:
        return False
    if not re.fullmatch(r"[A-Za-z0-9_\-]+", digest):
        return False
    try:
        raw = base64.urlsafe_b64decode(digest + "=" * (-len(digest) % 4))
        if base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii") != digest:
            return False  # non-canonical: unused bits set
        if algorithm.startswith("shake_"):
            return len(raw) > 0
        return len(raw) == hashlib.new(algorithm).digest_size
    except Exception:  # a decoding error, or a runtime that blocks the algorithm
        return False


def _records_file(dist, real_file: str) -> bool:
    """True if the distribution's RECORD lists the loaded module file.

    RECORD is parsed as CSV (quoted names, escaped quotes, embedded newlines);
    each row's first field is a path relative to the distribution's base, or
    absolute (PEP 376 / PyPA spec).  Paths are compared after normalisation,
    without touching the file system per entry.
    """
    if not real_file:
        return False
    base = dist.locate_file("")
    if not isinstance(base, PurePath):  # e.g. zip-backed metadata: not compared
        return False
    record = dist.read_text("RECORD")
    if not record:
        # No RECORD (egg-info): its SOURCES.txt is relative to the project root,
        # not to this base, and Distribution.files would stat every entry.
        return False
    base = _real(base)
    target = os.path.normcase(os.path.normpath(real_file))
    name = os.path.basename(target)
    try:
        rows = list(csv.reader(io.StringIO(record), strict=True))
    except csv.Error:
        return False  # malformed RECORD: no evidence
    for row in rows:
        if len(row) != 3 or not row[0] or not _record_hash_ok(row[1]) or not _RECORD_SIZE.fullmatch(row[2]):
            continue  # path,hash,size: anything else is not a standard row
        entry = row[0]
        if "\r" in entry or "\n" in entry:
            continue  # read_text() folds line endings, so such a name cannot be compared exactly
        if os.path.normcase(os.path.basename(entry.replace("/", os.sep))) != name:
            continue  # cheap filter before path normalisation
        path = entry if os.path.isabs(entry) else os.path.join(base, entry)
        if ".." in Path(path).parts:
            path = _real(path)  # lexical normalisation of '..' is wrong through symlinks
        if os.path.normcase(os.path.normpath(path)) == target:
            return True
    return False


def _owned_metadata(module_name: str, real_location: str, candidates, real_file: str = ""):
    """Owned installed metadata for the loaded copy.

    Returns (owned, notes): ``owned`` lists (distribution name, version) for
    every candidate whose files place ``module_name`` at the loaded location, or
    whose PEP 610 editable project contains it, in candidate order.  Candidates
    describing another copy, or whose metadata cannot be read, become notes.
    """
    owned, notes, project = [], [], {}
    found = []
    for candidate in candidates:
        try:
            found.extend((candidate, dist) for dist in _filesystem_distributions(candidate))
        except Exception as error:
            notes.append(f"metadata for {candidate} could not be read ({type(error).__name__})")
    for candidate, dist in found:
        try:
            fields = dist.metadata  # parsed once; ``dist.version`` would parse it again
            version = str((fields.get("Version") if fields is not None else None) or "").strip()
            name = str((fields.get("Name") if fields is not None else None) or candidate)
            # Owned only if its recorded files list the loaded module file (a
            # path constructed by locate_file() alone proves nothing), or it is
            # the editable install of the loaded copy's own project.
            match = _records_file(dist, real_file)
            if not match:
                root = _editable_root(dist)
                # The editable project must be the loaded copy's own project, not
                # merely an ancestor of a separate nested checkout.  A UNC root is
                # compared as written (resolving it would contact the server).
                root = root if root.startswith("//") else _real(root) if root else ""
                if root and "own" not in project:  # walk up once per row, only when needed
                    project["own"] = _project_root(real_location)
                own = project.get("own", "")
                match = bool(root) and bool(own) and _norm(own) == _norm(root)
        except Exception as error:
            notes.append(f"metadata for {candidate} could not be read ({type(error).__name__})")
            continue
        if match and version:
            owned.append((name, version))
        elif match:
            notes.append(f"installed metadata {name} owns the loaded copy but has no version")
        else:
            notes.append(f"installed metadata {name} {version or '(no version)'} "
                         f"is not shown to own the loaded copy")
    return owned, notes


_PROJECT_MARKERS = ("pyproject.toml", "setup.py", "setup.cfg", ".git")


def _project_root(real_location: str) -> str:
    """Nearest enclosing directory that is a project or checkout root, or ''."""
    directory = Path(real_location if os.path.isdir(real_location) else os.path.dirname(real_location))
    for candidate in (directory, *directory.parents):
        if any((candidate / marker).exists() for marker in _PROJECT_MARKERS):
            return str(candidate)
    return ""


def _canonical(name: str) -> str:
    return re.sub(r"[-_.]+", "-", str(name)).lower()


def _checkout_version(real_location: str, candidates) -> tuple[str, str]:
    """Static ``[project].version`` of the source checkout holding the module.

    Walks up from the loaded location to the nearest ``pyproject.toml`` (not
    past the git root) and accepts it only if its project name is one of the
    expected distribution names; installed copies are never treated as
    checkouts.  Returns (version, pyproject path) or ('', '').  A pyproject
    that cannot be read or has an unexpected shape raises.
    """
    directory = Path(real_location if os.path.isdir(real_location) else os.path.dirname(real_location))
    if any(part.lower() in _INSTALL_DIRS for part in directory.parts):
        return "", ""
    wanted = {_canonical(name) for name in candidates}
    for candidate in (directory, *directory.parents):
        pyproject = candidate / "pyproject.toml"
        if pyproject.is_file():
            data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
            project = data.get("project", {})
            if not isinstance(project, dict):
                raise ValueError("[project] is not a table")
            name, version = project.get("name"), project.get("version")
            if isinstance(name, str) and _canonical(name) in wanted and isinstance(version, str) and version.strip():
                return version.strip(), str(pyproject)
            return "", ""
        if (candidate / ".git").exists():
            return "", ""
    return "", ""


def _git_head(start: str, cache: dict) -> str:
    """HEAD of the nearest enclosing git checkout, read from files only."""
    directory = Path(start if os.path.isdir(start) else os.path.dirname(start))
    if any(part.lower() in _INSTALL_DIRS for part in directory.parts):
        return ""
    for candidate in (directory, *directory.parents):
        dotgit = candidate / ".git"
        if not dotgit.exists():
            continue
        key = _norm(candidate)
        if key not in cache:
            try:
                cache[key] = _read_head(candidate, dotgit)
            except (OSError, UnicodeDecodeError, ValueError):
                cache[key] = f"{UNKNOWN} (unreadable .git) in {candidate}"
        return cache[key]
    return ""


_SHA = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")
def _safe_refname(ref: str) -> bool:
    """A relative ``refs/...`` name that stays inside the git directory.

    Only path escapes are refused (rooted, drive-qualified, UNC, ``.``/``..``
    or empty components, control characters); git refuses ``:`` and ``\\``
    in ref names anyway.  Other characters git allows are kept.
    """
    if not ref.startswith("refs/") or "\\" in ref or ":" in ref:
        return False
    if any(ord(character) < 32 or character == "\x7f" for character in ref):
        return False
    return all(part and not part.startswith(".") for part in ref.split("/"))


def _resolve_ref(ref: str, gitdir: Path, common: Path, depth: int = 0) -> str:
    """Commit of ``ref``: loose refs (worktree, then common) before packed refs,
    following symbolic refs up to a fixed depth; '' when it does not resolve."""
    if depth > 8 or not _safe_refname(ref):
        return ""
    for base in (gitdir, common):
        loose = base / ref
        if loose.is_file():
            value = loose.read_text(encoding="utf-8").strip()
            if _SHA.fullmatch(value):
                return value
            if value.startswith("ref:"):
                return _resolve_ref(value[len("ref:"):].strip(), gitdir, common, depth + 1)
            return ""
    packed = common / "packed-refs"
    if packed.is_file():
        for line in packed.read_text(encoding="utf-8").splitlines():
            parts = line.split()
            if len(parts) == 2 and parts[1] == ref and _SHA.fullmatch(parts[0]):
                return parts[0]
    return ""


def _read_head(root: Path, dotgit: Path) -> str:
    gitdir = dotgit
    if dotgit.is_file():
        text = dotgit.read_text(encoding="utf-8").strip()
        if not text.startswith("gitdir:"):
            return ""
        gitdir = Path(text[len("gitdir:"):].strip())
        if not gitdir.is_absolute():
            gitdir = root / gitdir
    common = gitdir
    if (gitdir / "commondir").is_file():
        common_text = (gitdir / "commondir").read_text(encoding="utf-8").strip()
        common = Path(common_text) if os.path.isabs(common_text) else gitdir / common_text
    head = (gitdir / "HEAD").read_text(encoding="utf-8").strip()
    if _SHA.fullmatch(head):
        return f"{head[:12]} (detached) in {root}"
    if not head.startswith("ref:"):
        return ""
    ref = head[len("ref:"):].strip()
    if not _safe_refname(ref):
        return f"{UNKNOWN} (unsupported HEAD) in {root}"
    branch = ref.removeprefix("refs/heads/")
    sha = _resolve_ref(ref, gitdir, common)
    if sha:
        return f"{sha[:12]} ({branch}) in {root}"
    return f"{UNKNOWN} ({branch}, unresolved) in {root}"


def _guarded(notes, label, function, *args, default):
    """Run one provenance read; a failure becomes a note, never an exception."""
    try:
        return function(*args)
    except Exception as error:
        notes.append(f"{label} could not be read ({type(error).__name__})")
        return default


def _row(group, module_name, display, candidates, modules, git_cache, with_commit):
    module = modules.get(module_name)
    if module is None:
        return PackageRow(group, display, module_name, False)
    notes = []
    namespace = _guarded(notes, "module namespace", _namespace, module, default={})
    file = dict.get(namespace, "__file__")
    if type(file) is not str or not file:
        spread = _inert_paths(dict.get(namespace, "__path__"))
        if len({_norm(entry) for entry in spread}) > 1:
            return PackageRow(group, display, module_name, True, UNKNOWN,
                              "namespace package spanning several locations", "; ".join(spread), "", "",
                              (f"{len(spread)} locations; no single version or commit applies",))
    location = _guarded(notes, "module location", _module_location, module, default="")
    real = _real(location) if location else ""
    real_file = _real(file) if type(file) is str and file else ""
    owned, metadata_notes = (_guarded(notes, "installed metadata", _owned_metadata, module_name, real,
                                      candidates, real_file, default=([], [])) if real else ([], []))
    try:  # the module's own namespace only: a lazy module __getattr__ could import
        attribute = dict.get(_namespace(module), "__version__")
    except Exception:
        attribute = None
    attribute = str.strip(attribute) if type(attribute) is str else ""
    checkout, _pyproject = (_guarded(notes, "pyproject.toml", _checkout_version, real, candidates,
                                     default=("", "")) if real else ("", ""))
    # Source checkouts: pyproject.toml is current, while editable/egg-info
    # metadata is a snapshot from install time.  Otherwise owned metadata,
    # then the loaded module's __version__.  Every disagreeing source is shown.
    sources = []
    if checkout:
        sources.append((checkout, "pyproject.toml of the source checkout"))
    sources.extend((version, f"metadata ({name})") for name, version in owned)
    if attribute:
        sources.append((attribute, "module __version__"))
    if sources:
        version, source = sources[0]
        conflicts = [f"{label} says {value}" for value, label in sources[1:] if value != version]
        if conflicts:
            notes.append("version sources disagree: " + "; ".join(conflicts))
    else:
        version, source = UNKNOWN, "no pyproject, owned metadata or __version__"
    if not owned:
        notes.extend(metadata_notes)
    else:
        notes.extend(note for note in metadata_notes if "could not be read" in note)
    if not location:
        notes.append("module has no file location")
    commit = (_guarded(notes, "git HEAD", _git_head, real, git_cache, default="")
              if with_commit and real else "")
    return PackageRow(group, display, module_name, True, version, source,
                      location, real, commit, tuple(notes))


def _safe_row(group, module_name, display, candidates, modules, git_cache, with_commit):
    try:
        return _row(group, module_name, display, candidates, modules, git_cache, with_commit)
    except Exception as error:
        return PackageRow(group, display, module_name, module_name in modules, UNKNOWN,
                          "provenance could not be read",
                          notes=(f"{type(error).__name__}: {str(error)[:200]}",))


def _extra_ecosystem_modules(modules, known):
    """Loaded top-level ``any*`` source packages that the table does not name."""
    extra = []
    for name, module in list(modules.items()):
        if type(name) is not str or "." in name or name in known or not name.lower().startswith("any"):
            continue
        try:
            location = _module_location(module)
        except Exception:
            continue
        if not location or any(part.lower() in _INSTALL_DIRS for part in Path(location).parts):
            continue  # e.g. third-party ``anyio`` from site-packages
        extra.append((name, name, (name,)))
    return sorted(extra)


def _qt_runtime(qtcore) -> str:
    """``qVersion()`` only if it is the extension's own builtin bound to that module."""
    if qtcore is None:
        return UNKNOWN
    function = dict.get(_namespace(qtcore), "qVersion")
    if type(function) is not types.BuiltinFunctionType or function.__self__ is not qtcore:
        return UNKNOWN
    try:
        value = function()
    except Exception:
        return UNKNOWN
    return value if type(value) is str and value else UNKNOWN


def collect_loaded_packages(modules=None, qt_runtime=None) -> PackageReport:
    """Describe the loaded packages without importing anything new."""
    start = time.perf_counter()
    modules = dict(sys.modules if modules is None else modules)
    git_cache: dict = {}
    rows = []
    known = {entry[0] for entry in ECOSYSTEM_MODULES}
    for module_name, display, candidates in (*ECOSYSTEM_MODULES, *_extra_ecosystem_modules(modules, known)):
        rows.append(_safe_row("ANY ecosystem", module_name, display, candidates, modules, git_cache, True))
    for module_name, display, candidates in THIRD_PARTY_MODULES:
        rows.append(_safe_row("Third party", module_name, display, candidates, modules, git_cache, False))
    # Loaded first, then the not-loaded names, each in table order.
    rows.sort(key=lambda row: (row.group != "ANY ecosystem", not row.loaded))
    if type(qt_runtime) is not str or not qt_runtime:  # normally read here, on the worker thread
        qt_runtime = _qt_runtime(modules.get("PySide6.QtCore"))
    elapsed = (time.perf_counter() - start) * 1000.0
    notes = ("Commits are the checkout HEAD read from .git; uncommitted changes are not checked.",
             SCOPE_NOTE)
    return PackageReport(sys.version.split()[0], sys.executable, qt_runtime,
                         tuple(rows), elapsed, notes)


SCOPE_NOTE = ("Installed metadata counts only for standard layouts (a wheel RECORD listing the loaded file, "
              "or an editable install whose absolute file: URL is the loaded copy's own project root); "
              "other layouts show the checkout or module version, or unknown.")
