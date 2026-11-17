"""The 60 m drawing barge has no sliver strip under the deck.

Regression: side longitudinals were placed every 625 mm from the keel up to the
2.0 m deck, so the top row of the side shell was a 0.125 m strip along the full
length.  Quad-first refused it at targets of 0.7 m and above (aspect ratio and
triangle repair), and the structured fallback meshed the whole model at about
0.2 m whatever the target.  The drawing barge keeps 625 mm spacing from the keel
and omits the longitudinal that would sit 125 mm under the deck.
"""
import numpy as np
import pytest

from anyfem.application.examples import drawing_barge_60m, floating_barge_section


def _side_longitudinal_heights(project, y):
    geometry = project.geometry
    heights = set()
    for edge_id in project.edge_sections:
        edge = geometry.edges[edge_id]
        a = np.asarray(geometry.vertex_position(edge.start))
        b = np.asarray(geometry.vertex_position(edge.end))
        if abs(a[1] - y) < 1e-9 and abs(b[1] - y) < 1e-9 and abs(a[2] - b[2]) < 1e-9 and abs(a[0] - b[0]) > 1e-9:
            heights.add(round(float(a[2]), 6))
    return sorted(heights)


@pytest.mark.parametrize("y", [-2.5, 2.5])
def test_drawing_barge_side_longitudinals_leave_no_strip_under_the_deck(y):
    heights = _side_longitudinal_heights(drawing_barge_60m(), y)
    assert heights == [0.625, 1.25]


def test_shortest_drawing_barge_edge_is_not_a_sliver():
    geometry = drawing_barge_60m().geometry
    shortest = min(np.linalg.norm(np.subtract(geometry.vertex_position(edge.start),
                                              geometry.vertex_position(edge.end)))
                   for edge in geometry.edges.values())
    assert shortest >= 0.625 - 1e-9


def test_other_barges_keep_their_side_longitudinals():
    assert _side_longitudinal_heights(floating_barge_section(), -5.5) == [0.7, 1.4, 2.1]


def test_explicit_side_longitudinals_are_validated():
    for heights in ((0.,), (2.5,), (1.4, 0.7), (0.7, 0.7), (float("nan"),)):
        with pytest.raises(ValueError, match="side longitudinal"):
            floating_barge_section(side_longitudinal_heights=heights)
