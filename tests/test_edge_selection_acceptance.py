"""Public viewer contract for a narrow crossing on an edge or curved rim."""
from any3dview import (PickBinding, ProjectedPrimitive, ProjectedSelectionIndex,
                       SelectionDepth, SelectionFilter)


def segment(index, points, key="rim", depth=2):
    return ProjectedPrimitive(index, "segment", points, (depth, depth),
                              PickBinding.one(key, "geometry.edge"))


def hits(index, rectangle, *, crossing=True, depth=SelectionDepth.VISIBLE):
    return {hit.key for hit in index.rectangle_hits(rectangle, SelectionFilter(kinds={"geometry.edge"}),
                                                   crossing=crossing, depth=depth)}


def test_narrow_visible_crossing_selects_clipped_segment_without_legacy_sample():
    index = ProjectedSelectionIndex([segment(0, ((0, 20), (100, 20)))], 120, 60)
    rectangle = (20, 18, 30, 22)
    assert hits(index, rectangle, depth=SelectionDepth.THROUGH) == {"rim"}
    assert hits(index, rectangle) == {"rim"}
    assert not hits(index, rectangle, crossing=False)


def test_clipped_crossing_keeps_real_surface_occlusion():
    occluder = ProjectedPrimitive(1, "polygon", ((10, 10), (40, 10), (40, 30), (10, 30)),
                                  (1,)*4, PickBinding.one("shell", "geometry.face"))
    index = ProjectedSelectionIndex([segment(0, ((0, 20), (100, 20))), occluder], 120, 60)
    rectangle = (20, 18, 30, 22)
    assert hits(index, rectangle, depth=SelectionDepth.THROUGH) == {"rim"}
    assert not hits(index, rectangle)


def test_grouped_curve_window_requires_every_segment_but_crossing_accepts_one():
    index = ProjectedSelectionIndex([
        segment(0, ((0, 20), (50, 20))), segment(1, ((50, 20), (100, 40))),
    ], 120, 60)
    rectangle = (10, 18, 20, 22)
    assert hits(index, rectangle) == {"rim"}
    assert not hits(index, rectangle, crossing=False)
    assert hits(index, (-1, 0, 110, 55), crossing=False) == {"rim"}
