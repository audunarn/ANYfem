"""Focused integration tests for ANYtk3D's commercial selection profile."""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import numpy as np
import pytest
from anygeometry.entities import EntityRef
from anytk3d import (
    PickOwner,
    SelectionDepth,
    SelectionEvent,
    SelectionGesture,
    SelectionHit,
    SelectionOperation,
    SelectionTool,
)

from anyfem.selection import MeshEntityRef, Selection
from anyfem import Project
from anyfem.ui.scene import (
    FacePatch,
    PointMarker,
    Polyline,
    Scene,
    build_mesh_scene,
)
from anyfem.ui import viewport as viewport_module


def _point3d(*coordinates):
    return tuple(float(value) for value in coordinates)


class RichCanvas:
    """Headless recording double for the public ANYtk3D canvas contract."""

    def __init__(self, master, **options) -> None:
        self.master = master
        self.options = options
        self.profile = "legacy"
        self.selection_callback = None
        self.hover_callback = None
        self.selection_config = None
        self.config_updates = []
        self.legacy_pick = None
        self.highlights = []
        self.preselections = []
        self.face_calls = []
        self.line_calls = []
        self.marker_calls = []
        self.box_calls = []
        self.fit_calls = 0
        self.redraw_calls = 0

    def set_interaction_profile(self, profile: str) -> None:
        self.profile = profile

    def configure_selection(self, callback, *, hover_callback=None, config=None):
        self.selection_callback = callback
        self.hover_callback = hover_callback
        self.selection_config = config

    def update_selection_config(self, **changes):
        self.config_updates.append(changes)
        self.selection_config = replace(self.selection_config, **changes)
        return self.selection_config

    def set_pick_callback(self, callback, *, prefix="") -> None:
        self.legacy_pick = (callback, prefix)

    def set_preselection(self, key) -> None:
        self.preselections.append(key)

    def set_highlight(self, tags) -> None:
        self.highlights.append(list(tags))

    def add_faces(self, polygons, **options) -> None:
        self.face_calls.append((polygons, options))

    def add_line(self, start, end, **options) -> None:
        self.line_calls.append((start, end, options))

    def add_markers(self, points, **options) -> None:
        self.marker_calls.append((points, options))

    def add_box(self, *sizes, **options) -> None:
        self.box_calls.append((sizes, options))

    def add_sphere(self, *args, **kwargs) -> None:
        pass

    def add_arrow(self, *args, **kwargs) -> None:
        pass

    def set_thickness_legend(self, *args, **kwargs) -> None:
        pass

    def clear_thickness_legend(self) -> None:
        pass

    def clear(self, **kwargs) -> None:
        pass

    def fit_to_scene(self) -> None:
        self.fit_calls += 1

    def redraw(self) -> None:
        self.redraw_calls += 1

    def pack(self, **kwargs):
        return kwargs

    def grid(self, **kwargs):
        return kwargs

    def set_iso_view(self) -> None:
        pass

    def set_top_view(self) -> None:
        pass

    def set_front_view(self) -> None:
        pass

    def set_side_view(self) -> None:
        pass


class LegacyCanvas:
    def __init__(self, master, **options) -> None:
        self.legacy_pick = None
        self.highlights = []

    def set_pick_callback(self, callback, *, prefix="") -> None:
        self.legacy_pick = (callback, prefix)

    def set_highlight(self, tags) -> None:
        self.highlights.append(list(tags))


def _viewport(monkeypatch, selection=None, canvas_class=RichCanvas, **options):
    monkeypatch.setattr(
        viewport_module,
        "require_canvas",
        lambda: (_point3d, canvas_class),
    )
    return viewport_module.Viewport(
        object(), selection=selection, **options
    )


def _hit(identifier: int, *, key_style: str = "tag") -> SelectionHit:
    key = (
        f"geometry.face:{identifier}"
        if key_style == "canonical"
        else f"ent_face{identifier}"
    )
    return SelectionHit(
        PickOwner(key, "geometry.face", priority=10),
        primitive=identifier,
        depth=float(identifier),
    )


def _event(
    *hits: SelectionHit,
    operation=SelectionOperation.REPLACE,
    gesture=SelectionGesture.CLICK,
) -> SelectionEvent:
    return SelectionEvent(gesture=gesture, operation=operation, hits=hits)


def test_viewport_opts_into_commercial_profile_and_tracks_selection_filter(
    monkeypatch,
) -> None:
    selection = Selection("face")
    viewport = _viewport(monkeypatch, selection)
    canvas = viewport.canvas

    assert canvas.profile == "commercial"
    assert canvas.legacy_pick is None
    assert canvas.selection_callback is not None
    assert canvas.hover_callback is not None
    assert canvas.selection_config.filter.kinds == frozenset({"geometry.face"})

    viewport.configure_selection(
        tool="lasso", depth="through", operation="add"
    )
    assert canvas.selection_config.tool is SelectionTool.LASSO
    assert canvas.selection_config.depth is SelectionDepth.THROUGH

    selection.set_mode("edge")
    assert canvas.selection_config.filter.kinds == frozenset({"geometry.edge"})


