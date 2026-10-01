from __future__ import annotations

from dataclasses import dataclass

from tools.check_licenses import _license_from_metadata,_declared_requirements
import pytest


@dataclass
class _Distribution:
    metadata: dict[str, str]


def test_separate_extras_record_each_requirement_without_losing_source_pins():
    metadata={"build-system":{"requires":[]},"project":{"optional-dependencies":{
        "gui":["Viewer @ git+https://example.com/viewer@abc"],"qt":["Viewer>=1"]}}}
    assert _declared_requirements(metadata)=={("viewer","optional:gui"):"Viewer @ git+https://example.com/viewer@abc",
        ("viewer","optional:qt"):"Viewer>=1"}
    metadata["project"]["optional-dependencies"]["qt"].append("Viewer>=2")
    with pytest.raises(SystemExit,match="duplicate"):_declared_requirements(metadata)


def test_numpy_spdx_expression_is_preserved() -> None:
    expression = "BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0"
    distribution = _Distribution({"License-Expression": expression})

    assert _license_from_metadata(distribution, name="numpy") == expression


def test_reviewed_scipy_legacy_notice_is_normalized() -> None:
    distribution = _Distribution(
        {
            "License": "\n".join(
                (
                    "Copyright (c) 2001-2002 Enthought, Inc.",
                    "Name: OpenBLAS",
                    "License: BSD-3-Clause",
                    "Name: GCC runtime library",
                    "GPL-3.0-or-later WITH GCC-exception-3.1",
                )
            )
        }
    )

    assert _license_from_metadata(
        distribution, name="scipy"
    ) == "BSD-3-Clause AND bundled-component-licenses"


def test_incomplete_scipy_legacy_notice_is_not_normalized() -> None:
    legacy = "\n".join(
        (
            "Copyright (c) 2001-2002 Enthought, Inc.",
            "Name: OpenBLAS",
            "License: BSD-3-Clause",
        )
    )
    distribution = _Distribution({"License": legacy})

    assert _license_from_metadata(distribution, name="scipy") == legacy
