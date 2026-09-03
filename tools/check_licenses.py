"""Deterministically validate ANYfem licensing and direct dependencies."""

from __future__ import annotations

import argparse
import hashlib
from importlib import metadata as importlib_metadata
import json
from pathlib import Path
import re
import sys
import tomllib


ROOT = Path(__file__).resolve().parents[1]
INVENTORY_PATH = ROOT / "dependency-licenses.json"
EXPECTED_LICENSE = "MPL-2.0"
EXPECTED_LICENSE_FILES = [
    "LICENSE",
    "NOTICE",
    "THIRD_PARTY_NOTICES.md",
    "dependency-licenses.json",
    "docs/LICENSE.md",
]
MPL_NORMALIZED_SHA256 = (
    "3F3D9E0024B1921B067D6F7F88DEB4A60CBE7A78E76C64E3F1D7FC3B779B9D04"
)
ALLOWED_EXPRESSIONS = {
    "Apache-2.0",
    "BSD-3-Clause",
    "BSD-3-Clause AND bundled-component-licenses",
    "HPND",
    "MIT",
    "MPL-2.0",
}
FORBIDDEN_LICENSE_TOKENS = ("AGPL", "GPL", "SSPL")
INSTALLED_LICENSE_ALIASES = {
    "any3dview": {"MPL-2.0"},
    "anyfileio": {"MPL-2.0"},
    "anygeometry": {"MPL-2.0"},
    "anymaterial": {"MPL-2.0"},
    "anymesher": {"MPL-2.0"},
    "anysolver": {"MPL-2.0"},
    "anytk3d": {"MPL-2.0"},
    "build": {"MIT"},
    "h5py": {"BSD-3-Clause", "BSD License"},
    "numpy": {
        "BSD-3-Clause AND bundled-component-licenses",
        "BSD-3-Clause",
        "BSD License",
    },
    "pillow": {"HPND", "Historical Permission Notice and Disclaimer"},
    "platformdirs": {"MIT", "MIT License"},
    "pytest": {"MIT", "MIT License"},
    "scipy": {
        "BSD-3-Clause AND bundled-component-licenses",
        "BSD-3-Clause",
        "BSD License",
    },
    "setuptools": {"MIT", "MIT License"},
    "twine": {"Apache-2.0", "Apache Software License"},
    "wheel": {"MIT", "MIT License"},
}


def _fail(message: str) -> None:
    raise SystemExit(f"license check failed: {message}")


def _normalized_name(requirement: str) -> str:
    match = re.match(r"\s*([A-Za-z0-9_.-]+)", requirement)
    if match is None:
        _fail(f"cannot parse dependency requirement {requirement!r}")
    return re.sub(r"[-_.]+", "-", match.group(1)).lower()


def _declared_requirements(metadata: dict[str, object]) -> dict[str, tuple[str, str]]:
    declared: dict[str, tuple[str, str]] = {}

    def add(requirement: str, scope: str) -> None:
        name = _normalized_name(requirement)
        if name in declared:
            _fail(f"duplicate direct dependency {name!r}")
        declared[name] = (requirement, scope)

    for requirement in metadata["build-system"]["requires"]:
        add(requirement, "build")
    project = metadata["project"]
    for requirement in project.get("dependencies", []):
        add(requirement, "runtime")
    for extra, requirements in sorted(project.get("optional-dependencies", {}).items()):
        scope = "development" if extra == "dev" else f"optional:{extra}"
        for requirement in requirements:
            add(requirement, scope)
    return declared


def _read_inventory() -> dict[str, object]:
    raw = INVENTORY_PATH.read_text(encoding="utf-8")
    inventory = json.loads(raw)
    canonical = json.dumps(inventory, allow_nan=False, indent=2, sort_keys=True) + "\n"
    if raw.replace("\r\n", "\n") != canonical:
        _fail("dependency-licenses.json is not canonical sorted JSON")
    if inventory.get("schema") != "anyecosystem.dependency-licenses-v1":
        _fail("unknown dependency inventory schema")
    if inventory.get("project") != "ANYfem":
        _fail("dependency inventory names the wrong project")
    if inventory.get("project_license") != EXPECTED_LICENSE:
        _fail("dependency inventory has the wrong project license")
    return inventory


def _license_from_metadata(distribution: importlib_metadata.Distribution) -> str:
    expression = distribution.metadata.get("License-Expression")
    if expression:
        return expression.strip()
    legacy = distribution.metadata.get("License")
    if legacy and legacy.strip() and legacy.strip().upper() != "UNKNOWN":
        return legacy.strip()
    classifiers = distribution.metadata.get_all("Classifier", [])
    for classifier in classifiers:
        if classifier.endswith("MIT License"):
            return "MIT License"
        if classifier.endswith("Apache Software License"):
            return "Apache Software License"
        if classifier.endswith("BSD License"):
            return "BSD License"
    return ""


