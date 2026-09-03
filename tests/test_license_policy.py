from __future__ import annotations

from dataclasses import dataclass

from tools.check_licenses import _license_from_metadata


@dataclass
class _Distribution:
    metadata: dict[str, str]


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
