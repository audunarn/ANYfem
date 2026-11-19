"""Help > Loaded packages: what this session actually imported, and from where.

ANYfem normally loads its ecosystem through ``.ecosystem`` junctions or sibling
source checkouts, so an installed distribution's metadata can describe a copy
other than the one in ``sys.modules``.  The collector therefore reports metadata
only when that distribution is shown to own the loaded path; otherwise it falls
back to the loaded module's own ``__version__`` and finally to ``unknown``.  It
never imports anything: packages that this session has not imported are listed
as not loaded.  Everything is collected only when the dialog opens, on a
worker thread.
"""
from __future__ import annotations

import email.message  # noqa: F401  importlib.metadata imports these lazily on its first read;
import email.parser  # noqa: F401   load them with this module so collection itself imports nothing
import json
import os
import re
import sys
import time
import tomllib
from dataclasses import dataclass, field
from importlib import metadata
from pathlib import Path
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


def _module_location(module) -> str:
    """The file or package directory the module was imported from, or ''."""
    # The module's own namespace only: getattr/hasattr could run a lazy
    # module ``__getattr__`` (and so import) for a missing attribute.
    namespace = vars(module)
    file = namespace.get("__file__")
    if isinstance(file, str) and file:
        return str(Path(file).parent) if "__path__" in namespace else file
    paths = namespace.get("__path__")
    first = next(iter(list(paths)), None) if paths is not None else None
    return str(first) if isinstance(first, str) else ""


def _is_inside(child: str, parent: str) -> bool:
    child, parent = _norm(child), _norm(parent)
    return child == parent or child.startswith(parent.rstrip(os.sep) + os.sep)


def _file_url_path(url: str) -> str:
    """Local or UNC path of a ``file:`` URL; '' when it is not one."""
    parsed = urlparse(url)
    if parsed.scheme != "file":
        return ""
    path = unquote(parsed.path)
    host = parsed.netloc
    if host and host.lower() != "localhost":
        return "//" + host + path  # UNC: \\server\share\...
    if re.match(r"^/[A-Za-z]:", path):
        path = path[1:]
    return path


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


def _owned_metadata(module_name: str, real_location: str, candidates):
    """Owned installed metadata for the loaded copy.

    Returns (owned, notes): ``owned`` lists (distribution name, version) for
    every candidate whose files place ``module_name`` at the loaded location, or
    whose PEP 610 editable project contains it, in candidate order.  Candidates
    describing another copy, or whose metadata cannot be read, become notes.
    """
    owned, notes = [], []
    for candidate in candidates:
        try:
            dist = metadata.distribution(candidate)
        except metadata.PackageNotFoundError:
            continue
        except Exception as error:
            notes.append(f"metadata for {candidate} could not be read ({type(error).__name__})")
            continue
        try:
            fields = dist.metadata  # parsed once; ``dist.version`` would parse it again
            version = str((fields.get("Version") if fields is not None else None) or "").strip()
            name = str((fields.get("Name") if fields is not None else None) or candidate)
            match = False
            for relative in (module_name, module_name + ".py"):
                if _norm(_real(dist.locate_file(relative))) == _norm(real_location):
                    match = True
                    break
            if not match:
                root = _editable_root(dist)
                # The editable project must be the loaded copy's own project, not
                # merely an ancestor of a separate nested checkout.  A UNC root is
                # compared as written (resolving it would contact the server).
                root = root if root.startswith("//") else _real(root) if root else ""
                own = _project_root(real_location)
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
                         f"describes another copy, not the loaded one")
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
    location = _guarded(notes, "module location", _module_location, module, default="")
    real = _real(location) if location else ""
    owned, metadata_notes = (_guarded(notes, "installed metadata", _owned_metadata, module_name, real,
                                      candidates, default=([], [])) if real else ([], []))
    try:  # the module's own namespace only: a lazy module __getattr__ could import
        attribute = vars(module).get("__version__")
    except Exception:
        attribute = None
    attribute = attribute.strip() if isinstance(attribute, str) else ""
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
        if "." in name or name in known or not name.lower().startswith("any"):
            continue
        try:
            location = _module_location(module)
        except Exception:
            continue
        if not location or any(part.lower() in _INSTALL_DIRS for part in Path(location).parts):
            continue  # e.g. third-party ``anyio`` from site-packages
        extra.append((name, name, (name,)))
    return sorted(extra)


def collect_loaded_packages(modules=None) -> PackageReport:
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
    qt_runtime = UNKNOWN
    qtcore = modules.get("PySide6.QtCore")
    if qtcore is not None and callable(getattr(qtcore, "qVersion", None)):
        try:
            qt_runtime = str(qtcore.qVersion())
        except Exception:
            qt_runtime = UNKNOWN
    elapsed = (time.perf_counter() - start) * 1000.0
    notes = ("Commits are the checkout HEAD read from .git; uncommitted changes are not checked.",)
    return PackageReport(sys.version.split()[0], sys.executable, qt_runtime,
                         tuple(rows), elapsed, notes)


COLUMNS = ("Package", "Version", "Version source", "Loaded from", "Commit", "Notes")