def _check_installed(rows: list[dict[str, object]]) -> None:
    for row in rows:
        if row["scope"] == "build":
            continue
        name = _normalized_name(str(row["name"]))
        try:
            distribution = importlib_metadata.distribution(str(row["name"]))
        except importlib_metadata.PackageNotFoundError:
            _fail(f"declared dependency {name!r} is not installed")
        actual = _license_from_metadata(distribution)
        if not actual:
            _fail(f"installed dependency {name!r} has unknown license metadata")
        if any(token in actual.upper() for token in FORBIDDEN_LICENSE_TOKENS):
            _fail(
                f"installed dependency {name!r} reports review-required license "
                f"{actual!r}"
            )
        if actual not in INSTALLED_LICENSE_ALIASES[name]:
            _fail(
                f"installed dependency {name!r} reports {actual!r}, expected one of "
                f"{sorted(INSTALLED_LICENSE_ALIASES[name])!r}"
            )


def check(*, check_installed: bool = False) -> None:
    metadata = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    project = metadata["project"]
    if project.get("version") != "0.4.0":
        _fail("pyproject.toml does not declare the release version 0.4.0")
    if project.get("license") != EXPECTED_LICENSE:
        _fail("pyproject.toml does not declare MPL-2.0")
    if project.get("license-files") != EXPECTED_LICENSE_FILES:
        _fail("pyproject.toml license-files does not match the required set")

    for relative in EXPECTED_LICENSE_FILES:
        if not (ROOT / relative).is_file():
            _fail(f"required license artifact {relative!r} is missing")
    license_text = (ROOT / "LICENSE").read_text(encoding="utf-8").replace(
        "\r\n", "\n"
    )
    digest = hashlib.sha256(license_text.encode("utf-8")).hexdigest().upper()
    if digest != MPL_NORMALIZED_SHA256:
        _fail("LICENSE is not the approved unmodified MPL-2.0 text")
    if "Copyright (c) Audun Nyhus" not in (ROOT / "NOTICE").read_text(
        encoding="utf-8"
    ):
        _fail("NOTICE is missing the project copyright")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    if "Starting with version 0.4.0" not in readme:
        _fail("README does not describe the 0.4.0 licensing boundary")
    if "Mozilla Public License 2.0" not in readme:
        _fail("README does not identify the MPL-2.0 license")
    documentation = (ROOT / "docs" / "LICENSE.md").read_text(encoding="utf-8")
    if "Creative Commons Attribution 4.0" not in documentation:
        _fail("documentation license notice is missing CC BY 4.0")

    inventory = _read_inventory()
    rows = inventory.get("dependencies")
    if not isinstance(rows, list):
        _fail("dependency inventory rows are missing")
    declared = _declared_requirements(metadata)
    recorded: dict[str, tuple[str, str]] = {}
    for row in rows:
        if not isinstance(row, dict):
            _fail("dependency inventory contains a malformed row")
        name = _normalized_name(str(row.get("name", "")))
        if name in recorded:
            _fail(f"dependency inventory repeats {name!r}")
        if row.get("bundled") is not False:
            _fail(f"dependency {name!r} must be explicitly recorded as not bundled")
        expression = str(row.get("license_expression", ""))
        if expression not in ALLOWED_EXPRESSIONS:
            _fail(
                f"dependency {name!r} has unknown or review-required license "
                f"{expression!r}"
            )
        if any(token in expression.upper() for token in FORBIDDEN_LICENSE_TOKENS):
            _fail(f"dependency {name!r} has a forbidden strong-copyleft license")
        recorded[name] = (
            str(row.get("requirement", "")),
            str(row.get("scope", "")),
        )
    if recorded != declared:
        _fail(
            f"dependency inventory mismatch: recorded={recorded!r}, "
            f"declared={declared!r}"
        )

    notices = (ROOT / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8").lower()
    for name in recorded:
        if name not in notices:
            _fail(f"third-party notice is missing {name!r}")
    if check_installed:
        _check_installed(rows)
    print(f"license check passed: {EXPECTED_LICENSE}; {len(rows)} direct dependencies")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check-installed",
        action="store_true",
        help="also verify installed direct-dependency license metadata",
    )
    arguments = parser.parse_args(argv)
    check(check_installed=arguments.check_installed)
    return 0


if __name__ == "__main__":
    sys.exit(main())
