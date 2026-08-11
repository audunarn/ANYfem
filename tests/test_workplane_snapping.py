from dataclasses import replace
from types import SimpleNamespace

import numpy as np
import pytest

from anyfem.document import DocumentSession
from anyfem.geometry.construction import (
    ConstructionMode,
    ConstructionTask,
)
from anyfem.geometry.snapping import (
    GeometrySnapData,
    SnapEngine,
    SnapKind,
    SnapPoint,
    SnapSegment,
    geometry_snap_data,
)
from anyfem.model.coordinates import CoordinateSystem, GLOBAL_COORDINATES
from anyfem.model.project import Project
from anyfem.model.workplanes import Workplane, WorkplaneError
from anyfem.io.project_file import project_to_dict
from anyfem.ui.viewport import Viewport


def systems(*extra):
    return {item.id: item for item in (GLOBAL_COORDINATES, *extra)}


def test_workplane_resolves_named_right_handed_basis_and_validates_grid():
    rotated = CoordinateSystem(
        id="deck",
        name="Deck",
        origin=(10.0, 20.0, 30.0),
        axis=(1.0, 0.0, 0.0),
        reference=(0.0, 1.0, 0.0),
    )
    plane = Workplane("deck", offset=2.5, grid_spacing=0.25)
    frame = plane.resolve(systems(rotated))

    np.testing.assert_allclose(frame.origin, (12.5, 20.0, 30.0))
    np.testing.assert_allclose(frame.x_axis, (0.0, 1.0, 0.0))
    np.testing.assert_allclose(frame.normal, (1.0, 0.0, 0.0))
    point = frame.world_position((3.0, -4.0))
    np.testing.assert_allclose(frame.plane_coordinates(point), (3.0, -4.0))
    np.testing.assert_allclose(frame.project(point + 7.0 * frame.normal), point)

    with pytest.raises(WorkplaneError, match="greater than zero"):
        Workplane(grid_spacing=0.0)
    with pytest.raises(WorkplaneError, match="unavailable"):
        Workplane("missing").resolve(systems())


def test_snap_ranking_is_deterministic_and_not_render_order_dependent():
    workplane = Workplane(grid_spacing=1.0, snap_tolerance=0.2)
    frame = workplane.resolve(systems())
    # Grid is closer, but a qualified endpoint has commercial object-snap
    # precedence while it remains inside the declared tolerance.
    data = GeometrySnapData(
        endpoints=(
            SnapPoint("vertex:z", (1.12, 1.0, 0.0)),
            SnapPoint("vertex:a", (1.12, 1.0, 0.0)),
        )
    )
    first = SnapEngine().snap((1.01, 1.0, 9.0), workplane, frame, data)
    second = SnapEngine().snap(
        (1.01, 1.0, 9.0),
        workplane,
        frame,
        GeometrySnapData(endpoints=tuple(reversed(data.endpoints))),
    )

    assert first.kind is SnapKind.ENDPOINT
    assert first.position == pytest.approx((1.12, 1.0, 0.0))
    assert first.candidate.source_keys == ("vertex:a",)
    assert second == first
    assert first.raw_position == pytest.approx((1.01, 1.0, 0.0))


def test_endpoint_intersection_midpoint_axes_and_grid_can_be_selected_independently():
    base = Workplane(grid_spacing=1.0, snap_tolerance=0.15)
    frame = base.resolve(systems())
    data = GeometrySnapData(
        endpoints=(SnapPoint("v:1", (2.0, 2.0, 0.0)),),
        midpoints=(SnapPoint("e:mid", (0.5, 1.5, 0.0)),),
        segments=(
            SnapSegment("e:1", (0.0, 0.0, 0.0), (2.0, 2.0, 0.0)),
            SnapSegment("e:2", (0.0, 2.0, 0.0), (2.0, 0.0, 0.0)),
        ),
    )

    result = SnapEngine().snap((1.04, 1.03, 0.0), base, frame, data)
    assert result.kind is SnapKind.INTERSECTION
    assert result.position == pytest.approx((1.0, 1.0, 0.0))

    midpoint_only = replace(
        base,
        snap_endpoints=False,
        snap_intersections=False,
        snap_axes=False,
        snap_grid=False,
    )
    result = SnapEngine().snap((0.54, 1.48, 0.0), midpoint_only, frame, data)
    assert result.kind is SnapKind.MIDPOINT

    axes_only = replace(
        base,
        snap_endpoints=False,
        snap_intersections=False,
        snap_midpoints=False,
        snap_grid=False,
    )
    result = SnapEngine().snap((2.0, 0.04, 0.0), axes_only, frame)
    assert result.kind is SnapKind.AXIS
    assert result.position == pytest.approx((2.0, 0.0, 0.0))

    grid_only = replace(
        base,
        snap_endpoints=False,
        snap_intersections=False,
        snap_midpoints=False,
        snap_axes=False,
    )
    result = SnapEngine().snap((1.06, 1.02, 0.0), grid_only, frame)
    assert result.kind is SnapKind.GRID


