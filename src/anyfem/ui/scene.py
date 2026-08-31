"""Turning a model into draw instructions.

The scene builder produces plain data -- polygons, polylines, markers, each
carrying the tag of the entity it came from.  The viewport executes it against
the 3D canvas.  Keeping the two apart means what gets drawn can be tested
without a display, and it is the reason a picked tag always resolves back to a
geometry entity.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Dict, List, Optional, Sequence, TypeVar

import numpy as np
from anygeometry.curves import Straight
from anygeometry.entities import EntityRef
from anygeometry.model import GeometryModel
from anygeometry.operations import surface_point
from anygeometry.surfaces import Plane

from ..mesh.mapped import Mesh
from ..model.project import Project
from ..selection import MeshEntityRef, entity_tag

__all__ = [
    "Arrow",
    "Sphere",
    "FacePatch",
    "PointMarker",
    "Polyline",
    "Scene",
    "RESULT_COLORMAPS",
    "COLOR_IMPERFECTION",
    "build_geometry_scene",
    "build_mesh_scene",
    "build_attribute_overlay",
    "build_imperfection_overlay",
    "build_collision_overlay",
    "build_result_scene",
    "build_persisted_result_scene",
    "entity_sample_points",
    "face_display_polygons",
    "face_normal",
    "GeometryDisplayResolution",
    "geometry_display_resolution",
    "geometry_characteristic_size",
]

# Faces are drawn as a coarse Coons tessellation.  Straight-sided plates need
# only one quad, but curved ones need enough to read as curved.
DISPLAY_DIVISIONS = 8


@dataclass(frozen=True)
class GeometryDisplayResolution:
    """One full and interaction display resolution for model geometry.

    Structural cylinders/cones commonly contain many narrow curved faces.
    Tessellating each such face at the normal 8 x 8 preview resolution turns a
    few hundred engineering panels into tens of thousands of render polygons.
    The resolution is deliberately renderer-neutral so the viewport can swap
    to the interaction scene during pan/orbit without altering model intent.
    """

    divisions: int
    curve_samples: int
    interaction_divisions: int
    interaction_curve_samples: int


def geometry_display_resolution(
    geometry: GeometryModel,
    detail: str = "Auto",
) -> GeometryDisplayResolution:
    """Choose bounded display resolution for the current geometry.

    ``Auto`` preserves the high-quality preview for a small number of curved
    faces, then reduces *per-face* display tessellation as a revolved model
    gains structural segments.  Flat four-sided plates remain one polygon via
    :func:`_flat_four_edge_polygon`, so this specifically addresses curved
    geometry rather than reducing ordinary plate-model readability.
    """

    policies = {
        "Fine": (8, 24, 2, 8),
        "Balanced": (4, 16, 1, 6),
        "Fast": (2, 8, 1, 4),
    }
    try:
        normalized = str(detail).strip().title()
    except AttributeError as error:  # defensive for scripted display settings
        raise ValueError("geometry detail must be text") from error
    if normalized != "Auto":
        try:
            return GeometryDisplayResolution(*policies[normalized])
        except KeyError as error:
            raise ValueError(
                "geometry detail must be Auto, Fine, Balanced or Fast"
            ) from error

    curved_faces = sum(
        1
        for face in geometry.faces.values()
        if face.support_surface is not None
        and not isinstance(face.support_surface, Plane)
    )
    if curved_faces <= 64:
        return GeometryDisplayResolution(*policies["Fine"])
    if curved_faces <= 256:
        return GeometryDisplayResolution(*policies["Balanced"])
    return GeometryDisplayResolution(*policies["Fast"])

COLOR_PLATE = "#7ba7cc"
COLOR_LINE = "#37474f"
COLOR_BEAM = "#c1663a"
COLOR_POINT = "#263238"
COLOR_MESH_FILL = "#93b7c9"
COLOR_MESH_EDGE = "#4a6572"
COLOR_SUPPORT = "#2e7d32"
COLOR_LOAD = "#c62828"
COLOR_IMPERFECTION = "#8e24aa"

RESULT_COLORMAPS: Dict[str, tuple[tuple[float, str], ...]] = {
    "Cool-warm": (
        (0.00, "#3b4cc0"), (0.25, "#7396f5"), (0.50, "#e8e8e8"),
        (0.75, "#f49a7b"), (1.00, "#b40426"),
    ),
    "Viridis": (
        (0.00, "#440154"), (0.25, "#3b528b"), (0.50, "#21918c"),
        (0.75, "#5ec962"), (1.00, "#fde725"),
    ),
    "Plasma": (
        (0.00, "#0d0887"), (0.25, "#7e03a8"), (0.50, "#cc4778"),
        (0.75, "#f89540"), (1.00, "#f0f921"),
    ),
    "Turbo": (
        (0.00, "#30123b"), (0.20, "#466be3"), (0.40, "#1bcfd4"),
        (0.60, "#71fc6a"), (0.80, "#faba39"), (1.00, "#7a0403"),
    ),
    "Grayscale": ((0.00, "#101010"), (1.00, "#f5f5f5")),
}

SceneOwner = EntityRef | MeshEntityRef


@dataclass
class FacePatch:
    """One or more polygons drawn as a single tagged batch."""

    ref: Optional[SceneOwner]
    polygons: List[np.ndarray]
    colors: List[str]
    outline: str = ""
    # A tessellated geometry face has one semantic owner for the whole batch.
    # A mesh patch additionally has one element (and element-face) owner per
    # polygon.  Keeping those bindings as scene data lets ANYtk3D retain one
    # efficient face batch without losing element-level selection.
    owners: tuple[SceneOwner, ...] = ()
    polygon_owners: Optional[List[tuple[SceneOwner, ...]]] = None

    def __post_init__(self) -> None:
        if self.polygon_owners is not None and len(self.polygon_owners) != len(
            self.polygons
        ):
            raise ValueError(
                "polygon_owners must have one entry per face polygon"
            )

    @property
    def tag(self) -> str:
        return "" if self.ref is None else entity_tag(self.ref)

    def owners_for_polygon(self, index: int) -> tuple[SceneOwner, ...]:
        if self.polygon_owners is not None:
            return self.polygon_owners[index]
        if self.owners:
            return self.owners
        return () if self.ref is None else (self.ref,)


@dataclass
class Polyline:
    """A 3D polyline: a modelled line, a beam, or an annotation."""

    ref: Optional[SceneOwner]
    points: np.ndarray
    color: str = COLOR_LINE
    width: int = 2
    owners: tuple[SceneOwner, ...] = ()
    # Keep structural centre-lines legible when they are coplanar with a
    # shell.  Depth clipping otherwise makes one connected beam appear as
    # disconnected alternating segments.
    draw_overlay: bool = False

    @property
    def tag(self) -> str:
        return "" if self.ref is None else entity_tag(self.ref)

    @property
    def pick_owners(self) -> tuple[SceneOwner, ...]:
        if self.owners:
            return self.owners
        return () if self.ref is None else (self.ref,)


@dataclass
class PointMarker:
    ref: Optional[SceneOwner]
    position: np.ndarray
    color: str = COLOR_POINT
    size: float = 0.0
    owners: tuple[SceneOwner, ...] = ()

    @property
    def tag(self) -> str:
        return "" if self.ref is None else entity_tag(self.ref)

    @property
    def pick_owners(self) -> tuple[SceneOwner, ...]:
        if self.owners:
            return self.owners
        return () if self.ref is None else (self.ref,)


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
    if face.holes:
        # ANYgeometry owns the trim loops; Shapely supplies a constrained
        # planar triangulation which is then mapped back to the authoritative
        # structural surface.  Filtering with ``covers`` prevents Delaunay
        # triangles from bridging a hole or concave outer trim.
        from shapely.geometry import Polygon
        from shapely.ops import triangulate

        loops = geometry.face_trim_loops_uv(
            face_id, curve_samples=max(17, divisions * 4 + 1)
        )
        trimmed = Polygon(loops[0], holes=loops[1:])
        if not trimmed.is_valid or trimmed.is_empty:
            raise ValueError(f"face {face_id} has an invalid display trim")
        polygons = []
        for triangle in triangulate(trimmed):
            if not trimmed.covers(triangle):
                continue
            uv = list(triangle.exterior.coords)[:-1]
            polygons.append(
                np.asarray(
                    [
                        surface_point(geometry, face, float(u), float(v))
                        for u, v in uv
                    ]
                )
            )
        return polygons

    parameters = np.linspace(0.0, 1.0, divisions + 1)
    grid = np.asarray(
        [
            [surface_point(geometry, face, float(u), float(v)) for v in parameters]
            for u in parameters
        ]
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
    """Return one authoritative display quad, or require tessellation."""

    face = geometry.faces[face_id]
    if face.holes:
        return None
    sides = face.sides()
    if any(len(side) != 1 for side in sides):
        return None
    if any(
        not isinstance(geometry.edges[side[0].edge].curve, Straight)
        for side in sides
    ):
        return None

    # Straight topology does not prove a flat explicit surface (a ruled
    # surface can have straight carrier edges).  Sample through ANYgeometry so
    # the GUI fast path cannot flatten a surface the geometry owner keeps
    # curved.
    parameters = (0.0, 0.5, 1.0)
    sampled = np.asarray(
        [
            geometry.face_point(face_id, u, v)
            for u in parameters
            for v in parameters
        ]
    )
    corners = sampled[[0, 6, 8, 2]]
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
    distances = np.abs((sampled - corners[0]) @ normal) / normal_length
    return corners if float(np.max(distances)) <= 1.0e-9 * scale else None


def _beam_section_polygons(section, points, reference_z) -> List[np.ndarray]:
    """Sweep the actual idealized section rectangles along a beam polyline."""

    coordinates = np.asarray(points, dtype=float)
    reference_z = np.asarray(reference_z, dtype=float)
    polygons: List[np.ndarray] = []
    for start, end in zip(coordinates, coordinates[1:]):
        axis = end - start
        length = float(np.linalg.norm(axis))
        if length <= 0.0:
            continue
        axis /= length
        local_z = reference_z - float(reference_z @ axis) * axis
        z_length = float(np.linalg.norm(local_z))
        if z_length <= 1.0e-12:
            fallback = np.eye(3)[int(np.argmin(np.abs(np.eye(3) @ axis)))]
            local_z = fallback - float(fallback @ axis) * axis
            z_length = float(np.linalg.norm(local_z))
        local_z /= z_length
        local_y = np.cross(local_z, axis)
        local_y /= float(np.linalg.norm(local_y))
        for y, z, width, height in section.centered_profile_rectangles():
            if width <= 0.0 or height <= 0.0:
                continue
            offsets = (
                (y - width / 2.0) * local_y + (z - height / 2.0) * local_z,
                (y + width / 2.0) * local_y + (z - height / 2.0) * local_z,
                (y + width / 2.0) * local_y + (z + height / 2.0) * local_z,
                (y - width / 2.0) * local_y + (z + height / 2.0) * local_z,
            )
            first = [start + offset for offset in offsets]
            last = [end + offset for offset in offsets]
            polygons.extend(
                (
                    np.asarray(first),
                    np.asarray(last[::-1]),
                    np.asarray((first[0], first[1], last[1], last[0])),
                    np.asarray((first[1], first[2], last[2], last[1])),
                    np.asarray((first[2], first[3], last[3], last[2])),
                    np.asarray((first[3], first[0], last[0], last[3])),
                )
            )
    return polygons


def _add_beam_section_patch(
    scene: Scene,
    section,
    points,
    reference_z,
    *,
    color: str,
    ref: Optional[SceneOwner],
    owners: tuple[SceneOwner, ...] = (),
) -> bool:
    polygons = _beam_section_polygons(section, points, reference_z)
    if not polygons:
        return False
    scene.faces.append(
        FacePatch(
            ref=ref,
            polygons=polygons,
            colors=[color] * len(polygons),
            outline=COLOR_MESH_EDGE,
            owners=owners,
        )
    )
    return True


def build_geometry_scene(
    project: Project,
    *,
    divisions: int = DISPLAY_DIVISIONS,
    curve_samples: int = 24,
    show_points: bool = True,
    show_beam_sections: bool = True,
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
        points = geometry.sample_edge(
            edge_id, np.linspace(0.0, 1.0, samples)
        )
        rendered_section = False
        if is_beam and show_beam_sections:
            section = project.beam_section_of(edge_id)
            _axis, _local_y, local_z = project.beam_section_frame(edge_id)
            points = points + np.asarray(project.beam_offset_vector(edge_id))
            rendered_section = _add_beam_section_patch(
                scene,
                section,
                points,
                local_z,
                color=COLOR_BEAM,
                ref=EntityRef("edge", edge_id),
            )
        scene.lines.append(
            Polyline(
                ref=EntityRef("edge", edge_id),
                points=points,
                color=COLOR_BEAM if is_beam else COLOR_LINE,
                width=(3 if rendered_section else 4) if is_beam else 2,
                draw_overlay=is_beam,
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
def build_mesh_scene(
    project: Project, mesh: Mesh, *, show_beam_sections: bool = True
) -> Scene:
    """Draw a retained mesh with geometry and FE ownership in one scene.

    Shells remain batched by their owning geometry plate for rendering and
    legacy tag picking.  Each polygon nevertheless carries its own mesh
    element and element-face references for the commercial selection index.
    Nodes are emitted as marker data and the viewport submits all of them via
    one :meth:`ANYtk3D.add_markers` call.
    """

    scene = Scene()
    shells = mesh.shells
    grouped_shells: set[int] = set()

    for face_id, element_ids in sorted(mesh.elements_of_face.items()):
        available = [
            int(element_id)
            for element_id in element_ids
            if int(element_id) in shells
        ]
        polygons = [
            np.array([mesh.nodes[node] for node in mesh.corners_of(element_id)])
            for element_id in available
        ]
        if not polygons:
            continue
        grouped_shells.update(available)
        geometry_ref = (
            EntityRef("face", face_id)
            if face_id in project.geometry.faces
            else None
        )
        polygon_owners = [
            tuple(
                owner
                for owner in (
                    geometry_ref,
                    MeshEntityRef("element", element_id),
                    MeshEntityRef("element_face", (element_id, 0)),
                )
                if owner is not None
            )
            for element_id in available
        ]
        scene.faces.append(
            FacePatch(
                ref=geometry_ref,
                polygons=polygons,
                colors=[COLOR_MESH_FILL] * len(polygons),
                outline=COLOR_MESH_EDGE,
                polygon_owners=polygon_owners,
            )
        )

    # Imported meshes are allowed to have incomplete geometry associations.
    # They must still be visible and selectable directly as mesh entities.
    ungrouped_shells = sorted(set(shells) - grouped_shells)
    if ungrouped_shells:
        scene.faces.append(
            FacePatch(
                ref=None,
                polygons=[
                    np.array(
                        [mesh.nodes[node] for node in mesh.corners_of(element_id)]
                    )
                    for element_id in ungrouped_shells
                ],
                colors=[COLOR_MESH_FILL] * len(ungrouped_shells),
                outline=COLOR_MESH_EDGE,
                polygon_owners=[
                    (
                        MeshEntityRef("element", element_id),
                        MeshEntityRef("element_face", (element_id, 0)),
                    )
                    for element_id in ungrouped_shells
                ],
            )
        )

    grouped_beams: set[int] = set()
    for edge_id, element_ids in sorted(mesh.elements_of_edge.items()):
        for element_id in element_ids:
            if element_id not in mesh.beams:
                continue
            grouped_beams.add(int(element_id))
            # Through every node, so a quadratic beam draws its curvature.
            beam_nodes = mesh.beams[element_id]
            geometry_ref = (
                EntityRef("edge", edge_id)
                if edge_id in project.geometry.edges
                else None
            )
            owners = tuple(
                owner
                for owner in (
                    geometry_ref,
                    MeshEntityRef("element", int(element_id)),
                )
                if owner is not None
            )
            rendered_section = False
            if show_beam_sections and edge_id in project.edge_sections:
                section = project.beam_section_of(edge_id)
                _axis, _local_y, local_z = project.beam_section_frame(edge_id)
                rendered_section = _add_beam_section_patch(
                    scene,
                    section,
                    np.asarray([mesh.nodes[node] for node in beam_nodes]),
                    local_z,
                    color=COLOR_BEAM,
                    ref=geometry_ref,
                    owners=owners,
                )
            scene.lines.append(
                Polyline(
                    ref=geometry_ref,
                    points=np.array([mesh.nodes[node] for node in beam_nodes]),
                    color=COLOR_BEAM,
                    width=1 if rendered_section else 4,
                    draw_overlay=True,
                    owners=owners,
                )
            )

    for element_id in sorted(set(mesh.beams) - grouped_beams):
        span = mesh.beams[element_id]
        scene.lines.append(
            Polyline(
                ref=None,
                points=np.array([mesh.nodes[node] for node in span]),
                color=COLOR_BEAM,
                width=4,
                draw_overlay=True,
                owners=(MeshEntityRef("element", int(element_id)),),
            )
        )

    vertex_of_node = {
        int(node_id): int(vertex_id)
        for vertex_id, node_id in mesh.node_of_vertex.items()
        if vertex_id in project.geometry.vertices
    }
    for node_id in sorted(mesh.nodes):
        vertex_id = vertex_of_node.get(int(node_id))
        geometry_ref = (
            None if vertex_id is None else EntityRef("vertex", vertex_id)
        )
        mesh_ref = MeshEntityRef("node", int(node_id))
        scene.points.append(
            PointMarker(
                ref=mesh_ref,
                position=np.asarray(mesh.nodes[node_id], dtype=float),
                owners=tuple(
                    owner
                    for owner in (geometry_ref, mesh_ref)
                    if owner is not None
                ),
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
    display_units: str = "SI (m / Pa)",
    show_nodes: bool = False,
    show_beam_sections: bool = True,
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
    try:
        positions = shape.deformed_positions(scale)
    except KeyError:
        # Imported stress-only files have no translation field.  They remain
        # valid contour results and are displayed on the undeformed mesh.
        positions = mesh.node_positions()
    deformed = {node: positions[index] for index, node in enumerate(ordered_nodes)}
    # Lightweight imported/test result adapters historically expose only a
    # mesh on ``built``.  Geometry ownership is additive: keep those adapters
    # renderable and simply omit a geometry PickOwner when no project exists.
    built_project = getattr(shape.built, "project", None)
    geometry = getattr(built_project, "geometry", None)

    from .result_display import unit_transform

    value_scale, display_unit = unit_transform(resolved.unit, display_units)
    finite_values = [
        float(value)
        for value in resolved.values.values()
        if np.isfinite(float(value))
    ]
    has_values = bool(finite_values)
    if limits is None:
        if has_values:
            raw_low, raw_high = min(finite_values), max(finite_values)
            if raw_high <= raw_low:
                raw_high = raw_low + 1.0
        else:
            raw_low, raw_high = 0.0, 1.0
        low, high = raw_low * value_scale, raw_high * value_scale
    else:
        low, high = limits
    if high <= low:
        high = low + 1.0
    span = high - low

    def colour(value: float) -> str:
        return _ramp_color((value * value_scale - low) / span, colormap)

    def nodal_average(node_ids: Sequence[int]) -> Optional[float]:
        """Average only a complete finite nodal value set.

        A result quantity may deliberately cover only part of a mixed mesh.
        Missing values are unavailable, not zero, and must not make result
        browsing fail merely because an adjacent element uses another result
        location or formulation.
        """

        samples = []
        for node_id in node_ids:
            value = resolved.node_values.get(int(node_id))
            if value is None or not np.isfinite(float(value)):
                return None
            samples.append(float(value))
        return float(np.mean(samples)) if samples else None

    scene = Scene()
    shells = mesh.shells
    for face_id, element_ids in sorted(mesh.elements_of_face.items()):
        polygons: List[np.ndarray] = []
        colors: List[str] = []
        rendered_ids: List[int] = []
        for element_id in element_ids:
            if element_id not in shells:
                continue
            nodes = shells[element_id]
            polygons.append(
                np.array(
                    [deformed[node] for node in mesh.corners_of(element_id)]
                )
            )
            rendered_ids.append(int(element_id))
            if resolved.per_element:
                value = resolved.element_values.get(element_id)
            else:
                value = nodal_average(nodes)
            colors.append(
                COLOR_MESH_FILL
                if value is None or not np.isfinite(float(value))
                else colour(float(value))
            )
        if polygons:
            geometry_ref = (
                EntityRef("face", face_id)
                if geometry is not None and face_id in geometry.faces
                else None
            )
            scene.faces.append(
                FacePatch(
                    ref=geometry_ref,
                    polygons=polygons,
                    colors=colors,
                    outline="",
                    polygon_owners=[
                        tuple(
                            owner
                            for owner in (
                                geometry_ref,
                                MeshEntityRef("element", element_id),
                                MeshEntityRef(
                                    "element_face", (element_id, 0)
                                ),
                            )
                            if owner is not None
                        )
                        for element_id in rendered_ids
                    ],
                )
            )

    for edge_id, element_ids in sorted(mesh.elements_of_edge.items()):
        for element_id in element_ids:
            if element_id not in mesh.beams:
                continue
            beam_nodes = mesh.beams[element_id]
            if resolved.per_element:
                value = resolved.element_values.get(element_id)
                line_colour = (
                    COLOR_BEAM
                    if value is None or not np.isfinite(float(value))
                    else colour(float(value))
                )
            else:
                value = nodal_average(beam_nodes)
                line_colour = (
                    COLOR_BEAM if value is None else colour(float(value))
                )
            geometry_ref = (
                EntityRef("edge", edge_id)
                if geometry is not None and edge_id in geometry.edges
                else None
            )
            owners = tuple(
                owner
                for owner in (
                    geometry_ref,
                    MeshEntityRef("element", int(element_id)),
                )
                if owner is not None
            )
            rendered_section = False
            if (
                show_beam_sections
                and built_project is not None
                and edge_id in built_project.edge_sections
            ):
                section = built_project.beam_section_of(edge_id)
                _axis, _local_y, local_z = built_project.beam_section_frame(edge_id)
                rendered_section = _add_beam_section_patch(
                    scene,
                    section,
                    np.asarray([deformed[node] for node in beam_nodes]),
                    local_z,
                    color=line_colour,
                    ref=geometry_ref,
                    owners=owners,
                )
            scene.lines.append(
                Polyline(
                    ref=geometry_ref,
                    points=np.array([deformed[node] for node in beam_nodes]),
                    color=line_colour,
                    width=1 if rendered_section else 4,
                    draw_overlay=True,
                    owners=owners,
                )
            )

    if show_nodes:
        scene.points.extend(
            PointMarker(
                ref=MeshEntityRef("node", int(node)),
                position=np.asarray(deformed[node], dtype=float),
                color=COLOR_POINT,
                owners=(MeshEntityRef("node", int(node)),),
            )
            for node in ordered_nodes
        )

    unit = display_unit or field_unit(name)
    legend_colors = (
        [colour(float(value) / value_scale) for value in np.linspace(low, high, 5)]
        if has_values
        else []
    )
    scene.legend = {
        "levels": list(np.linspace(low, high, 5)) if has_values else [],
        "unit": unit,
        "title": (
            _component_title(name)
            if has_values
            else f"{_component_title(name)} (unavailable)"
        ),
        "colors": legend_colors,
    }
    return scene


def build_persisted_result_scene(
    project: Project,
    mesh: Mesh,
    dataset,
    field_key: str,
    *,
    frame: int = 0,
    component: str | None = None,
    scale: float = 1.0,
    limits: Optional[tuple[float, float]] = None,
    colormap: Optional[Sequence[tuple[float, str]]] = None,
    display_units: str = "SI (m / Pa)",
    show_nodes: bool = False,
    show_beam_sections: bool = True,
) -> Scene:
    """Render one lazily-read artifact field without inventing quantities."""

    from ..post.fields import Field

    stored = dataset.field(field_key)
    descriptor = stored.descriptor
    values = stored.read(_artifact_frame(stored.shape, frame))
    location = descriptor.location
    if location not in ("node", "element", "element_face", "integration_point"):
        raise ValueError(f"{descriptor.label} is not a spatial contour quantity")
    table_kind = "node" if location == "node" else "element"
    table_key = f"{field_key}_{table_kind}_ids"
    if table_key not in dataset.table_keys:
        raise ValueError(f"{descriptor.label} has no persisted {table_kind} association")
    identifiers = np.asarray(dataset.table(table_key), dtype=int).reshape(-1)
    scalar = _artifact_scalar_rows(values, descriptor.components, component)
    if len(scalar) != len(identifiers):
        raise ValueError(
            f"{descriptor.label} has {len(scalar)} values for {len(identifiers)} IDs"
        )
    mapping = {
        int(identifier): float(value)
        for identifier, value in zip(identifiers, scalar)
        if np.isfinite(value)
    }
    field = Field(
        name=field_key,
        unit=descriptor.unit,
        node_values=mapping if table_kind == "node" else {},
        element_values=mapping if table_kind == "element" else {},
        reduction=descriptor.reduction,
    )

    ordered_nodes = sorted(mesh.nodes)
    positions = np.asarray([mesh.nodes[node] for node in ordered_nodes], dtype=float)
    if "displacement" in dataset.field_keys and scale != 0.0:
        displacement = dataset.field("displacement")
        displacement_key = "displacement_node_ids"
        if displacement_key in dataset.table_keys:
            displacement_values = np.asarray(
                displacement.read(_artifact_frame(displacement.shape, frame)),
                dtype=float,
            )
            displacement_ids = np.asarray(
                dataset.table(displacement_key), dtype=int
            ).reshape(-1)
            if displacement_values.ndim >= 2 and displacement_values.shape[-1] >= 3:
                by_node = {
                    int(node): np.asarray(row[..., :3], dtype=float).reshape(-1, 3)[0]
                    for node, row in zip(displacement_ids, displacement_values)
                }
                positions = np.asarray(
                    [
                        mesh.nodes[node]
                        + scale * by_node.get(int(node), np.zeros(3))
                        for node in ordered_nodes
                    ],
                    dtype=float,
                )

    shape = SimpleNamespace(
        built=SimpleNamespace(mesh=mesh, project=project),
        deformed_positions=lambda _scale=1.0: positions,
    )
    return build_result_scene(
        shape,
        field=field_key,
        scale=1.0,
        limits=limits,
        values=field,
        colormap=colormap,
        display_units=display_units,
        show_nodes=show_nodes,
        show_beam_sections=show_beam_sections,
    )


def _artifact_frame(shape: Sequence[int], frame: int) -> int | None:
    if not shape:
        return None
    return min(max(int(frame), 0), int(shape[0]) - 1)


def _artifact_scalar_rows(
    values: np.ndarray, components: Sequence[str], component: str | None
) -> np.ndarray:
    array = np.asarray(values, dtype=float)
    if array.ndim == 0:
        return array.reshape(1)
    if array.ndim == 1:
        return array
    if component and component in components:
        array = np.take(array, components.index(component), axis=-1)
    elif components and array.shape[-1] == len(components):
        translation_count = 3 if set(("ux", "uy", "uz")) <= set(components) else len(components)
        array = np.linalg.norm(array[..., :translation_count], axis=-1)
    while array.ndim > 1:
        array = np.nanmax(np.abs(array), axis=-1)
    return array


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
    "equivalent_plastic_strain": "Equivalent plastic strain (PEEQ)",
}


def _component_title(name: str) -> str:
    if name in _FIELD_TITLES:
        return _FIELD_TITLES[name]
    return name.replace("_", " ")


_DEFAULT_RAMP = RESULT_COLORMAPS["Cool-warm"]


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
    """The authoritative surface normal at the centre of a plate."""

    return geometry.face_normal(face_id, 0.5, 0.5)


def _imperfection_amplitude(item, coordinates: np.ndarray) -> float:
    if item.amplitude is not None:
        return float(item.amplitude)
    if len(coordinates) < 2:
        return 0.0
    if item.resolved_kind == "plate_mode":
        axes = [int(item.axes[0]), int(item.axes[1])]
        spans = np.ptp(coordinates[:, axes], axis=0)
        return float(np.min(spans) / 200.0)
    return float(np.linalg.norm(coordinates[-1] - coordinates[0]) / 300.0)


def _preview_factor(amplitude: float, span: float) -> float:
    """Make small fabrication-scale offsets visible without changing the model."""

    if amplitude <= 0.0:
        return 1.0
    return min(50.0, max(1.0, 0.03 * span / amplitude))


def _plate_preview_grid(geometry, item, mesh: Optional[Mesh]) -> np.ndarray:
    if mesh is not None and item.ref.id in mesh.grid_of_face:
        identifiers = np.asarray(mesh.grid_of_face[item.ref.id])
        if identifiers.ndim == 2 and identifiers.size:
            return np.asarray(
                [[mesh.nodes[int(node)] for node in row] for row in identifiers],
                dtype=float,
            )
    face = geometry.faces[item.ref.id]
    parameters = np.linspace(0.0, 1.0, 13)
    return np.asarray(
        [
            [surface_point(geometry, face, float(u), float(v)) for v in parameters]
            for u in parameters
        ],
        dtype=float,
    )


def _plate_imperfection_preview(
    geometry, item, mesh: Optional[Mesh], span: float
) -> tuple[list[np.ndarray], Optional[Arrow]]:
    grid = _plate_preview_grid(geometry, item, mesh)
    coordinates = grid.reshape(-1, 3)
    amplitude = _imperfection_amplitude(item, coordinates)
    direction = np.asarray(item.direction, dtype=float)
    direction /= float(np.linalg.norm(direction))
    axes = [int(item.axes[0]), int(item.axes[1])]
    selected = coordinates[:, axes]
    lower = selected.min(axis=0)
    spans = np.maximum(selected.max(axis=0) - lower, 1.0e-14)
    sx = (selected[:, 0] - lower[0]) / spans[0]
    sy = (selected[:, 1] - lower[1]) / spans[1]
    shape = (
        np.sin(int(item.waves[0]) * np.pi * sx)
        * np.sin(int(item.waves[1]) * np.pi * sy)
    )
    offsets = (
        _preview_factor(amplitude, span)
        * amplitude
        * shape[:, None]
        * direction[None, :]
    )
    displaced = (coordinates + offsets).reshape(grid.shape)
    lines = [np.asarray(row) for row in displaced]
    lines.extend(np.asarray(column) for column in np.swapaxes(displaced, 0, 1))
    peak = int(np.argmax(np.linalg.norm(offsets, axis=1)))
    arrow = None
    if float(np.linalg.norm(offsets[peak])) > 0.0:
        arrow = Arrow(
            start=coordinates[peak],
            end=coordinates[peak] + offsets[peak],
            color=COLOR_IMPERFECTION,
        )
    return lines, arrow


def _member_imperfection_preview(
    geometry, item, mesh: Optional[Mesh], span: float
) -> tuple[np.ndarray, Optional[Arrow]]:
    if mesh is not None and item.ref.id in mesh.nodes_of_edge:
        coordinates = np.asarray(
            [mesh.nodes[int(node)] for node in mesh.nodes_of_edge[item.ref.id]],
            dtype=float,
        )
    else:
        coordinates = np.asarray(
            geometry.sample_edge(item.ref.id, np.linspace(0.0, 1.0, 25)),
            dtype=float,
        )
    amplitude = _imperfection_amplitude(item, coordinates)
    axis = coordinates[-1] - coordinates[0]
    length = float(np.linalg.norm(axis))
    if length <= 0.0:
        return coordinates, None
    axis /= length
    direction = np.asarray(item.direction, dtype=float)
    direction -= float(direction @ axis) * axis
    if float(np.linalg.norm(direction)) <= 1.0e-14:
        basis = np.eye(3)[int(np.argmin(np.abs(axis)))]
        direction = basis - float(basis @ axis) * axis
    direction /= float(np.linalg.norm(direction))
    distances = np.asarray(
        [float((coordinate - coordinates[0]) @ axis) / length for coordinate in coordinates]
    )
    offsets = (
        _preview_factor(amplitude, span)
        * amplitude
        * np.sin(np.pi * np.clip(distances, 0.0, 1.0))[:, None]
        * direction[None, :]
    )
    peak = int(np.argmax(np.linalg.norm(offsets, axis=1)))
    arrow = None
    if float(np.linalg.norm(offsets[peak])) > 0.0:
        arrow = Arrow(
            start=coordinates[peak],
            end=coordinates[peak] + offsets[peak],
            color=COLOR_IMPERFECTION,
        )
    return coordinates + offsets, arrow


def build_imperfection_overlay(
    project, *, mesh: Optional[Mesh] = None, scale: Optional[float] = None
) -> Scene:
    """Draw the stress-free imperfect reference shape as purple wirework."""

    scene = Scene()
    geometry = project.geometry
    span = float(scale or geometry_characteristic_size(geometry))
    for item in project.imperfections:
        if item.ref.kind == "face" and item.ref.id in geometry.faces:
            lines, arrow = _plate_imperfection_preview(geometry, item, mesh, span)
            scene.lines.extend(
                Polyline(
                    ref=item.ref,
                    points=points,
                    color=COLOR_IMPERFECTION,
                    width=3,
                )
                for points in lines
            )
        elif item.ref.kind == "edge" and item.ref.id in geometry.edges:
            points, arrow = _member_imperfection_preview(geometry, item, mesh, span)
            scene.lines.append(
                Polyline(
                    ref=item.ref,
                    points=points,
                    color=COLOR_IMPERFECTION,
                    width=4,
                )
            )
        else:
            continue
        if arrow is not None:
            scene.arrows.append(arrow)
    return scene


def build_attribute_overlay(
    project,
    *,
    case_name: Optional[str] = "default",
    scale: Optional[float] = None,
    show_supports: bool = True,
    show_loads: bool = True,
    show_masses: bool = True,
    show_imperfections: bool = True,
    mesh: Optional[Mesh] = None,
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

    if show_imperfections:
        scene.merge(build_imperfection_overlay(project, mesh=mesh, scale=span))

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
