"""Validate ANYfem wheel and sdist contents before publication."""

from __future__ import annotations

import argparse
from pathlib import Path, PurePosixPath
import sys
import tarfile
import zipfile


VERSION = "0.4.0"
LICENSE_FILES = (
    "LICENSE",
    "NOTICE",
    "THIRD_PARTY_NOTICES.md",
    "dependency-licenses.json",
    "docs/LICENSE.md",
)


def _fail(message: str) -> None:
    raise SystemExit(f"artifact check failed: {message}")


def _assert_safe_names(names: set[str], artifact: Path) -> None:
    forbidden = (
        ".worktrees/",
        ".git/",
        ".pytest_cache/",
        ".pytest_tmp",
    )
    for name in names:
        normalized = name.replace("\\", "/")
        if any(token in normalized for token in forbidden):
            _fail(f"{artifact.name} contains forbidden path {name!r}")


def _check_wheel(path: Path) -> None:
    with zipfile.ZipFile(path) as archive:
        names = set(archive.namelist())
        _assert_safe_names(names, path)
        metadata_names = [name for name in names if name.endswith(".dist-info/METADATA")]
        if len(metadata_names) != 1:
            _fail(f"{path.name} has {len(metadata_names)} METADATA files")
        metadata_name = metadata_names[0]
        metadata = archive.read(metadata_name).decode("utf-8").replace("\r\n", "\n")
        if f"Version: {VERSION}\n" not in metadata:
            _fail(f"{path.name} does not declare version {VERSION}")
        if "License-Expression: MPL-2.0\n" not in metadata:
            _fail(f"{path.name} does not declare MPL-2.0")
        license_root = PurePosixPath(metadata_name).parent / "licenses"
        for relative in LICENSE_FILES:
            member = str(license_root / relative)
            if member not in names:
                _fail(f"{path.name} is missing {member}")


def _check_sdist(path: Path) -> None:
    with tarfile.open(path, "r:gz") as archive:
        names = {member.name for member in archive.getmembers()}
        _assert_safe_names(names, path)
        roots = {PurePosixPath(name).parts[0] for name in names if name}
        if len(roots) != 1:
            _fail(f"{path.name} does not have one archive root")
        root = next(iter(roots))
        for relative in (*LICENSE_FILES, "CHANGELOG.md", "pyproject.toml"):
            member = str(PurePosixPath(root) / relative)
            if member not in names:
                _fail(f"{path.name} is missing {member}")


def check(directory: Path) -> None:
    wheels = sorted(directory.glob("*.whl"))
    sdists = sorted(directory.glob("*.tar.gz"))
    if len(wheels) != 1 or len(sdists) != 1:
        _fail(
            f"expected one wheel and one sdist in {directory}, found "
            f"{len(wheels)} wheel(s) and {len(sdists)} sdist(s)"
        )
    _check_wheel(wheels[0])
    _check_sdist(sdists[0])
    print(f"artifact check passed: {wheels[0].name}; {sdists[0].name}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path, help="directory containing dist files")
    arguments = parser.parse_args(argv)
    check(arguments.directory)
    return 0


if __name__ == "__main__":
    sys.exit(main())
