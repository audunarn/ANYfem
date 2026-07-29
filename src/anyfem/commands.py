"""Commands: one path for every model change, and therefore undo.

Both the GUI and the scripting API go through this stack, so anything the user
can do in the application can be scripted, and anything scripted can be undone.
Retrofitting that later is painful, so it is here from the start.

Geometry commands share one inverse strategy: snapshot the topology before
running, restore it to undo, and re-run the same deterministic operation to
redo.  Entities therefore come back with *exactly* the IDs they had, which
matters because loads, supports and sections reference entities by ID -- an
undo that renumbered the model would silently re-target them.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

import numpy as np

from .geometry import operations
from .geometry.entities import EntityRef
from .model.attributes import Mass, PointLoad, Support
from .model.imperfections import Imperfection
from .model.project import Project, ProjectError

__all__ = [
    "AddArc",
    "AddCombination",
    "AddFace",
    "AddImperfection",
    "AddLine",
    "AddLineLoad",
    "AddLoadCase",
    "AddMass",
    "AddPlate",
    "AddPoint",
    "AddPointLoad",
    "AddPolyline",
    "AddPressure",
    "AddRefinement",
    "AddSupport",
    "AddSurfaceTraction",
    "AssignBeam",
    "AssignPlate",
    "Command",
    "CommandStack",
    "DeleteEntity",
    "DeleteLoadCase",
    "Extrude",
    "MovePoint",
    "PunchHole",
    "RefineForImpact",
    "Revolve",
    "SetAcceleration",
    "SetFaceCorners",
    "SetElementOrder",
    "SetFollowerPressure",
    "SplitEdge",
    "SplitFace",
    "StripFace",
    "TriangleToQuads",
]


class Command:
    """A reversible change to a project."""

    label: str = "command"

    def do(self, project: Project) -> Any:
        raise NotImplementedError

    def undo(self, project: Project) -> None:
        raise NotImplementedError

    def redo(self, project: Project) -> Any:
        return self.do(project)


class CommandStack:
    """Runs commands and keeps the undo and redo history."""

    def __init__(self, project: Project) -> None:
        self.project = project
        self._done: List[Command] = []
        self._undone: List[Command] = []
        self._listeners: List[Callable[[], None]] = []

    # ------------------------------------------------------------------
    def run(self, command: Command) -> Any:
        result = command.do(self.project)
        self._done.append(command)
        # A new action invalidates the redo branch.
        self._undone.clear()
        self._notify()
        return result

    def undo(self) -> bool:
        if not self._done:
            return False
        command = self._done.pop()
        command.undo(self.project)
        self._undone.append(command)
        self._notify()
        return True

    def redo(self) -> bool:
        if not self._undone:
            return False
        command = self._undone.pop()
        command.redo(self.project)
        self._done.append(command)
        self._notify()
        return True

    @property
    def can_undo(self) -> bool:
        return bool(self._done)

    @property
    def can_redo(self) -> bool:
        return bool(self._undone)

    @property
    def undo_label(self) -> Optional[str]:
        return self._done[-1].label if self._done else None

    @property
    def redo_label(self) -> Optional[str]:
        return self._undone[-1].label if self._undone else None

    def history(self) -> List[str]:
        return [command.label for command in self._done]

    def clear(self) -> None:
        self._done.clear()
        self._undone.clear()
        self._notify()

    # ------------------------------------------------------------------
    def add_listener(self, callback: Callable[[], None]) -> None:
        self._listeners.append(callback)

    def remove_listener(self, callback: Callable[[], None]) -> None:
        """Detach a listener, e.g. when its widget is being destroyed."""

        while callback in self._listeners:
            self._listeners.remove(callback)

    def _notify(self) -> None:
        for callback in list(self._listeners):
            callback()


# ----------------------------------------------------------------------
# geometry
# ----------------------------------------------------------------------
class GeometryCommand(Command):
    """Base for commands that change geometry.

    Subclasses implement :meth:`operate`; the inverse comes for free.

    The inverse is a topology snapshot rather than a list of what was created,
    because the decomposition tools do not only add: splitting an edge rewrites
    the loops of every face that used it, and splitting a face deletes the
    original.  A snapshot handles creation, deletion and in-place rewriting
    uniformly, and costs only a dictionary of references.

    Redo restores the ID counters and re-runs the same deterministic
    operation, so entities come back with exactly the IDs they had.  That is
    what keeps loads and sections pointing at the right things.
    """

    def __init__(self) -> None:
        self._snapshot: Dict[str, object] = {}
        self._attributes: Dict[str, Any] = {}

    def operate(self, project: Project) -> Any:
        raise NotImplementedError

    def do(self, project: Project) -> Any:
        self._snapshot = project.geometry.topology_snapshot()
        self._attributes = _attribute_snapshot(project)
        project.geometry.begin_replacement_log()
        result = self.operate(project)
        # Splitting a line that carries a load, or a plate that carries a
        # pressure, must not throw the attribute away: it follows onto
        # whatever replaced the entity.
        _apply_replacements(project, project.geometry.replacement_log())
        return result

    def undo(self, project: Project) -> None:
        project.geometry.restore_topology(self._snapshot)
        _restore_attributes(project, self._attributes)

    def redo(self, project: Project) -> Any:
        project.geometry.restore_topology(self._snapshot)
        _restore_attributes(project, self._attributes)
        return self.do(project)


def _attribute_snapshot(project: Project) -> Dict[str, Any]:
    """Cheap snapshot of everything attached to geometry."""

    return {
        "face_sections": dict(project.face_sections),
        "edge_sections": dict(project.edge_sections),
        "supports": list(project.supports),
        "refinements": list(project.refinements),
        "element_order": project.element_order,
        "loads": {
            name: (
                list(case.point_loads),
                list(case.pressures),
                list(case.line_loads),
            )
            for name, case in project.load_cases.items()
        },
    }


def _restore_attributes(project: Project, snapshot: Dict[str, Any]) -> None:
    if not snapshot:
        return
    project.face_sections.clear()
    project.face_sections.update(snapshot["face_sections"])
    project.edge_sections.clear()
    project.edge_sections.update(snapshot["edge_sections"])
    project.supports[:] = list(snapshot["supports"])
    project.refinements[:] = list(snapshot.get("refinements", ()))
    project.element_order = snapshot.get("element_order", project.element_order)
    for name, (points, pressures, lines) in snapshot["loads"].items():
        case = project.load_case(name)
        case.point_loads[:] = list(points)
        case.pressures[:] = list(pressures)
        case.line_loads[:] = list(lines)


def _apply_replacements(
    project: Project, log: Sequence[Tuple[EntityRef, Tuple[EntityRef, ...]]]
) -> None:
    """Move sections, supports and loads onto whatever replaced an entity."""

    for old, replacements in log:
        if not replacements:
            continue

        if old.kind == "edge":
            section = project.edge_sections.pop(old.id, None)
            if section is not None:
                for new in replacements:
                    project.edge_sections[new.id] = section
        elif old.kind == "face":
            section = project.face_sections.pop(old.id, None)
            if section is not None:
                for new in replacements:
                    project.face_sections[new.id] = section

        kept: List[Support] = []
        for support in project.supports:
            if support.ref != old:
                kept.append(support)
                continue
            for new in replacements:
                kept.append(
                    Support(
                        name=f"{support.name}_{new.id}",
                        ref=new,
                        constraints=dict(support.constraints),
                    )
                )
        project.supports[:] = kept

        for case in project.load_cases.values():
            _replace_loads(case.point_loads, old, replacements)
            _replace_loads(case.pressures, old, replacements)
            _replace_loads(case.line_loads, old, replacements)


def _replace_loads(
    container: List[Any], old: EntityRef, replacements: Sequence[EntityRef]
) -> None:
    """Re-target loads from a removed entity onto its replacements.

    A pressure or a distributed line load applies over an area or a length, so
    each piece keeps the same intensity.  A point load cannot be split that
    way, so it follows the first replacement only.
    """

    survivors: List[Any] = []
    for load in container:
        if load.ref != old:
            survivors.append(load)
            continue
        targets = (
            replacements[:1]
            if isinstance(load, PointLoad)
            else replacements
        )
        for new in targets:
            survivors.append(replace(load, ref=new))
    container[:] = survivors


@dataclass(eq=False)
class AddPoint(GeometryCommand):
    x: float
    y: float
    z: float = 0.0
    label: str = "add point"

    def __post_init__(self) -> None:
        GeometryCommand.__init__(self)

    def operate(self, project: Project) -> int:
        return project.geometry.add_point(self.x, self.y, self.z)


@dataclass(eq=False)
class AddLine(GeometryCommand):
    start: int
    end: int
    label: str = "add line"

    def __post_init__(self) -> None:
        GeometryCommand.__init__(self)

    def operate(self, project: Project) -> int:
        return project.geometry.add_line(self.start, self.end)


@dataclass(eq=False)
class AddArc(GeometryCommand):
    start: int
    via: int
    end: int
    label: str = "add arc"

    def __post_init__(self) -> None:
        GeometryCommand.__init__(self)

    def operate(self, project: Project) -> int:
        return project.geometry.add_arc(self.start, self.via, self.end)


@dataclass(eq=False)
class AddPolyline(GeometryCommand):
    vertex_ids: Sequence[int]
    close: bool = False
    label: str = "add polyline"

    def __post_init__(self) -> None:
        GeometryCommand.__init__(self)

    def operate(self, project: Project) -> List[int]:
        return project.geometry.add_polyline(self.vertex_ids, close=self.close)


@dataclass(eq=False)
class AddFace(GeometryCommand):
    edge_ids: Sequence[int]
    corners: Optional[Sequence[int]] = None
    label: str = "add plate"

    def __post_init__(self) -> None:
        GeometryCommand.__init__(self)

    def operate(self, project: Project) -> int:
        return project.geometry.add_face(self.edge_ids, corners=self.corners)


@dataclass(eq=False)
class AddPlate(GeometryCommand):
    """Lines and a plate in one step, from an ordered ring of points."""

    vertex_ids: Sequence[int]
    label: str = "add plate"

    def __post_init__(self) -> None:
        GeometryCommand.__init__(self)

    def operate(self, project: Project) -> int:
        return project.geometry.add_plate(self.vertex_ids)


@dataclass(eq=False)
class Extrude(GeometryCommand):
    edge_ids: Sequence[int]
    vector: Sequence[float]
    label: str = "extrude"

    def __post_init__(self) -> None:
        GeometryCommand.__init__(self)

    def operate(self, project: Project) -> List[int]:
        return project.geometry.extrude(self.edge_ids, self.vector)


@dataclass(eq=False)
class MovePoint(Command):
    vertex_id: int
    x: float
    y: float
    z: float = 0.0
    label: str = "move point"
    _previous: Optional[Tuple[float, float, float]] = field(default=None, init=False)

    def do(self, project: Project) -> None:
        position = project.geometry.vertex_position(self.vertex_id)
        self._previous = (float(position[0]), float(position[1]), float(position[2]))
        project.geometry.move_point(self.vertex_id, self.x, self.y, self.z)

    def undo(self, project: Project) -> None:
        assert self._previous is not None
        project.geometry.move_point(self.vertex_id, *self._previous)


@dataclass(eq=False)
class DeleteEntity(Command):
    """Delete one entity, taking its dependent attributes with it."""

    ref: EntityRef
    label: str = "delete"
    _entity: Any = field(default=None, init=False)
    _attributes: Dict[str, Any] = field(default_factory=dict, init=False)

    def do(self, project: Project) -> None:
        geometry = project.geometry
        self._attributes = _detach_attributes(project, self.ref)
        if self.ref.kind == "face":
            self._entity = geometry.faces[self.ref.id]
            geometry.remove_face(self.ref.id)
        elif self.ref.kind == "edge":
            self._entity = geometry.edges[self.ref.id]
            geometry.remove_edge(self.ref.id)
        else:
            self._entity = geometry.vertices[self.ref.id]
            geometry.remove_vertex(self.ref.id)

    def undo(self, project: Project) -> None:
        geometry = project.geometry
        if self.ref.kind == "face":
            geometry.faces[self.ref.id] = self._entity
        elif self.ref.kind == "edge":
            geometry.edges[self.ref.id] = self._entity
        else:
            geometry.vertices[self.ref.id] = self._entity
        _reattach_attributes(project, self.ref, self._attributes)
        self._entity = None
        self._attributes = {}


def _detach_attributes(project: Project, ref: EntityRef) -> Dict[str, Any]:
    """Remove and record everything that referenced a deleted entity."""

    removed: Dict[str, Any] = {
        "face_section": None,
        "edge_section": None,
        "supports": [],
        "loads": [],
    }

    if ref.kind == "face":
        removed["face_section"] = project.face_sections.pop(ref.id, None)
    elif ref.kind == "edge":
        removed["edge_section"] = project.edge_sections.pop(ref.id, None)

    kept: List[Support] = []
    for item in project.supports:
        if item.ref == ref:
            removed["supports"].append(item)
        else:
            kept.append(item)
    project.supports[:] = kept

    for case in project.load_cases.values():
        for attribute in ("point_loads", "pressures", "line_loads"):
            container = getattr(case, attribute)
            survivors = []
            for load in container:
                if load.ref == ref:
                    removed["loads"].append((case.name, attribute, load))
                else:
                    survivors.append(load)
            container[:] = survivors
    return removed


def _reattach_attributes(
    project: Project, ref: EntityRef, removed: Dict[str, Any]
) -> None:
    if not removed:
        return

    face_section = removed.get("face_section")
    if face_section is not None:
        project.face_sections[ref.id] = face_section
    edge_section = removed.get("edge_section")
    if edge_section is not None:
        project.edge_sections[ref.id] = edge_section

    for load_case_name, attribute, load in removed.get("loads", ()):
        case = project.load_case(load_case_name)
        getattr(case, attribute).append(load)
    project.supports.extend(removed.get("supports", ()))


# ----------------------------------------------------------------------
# attributes
# ----------------------------------------------------------------------
@dataclass(eq=False)
class AssignPlate(Command):
    face_id: int
    section: str
    label: str = "assign plate section"
    _previous: Optional[str] = field(default=None, init=False)

    def do(self, project: Project) -> None:
        self._previous = project.face_sections.get(self.face_id)
        project.assign_plate(self.face_id, self.section)

    def undo(self, project: Project) -> None:
        if self._previous is None:
            project.face_sections.pop(self.face_id, None)
        else:
            project.face_sections[self.face_id] = self._previous


@dataclass(eq=False)
class AssignBeam(Command):
    edge_id: int
    section: str
    label: str = "assign beam section"
    _previous: Optional[str] = field(default=None, init=False)

    def do(self, project: Project) -> None:
        self._previous = project.edge_sections.get(self.edge_id)
        project.assign_beam(self.edge_id, self.section)

    def undo(self, project: Project) -> None:
        if self._previous is None:
            project.edge_sections.pop(self.edge_id, None)
        else:
            project.edge_sections[self.edge_id] = self._previous


@dataclass(eq=False)
class AddSupport(Command):
    support: Support
    label: str = "add support"

    def do(self, project: Project) -> Support:
        return project.add_support(self.support)

    def undo(self, project: Project) -> None:
        project.supports.remove(self.support)


@dataclass(eq=False)
class AddPointLoad(Command):
    ref: EntityRef
    force: Sequence[float] = (0.0, 0.0, 0.0)
    moment: Sequence[float] = (0.0, 0.0, 0.0)
    case: str = "default"
    label: str = "add point load"
    _load: Any = field(default=None, init=False)

    def do(self, project: Project) -> Any:
        self._load = project.load_case(self.case).add_point_load(
            self.ref, self.force, self.moment
        )
        return self._load

    def undo(self, project: Project) -> None:
        project.load_case(self.case).point_loads.remove(self._load)


@dataclass(eq=False)
class AddPressure(Command):
    ref: EntityRef
    value: float
    case: str = "default"
    label: str = "add pressure"
    _load: Any = field(default=None, init=False)

    def do(self, project: Project) -> Any:
        self._load = project.load_case(self.case).add_pressure(self.ref, self.value)
        return self._load

    def undo(self, project: Project) -> None:
        project.load_case(self.case).pressures.remove(self._load)


# ----------------------------------------------------------------------
# decomposition
# ----------------------------------------------------------------------
@dataclass(eq=False)
class SplitEdge(GeometryCommand):
    """Imprint a point on a line, rewriting every face that used it."""

    edge_id: int
    fraction: float = 0.5
    label: str = "split line"

    def __post_init__(self) -> None:
        GeometryCommand.__init__(self)

    def operate(self, project: Project) -> int:
        vertex, _halves = project.geometry.split_edge(self.edge_id, self.fraction)
        return vertex


@dataclass(eq=False)
class SetFaceCorners(GeometryCommand):
    """Override which boundary positions begin each of the four sides."""

    face_id: int
    corners: Sequence[int] = ()
    label: str = "set plate corners"

    def __post_init__(self) -> None:
        GeometryCommand.__init__(self)

    def operate(self, project: Project) -> None:
        project.geometry.set_face_corners(self.face_id, self.corners)


@dataclass(eq=False)
class Revolve(GeometryCommand):
    """Sweep lines about an axis into curved plates."""

    edge_ids: Sequence[int] = ()
    axis_point: Sequence[float] = (0.0, 0.0, 0.0)
    axis_direction: Sequence[float] = (0.0, 0.0, 1.0)
    angle: float = np.pi / 2.0
    segments: Optional[int] = None
    label: str = "revolve"

    def __post_init__(self) -> None:
        GeometryCommand.__init__(self)

    def operate(self, project: Project) -> List[int]:
        return project.geometry.revolve(
            self.edge_ids,
            self.axis_point,
            self.axis_direction,
            self.angle,
            self.segments,
        )


@dataclass(eq=False)
class SplitFace(GeometryCommand):
    """Cut a plate in two across one parametric direction."""

    face_id: int
    axis: int = 0
    fraction: float = 0.5
    label: str = "split plate"

    def __post_init__(self) -> None:
        GeometryCommand.__init__(self)

    def operate(self, project: Project) -> Tuple[int, int]:
        _edge, faces = operations.split_face_at(
            project.geometry, self.face_id, self.axis, self.fraction
        )
        return faces


@dataclass(eq=False)
class StripFace(GeometryCommand):
    """Divide a plate into strips, leaving lines ready to carry stiffeners."""

    face_id: int
    axis: int = 0
    count: int = 2
    section: Optional[str] = None
    label: str = "split into strips"

    def __post_init__(self) -> None:
        GeometryCommand.__init__(self)

    def operate(self, project: Project) -> Tuple[List[int], List[int]]:
        strips, dividers = operations.strip_face(
            project.geometry, self.face_id, self.axis, self.count
        )
        if self.section is not None:
            for edge_id in dividers:
                project.assign_beam(edge_id, self.section)
        return strips, dividers


@dataclass(eq=False)
class AddRefinement(Command):
    """Ask for smaller elements in one region."""

    refinement: Any = None
    label: str = "add refinement"

    def do(self, project: Project) -> Any:
        return project.add_refinement(self.refinement)

    def undo(self, project: Project) -> None:
        project.refinements.remove(self.refinement)


@dataclass(eq=False)
class SetElementOrder(Command):
    """Switch the mesh between Q4 and Q8 shells."""

    order: str = "linear"
    label: str = "set element order"
    _previous: str = "linear"

    def do(self, project: Project) -> str:
        self._previous = project.element_order
        return project.set_element_order(self.order)

    def undo(self, project: Project) -> None:
        project.element_order = self._previous


@dataclass(eq=False)
class RefineForImpact(GeometryCommand):
    """Decompose the struck plate and refine the mesh under the sphere.

    A mapped mesh cannot refine the *interior* of a plate: the interior grid is
    the transfinite blend of the boundary, so a size zone in the middle of a
    plate changes nothing at all.  Refining there means decomposing there,
    which is the same answer this mesher gives to every other awkward region.

    So this cuts the struck plate to bracket the contact patch, and puts the
    size zone on the resulting sub-plate.  Because it changes the geometry it
    is a command -- visible in the undo history and reversible -- rather than
    something a solve does to the model on its way past.
    """

    collision: Any = None
    target_size: float = 0.0
    elements_per_radius: float = 4.0
    zone_radii: float = 1.5
    label: str = "refine for impact"

    def __post_init__(self) -> None:
        GeometryCommand.__init__(self)

    def operate(self, project: Project) -> Dict[str, Any]:
        from .model.collision import impact_point, impact_refinement

        if self.target_size <= 0.0:
            raise ValueError("refine_for_impact needs a positive target_size")

        geometry = project.geometry
        scout = project.generate_mesh(self.target_size)
        point = impact_point(scout, self.collision)
        zone = impact_refinement(
            scout,
            self.collision,
            elements_per_radius=self.elements_per_radius,
            zone_radii=self.zone_radii,
        )

        struck, _where = _closest_face(geometry, list(geometry.faces), point)
        patch, produced = _bracket_face(geometry, struck, point, zone.radius)
        project.add_refinement(zone)
        return {
            "struck": struck,
            "patch": patch,
            "faces": produced,
            "point": point,
            "zone": zone,
        }


def _face_samples(geometry, face_id: int, count: int = 32):
    """A sampled grid over a face, with the parameters of each sample."""

    from .mesh.mapped import coons_grid, sample_chain

    sides = geometry.faces[face_id].sides()
    grid = coons_grid(
        sample_chain(geometry, sides[0], count),
        sample_chain(geometry, sides[1], count),
        sample_chain(geometry, sides[2], count)[::-1],
        sample_chain(geometry, sides[3], count)[::-1],
    )
    return grid, np.linspace(0.0, 1.0, count + 1)


def _closest_face(
    geometry, face_ids: Sequence[int], point: np.ndarray
) -> Tuple[int, Tuple[float, float]]:
    """Which face a point lies on, and roughly where in its parameters.

    Found by sampling the surface rather than inverting it.  The parameters
    only have to be good enough to place a cut either side of a contact patch,
    and a sampled grid is exact about *which* face without needing a Newton
    solve that could fail on a folded parameterisation.
    """

    best: Optional[Tuple[float, int, Tuple[float, float]]] = None
    for face_id in face_ids:
        grid, parameters = _face_samples(geometry, face_id)
        gaps = np.linalg.norm(grid - np.asarray(point, dtype=float), axis=2)
        index = np.unravel_index(int(np.argmin(gaps)), gaps.shape)
        gap = float(gaps[index])
        if best is None or gap < best[0]:
            best = (
                gap,
                face_id,
                (float(parameters[index[0]]), float(parameters[index[1]])),
            )
    if best is None:
        raise ValueError("no plate to refine: the model has no plates")
    return best[1], best[2]


def _face_spans(geometry, face_id: int) -> Tuple[float, float]:
    """Arc length across a face in each parametric direction."""

    sides = geometry.faces[face_id].sides()
    return (
        sum(geometry.edge_length(item.edge) for item in sides[0]),
        sum(geometry.edge_length(item.edge) for item in sides[1]),
    )


def _bracket_face(
    geometry, face_id: int, point: np.ndarray, radius: float
) -> Tuple[int, List[int]]:
    """Cut a face until the contact patch has a sub-face of its own.

    Up to two isoparametric cuts per direction, one either side of the patch.
    After each cut the point is re-located in whichever piece now holds it,
    because a fraction means something different in the sub-face's own
    parameters than it did in the parent's -- reusing the parent's number is
    the easy mistake here, and it puts the second cut in the wrong place.

    A cut that would land within 2% of a boundary is skipped rather than made:
    the patch simply runs to that edge, which is what it physically does when
    the sphere lands near one, and a hairline sliver face would only wreck the
    seeding.
    """

    patch = face_id
    produced = [face_id]
    for axis in (0, 1):
        for side in (-1.0, 1.0):
            span = _face_spans(geometry, patch)[axis]
            if span <= 0.0:
                break
            _face, where = _closest_face(geometry, [patch], point)
            fraction = where[axis] + side * radius / span
            if not 0.02 < fraction < 0.98:
                continue
            try:
                _edge, pair = operations.split_face_at(
                    geometry, patch, axis, fraction
                )
            except operations.GeometryError:
                continue
            produced = [item for item in produced if item != patch]
            produced.extend(pair)
            patch, _where = _closest_face(geometry, list(pair), point)
    return patch, produced


@dataclass(eq=False)
class TriangleToQuads(GeometryCommand):
    """Turn a three-sided region into three mapped plates."""

    edge_ids: Sequence[int] = ()
    label: str = "triangle to plates"

    def __post_init__(self) -> None:
        GeometryCommand.__init__(self)

    def operate(self, project: Project) -> List[int]:
        return operations.triangle_to_quads(project.geometry, self.edge_ids)


@dataclass(eq=False)
class PunchHole(GeometryCommand):
    """Replace a plate with the butterfly decomposition around a hole."""

    face_id: int
    centre: Sequence[float] = (0.0, 0.0, 0.0)
    radius: float = 0.1
    label: str = "punch hole"

    def __post_init__(self) -> None:
        GeometryCommand.__init__(self)

    def operate(self, project: Project) -> Tuple[List[int], List[int]]:
        return operations.punch_circular_hole(
            project.geometry, self.face_id, self.centre, self.radius
        )


# ----------------------------------------------------------------------
# loads, masses, combinations and imperfections
# ----------------------------------------------------------------------
@dataclass(eq=False)
class AddLineLoad(Command):
    ref: EntityRef
    force_per_length: Sequence[float] = (0.0, 0.0, 0.0)
    case: str = "default"
    label: str = "add line load"
    _load: Any = field(default=None, init=False)

    def do(self, project: Project) -> Any:
        self._load = project.load_case(self.case).add_line_load(
            self.ref, self.force_per_length
        )
        return self._load

    def undo(self, project: Project) -> None:
        project.load_case(self.case).line_loads.remove(self._load)


@dataclass(eq=False)
class AddSurfaceTraction(Command):
    ref: EntityRef
    traction: Sequence[float] = (0.0, 0.0, 0.0)
    case: str = "default"
    label: str = "add surface traction"
    _load: Any = field(default=None, init=False)

    def do(self, project: Project) -> Any:
        self._load = project.load_case(self.case).add_surface_traction(
            self.ref, self.traction
        )
        return self._load

    def undo(self, project: Project) -> None:
        project.load_case(self.case).surface_tractions.remove(self._load)


@dataclass(eq=False)
class AddMass(Command):
    ref: EntityRef
    value: float = 0.0
    name: str = "mass"
    label: str = "add mass"
    _mass: Any = field(default=None, init=False)

    def do(self, project: Project) -> Any:
        self._mass = project.add_mass(
            Mass(ref=self.ref, value=self.value, name=self.name)
        )
        return self._mass

    def undo(self, project: Project) -> None:
        project.masses.remove(self._mass)


@dataclass(eq=False)
class SetAcceleration(Command):
    """Gravity or a design acceleration field on one case."""

    vector: Sequence[float] = (0.0, 0.0, -9.81)
    case: str = "default"
    label: str = "set acceleration"
    _previous: Any = field(default=None, init=False)
    _had: bool = field(default=False, init=False)

    def do(self, project: Project) -> None:
        case = project.load_case(self.case)
        self._had = case.gravity is not None
        self._previous = None if case.gravity is None else np.array(case.gravity)
        case.set_acceleration(*(float(value) for value in self.vector))

    def undo(self, project: Project) -> None:
        case = project.load_case(self.case)
        case.gravity = self._previous if self._had else None


@dataclass(eq=False)
class SetFollowerPressure(Command):
    follower: bool = True
    case: str = "default"
    label: str = "set follower pressure"
    _previous: bool = field(default=False, init=False)

    def do(self, project: Project) -> None:
        case = project.load_case(self.case)
        self._previous = case.follower_pressure
        case.set_follower_pressure(self.follower)

    def undo(self, project: Project) -> None:
        project.load_case(self.case).follower_pressure = self._previous


@dataclass(eq=False)
class AddCombination(Command):
    name: str
    factors: Mapping[str, float] = field(default_factory=dict)
    label: str = "add combination"
    _previous: Any = field(default=None, init=False)
    _had: bool = field(default=False, init=False)

    def do(self, project: Project) -> Any:
        self._had = self.name in project.combinations
        self._previous = project.combinations.get(self.name)
        return project.add_combination(self.name, self.factors)

    def undo(self, project: Project) -> None:
        if self._had:
            project.combinations[self.name] = self._previous
        else:
            project.combinations.pop(self.name, None)


@dataclass(eq=False)
class AddImperfection(Command):
    imperfection: Imperfection
    label: str = "add imperfection"

    def do(self, project: Project) -> Any:
        return project.add_imperfection(self.imperfection)

    def undo(self, project: Project) -> None:
        project.imperfections.remove(self.imperfection)


@dataclass(eq=False)
class AddLoadCase(Command):
    """Create an empty load case."""

    name: str
    label: str = "add load case"
    _created: bool = field(default=False, init=False)

    def do(self, project: Project) -> Any:
        self._created = self.name not in project.load_cases
        return project.load_case(self.name)

    def undo(self, project: Project) -> None:
        if self._created:
            project.load_cases.pop(self.name, None)


@dataclass(eq=False)
class DeleteLoadCase(Command):
    name: str
    label: str = "delete load case"
    _case: Any = field(default=None, init=False)

    def do(self, project: Project) -> None:
        if self.name not in project.load_cases:
            raise ProjectError(f"no load case named {self.name!r}")
        using = [
            combination.name
            for combination in project.combinations.values()
            if self.name in combination.factors
        ]
        if using:
            raise ProjectError(
                f"load case {self.name!r} is used by combination(s) "
                f"{sorted(using)}; remove those first"
            )
        self._case = project.load_cases.pop(self.name)

    def undo(self, project: Project) -> None:
        project.load_cases[self.name] = self._case