def test_selection_events_map_owners_and_combine_toolbar_and_modifiers(
    monkeypatch,
) -> None:
    selection = Selection("face")
    viewport = _viewport(monkeypatch, selection)
    callback = viewport.canvas.selection_callback

    callback(_event(_hit(1, key_style="canonical")))
    assert selection.items == [EntityRef("face", 1)]
    # A mere selection highlight must not reset ANYtk3D's occlusion cycle.
    assert viewport.canvas.config_updates == []

    viewport.configure_selection(tool="box", depth="visible", operation="add")
    callback(_event(_hit(2)))  # unmodified REPLACE uses toolbar base operation
    assert selection.items == [EntityRef("face", 1), EntityRef("face", 2)]

    callback(_event(_hit(1), operation=SelectionOperation.REMOVE))
    assert selection.items == [EntityRef("face", 2)]

    callback(_event(_hit(2), operation=SelectionOperation.TOGGLE))
    assert selection.items == []

    viewport.configure_selection(
        tool="box", depth="through", operation="replace"
    )
    callback(
        _event(
            _hit(3),
            _hit(4),
            gesture=SelectionGesture.CROSSING,
        )
    )
    assert selection.items == [EntityRef("face", 3), EntityRef("face", 4)]


def test_single_tool_suppresses_region_gestures_but_keeps_clicks(monkeypatch) -> None:
    selection = Selection("face")
    viewport = _viewport(monkeypatch, selection)
    callback = viewport.canvas.selection_callback
    viewport.configure_selection(
        tool="single", depth="visible", operation="replace"
    )

    callback(_event(_hit(5)))
    callback(_event(_hit(6), gesture=SelectionGesture.WINDOW))
    assert selection.items == [EntityRef("face", 5)]


def test_scene_primitives_carry_geometry_owners_and_points_are_batched(
    monkeypatch,
) -> None:
    viewport = _viewport(monkeypatch, Selection("face"))
    scene = Scene(
        faces=[
            FacePatch(
                EntityRef("face", 7),
                [
                    np.asarray(
                        [[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0]],
                        dtype=float,
                    )
                ],
                ["#7799bb"],
            )
        ],
        lines=[
            Polyline(
                EntityRef("edge", 8),
                np.asarray([[0, 0, 0], [1, 0, 0], [1, 1, 0]], dtype=float),
            )
        ],
        points=[
            PointMarker(EntityRef("vertex", 9), np.asarray([0, 0, 0])),
            PointMarker(EntityRef("vertex", 10), np.asarray([1, 1, 0])),
        ],
    )

    viewport.show(scene)
    face_binding = viewport.canvas.face_calls[0][1]["bindings"]
    line_binding = viewport.canvas.line_calls[0][2]["binding"]
    marker_bindings = viewport.canvas.marker_calls[0][1]["bindings"]

    assert face_binding.owners == (PickOwner("ent_face7", "geometry.face", 10),)
    assert len(viewport.canvas.line_calls) == 2
    assert line_binding.owners == (PickOwner("ent_edge8", "geometry.edge", 20),)
    assert len(viewport.canvas.marker_calls) == 1
    assert [binding.owners[0].key for binding in marker_bindings] == [
        "ent_vertex9",
        "ent_vertex10",
    ]
    assert viewport.canvas.box_calls == []


def test_mesh_domain_filter_and_owner_are_forward_compatible(monkeypatch) -> None:
    selection = Selection("node")
    viewport = _viewport(monkeypatch, selection)
    node = MeshEntityRef("node", 21)

    assert viewport.canvas.selection_config.filter.kinds == frozenset(
        {"mesh.node"}
    )
    viewport.show(
        Scene(points=[PointMarker(node, np.asarray([0.0, 0.0, 0.0]))])
    )
    binding = viewport.canvas.marker_calls[0][1]["bindings"][0]
    assert binding.owners == (PickOwner("ent_node21", "mesh.node", 30),)

    viewport.canvas.selection_callback(
        _event(
            SelectionHit(
                PickOwner("mesh.node:21", "mesh.node", 30),
                primitive=0,
                depth=1.0,
            )
        )
    )
    assert selection.items == [node]