def test_intersection_query_is_bounded_and_ignores_chords_from_same_curve():
    engine = SnapEngine(max_intersection_segments=2)
    workplane = Workplane(
        snap_tolerance=0.25,
        snap_endpoints=False,
        snap_midpoints=False,
        snap_axes=False,
        snap_grid=False,
    )
    frame = workplane.resolve(systems())
    data = GeometrySnapData(
        segments=(
            SnapSegment("a:0", (-1, -1, 0), (1, 1, 0), owner_key="a"),
            SnapSegment("a:1", (-1, 1, 0), (1, -1, 0), owner_key="a"),
            SnapSegment("b:0", (0, -1, 0), (0, 1, 0), owner_key="b"),
        )
    )
    # Only the first two equally-near deterministic segments enter the bound,
    # and same-owner chords are not reported as a geometric intersection.
    result = engine.snap((0, 0, 0), workplane, frame, data)
    assert result.candidate is None


def test_geometry_snap_data_uses_stable_topology_keys_and_true_curve_midpoint():
    project = Project("snap data")
    first = project.geometry.add_point(0.0, 0.0, 0.0)
    via = project.geometry.add_point(1.0, 1.0, 0.0)
    last = project.geometry.add_point(2.0, 0.0, 0.0)
    edge = project.geometry.add_arc(first, via, last)

    data = geometry_snap_data(project.geometry, curve_segments=8)
    assert [point.key for point in data.endpoints] == [
        f"vertex:{first}", f"vertex:{via}", f"vertex:{last}"
    ]
    assert data.midpoints[0].key == f"edge:{edge}"
    assert len(data.segments) == 8
    assert {segment.owner_key for segment in data.segments} == {f"edge:{edge}"}


def test_construction_working_copy_commits_once_and_round_trips_undo_redo():
    project = Project("construction")
    session = DocumentSession(project)
    task = ConstructionTask(ConstructionMode.POLYLINE)
    task.add((0.0, 0.0, 0.0))
    task.add((1.0, 0.0, 0.0))
    task.add((1.0, 1.0, 0.0))

    assert not project.geometry.vertices
    assert not project.geometry.edges
    result = task.apply(session.execute)
    assert len(result.vertices) == 3
    assert len(result.edges) == 2
    assert session.revision.sequence == 1
    assert len(project.geometry.features.records) == 4  # three points + polyline
    assert task.points == ()

    assert session.undo()
    assert not project.geometry.vertices
    assert not project.geometry.edges
    assert session.redo()
    assert tuple(sorted(project.geometry.vertices)) == tuple(
        item.id for item in result.vertices
    )
    assert tuple(sorted(project.geometry.edges)) == tuple(item.id for item in result.edges)


def test_cancel_and_session_workplane_preferences_never_dirty_the_model():
    project = Project("preview")
    session = DocumentSession(project)
    original_hash = session.revision.document_hash
    task = ConstructionTask("line")
    task.add((0, 0, 0))
    task.cancel()
    session.active_workplane = Workplane(grid_spacing=0.125)

    assert task.points == ()
    assert task.cancelled
    assert project.geometry.vertices == {}
    assert session.revision.document_hash == original_hash
    assert not session.dirty


