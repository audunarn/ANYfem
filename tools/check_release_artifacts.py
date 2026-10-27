"""Validate ANYfem wheel and sdist contents before publication."""

from __future__ import annotations

import argparse
from email.parser import BytesParser
from pathlib import Path, PurePosixPath
import sys
import tarfile
import tomllib
import zipfile

try:
    from .check_licenses import (
        EXPECTED_LICENSE_FILES, check_distribution_metadata, check_source_text,
    )
except ImportError:
    from check_licenses import (
        EXPECTED_LICENSE_FILES, check_distribution_metadata, check_source_text,
    )


ROOT = Path(__file__).resolve().parents[1]
VERSION = str(
    tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"][
        "version"
    ]
)
LICENSE_FILES = tuple(EXPECTED_LICENSE_FILES)


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
        if normalized.startswith("/") or ".." in PurePosixPath(normalized).parts or ":" in normalized:
            _fail(f"{artifact.name} contains unsafe path {name!r}")
        if any(token in normalized for token in forbidden):
            _fail(f"{artifact.name} contains forbidden path {name!r}")


def _check_notice(relative: str, content: bytes, path: Path) -> None:
    actual = content.decode("utf-8").replace("\r\n", "\n")
    expected = (ROOT / relative).read_text(encoding="utf-8")
    if actual != expected:
        _fail(f"{path.name} notice differs from source: {relative}")


def _check_metadata(content: bytes, path: Path) -> None:
    metadata = BytesParser().parsebytes(content)
    if metadata.get_all("Name") != ["ANYfem"]:
        _fail(f"{path.name} has the wrong or duplicate package name")
    if metadata.get_all("Version") != [VERSION]:
        _fail(f"{path.name} does not declare version {VERSION}")
    check_distribution_metadata(metadata, label=path.name)


def _check_wheel(path: Path) -> None:
    with zipfile.ZipFile(path) as archive:
        names = set(archive.namelist())
        if len(names) != len(archive.namelist()):
            _fail(f"{path.name} contains duplicate archive members")
        _assert_safe_names(names, path)
        metadata_names = [name for name in names if name.endswith(".dist-info/METADATA")]
        if len(metadata_names) != 1:
            _fail(f"{path.name} has {len(metadata_names)} METADATA files")
        metadata_name = metadata_names[0]
        _check_metadata(archive.read(metadata_name), path)
        license_root = PurePosixPath(metadata_name).parent / "licenses"
        for relative in LICENSE_FILES:
            member = str(license_root / relative)
            if member not in names:
                _fail(f"{path.name} is missing {member}")
            _check_notice(relative, archive.read(member), path)
        for name in names:
            if name.startswith("anyfem/") and name.endswith(".py"):
                check_source_text(name, archive.read(name).decode("utf-8"))


def _check_sdist(path: Path) -> None:
    with tarfile.open(path, "r:gz") as archive:
        members = archive.getmembers()
        names = {member.name for member in members}
        if len(names) != len(members):
            _fail(f"{path.name} contains duplicate archive members")
        if any(member.issym() or member.islnk() for member in members):
            _fail(f"{path.name} contains archive links")
        _assert_safe_names(names, path)
        roots = {PurePosixPath(name).parts[0] for name in names if name}
        if len(roots) != 1:
            _fail(f"{path.name} does not have one archive root")
        root = next(iter(roots))
        for relative in (*LICENSE_FILES, "CHANGELOG.md", "pyproject.toml"):
            member = str(PurePosixPath(root) / relative)
            if member not in names:
                _fail(f"{path.name} is missing {member}")
        for relative in LICENSE_FILES:
            _check_notice(relative, archive.extractfile(f"{root}/{relative}").read(), path)
        metadata_member = f"{root}/PKG-INFO"
        if metadata_member not in names:
            _fail(f"{path.name} is missing PKG-INFO")
        _check_metadata(archive.extractfile(metadata_member).read(), path)
        candidate = tomllib.loads(archive.extractfile(f"{root}/pyproject.toml").read().decode("utf-8"))["project"]
        if candidate.get("license") != "GPL-3.0-only" or candidate.get("version") != VERSION:
            _fail(f"{path.name} has inconsistent pyproject license/version")
        if candidate.get("license-files") != EXPECTED_LICENSE_FILES:
            _fail(f"{path.name} has inconsistent pyproject license-files")
        for member in members:
            if member.isfile() and PurePosixPath(member.name).suffix in {".py", ".sh", ".yml", ".yaml", ".toml", ".in"}:
                check_source_text(member.name, archive.extractfile(member).read().decode("utf-8"))


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
