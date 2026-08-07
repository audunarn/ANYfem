"""Turning a model into draw instructions.

The scene builder produces plain data -- polygons, polylines, markers, each
carrying the tag of the entity it came from.  The viewport executes it against
the 3D canvas.  Keeping the two apart means what gets drawn can be tested
without a display, and it is the reason a picked tag always resolves back to a
geometry entity.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, TypeVar

import numpy as np

from ..geometry.entities import EntityRef
from ..geometry.model import GeometryModel
from ..geometry.curves import Straight
from ..geometry.operations import surface_point
from ..mesh.mapped import Mesh, coons_grid, sample_chain
from ..model.project import Project
from ..selection import entity_tag

__all__ = [
    "Arrow",
    "Sphere",
    "FacePatch",
    "PointMarker",
    "Polyline",
    "Scene",
    "build_geometry_scene",
    "build_mesh_scene",
    "build_attribute_overlay",
    "build_collision_overlay",
    "build_result_scene",
    "entity_sample_points",
    "face_display_polygons",
    "face_normal",
    "geometry_characteristic_size",
]

# Faces are drawn as a coarse Coons tessellation.  Straight-sided plates need
# only one quad, but curved ones need enough to read as curved.
DISPLAY_DIVISIONS = 8

COLOR_PLATE = "#7ba7cc"
COLOR_LINE = "#37474f"
COLOR_BEAM = "#c1663a"
COLOR_POINT = "#263238"
COLOR_MESH_FILL = "#93b7c9"
COLOR_MESH_EDGE = "#4a6572"
COLOR_SUPPORT = "#2e7d32"
COLOR_LOAD = "#c62828"


@dataclass
class FacePatch:
    """One or more polygons drawn as a single tagged batch."""

    ref: Optional[EntityRef]
    polygons: List[np.ndarray]
    colors: List[str]
    outline: str = ""

    @property
    def tag(self) -> str:
        return "" if self.ref is None else entity_tag(self.ref)


@dataclass
class Polyline:
    """A 3D polyline: a modelled line, a beam, or an annotation."""

    ref: Optional[EntityRef]
    points: np.ndarray
    color: str = COLOR_LINE
    width: int = 2

    @property
    def tag(self) -> str:
        return "" if self.ref is None else entity_tag(self.ref)


@dataclass
class PointMarker:
    ref: Optional[EntityRef]
    position: np.ndarray
    color: str = COLOR_POINT
    size: float = 0.0

    @property
    def tag(self) -> str:
        return "" if self.ref is None else entity_tag(self.ref)


@dataclass
class Sphere:
    """A solid sphere annotation, for an impacting body."""

    centre: np.ndarray
    radius: float
    color: str = "#455a64"
    opacity: float = 1.0

    @property
    def tag(self) -> str:
        return ""


@dataclass
class Scene:
    """Everything to draw for one view of the model."""

    faces: List[FacePatch] = field(default_factory=list)
    lines: List[Polyline] = field(default_factory=list)
    points: List[PointMarker] = field(default_factory=list)
    arrows: List["Arrow"] = field(default_factory=list)
    spheres: List["Sphere"] = field(default_factory=list)
    legend: Optional[Dict[str, object]] = None

    def tags(self) -> List[str]:
        return [
            item.tag
            for group in (self.faces, self.lines, self.points)
            for item in group
            if item.tag
        ]

    def merge(self, other: "Scene") -> "Scene":
        """Draw another scene on top of this one."""

        self.faces.extend(other.faces)
        self.lines.extend(other.lines)
        self.points.extend(other.points)
        self.arrows.extend(other.arrows)
        self.spheres.extend(other.spheres)
        if other.legend is not None:
            self.legend = other.legend
        return self

    def bounds(self) -> Optional[tuple[np.ndarray, np.ndarray]]:
        """Axis-aligned bounds of everything in the scene."""

        low: Optional[np.ndarray] = None
        high: Optional[np.ndarray] = None

        def include(values) -> None:
            nonlocal low, high
            try:
                array = np.asarray(values, dtype=float)
            except (TypeError, ValueError):
                for value in values:
                    include(value)
                return
            if array.size == 0:
                return
            points = array.reshape(-1, 3)
            local_low = points.min(axis=0)
            local_high = points.max(axis=0)
            low = local_low if low is None else np.minimum(low, local_low)
            high = local_high if high is None else np.maximum(high, local_high)

        for patch in self.faces:
            include(patch.polygons)
        for line in self.lines:
            include(line.points)
        for marker in self.points:
            include(marker.position)
        for arrow in self.arrows:
            include(arrow.start)
            include(arrow.end)
        for sphere in self.spheres:
            offset = np.full(3, sphere.radius)
            include(sphere.centre - offset)
            include(sphere.centre + offset)
        if low is None or high is None:
            return None
        return low, high

    def characteristic_size(self) -> float:
        """A length to scale markers and symbols against."""

        extent = self.bounds()
        if extent is None:
            return 1.0
        span = float(np.linalg.norm(extent[1] - extent[0]))
        return span if span > 0.0 else 1.0


# ----------------------------------------------------------------------
# geometry
# ----------------------------------------------------------------------
def face_display_polygons(
    geometry: GeometryModel, face_id: int, divisions: int = DISPLAY_DIVISIONS
) -> List[np.ndarray]:
    """Tessellate a face for display, using the same blend the mesher uses.

    A plate is therefore never drawn as a different surface from the one that
    gets meshed.
    """

    face = geometry.faces[face_id]
    sides = face.sides()
    grid = coons_grid(
        sample_chain(geometry, sides[0], divisions),
        sample_chain(geometry, sides[1], divisions),
        sample_chain(geometry, sides[2], divisions)[::-1],
        sample_chain(geometry, sides[3], divisions)[::-1],
    )

    polygons: List[np.ndarray] = []
    for i in range(divisions):
        for j in range(divisions):
            polygons.append(
                np.array(
                    [grid[i, j], grid[i + 1, j], grid[i + 1, j + 1], grid[i, j + 1]]
                )
            )
    return polygons


def _flat_four_edge_polygon(
    geometry: GeometryModel, face_id: int
) -> Optional[np.ndarray]:
    """Return one exact display quad, or ``None`` when tessellation is needed."""

    sides = geometry.faces[face_id].sides()
    if any(len(side) != 1 for side in sides):
        return None
    if any(
        not isinstance(geometry.edges[side[0].edge].curve, Straight)
        for side in sides
    ):
        return None

    corners = np.array(
        [
            geometry.vertex_position(
                geometry.oriented_start_vertex(side[0])
            )
            for side in sides
        ]
    )
    first = corners[1] - corners[0]
    second = corners[2] - corners[0]
    normal = np.cross(first, second)
    normal_length = float(np.linalg.norm(normal))
    scale = max(
        float(np.linalg.norm(first)),
        float(np.linalg.norm(second)),
        float(np.linalg.norm(corners[3] - corners[0])),
        1.0e-12,
    )
    if normal_length <= 1.0e-12 * scale * scale:
        return None
    distance = abs(float((corners[3] - corners[0]) @ normal)) / normal_length
    return corners if distance <= 1.0e-9 * scale else None


def build_geometry_scene(
    project: Project,
    *,
    divisions: int = DISPLAY_DIVISIONS,
    curve_samples: int = 24,
    show_points: bool = True,
) -> Scene:
    """Draw the model as modelled: points, lines, plates."""

    geometry = project.geometry
    scene = Scene()

    for face_id in sorted(geometry.faces):
        flat_polygon = _flat_four_edge_polygon(geometry, face_id)
        polygons = (
            [flat_polygon]
            if flat_polygon is not None
            else face_display_polygons(geometry, face_id, divisions)
        )
        scene.faces.append(
            FacePatch(
                ref=EntityRef("face", face_id),
                polygons=polygons,
                colors=[COLOR_PLATE] * len(polygons),
                outline="",
            )
        )

    for edge_id in sorted(geometry.edges):
        is_beam = edge_id in project.edge_sections
        samples = (
            2
            if isinstance(geometry.edges[edge_id].curve, Straight)
            else curve_samples
        )
        scene.lines.append(
            Polyline(
                ref=EntityRef("edge", edge_id),
                points=geometry.sample_edge(
                    edge_id, np.linspace(0.0, 1.0, samples)
                ),
                color=COLOR_BEAM if is_beam else COLOR_LINE,
                width=4 if is_beam else 2,
            )
        )

    if show_points:
        for vertex_id in sorted(geometry.vertices):
            scene.points.append(
                PointMarker(
                    ref=EntityRef("vertex", vertex_id),
                    position=geometry.vertex_position(vertex_id),
                )
            )

    return scene


# ----------------------------------------------------------------------
# mesh
# ----------------------------------------------------------------------
def build_mesh_scene(project: Project, mesh: Mesh) -> Scene:
    """Draw the mesh, one tagged batch per plate so picking still works."""

    scene = Scene()

    for face_id, element_ids in sorted(mesh.elements_of_face.items()):
        polygons = [
            np.array([mesh.nodes[node] for node in mesh.corners_of(element_id)])
            for element_id in element_ids
        ]
        if not polygons:
            continue
        scene.faces.append(
            FacePatch(
                ref=EntityRef("face", face_id),
                polygons=polygons,
                colors=[COLOR_MESH_FILL] * len(polygons),
                outline=COLOR_MESH_EDGE,
            )
        )

    for edge_id, element_ids in sorted(mesh.elements_of_edge.items()):
        for element_id in element_ids:
            # Through every node, so a quadratic beam draws its curvature.
            span = mesh.beams[element_id]
            scene.lines.append(
                Polyline(
                    ref=EntityRef("edge", edge_id),
                    points=np.array([mesh.nodes[node] for node in span]),
                    color=COLOR_BEAM,
                    width=4,
                )
            )

    return scene


# ----------------------------------------------------------------------
# results
# ----------------------------------------------------------------------
def build_result_scene(
    shape,
    *,
    component: str = "magnitude",
    field: Optional[str] = None,
    scale: float = 1.0,
    colormap: Optional[Sequence[tuple[float, str]]] = None,
    limits: Optional[tuple[float, float]] = None,
    values: Optional[object] = None,
) -> Scene:
    """Draw the deformed shape, coloured by any field.

    ``field`` names what to colour by -- a displacement component or a stress
    component.  A per-node field is averaged onto each element for shading; a
    per-element field such as von Mises colours each element with its own
    value, which is where it actually lives.  ``values`` accepts a
    pre-computed :class:`~anyfem.post.fields.Field`, so an envelope can be
    drawn the same way a single shape is.
    """

    from ..post.fields import Field, evaluate_field, field_unit

    mesh = shape.built.mesh
    name = field or component
    if values is None:
        resolved = evaluate_field(shape, name)
    elif isinstance(values, Field):
        resolved = values
        name = resolved.name
    else:
        raise TypeError("values must be a Field")

    ordered_nodes = sorted(mesh.nodes)
    positions = shape.deformed_positions(scale)
    deformed = {node: positions[index] for index, node in enumerate(ordered_nodes)}

    low, high = limits if limits is not None else resolved.range()
    if high <= low:
        high = low + 1.0
    span = high - low

    def colour(value: float) -> str:
        return _ramp_color((value - low) / span, colormap)

    scene = Scene()
    shells = mesh.shells
    for face_id, element_ids in sorted(mesh.elements_of_face.items()):
        polygons: List[np.ndarray] = []
        colors: List[str] = []
        for element_id in element_ids:
            nodes = shells[element_id]
            polygons.append(
                np.array(
                    [deformed[node] for node in mesh.corners_of(element_id)]
                )
            )
            if resolved.per_element:
                value = resolved.element_values.get(element_id)
            else:
                value = float(
                    np.mean([resolved.node_values[node] for node in nodes])
                )
            colors.append(COLOR_MESH_FILL if value is None else colour(value))
        if polygons:
            scene.faces.append(
                FacePatch(
                    ref=EntityRef("face", face_id),
                    polygons=polygons,
                    colors=colors,
                    outline="",
                )
            )

    for edge_id, element_ids in sorted(mesh.elements_of_edge.items()):
        for element_id in element_ids:
            span = mesh.beams[element_id]
            if resolved.per_element:
                value = resolved.element_values.get(element_id)
                line_colour = COLOR_BEAM if value is None else colour(value)
            else:
                line_colour = colour(
                    float(
                        np.mean([resolved.node_values[node] for node in span])
                    )
                )
            scene.lines.append(
                Polyline(
                    ref=EntityRef("edge", edge_id),
                    points=np.array([deformed[node] for node in span]),
                    color=line_colour,
                    width=4,
                )
            )

    unit = resolved.unit or field_unit(name)
    scene.legend = {
        "levels": list(np.linspace(low, high, 5)),
        "unit": unit,
        "title": _component_title(name),
    }
    return scene


def _nodal_values(solution, component: str) -> np.ndarray:
    if component == "magnitude":
        return np.linalg.norm(solution.translations(), axis=1)
    return np.abs(solution.component(component))


_FIELD_TITLES = {
    "magnitude": "Displacement",
    "ux": "Displacement X",
    "uy": "Displacement Y",
    "uz": "Displacement Z",
    "rx": "Rotation X",
    "ry": "Rotation Y",
    "rz": "Rotation Z",
    "von_mises": "von Mises",
}


def _component_title(name: str) -> str:
    if name in _FIELD_TITLES:
        return _FIELD_TITLES[name]
    return name.replace("_", " ")


_DEFAULT_RAMP: tuple[tuple[float, str], ...] = (
    (0.00, "#3b4cc0"),
    (0.25, "#7396f5"),
    (0.50, "#e8e8e8"),
    (0.75, "#f49a7b"),
    (1.00, "#b40426"),
)


def _ramp_color(
    position: float, colormap: Optional[Sequence[tuple[float, str]]] = None
) -> str:
    stops = tuple(colormap) if colormap else _DEFAULT_RAMP
    value = min(max(float(position), 0.0), 1.0)
    for (low, low_color), (high, high_color) in zip(stops, stops[1:]):
        if value <= high:
            span = high - low
            local = 0.0 if span <= 0.0 else (value - low) / span
            return _blend(low_color, high_color, local)
    return stops[-1][1]


def _blend(first: str, second: str, amount: float) -> str:
    start = np.array([int(first[i : i + 2], 16) for i in (1, 3, 5)], dtype=float)
    end = np.array([int(second[i : i + 2], 16) for i in (1, 3, 5)], dtype=float)
    mixed = np.clip(start + (end - start) * amount, 0, 255).astype(int)
    return "#{:02x}{:02x}{:02x}".format(*mixed)


# ----------------------------------------------------------------------
# loads and supports
# ----------------------------------------------------------------------
COLOR_MASS = "#6a1b9a"
COLOR_PRESSURE = "#1565c0"
COLOR_MOMENT = "#ef6c00"
COLOR_ROTATION = "#00838f"

# Arrows are drawn at a fraction of the model size, not to scale with the
# load: a 10 kN arrow and a 10 MN arrow should both be readable.
ARROW_FRACTION = 0.10
SYMBOL_FRACTION = 0.022

# How many points along a line or across a plate carry a symbol.  Enough to
# read as "all along here", few enough not to bury the model.
_EDGE_SAMPLES = 3
_FACE_SAMPLES = 2

# Dense assignments stay visible without creating thousands of canvas items.
# Each attribute kind gets its own budget, so pressure cannot hide point loads
# or supports merely because it happens to be more numerous.
OVERLAY_SYMBOL_LIMIT = 256

_T = TypeVar("_T")


@dataclass
class Arrow:
    """An annotation arrow.  Never tagged, so it cannot be picked."""

    start: np.ndarray
    end: np.ndarray
    color: str = COLOR_LOAD

    @property
    def tag(self) -> str:
        return ""


def _limited(items: Sequence[_T], limit: int = OVERLAY_SYMBOL_LIMIT) -> List[_T]:
    """Keep a deterministic, evenly distributed subset of dense symbols."""

    if len(items) <= limit:
        return list(items)
    if limit <= 1:
        return [items[0]] if limit == 1 else []
    last = len(items) - 1
    return [items[index * last // (limit - 1)] for index in range(limit)]


def _entity_symbol_samples(ref: EntityRef) -> int:
    """Number of placement points produced for one entity, without sampling."""

    return {
        "vertex": 1,
        "edge": _EDGE_SAMPLES,
        "face": _FACE_SAMPLES * _FACE_SAMPLES,
    }.get(ref.kind, 0)


def _budgeted_assignments(items: Sequence[_T]) -> List[_T]:
    """Distribute a symbol budget across assignments before sampling geometry.

    ``entity_sample_points`` is substantially more expensive for a curved face
    than constructing an Arrow.  Limiting the finished Arrow list therefore
    comes too late: hundreds of discarded face samples have already been
    evaluated.  Every model attribute passed here has a geometry ``ref``.
    Using the largest sample count in a mixed list keeps placement work within
    the same budget while ``_limited`` retains the first, last and evenly
    distributed assignments.
    """

    if not items:
        return []
    samples = max(_entity_symbol_samples(item.ref) for item in items)
    if samples <= 0:
        return []
    return _limited(items, max(1, OVERLAY_SYMBOL_LIMIT // samples))


def _geometry_bounds(
    geometry: GeometryModel,
) -> Optional[tuple[np.ndarray, np.ndarray]]:
    if not geometry.vertices:
        return None
    positions = np.array(
        [
            geometry.vertex_position(vertex_id)
            for vertex_id in sorted(geometry.vertices)
        ],
        dtype=float,
    )
    return positions.min(axis=0), positions.max(axis=0)


def geometry_characteristic_size(geometry: GeometryModel) -> float:
    """Model span for annotation sizing, without constructing a scene."""

    bounds = _geometry_bounds(geometry)
    if bounds is None:
        return 1.0
    span = float(np.linalg.norm(bounds[1] - bounds[0]))
    return span if span > 0.0 else 1.0


def _geometry_centre(geometry: GeometryModel) -> np.ndarray:
    bounds = _geometry_bounds(geometry)
    if bounds is None:
        return np.zeros(3, dtype=float)
    return 0.5 * (bounds[0] + bounds[1])


def entity_sample_points(
    geometry: GeometryModel, ref: EntityRef, mesh: Optional[Mesh] = None
) -> List[np.ndarray]:
    """A few representative points on an entity, for placing symbols."""

    if ref.kind == "vertex":
        return [geometry.vertex_position(ref.id)]
    if ref.kind == "edge":
        return list(
            geometry.sample_edge(ref.id, np.linspace(0.15, 0.85, _EDGE_SAMPLES))
        )
    if ref.kind == "face":
        face = geometry.faces[ref.id]
        steps = np.linspace(0.25, 0.75, _FACE_SAMPLES)
        return [
            surface_point(geometry, face, float(u), float(v))
            for u in steps
            for v in steps
        ]
    return []


def face_normal(geometry: GeometryModel, face_id: int) -> np.ndarray:
    """An outward normal for a plate, from its Coons surface."""

    face = geometry.faces[face_id]
    origin = surface_point(geometry, face, 0.5, 0.5)
    along_u = surface_point(geometry, face, 0.75, 0.5) - origin
    along_v = surface_point(geometry, face, 0.5, 0.75) - origin
    normal = np.cross(along_u, along_v)
    length = float(np.linalg.norm(normal))
    if length <= 0.0:
        return np.array([0.0, 0.0, 1.0])
    return normal / length


def build_attribute_overlay(
    project,
    *,
    case_name: Optional[str] = "default",
    scale: Optional[float] = None,
    show_supports: bool = True,
    show_loads: bool = True,
    show_masses: bool = True,
) -> Scene:
    """Draw supports, loads and masses over the model.

    Nothing here carries an entity tag, so an arrow drawn over a plate never
    steals the click: picking walks past untagged items to the geometry behind.
    """

    geometry = project.geometry
    scene = Scene()
    span = (
        scale
        if scale is not None
        else geometry_characteristic_size(geometry)
    )
    arrow_length = ARROW_FRACTION * span
    symbol = SYMBOL_FRACTION * span

    if show_supports:
        supports = Scene()
        for support in _budgeted_assignments(project.supports):
            _draw_support(supports, geometry, support, symbol)
        scene.points.extend(_limited(supports.points))
        scene.lines.extend(_limited(supports.lines))
        scene.arrows.extend(_limited(supports.arrows))

    if show_masses:
        markers: List[PointMarker] = []
        for mass in _budgeted_assignments(project.masses):
            for point in entity_sample_points(geometry, mass.ref):
                markers.append(
                    PointMarker(
                        ref=None, position=point, color=COLOR_MASS,
                        size=1.6 * symbol,
                    )
                )
        scene.points.extend(_limited(markers))

    if show_loads and case_name is not None:
        case = project.load_cases.get(case_name)
        if case is not None:
            _draw_loads(scene, geometry, case, arrow_length)

    return scene


def _draw_support(scene: Scene, geometry, support, symbol: float) -> None:
    """Draw a restraint marker plus axes for the constrained directions."""

    held = len(support.constraints)
    prescribed_motion = any(
        abs(float(value)) > 0.0 for value in support.constraints.values()
    )
    colour = COLOR_LOAD if prescribed_motion else COLOR_SUPPORT
    size = symbol * (0.6 + 0.4 * held / 6.0)
    axes = {
        "x": np.array([1.0, 0.0, 0.0]),
        "y": np.array([0.0, 1.0, 0.0]),
        "z": np.array([0.0, 0.0, 1.0]),
    }
    for point in entity_sample_points(geometry, support.ref):
        scene.points.append(
            PointMarker(ref=None, position=point, color=colour, size=size)
        )
        for dof, value in support.constraints.items():
            axis = axes.get(dof[-1:])
            if axis is None:
                continue
            if dof.startswith("u"):
                scene.lines.append(
                    Polyline(
                        ref=None,
                        points=np.vstack([point, point + 1.8 * symbol * axis]),
                        color=COLOR_SUPPORT,
                        width=3,
                    )
                )
                if abs(float(value)) > 0.0:
                    direction = axis if float(value) > 0.0 else -axis
                    scene.arrows.append(
                        Arrow(
                            start=point,
                            end=point + 2.6 * symbol * direction,
                            color=COLOR_LOAD,
                        )
                    )
            elif dof.startswith("r"):
                scene.lines.append(
                    Polyline(
                        ref=None,
                        points=np.vstack(
                            [
                                point - 0.9 * symbol * axis,
                                point + 0.9 * symbol * axis,
                            ]
                        ),
                        color=COLOR_ROTATION,
                        width=2,
                    )
                )


def _draw_loads(scene: Scene, geometry, case, arrow_length: float) -> None:
    point_arrows: List[Arrow] = []
    moment_arrows: List[Arrow] = []
    moment_markers: List[PointMarker] = []
    force_loads = [load for load in case.point_loads if _unit(load.force) is not None]
    for load in _limited(force_loads):
        direction = _unit(load.force)
        point = geometry.vertex_position(load.ref.id)
        point_arrows.append(
            Arrow(start=point - arrow_length * direction, end=point)
        )

    moment_loads = [
        load for load in case.point_loads if _unit(load.moment) is not None
    ]
    for load in _limited(moment_loads):
        moment_direction = _unit(load.moment)
        point = geometry.vertex_position(load.ref.id)
        moment_arrows.append(
            Arrow(
                start=point - 0.5 * arrow_length * moment_direction,
                end=point + 0.5 * arrow_length * moment_direction,
                color=COLOR_MOMENT,
            )
        )
        moment_markers.append(
            PointMarker(
                ref=None,
                position=point,
                color=COLOR_MOMENT,
                size=0.25 * arrow_length,
            )
        )
    scene.arrows.extend(_limited(point_arrows))
    scene.arrows.extend(_limited(moment_arrows))
    scene.points.extend(_limited(moment_markers))

    line_arrows: List[Arrow] = []
    line_loads = [
        load for load in case.line_loads
        if _unit(load.force_per_length) is not None
    ]
    for load in _budgeted_assignments(line_loads):
        direction = _unit(load.force_per_length)
        for point in entity_sample_points(geometry, load.ref):
            line_arrows.append(
                Arrow(start=point - arrow_length * direction, end=point)
            )
    scene.arrows.extend(_limited(line_arrows))

    traction_arrows: List[Arrow] = []
    traction_loads = [
        load for load in case.surface_tractions if _unit(load.traction) is not None
    ]
    for load in _budgeted_assignments(traction_loads):
        direction = _unit(load.traction)
        for point in entity_sample_points(geometry, load.ref):
            traction_arrows.append(
                Arrow(start=point - arrow_length * direction, end=point)
            )
    scene.arrows.extend(_limited(traction_arrows))

    pressure_arrows: List[Arrow] = []
    pressure_loads = [load for load in case.pressures if load.value != 0.0]
    for load in _budgeted_assignments(pressure_loads):
        # Positive pressure pushes along the plate normal; the arrows show
        # which face it acts on, which is the thing that is easy to get wrong.
        normal = face_normal(geometry, load.ref.id)
        direction = normal if load.value > 0.0 else -normal
        for point in entity_sample_points(geometry, load.ref):
            pressure_arrows.append(
                Arrow(
                    start=point - 0.6 * arrow_length * direction,
                    end=point,
                    color=COLOR_PRESSURE,
                )
            )
    scene.arrows.extend(_limited(pressure_arrows))

    gravity_direction = _unit(case.gravity) if case.gravity is not None else None
    if gravity_direction is not None:
        point = _geometry_centre(geometry)
        scene.arrows.append(
            Arrow(
                start=point - arrow_length * gravity_direction,
                end=point,
                color=COLOR_MASS,
            )
        )


def _unit(vector) -> Optional[np.ndarray]:
    array = np.asarray(vector, dtype=float)
    length = float(np.linalg.norm(array))
    if length <= 0.0:
        return None
    return array / length


COLOR_SPHERE = "#455a64"
COLOR_PATH = "#78909c"


def build_collision_overlay(solution, index: int = 0) -> Scene:
    """Draw the sphere where it is at one step, and the path it took.

    The sphere is drawn at its actual radius, so an obviously wrong radius or
    aim is visible rather than only showing up as a strange contact force.
    """

    scene = Scene()
    positions = np.asarray(
        getattr(solution, "sphere_positions", np.zeros((0, 3))), dtype=float
    )
    if positions.size == 0:
        return scene

    collision = getattr(solution, "collision", None)
    radius = 0.1 if collision is None else float(collision.radius)

    step = min(max(int(index), 0), len(positions) - 1)
    scene.spheres.append(
        Sphere(centre=positions[step], radius=radius, color=COLOR_SPHERE)
    )
    if len(positions) > 1:
        scene.lines.append(
            Polyline(ref=None, points=positions, color=COLOR_PATH, width=2)
        )
    return scene