def test_failed_construction_apply_is_exactly_non_mutating(monkeypatch):
    project = Project("failed preview")
    session = DocumentSession(project)
    task = ConstructionTask("polyline")
    task.add((0.0, 0.0, 0.0))
    task.add((1.0, 0.0, 0.0))
    before = project_to_dict(project)

    def fail(*_args, **_kwargs):
        raise RuntimeError("synthetic polyline failure")

    monkeypatch.setattr(project.geometry, "add_polyline", fail)
    with pytest.raises(RuntimeError, match="synthetic"):
        task.apply(session.execute)

    assert project_to_dict(project) == before
    assert task.points == ((0.0, 0.0, 0.0), (1.0, 0.0, 0.0))
    assert session.revision.sequence == 0
    assert not session.dirty


class _UnprojectCanvas:
    def __init__(self, point=(0.24, 0.26, 0.0)):
        self.point = point
        self.calls = []

    def unproject_to_plane(self, x, y, plane_point, plane_normal):
        self.calls.append((x, y, plane_point, plane_normal))
        return self.point


def _headless_viewport(point=(0.24, 0.26, 0.0)):
    viewport = object.__new__(Viewport)
    viewport.canvas = _UnprojectCanvas(point)
    viewport._construction_task = None
    viewport._construction_workplane = None
    viewport._construction_coordinate_systems = None
    viewport._construction_snap_engine = None
    viewport._construction_snap_data = None
    viewport._on_construction_update = None
    viewport._on_construction_apply = None
    return viewport


def test_viewport_projects_with_anytk_plane_api_and_routes_click_enter_escape():
    viewport = _headless_viewport()
    plane = Workplane(grid_spacing=0.25, snap_tolerance=0.2)
    task = ConstructionTask("point")
    updates = []
    applies = []
    viewport.begin_construction(
        task,
        plane,
        systems(),
        update_handler=lambda active, snap: updates.append((active.points, snap)),
        apply_handler=lambda: applies.append("apply"),
    )

    event = SimpleNamespace(gesture="click", end=(50, 75))
    viewport._handle_selection_event(event)
    assert task.points == ((0.25, 0.25, 0.0),)
    assert updates[-1][1].kind is SnapKind.GRID
    assert viewport.canvas.calls[-1][0:2] == (50.0, 75.0)
    assert viewport.canvas.calls[-1][2] == pytest.approx((0.0, 0.0, 0.0))
    assert viewport.canvas.calls[-1][3] == pytest.approx((0.0, 0.0, 1.0))

    viewport._handle_construction_enter()
    assert applies == ["apply"]
    assert viewport.cancel_construction()
    assert task.cancelled
    assert not viewport.construction_active
    assert updates[-1][1] is None


def test_viewport_parallel_workplane_ray_leaves_preview_unchanged():
    viewport = _headless_viewport(point=None)
    task = ConstructionTask("line")
    viewport.begin_construction(task, Workplane(), systems())
    assert viewport.construction_click(10, 10) is None
    assert task.points == ()


class _OverlayCanvas:
    def __init__(self):
        self.lines = []
        self.markers = []

    def add_line(self, start, end, **options):
        self.lines.append((start, end, options))

    def add_markers(self, points, **options):
        self.markers.append((points, options))


def test_workplane_grid_overlay_is_bounded_even_for_tiny_spacing():
    viewport = object.__new__(Viewport)
    viewport.canvas = _OverlayCanvas()
    viewport._point3d = lambda *point: tuple(point)
    viewport._marker_size = 0.01
    viewport._construction_task = ConstructionTask("polyline")
    viewport._construction_task.add((0.0, 0.0, 0.0))
    viewport._construction_task.add((1.0, 1.0, 0.0))
    viewport._construction_workplane = Workplane(
        grid_spacing=1.0e-9, snap_tolerance=0.01
    )
    viewport._construction_coordinate_systems = systems()
    viewport._construction_grid_extent = (-1000.0, 1000.0, -1000.0, 1000.0)

    viewport._draw_construction_overlay()

    # 101 lines per direction plus one working polyline segment.
    assert len(viewport.canvas.lines) == 203
    assert len(viewport.canvas.markers[0][0]) == 2