def _fill_table(table, report):
    from PySide6.QtGui import QColor
    from PySide6.QtWidgets import QHeaderView, QTableWidgetItem

    table.setRowCount(len(report.rows))
    for index, row in enumerate(report.rows):
        if row.loaded:
            resolved = row.real_path and _norm(row.real_path) != _norm(row.import_path)
            path = row.import_path + (f"\n→ {row.real_path}" if resolved else "")
            flagged = any(note.startswith("version sources disagree") for note in row.notes)
            values = (row.name, ("⚠ " if flagged else "") + row.version, row.version_source,
                      path, row.commit, "; ".join(row.notes))
        else:
            values = (row.name, "—", "not loaded in this session", "", "", "")
        for column, value in enumerate(values):
            # The commit cell names the checkout in its tooltip and the copied text.
            item = QTableWidgetItem(value.split(" in ", 1)[0] if column == 4 else value)
            item.setToolTip(value)
            if not row.loaded:
                item.setForeground(QColor(128, 128, 128))
            table.setItem(index, column, item)
    header = table.horizontalHeader()
    table.resizeColumnsToContents()
    header.setSectionResizeMode(QHeaderView.Interactive)
    header.setStretchLastSection(True)
    table.resizeRowsToContents()


def open_loaded_packages(window):
    """Show the dialog at once; collect on a worker thread and fill it in.

    Provenance reads touch the file system (``sys.path`` scans, ``.git``,
    ``pyproject.toml``), which can stall on a network share, so none of it runs
    on the GUI thread.  ``sys.modules`` is snapshotted here, before the thread
    starts.  A dialog closed early simply drops the late result.
    """
    import threading
    from html import escape

    from PySide6.QtCore import Qt, QTimer
    from PySide6.QtWidgets import (QAbstractItemView, QDialog, QDialogButtonBox, QLabel,
                                   QPushButton, QTableWidget, QVBoxLayout)

    opened = time.perf_counter()
    modules = dict(sys.modules)
    dialog = QDialog(window)
    dialog.setObjectName("LoadedPackagesDialog")
    dialog.setWindowTitle("Loaded packages")
    dialog.setAttribute(Qt.WA_DeleteOnClose)
    dialog.report = None
    dialog.error = None
    dialog.ready_ms = None
    layout = QVBoxLayout(dialog)
    summary = QLabel("Collecting loaded packages…", dialog)
    summary.setObjectName("LoadedPackagesSummary")
    summary.setTextFormat(Qt.RichText)  # paths are not markup: only the summary's own <br>/<i> tags
    summary.setWordWrap(True)
    summary.setTextInteractionFlags(Qt.TextSelectableByMouse)
    layout.addWidget(summary)
    table = QTableWidget(0, len(COLUMNS), dialog)
    table.setObjectName("LoadedPackagesTable")
    table.setHorizontalHeaderLabels(COLUMNS)
    table.setEditTriggers(QAbstractItemView.NoEditTriggers)
    table.setSelectionBehavior(QAbstractItemView.SelectRows)
    table.setWordWrap(False)
    table.verticalHeader().setVisible(False)
    layout.addWidget(table)
    buttons = QDialogButtonBox(QDialogButtonBox.Close, dialog)
    copy = QPushButton("Copy to clipboard", dialog)
    copy.setObjectName("CopyLoadedPackages")
    copy.setEnabled(False)
    buttons.addButton(copy, QDialogButtonBox.ActionRole)
    buttons.rejected.connect(dialog.close)
    layout.addWidget(buttons)

    def copy_report():
        if dialog.report is None:
            return
        window.clipboard.copy_text(dialog.report.text())
        try:
            window.set_status("Loaded package list copied to clipboard.")
        except Exception:
            pass

    copy.clicked.connect(copy_report)

    # No Qt object crosses threads: the worker fills a plain box and sets an
    # event; a timer owned by the dialog polls it on the GUI thread, so the
    # poller, its closure and the dialog go away together.
    box = {}
    ready = threading.Event()

    def finished(report, error):
        if error is not None:
            dialog.error = error
            summary.setText(f"Could not collect the package list: {type(error).__name__}: {str(error)[:300]}")
            return
        dialog.report = report
        dialog.ready_ms = (time.perf_counter() - opened) * 1000.0
        summary.setText(
            f"Python {escape(report.python)} — {escape(report.executable)}<br>"
            f"Qt runtime {escape(report.qt_runtime)} — collected in {report.elapsed_ms:.0f} ms (background)<br>"
            f"<i>{escape(report.notes[0])}</i>")
        _fill_table(table, report)
        copy.setEnabled(True)
        layout.activate()  # the summary grew from one line to three

    poller = QTimer(dialog)
    poller.setObjectName("LoadedPackagesPoller")
    poller.setInterval(25)

    def poll():
        if ready.is_set():
            poller.stop()
            finished(box.get("report"), box.get("error"))

    poller.timeout.connect(poll)

    def work():  # touches only the snapshot, the box and the event
        try:
            box["report"] = collect_loaded_packages(modules)
        except Exception as caught:  # collect is guarded per row; this is the last net
            box["error"] = caught
        finally:
            ready.set()

    dialog.resize(1400, 640)
    window._loaded_packages_dialog = dialog
    dialog.show()
    dialog.shown_ms = (time.perf_counter() - opened) * 1000.0
    threading.Thread(target=work, name="anyfem-loaded-packages", daemon=True).start()
    poller.start()
    return dialog
