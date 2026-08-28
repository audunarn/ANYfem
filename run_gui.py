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


def _version_before(value: object, maximum: object) -> bool:
    """Return whether a release is below an exclusive compatibility cap."""

    return _numeric_version(value) < _numeric_version(maximum)


def _ecosystem_root(repository_root: Path | None = None) -> Path:
    """Locate sibling repositories from a checkout or nested Git worktree."""

    root = _ROOT if repository_root is None else Path(repository_root).resolve()
    embedded = root / ".ecosystem"
    if (embedded / "ANYsolver").is_dir() and (embedded / "ANYmesh").is_dir():
        return embedded
    for candidate in root.parents:
        if (candidate / "ANYsolver").is_dir() and (
            candidate / "ANYmesh"
        ).is_dir():
            return candidate
    return root.parent


_ECOSYSTEM_ROOT = _ecosystem_root()


def _project_root(environment: str, fallback: Path) -> Path:
    """Resolve one explicitly bound sibling checkout or its normal fallback."""

    override = os.environ.get(environment, "").strip()
    return Path(override).resolve() if override else fallback


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

    The qualified companion is bound to the 0.3 compatibility series.  A
    different minor series requires its own integration review.
    """

    override = (
        os.environ.get("ANYMESHER_SOURCE", "").strip()
        or os.environ.get("ANYMESHER_030_SOURCE", "").strip()
    )
    sibling_checkout = _ECOSYSTEM_ROOT / "ANYmesh"
    release_checkout = _ECOSYSTEM_ROOT / "ANYsolver" / ".compat_anymesher_030"
    candidates = ([Path(override)] if override else []) + [
        sibling_checkout,
        release_checkout,
    ]
    for candidate in candidates:
        version = _declared_project_version(candidate)
        if (
            version is not None
            and _version_at_least(version, "0.3.2")
            and _version_before(version, "0.4.0")
        ):
            return candidate
    # Fail closed when neither the explicit candidate, sibling checkout, nor
    # retained compatibility checkout declares the qualified API generation.
    # The missing source then becomes an actionable preflight diagnostic.
    return release_checkout


_ANYMATERIAL_PROJECT = _project_root(
    "ANYFEM_ANYMATERIAL_ROOT", _ECOSYSTEM_ROOT / "ANYmaterial"
)
_ANYGEOMETRY_PROJECT = _project_root(
    "ANYFEM_ANYGEOMETRY_ROOT", _ECOSYSTEM_ROOT / "ANYgeometry"
)
_ANYFILEIO_PROJECT = _project_root(
    "ANYFEM_ANYFILEIO_ROOT", _ECOSYSTEM_ROOT / "ANYfileIO"
)
_ANY3DVIEW_PROJECT = _project_root(
    "ANYFEM_ANY3DVIEW_ROOT", _ECOSYSTEM_ROOT / "ANY3dView"
)
_ANYTK3D_PROJECT = _project_root(
    "ANYFEM_ANYTK3D_ROOT", _ECOSYSTEM_ROOT / "ANYtk3D"
)
_ANYSOLVER_PROJECT = _project_root(
    "ANYFEM_ANYSOLVER_ROOT", _ECOSYSTEM_ROOT / "ANYsolver"
)
_ANYMESHER_PROJECT = _qualified_anymesher_project()
_SOURCE_PROJECTS = (
    ("ANYmaterial", "anymaterial", _ANYMATERIAL_PROJECT / "src"),
    ("ANYgeometry", "anygeometry", _ANYGEOMETRY_PROJECT / "src"),
    ("ANYfileio", "anyfileio", _ANYFILEIO_PROJECT / "src"),
    ("ANYmesher", "anymesher", _ANYMESHER_PROJECT / "src"),
    ("ANY3dView", "any3dview", _ANY3DVIEW_PROJECT / "src"),
    ("ANYtk3D", "anytk3d", _ANYTK3D_PROJECT / "src"),
    ("ANYsolver", "anysolver", _ANYSOLVER_PROJECT / "src"),
    ("ANYfem", "anyfem", _ROOT / "src"),
)

# Insert in reverse so ANYfem itself remains first and every module inspected
# below is the sibling checkout that the launcher is about to execute.
for _distribution, _module, _source in reversed(_SOURCE_PROJECTS):
    if _source.is_dir() and str(_source) not in sys.path:
        sys.path.insert(0, str(_source))


ECOSYSTEM_REQUIREMENTS = (
    ("ANYmaterial", "ANYmaterial>=0.1.1,<0.2", "0.1.1", "0.2.0"),
    (
        "ANYgeometry",
        "ANYgeometry[planar]>=0.4.1,<0.5",
        "0.4.1",
        "0.5.0",
    ),
    (
        "ANYfileio",
        "ANYfileio[semantics]>=0.2.1,<0.3",
        "0.2.1",
        "0.3.0",
    ),
    ("ANYmesher", "ANYmesher>=0.3.2,<0.4", "0.3.2", "0.4.0"),
    ("ANY3dView", "ANY3dView[gpu]>=0.5.4,<0.6", "0.5.4", "0.6.0"),
    ("ANYtk3D", "ANYtk3D>=0.5.3,<0.6", "0.5.3", "0.6.0"),
    ("ANYsolver", "ANYsolver>=0.4.0,<0.5", "0.4.0", "0.5.0"),
    ("ANYfem", "ANYfem>=0.4.0,<0.5", "0.4.0", "0.5.0"),
)


def ecosystem_compatibility_problems(
    version_reader: Callable[[str], str] | None = None,
) -> tuple[str, ...]:
    """Return missing or out-of-range distribution metadata."""

    read_version = metadata.version if version_reader is None else version_reader
    problems: list[str] = []
    for distribution, requirement, minimum, maximum in ECOSYSTEM_REQUIREMENTS:
        try:
            installed = str(read_version(distribution))
        except metadata.PackageNotFoundError:
            problems.append(f"{requirement}: distribution metadata is missing")
            continue
        if not _version_at_least(installed, minimum) or (
            maximum is not None
            and _numeric_version(installed) >= _numeric_version(maximum)
        ):
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
        str(_ANYMATERIAL_PROJECT),
        str(_ANYGEOMETRY_PROJECT) + "[planar]",
        str(_ANYMESHER_PROJECT),
        str(_ANYFILEIO_PROJECT) + "[semantics]",
        str(_ANY3DVIEW_PROJECT) + "[gpu]",
        str(_ANYTK3D_PROJECT),
        str(_ANYSOLVER_PROJECT),
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
            "ANYfem 0.4.0 cannot start with this mixed ecosystem:\n- "
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
