"""Headless policy tests for the compact selection toolbar."""

from anyfem.ui.workspace import quick_filter_labels


def test_quick_selection_choices_follow_the_active_domain():
    assert quick_filter_labels("Geometry") == ("Point", "Line", "Plate")
    assert quick_filter_labels("Mesh") == ("Node", "Element", "Element face")
    assert quick_filter_labels("unknown") == ()
