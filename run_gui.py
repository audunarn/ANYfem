#!/usr/bin/env python
"""Run ANYfem from a latest-only editable sibling environment."""

from __future__ import annotations

import os
import re
import sys
from importlib import metadata, util
from pathlib import Path
from typing import Callable


_ROOT = Path(__file__).resolve().parent


def _numeric_version(value: object) -> tuple[int, int, int]:
    """Return the numeric release prefix used by the lightweight launcher."""

    parts = [int(item) for item in re.findall(r"\d+", str(value or ""))[:3]]
    return tuple((parts + [0, 0, 0])[:3])


def _version_at_least(value: object, minimum: object) -> bool:
    """Accept newer ecosystem generations while retaining a safe floor."""

    return _numeric_version(value) >= _numeric_version(minimum)


def _declared_project_version(project: Path) -> str | None:
    """Read a source checkout's declared version without importing it."""

    try:
        text = (project / "pyproject.toml").read_text(encoding="utf-8")
    except OSError:
        return None
    match = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
    return None if match is None else match.group(1)


def _qualified_anymesher_project() -> Path:
    """Return a compatible ANYmesher checkout, preferring the shared tree.

    ANYmesher follows semantic versioning at its public boundary.  A newer
    checkout must not be rejected merely because it has crossed a minor or
    major release boundary; the launcher enforces the minimum API generation
    and lets normal import/contract tests report an actual incompatibility.
    """

    override = (
        os.environ.get("ANYMESHER_SOURCE", "").strip()
        or os.environ.get("ANYMESHER_025_SOURCE", "").strip()
    )
    release_checkout = _ROOT.parent / "ANYsolver" / ".compat_anymesher_025"
    candidates = ([Path(override)] if override else []) + [
        _ROOT.parent / "ANYmesh",
        release_checkout,
    ]
    for candidate in candidates:
        version = _declared_project_version(candidate)
        if version is not None and _version_at_least(version, "0.2.5"):
            return candidate
    return Path(override) if override else release_checkout


_ANY3DVIEW_PROJECT = _ROOT.parent / "ANY3dView"
_ANYTK3D_PROJECT = _ROOT.parent / "ANYtk3D"
_ANYMESHER_PROJECT = _qualified_anymesher_project()
_SOURCE_PROJECTS = (
    ("ANYmaterial", "anymaterial", _ROOT.parent / "ANYmaterial" / "src"),
    ("ANYgeometry", "anygeometry", _ROOT.parent / "ANYgeometry" / "src"),
    ("ANYfileio", "anyfileio", _ROOT.parent / "ANYio" / "src"),
    ("ANYmesher", "anymesher", _ANYMESHER_PROJECT / "src"),
    ("ANY3dView", "any3dview", _ANY3DVIEW_PROJECT / "src"),
    ("ANYtk3D", "anytk3d", _ANYTK3D_PROJECT / "src"),
    ("ANYsolver", "anysolver", _ROOT.parent / "ANYsolver" / "src"),
    ("ANYfem", "anyfem", _ROOT / "src"),
)

# Insert in reverse so ANYfem itself remains first and every module inspected
# below is the sibling checkout that the launcher is about to execute.
for _distribution, _module, _source in reversed(_SOURCE_PROJECTS):
    if _source.is_dir() and str(_source) not in sys.path:
        sys.path.insert(0, str(_source))


ECOSYSTEM_REQUIREMENTS = (
    ("ANYmaterial", "ANYmaterial>=0.1", "0.1.0"),
    ("ANYgeometry", "ANYgeometry[planar]>=0.2.4", "0.2.4"),
    ("ANYfileio", "ANYfileio[semantics]>=0.2", "0.2.0"),
    ("ANYmesher", "ANYmesher>=0.2.5", "0.2.5"),
    ("ANY3dView", "ANY3dView[gpu]>=0.5", "0.5.0"),
    ("ANYtk3D", "ANYtk3D>=0.5", "0.5.0"),
    ("ANYsolver", "ANYsolver>=0.3.0", "0.3.0"),
    ("ANYfem", "ANYfem>=0.3.0", "0.3.0"),
)


def ecosystem_compatibility_problems(
    version_reader: Callable[[str], str] | None = None,
) -> tuple[str, ...]:
    """Return missing or out-of-range distribution metadata."""

    read_version = metadata.version if version_reader is None else version_reader
    problems: list[str] = []
    for distribution, requirement, minimum in ECOSYSTEM_REQUIREMENTS:
        try:
            installed = str(read_version(distribution))
        except metadata.PackageNotFoundError:
            problems.append(f"{requirement}: distribution metadata is missing")
            continue
        if not _version_at_least(installed, minimum):
            problems.append(f"{requirement}: installed metadata reports {installed}")
    return tuple(problems)


def _default_origin_reader(module: str) -> str | None:
    spec = util.find_spec(module)
    return None if spec is None else spec.origin


def ecosystem_origin_problems(
    origin_reader: Callable[[str], str | None] | None = None,
) -> tuple[str, ...]:
    """Return source modules that do not resolve from their sibling checkout."""

    read_origin = _default_origin_reader if origin_reader is None else origin_reader
    problems: list[str] = []
    for distribution, module, source in _SOURCE_PROJECTS:
        if not source.is_dir():
            problems.append(f"{distribution}: sibling source tree is missing: {source}")
            continue
        raw_origin = read_origin(module)
        if not raw_origin:
            problems.append(f"{distribution}: module {module!r} is not importable")
            continue
        try:
            origin = Path(raw_origin).resolve()
            expected = source.resolve()
            origin.relative_to(expected)
        except (OSError, ValueError):
            problems.append(
                f"{distribution}: module {module!r} resolves from {raw_origin}, "
                f"expected {source}"
            )
    return tuple(problems)


def editable_repair_command() -> str:
    """One copy/paste bootstrap command in release dependency order."""

    projects = (
        str(_ROOT.parent / "ANYmaterial"),
        str(_ROOT.parent / "ANYgeometry") + "[planar]",
        str(_ANYMESHER_PROJECT),
        str(_ROOT.parent / "ANYio") + "[semantics]",
        str(_ANY3DVIEW_PROJECT) + "[gpu]",
        str(_ANYTK3D_PROJECT),
        str(_ROOT.parent / "ANYsolver"),
        str(_ROOT) + "[gui]",
    )
    editables = " ".join(f'-e "{project}"' for project in projects)
    return f'"{sys.executable}" -m pip install --upgrade {editables}'


def require_compatible_ecosystem(
    version_reader: Callable[[str], str] | None = None,
    origin_reader: Callable[[str], str | None] | None = None,
) -> None:
    """Fail before importing Tk when sources and metadata do not agree."""

    problems = (
        *ecosystem_compatibility_problems(version_reader),
        *ecosystem_origin_problems(origin_reader),
    )
    if problems:
        raise RuntimeError(
            "ANYfem 0.3.2 cannot start with this mixed ecosystem:\n- "
            + "\n- ".join(problems)
            + "\nRepair the editable environment, then restart:\n"
            + editable_repair_command()
        )


def main() -> None:
    """Launch the GUI only after the latest-only release graph is verified."""

    require_compatible_ecosystem()
    from anyfem.ui.app import main as gui_main

    gui_main()


if __name__ == "__main__":
    main()
