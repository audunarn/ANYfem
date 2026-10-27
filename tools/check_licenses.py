"""Deterministically validate ANYfem licensing and direct dependencies."""

from __future__ import annotations

import argparse
import hashlib
from importlib import metadata as importlib_metadata
import json
from pathlib import Path
import re
import subprocess
import sys
import tomllib


ROOT = Path(__file__).resolve().parents[1]
INVENTORY_PATH = ROOT / "dependency-licenses.json"
EXPECTED_LICENSE = "GPL-3.0-only"
EXPECTED_LICENSE_FILES = [
    "LICENSE",
    "NOTICE",
    "THIRD_PARTY_NOTICES.md",
    "dependency-licenses.json",
    "docs/LICENSE.md",
    "SOURCE_LICENSES.md",
    "DEPENDENCY_NOTICES.md",
    "docs/COMMERCIAL_LICENSING.md",
    "docs/LICENSING_TRANSITION.md",
    "docs/LICENSING_AUDIT.md",
    "docs/drafts/COMMERCIAL_LICENSE_TERM_SHEET.md",
]
GPL_NORMALIZED_SHA256 = (
    "FB981668C18A279E285FC4D83FBA1E836CC84DD4DAA73C9697D3CFD2D8ACA6E0"
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
    "pyside6": {"LGPL-3.0-only"},
    "any3dview": {"MPL-2.0"},
    "anyfileio": {"MPL-2.0"},
    "anygeometry": {"MPL-2.0"},
    "anyloads": {"MPL-2.0"},
    "anymaterial": {"MPL-2.0"},
    "anymesher": {"MPL-2.0"},
    "anysection": {"MPL-2.0"},
    "anysolver": {"MPL-2.0"},
    "anypost": {"MPL-2.0"},
    "anysolver-results": {"MPL-2.0"},
    "anytk3d": {"MPL-2.0"},
    "build": {"MIT"},
    "h5py": {"BSD-3-Clause", "BSD License"},
    "numpy": {
        "BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0",
        "BSD-3-Clause AND bundled-component-licenses",
        "BSD-3-Clause",
        "BSD License",
    },
    "pillow": {"HPND", "Historical Permission Notice and Disclaimer", "MIT-CMU"},
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


def check_source_text(name: str, text: str) -> None:
    """Refuse contradictory explicit source headers; absent headers inherit scope."""
    for expression in re.findall(
        r"^\s*#\s*SPDX-License-Identifier:\s*([^\r\n]+)", text, re.MULTILINE
    ):
        if expression.strip() != EXPECTED_LICENSE:
            _fail(f"{name}: conflicting source SPDX {expression.strip()!r}")


def _check_source_scope() -> None:
    if (ROOT / ".git").exists():
        result = subprocess.run(
            ["git", "-C", str(ROOT), "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
            check=True, capture_output=True,
        )
        paths = [ROOT / p for p in result.stdout.decode("utf-8").split("\0") if p]
    else:
        # An extracted sdist has no Git metadata. Limit scanning to delivered code.
        paths = [p for directory in ("src", "tools", "tests")
                 for p in (ROOT / directory).rglob("*") if p.is_file()]
        paths.extend(p for p in ROOT.iterdir() if p.is_file())
    for path in paths:
        if path.suffix in {".py", ".sh", ".yml", ".yaml", ".toml", ".in"}:
            check_source_text(str(path.relative_to(ROOT)), path.read_text(encoding="utf-8"))


def render_dependency_notices(inventory: dict[str, object]) -> str:
    """Deterministic notice index, not a substitute for component license texts."""
    lines = [
        "# Generated dependency notice index", "",
        "Generated from `dependency-licenses.json`; do not edit this table by hand.",
        "Dependencies retain their own terms and are installed separately from the ANYfem wheel.",
        "This index is not a bundled-component SBOM or a replacement for complete license texts.",
        "For installers/OEM bundles preserve each actual component's notices, source access",
        "and applicable replacement/relinking rights. See `THIRD_PARTY_NOTICES.md`.", "",
        "| Dependency | Scope | Declared license | Upstream |",
        "|---|---|---|---|",
    ]
    for row in inventory["dependencies"]:
        cells = [str(row[key]) for key in ("name", "scope", "license_expression", "upstream")]
        lines.append("| " + " | ".join(cell.replace("|", "\\|") for cell in cells) + " |")
    return "\n".join(lines) + "\n"


def check_distribution_metadata(metadata, *, label: str) -> None:
    if metadata.get_all("License-Expression") != [EXPECTED_LICENSE]:
        _fail(f"{label}: wrong or duplicate License-Expression")
    files = metadata.get_all("License-File", [])
    if sorted(files) != sorted(EXPECTED_LICENSE_FILES):
        _fail(f"{label}: missing, duplicate or unexpected License-File entries")


def check_installed_distribution() -> None:
    """Check the installed wheel's public notices without importing its runtime."""
    dist = importlib_metadata.distribution("ANYfem")
    check_distribution_metadata(dist.metadata, label="installed ANYfem")
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    if dist.version != project["version"]:
        _fail("installed ANYfem version differs from candidate")
    files = list(dist.files or [])
    for relative in EXPECTED_LICENSE_FILES:
        matches = [f for f in files if str(f).replace("\\", "/").endswith(".dist-info/licenses/" + relative)]
        if len(matches) != 1:
            _fail(f"installed ANYfem missing or duplicate notice {relative}")
        actual = Path(dist.locate_file(matches[0])).read_text(encoding="utf-8")
        expected = (ROOT / relative).read_text(encoding="utf-8")
        if actual != expected:
            _fail(f"installed notice differs from source: {relative}")
    for member in files:
        name = str(member).replace("\\", "/")
        if name.startswith("anyfem/") and name.endswith(".py"):
            check_source_text(name, Path(dist.locate_file(member)).read_text(encoding="utf-8"))
    print("installed distribution license check passed: GPL-3.0-only (no runtime import)")


def _normalized_name(requirement: str) -> str:
    match = re.match(r"\s*([A-Za-z0-9_.-]+)", requirement)
    if match is None:
        _fail(f"cannot parse dependency requirement {requirement!r}")
    return re.sub(r"[-_.]+", "-", match.group(1)).lower()


def _declared_requirements(metadata: dict[str, object]) -> dict[tuple[str,str], str]:
    declared: dict[tuple[str,str], str] = {}

    def add(requirement: str, scope: str) -> None:
        name = _normalized_name(requirement)
        key=(name,scope)
        if key in declared:
            _fail(f"duplicate direct dependency {name!r} in {scope!r}")
        declared[key] = requirement

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


def _license_from_metadata(
    distribution: importlib_metadata.Distribution, *, name: str
) -> str:
    expression = distribution.metadata.get("License-Expression")
    if name == "pyside6":
        reported = (expression or distribution.metadata.get("License", "")).strip()
        if reported == "LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only":
            return "LGPL-3.0-only"  # Explicit dynamic Qt route; see notices.
    if expression:
        return expression.strip()
    legacy = distribution.metadata.get("License")
    if legacy and legacy.strip() and legacy.strip().upper() != "UNKNOWN":
        normalized_legacy = legacy.strip()
        # Current SciPy wheels still publish the complete project and bundled
        # runtime notices in the legacy License field instead of exposing a
        # License-Expression.  Recognize that reviewed notice set narrowly;
        # a missing or changed component marker remains a hard failure.
        if name == "scipy" and all(
            marker in normalized_legacy
            for marker in (
                "Copyright (c) 2001-2002 Enthought, Inc.",
                "Name: OpenBLAS",
                "License: BSD-3-Clause",
                "Name: GCC runtime library",
                "GPL-3.0-or-later WITH GCC-exception-3.1",
            )
        ):
            return "BSD-3-Clause AND bundled-component-licenses"
        return normalized_legacy
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
        actual = _license_from_metadata(distribution, name=name)
        if not actual:
            _fail(f"installed dependency {name!r} has unknown license metadata")
        qt_lgpl = name == "pyside6" and actual == "LGPL-3.0-only"
        if not qt_lgpl and any(token in actual.upper() for token in FORBIDDEN_LICENSE_TOKENS):
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
    version_match = re.fullmatch(
        r"(\d+)\.(\d+)\.(\d+)", str(project.get("version", ""))
    )
    if version_match is None or tuple(map(int, version_match.groups())) < (0, 4, 0):
        _fail("pyproject.toml does not declare a release version >=0.4.0")
    if project.get("license") != EXPECTED_LICENSE:
        _fail("pyproject.toml does not declare GPL-3.0-only")
    if project.get("license-files") != EXPECTED_LICENSE_FILES:
        _fail("pyproject.toml license-files does not match the required set")

    for relative in EXPECTED_LICENSE_FILES:
        if not (ROOT / relative).is_file():
            _fail(f"required license artifact {relative!r} is missing")
    license_text = (ROOT / "LICENSE").read_text(encoding="utf-8").replace(
        "\r\n", "\n"
    )
    digest = hashlib.sha256(license_text.encode("utf-8")).hexdigest().upper()
    if digest != GPL_NORMALIZED_SHA256:
        _fail("LICENSE is not the approved unmodified GPL-3.0-only text")
    if "Copyright (c) Audun Nyhus" not in (ROOT / "NOTICE").read_text(
        encoding="utf-8"
    ):
        _fail("NOTICE is missing the project copyright")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    if "Earlier published releases retain" not in readme:
        _fail("README does not preserve earlier published release licensing")
    if "GNU General Public License, version 3 only" not in readme:
        _fail("README does not identify the GPL-3.0-only license")
    documentation = (ROOT / "docs" / "LICENSE.md").read_text(encoding="utf-8")
    if "Creative Commons Attribution 4.0" not in documentation:
        _fail("documentation license notice is missing CC BY 4.0")

    notice = (ROOT / "NOTICE").read_text(encoding="utf-8")
    if "SPDX-License-Identifier: GPL-3.0-only" not in notice:
        _fail("NOTICE does not declare GPL-3.0-only")
    scope = (ROOT / "SOURCE_LICENSES.md").read_text(encoding="utf-8")
    if "GPL-3.0-only" not in scope or "CC BY 4.0" not in scope:
        _fail("source/documentation scope notice is inconsistent")
    commercial = (ROOT / "docs/COMMERCIAL_LICENSING.md").read_text(encoding="utf-8")
    if "No commercial rights are granted by this policy" not in commercial:
        _fail("commercial policy must not itself grant rights")
    draft = (ROOT / "docs/drafts/COMMERCIAL_LICENSE_TERM_SHEET.md").read_text(encoding="utf-8")
    if not draft.startswith("# NON-OPERATIVE / LEGAL REVIEW REQUIRED"):
        _fail("commercial term sheet lacks its non-operative warning")
    _check_source_scope()

    inventory = _read_inventory()
    generated = (ROOT / "DEPENDENCY_NOTICES.md").read_text(encoding="utf-8")
    if generated != render_dependency_notices(inventory):
        _fail("generated dependency notices drifted from inventory")
    rows = inventory.get("dependencies")
    if not isinstance(rows, list):
        _fail("dependency inventory rows are missing")
    declared = _declared_requirements(metadata)
    recorded: dict[tuple[str,str], str] = {}
    for row in rows:
        if not isinstance(row, dict):
            _fail("dependency inventory contains a malformed row")
        name = _normalized_name(str(row.get("name", "")))
        key=(name,str(row.get("scope","")))
        if key in recorded:
            _fail(f"dependency inventory repeats {name!r}")
        if row.get("bundled") is not False:
            _fail(f"dependency {name!r} must be explicitly recorded as not bundled")
        expression = str(row.get("license_expression", ""))
        qt_lgpl = name == "pyside6" and expression == "LGPL-3.0-only"
        if expression not in ALLOWED_EXPRESSIONS and not qt_lgpl:
            _fail(
                f"dependency {name!r} has unknown or review-required license "
                f"{expression!r}"
            )
        if not qt_lgpl and any(token in expression.upper() for token in FORBIDDEN_LICENSE_TOKENS):
            _fail(f"dependency {name!r} has a forbidden strong-copyleft license")
        recorded[key] = str(row.get("requirement", ""))
    if recorded != declared:
        _fail(
            f"dependency inventory mismatch: recorded={recorded!r}, "
            f"declared={declared!r}"
        )

    notices = (ROOT / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8").lower()
    for name,_scope in recorded:
        if name not in notices:
            _fail(f"third-party notice is missing {name!r}")
    if check_installed:
        _check_installed(rows)
    print(f"license check passed: {EXPECTED_LICENSE}; {len({name for name,_scope in recorded})} direct dependencies")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--distribution-only", action="store_true",
        help="check installed ANYfem metadata/notices only, without importing runtime",
    )
    parser.add_argument(
        "--check-installed",
        action="store_true",
        help="also verify installed direct-dependency license metadata",
    )
    arguments = parser.parse_args(argv)
    if arguments.distribution_only:
        if arguments.check_installed:
            parser.error("--distribution-only and --check-installed are mutually exclusive")
        check_installed_distribution()
    else:
        check(check_installed=arguments.check_installed)
    return 0


if __name__ == "__main__":
    sys.exit(main())
