"""Bounded, entity-aware formatting for solver preflight failures."""

from types import SimpleNamespace

from anyfem.ui.app import _format_preflight_errors


def test_repeated_element_failures_are_grouped_with_bounded_ids() -> None:
    issues = tuple(
        SimpleNamespace(
            code="MESH005",
            message="Shell mesh-quality evaluation failed",
            suggestion="Check element connectivity and node coordinates.",
            entity_type="element",
            entity_id=identifier,
            measured=None,
            limit=None,
        )
        for identifier in range(1, 27)
    )

    text = _format_preflight_errors(issues)

    assert text.count("[MESH005]") == 1
    assert "Affected element ID(s): 1, 2, 3" in text
    assert "+10 more" in text
    assert "Suggestion: Check element connectivity" in text