def test_mesh_scene_uses_multi_owner_faces_and_one_node_marker_batch(
    monkeypatch,
) -> None:
    project = Project("multi-owner")
    points = project.geometry.add_points(
        [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0),
         (1.0, 1.0, 0.0), (0.0, 1.0, 0.0)]
    )
    edges = project.geometry.add_polyline(points, close=True)
    face_id = project.geometry.add_face(edges)
    # Scene ownership is independent of solver sections, so a neutral mapped
    # mesh is sufficient for this viewport integration check.
    from anyfem.mesh.mapped import generate_mesh

    mesh = generate_mesh(project.geometry, target_size=0.5)
    element_id = mesh.elements_of_face[face_id][0]
    scene = build_mesh_scene(project, mesh)

    selection = Selection("element")
    viewport = _viewport(monkeypatch, selection)
    viewport.show(scene)

    bindings = viewport.canvas.face_calls[0][1]["bindings"]
    assert isinstance(bindings, list)
    assert {owner.kind for owner in bindings[0].owners} == {
        "geometry.face",
        "mesh.element",
        "mesh.element_face",
    }
    assert len(viewport.canvas.marker_calls) == 1
    marker_bindings = viewport.canvas.marker_calls[0][1]["bindings"]
    assert len(marker_bindings) == len(mesh.nodes)
    assert all(
        any(owner.kind == "mesh.node" for owner in binding.owners)
        for binding in marker_bindings
    )

    element_owner = next(
        owner for owner in bindings[0].owners if owner.kind == "mesh.element"
    )
    viewport.canvas.selection_callback(
        _event(SelectionHit(element_owner, primitive=0, depth=1.0))
    )
    assert selection.items == [MeshEntityRef("element", element_id)]

    selection.set_mode("face")
    geometry_owner = next(
        owner for owner in bindings[0].owners if owner.kind == "geometry.face"
    )
    viewport.canvas.selection_callback(
        _event(SelectionHit(geometry_owner, primitive=0, depth=1.0))
    )
    assert selection.items == [EntityRef("face", face_id)]

    selection.set_mode("element_face")
    element_face_owner = next(
        owner for owner in bindings[0].owners
        if owner.kind == "mesh.element_face"
    )
    viewport.canvas.selection_callback(
        _event(SelectionHit(element_face_owner, primitive=0, depth=1.0))
    )
    assert selection.items == [
        MeshEntityRef("element_face", (element_id, 0))
    ]


def test_hover_prehighlight_maps_owner_and_frame_selection_has_fallback(
    monkeypatch,
) -> None:
    selection = Selection("face")
    viewport = _viewport(monkeypatch, selection)
    hovered = []
    viewport.set_hover_handler(hovered.append)

    viewport.canvas.hover_callback(_hit(11, key_style="canonical"))
    assert viewport.hovered == EntityRef("face", 11)
    assert hovered[-1] == EntityRef("face", 11)
    assert viewport.canvas.preselections[-1] == "geometry.face:11"

    viewport.configure_selection(tool="lasso", depth="through")
    assert viewport.canvas.preselections[-1] == "geometry.face:11"

    selection.set_mode("edge")
    assert viewport.hovered is None
    assert hovered[-1] is None
    viewport.canvas.hover_callback(_hit(11))
    assert viewport.hovered is None
    assert viewport.canvas.preselections[-1] is None

    selection.set_mode("face")
    selection.select(EntityRef("face", 12))
    framed = []
    viewport.set_frame_selection_handler(lambda refs: framed.append(refs))
    redraws = viewport.canvas.redraw_calls
    viewport.frame_selection()
    assert framed == [[EntityRef("face", 12)]]
    assert viewport.canvas.redraw_calls == redraws + 1

    viewport.set_frame_selection_handler(lambda _refs: False)
    viewport.frame_selection()
    assert viewport.canvas.fit_calls == 1


def test_legacy_canvas_and_explicit_opt_out_keep_tag_pick_contract(monkeypatch) -> None:
    selection = Selection("face")
    legacy = _viewport(monkeypatch, selection, canvas_class=LegacyCanvas)
    callback, prefix = legacy.canvas.legacy_pick
    assert prefix == "ent_"

    callback(SimpleNamespace(tag="ent_face13", shift=False))
    assert selection.items == [EntityRef("face", 13)]

    opted_out = _viewport(monkeypatch, commercial_interaction=False)
    assert opted_out.canvas.profile == "legacy"
    assert opted_out.canvas.legacy_pick is not None
    assert opted_out.canvas.selection_callback is None


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        ({"tool": "paint"}, "selection tool"),
        ({"depth": "xray"}, "selection depth"),
        ({"operation": "union"}, "selection operation"),
    ],
)
def test_selection_configuration_rejects_unknown_policy(
    monkeypatch, arguments, message
) -> None:
    viewport = _viewport(monkeypatch)
    with pytest.raises(ValueError, match=message):
        viewport.configure_selection(**arguments)
