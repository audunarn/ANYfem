"""Help > Loaded packages: what this session actually imported, and from where.

ANYfem normally loads its ecosystem through ``.ecosystem`` junctions or sibling
source checkouts, so an installed distribution's metadata can describe a copy
other than the one in ``sys.modules``.  The collector therefore reports metadata
only when that distribution is shown to own the loaded path; otherwise it falls
back to the loaded module's own ``__version__`` and finally to ``unknown``.  It
never imports anything: packages that this session has not imported are listed
as not loaded.  Everything is collected only when the dialog opens.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from dataclasses import dataclass, field
from importlib import metadata
from pathlib import Path

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
    file = getattr(module, "__file__", None)
    if file:
        return str(Path(file).parent) if hasattr(module, "__path__") else str(file)
    paths = list(getattr(module, "__path__", None) or ())
    return str(paths[0]) if paths else ""


def _is_inside(child: str, parent: str) -> bool:
    child, parent = _norm(child), _norm(parent)
    return child == parent or child.startswith(parent.rstrip(os.sep) + os.sep)


def _editable_root(dist) -> str:
    """The project directory of a PEP 610 editable install, or ''."""
    try:
        raw = dist.read_text("direct_url.json")
    except Exception:
        return ""
    if not raw:
        return ""
    try:
        data = json.loads(raw)
    except ValueError:
        return ""
    if not (data.get("dir_info") or {}).get("editable"):
        return ""
    url = str(data.get("url") or "")
    if not url.startswith("file:"):
        return ""
    from urllib.parse import unquote, urlparse
    path = unquote(urlparse(url).path)
    if re.match(r"^/[A-Za-z]:", path):
        path = path[1:]
    return path


def _owned_metadata(module_name: str, real_location: str, candidates):
    """Return (distribution name, version, unowned notes) for the loaded copy.

    A distribution counts only if its files place ``module_name`` at the loaded
    location, or it is an editable install whose project contains it.
    """
    unowned = []
    for candidate in candidates:
        try:
            dist = metadata.distribution(candidate)
        except metadata.PackageNotFoundError:
            continue
        except Exception:
            continue
        fields = dist.metadata  # parsed once; ``dist.version`` would parse it again
        version = (fields.get("Version") or "").strip() if fields is not None else ""
        name = (fields.get("Name") if fields is not None else None) or candidate
        owned = False
        for relative in (module_name, module_name + ".py"):
            try:
                located = _real(dist.locate_file(relative))
            except Exception:
                continue
            if _norm(located) == _norm(real_location):
                owned = True
                break
        if not owned:
            root = _editable_root(dist)
            owned = bool(root) and _is_inside(real_location, _real(root))
        if owned and version:
            return name, version, ()
        unowned.append(f"installed metadata {name} {version or '(no version)'} "
                       f"describes another copy, not the loaded one")
    return "", "", tuple(unowned)


def _canonical(name: str) -> str:
    return re.sub(r"[-_.]+", "-", str(name)).lower()


def _checkout_version(real_location: str, candidates) -> tuple[str, str]:
    """Static ``[project].version`` of the source checkout holding the module.

    Walks up from the loaded location to the nearest ``pyproject.toml`` (not
    past the git root) and accepts it only if its project name is one of the
    expected distribution names; installed copies are never treated as
    checkouts.  Returns (version, pyproject path) or ('', '').
    """
    try:
        import tomllib
    except ImportError:  # Python < 3.11
        return "", ""
    directory = Path(real_location if os.path.isdir(real_location) else os.path.dirname(real_location))
    if any(part.lower() in _INSTALL_DIRS for part in directory.parts):
        return "", ""
    wanted = {_canonical(name) for name in candidates}
    for candidate in (directory, *directory.parents):
        pyproject = candidate / "pyproject.toml"
        if pyproject.is_file():
            try:
                project = tomllib.loads(pyproject.read_text(encoding="utf-8")).get("project") or {}
            except (OSError, UnicodeDecodeError, ValueError):
                return "", ""
            version = project.get("version")
            if _canonical(project.get("name", "")) in wanted and isinstance(version, str) and version.strip():
                return version.strip(), str(pyproject)
            return "", ""
        if (candidate / ".git").exists():
            return "", ""
    return "", ""


def _git_head(start: str, cache: dict) -> str:
    """HEAD of the nearest enclosing git checkout, read from files only."""
    try:
        directory = Path(start if os.path.isdir(start) else os.path.dirname(start))
    except (TypeError, ValueError):
        return ""
    if any(part.lower() in _INSTALL_DIRS for part in directory.parts):
        return ""
    for candidate in (directory, *directory.parents):
        dotgit = candidate / ".git"
        if not dotgit.exists():
            continue
        key = _norm(candidate)
        if key not in cache:
            cache[key] = _read_head(candidate, dotgit)
        return cache[key]
    return ""


def _read_head(root: Path, dotgit: Path) -> str:
    try:
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
        if re.fullmatch(r"[0-9a-f]{40}", head):
            return f"{head[:12]} (detached) in {root}"
        if not head.startswith("ref:"):
            return ""
        ref = head[len("ref:"):].strip()
        branch = ref.removeprefix("refs/heads/")
        for base in (gitdir, common):
            loose = base / ref
            if loose.is_file():
                sha = loose.read_text(encoding="utf-8").strip()
                if re.fullmatch(r"[0-9a-f]{40}", sha):
                    return f"{sha[:12]} ({branch}) in {root}"
        packed = common / "packed-refs"
        if packed.is_file():
            for line in packed.read_text(encoding="utf-8").splitlines():
                parts = line.split()
                if len(parts) == 2 and parts[1] == ref and re.fullmatch(r"[0-9a-f]{40}", parts[0]):
                    return f"{parts[0][:12]} ({branch}) in {root}"
        return f"{UNKNOWN} ({branch}, no commit yet) in {root}"
    except (OSError, UnicodeDecodeError):
        return ""


def _row(group, module_name, display, candidates, modules, git_cache, with_commit):
    module = modules.get(module_name)
    if module is None:
        return PackageRow(group, display, module_name, False)
    location = _module_location(module)
    real = _real(location) if location else ""
    notes = []
    dist_name, version, unowned = _owned_metadata(module_name, real, candidates) if real else ("", "", ())
    attribute = getattr(module, "__version__", None)
    attribute = attribute.strip() if isinstance(attribute, str) else ""
    checkout, pyproject = _checkout_version(real, candidates) if real else ("", "")
    # Source checkouts: pyproject.toml is current, while editable/egg-info
    # metadata is a snapshot from install time.  Otherwise owned metadata,
    # then the loaded module's __version__.  Every disagreeing source is shown.
    sources = []
    if checkout:
        sources.append((checkout, "pyproject.toml of the source checkout"))
    if version:
        sources.append((version, f"metadata ({dist_name})"))
    if attribute:
        sources.append((attribute, "module __version__"))
    if sources:
        version, source = sources[0]
        conflicts = [f"{label} says {value}" for value, label in sources[1:] if value != version]
        if conflicts:
            notes.append("version sources disagree: " + "; ".join(conflicts))
    else:
        version, source = UNKNOWN, "no pyproject, owned metadata or __version__"
    if not dist_name:
        notes.extend(unowned)
    if not location:
        notes.append("module has no file location")
    commit = _git_head(real, git_cache) if with_commit and real else ""
    return PackageRow(group, display, module_name, True, version, source,
                      location, real, commit, tuple(notes))


def _extra_ecosystem_modules(modules, known):
    """Loaded top-level ``any*`` source packages that the table does not name."""
    extra = []
    for name, module in list(modules.items()):
        if "." in name or name in known or not name.lower().startswith("any"):
            continue
        location = _module_location(module)
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
        rows.append(_row("ANY ecosystem", module_name, display, candidates, modules, git_cache, True))
    for module_name, display, candidates in THIRD_PARTY_MODULES:
        rows.append(_row("Third party", module_name, display, candidates, modules, git_cache, False))
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


def open_loaded_packages(window):
    """Build the report now and show it in a non-modal dialog."""
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QColor
    from PySide6.QtWidgets import (QAbstractItemView, QDialog, QDialogButtonBox, QHeaderView,
                                   QLabel, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout)

    report = collect_loaded_packages()
    dialog = QDialog(window)
    dialog.setObjectName("LoadedPackagesDialog")
    dialog.setWindowTitle("Loaded packages")
    dialog.setAttribute(Qt.WA_DeleteOnClose)
    dialog.report = report
    layout = QVBoxLayout(dialog)
    summary = QLabel(
        f"Python {report.python} — {report.executable}<br>"
        f"Qt runtime {report.qt_runtime} — collected in {report.elapsed_ms:.0f} ms<br>"
        f"<i>{report.notes[0]}</i>", dialog)
    summary.setObjectName("LoadedPackagesSummary")
    summary.setTextInteractionFlags(Qt.TextSelectableByMouse)
    layout.addWidget(summary)
    table = QTableWidget(len(report.rows), len(COLUMNS), dialog)
    table.setObjectName("LoadedPackagesTable")
    table.setHorizontalHeaderLabels(COLUMNS)
    table.setEditTriggers(QAbstractItemView.NoEditTriggers)
    table.setSelectionBehavior(QAbstractItemView.SelectRows)
    table.verticalHeader().setVisible(False)
    for index, row in enumerate(report.rows):
        if row.loaded:
            path = row.import_path + (f"\n→ {row.real_path}" if row.real_path and _norm(row.real_path) != _norm(row.import_path) else "")
            flagged = any(note.startswith("version sources disagree") for note in row.notes)
            values = (row.name, ("⚠ " if flagged else "") + row.version, row.version_source,
                      path, row.commit, "; ".join(row.notes))
        else:
            values = (row.name, "—", "not loaded in this session", "", "", "")
        for column, value in enumerate(values):
            item = QTableWidgetItem(value)
            item.setToolTip(value)
            if not row.loaded:
                item.setForeground(QColor(128, 128, 128))
            table.setItem(index, column, item)
    header = table.horizontalHeader()
    for column in range(len(COLUMNS)):
        header.setSectionResizeMode(column, QHeaderView.ResizeToContents)
    header.setSectionResizeMode(3, QHeaderView.Stretch)
    table.resizeRowsToContents()
    layout.addWidget(table)
    buttons = QDialogButtonBox(QDialogButtonBox.Close, dialog)
    copy = QPushButton("Copy to clipboard", dialog)
    copy.setObjectName("CopyLoadedPackages")
    buttons.addButton(copy, QDialogButtonBox.ActionRole)
    buttons.rejected.connect(dialog.close)

    def copy_report():
        window.clipboard.copy_text(report.text())
        try:
            window.set_status("Loaded package list copied to clipboard.")
        except Exception:
            pass

    copy.clicked.connect(copy_report)
    layout.addWidget(buttons)
    dialog.resize(1100, 560)
    window._loaded_packages_dialog = dialog
    dialog.show()
    return dialog
