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

from copy import copy
from dataclasses import dataclass, field, replace
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

import numpy as np
from anygeometry.entities import EntityRef
from anygeometry.editing import (
    InsertResult,
    Measurement,
    PatternResult,
    circular_pattern,
    copy_entities,
    linear_pattern,
    measure,
    mirror_entities,
    reverse_edge,
    reverse_face,
)
from anygeometry.errors import GeometryError
from anygeometry import Orientation, SketchDefinition
from anygeometry.operations import (
    closest_point,
    punch_hole,
    project as project_point,
)
from anymesher.decomposition import (
    punch_circular_hole,
    split_face_at,
    strip_face,
    triangle_to_quads,
)

from .model.attributes import (
    LineLoad,
    Mass,
    PointLoad,
    Pressure,
    Support,
    SurfaceTraction,
)
from .model.imperfections import Imperfection
from .model.project import Project, ProjectError
from .model.coordinates import CoordinateSystem
from .model.records import MeshRecord, OutputRequest
from .model.regions import Region
from .model.materials import MaterialSpec
from .model.ownership import SheetJoinIntent, join_anchors
from .model.sections import PlateSection
from .model.units import UnitProfile

__all__ = [
    "AddArc",
    "AddCombination",
    "AddCone",
    "AddCylinder",
    "AddFace",
    "AddFeature",
    "AddImperfection",
    "AddLine",
    "AddLineLoad",
    "AddLoadCase",
    "AddMass",
    "AddMaterial",
    "AddMeshRecord",
    "AddOutputRequest",
    "AddPlate",
    "AddPoint",
    "AddPointLoad",
    "AddPlateSection",
    "AddPolyline",
    "AddPressure",
    "AddRefinement",
    "AddRegion",
    "AddSupport",
    "AddSurfaceTraction",
    "AddCoordinateSystem",
    "AddStiffenedPanel",
    "AddSketch",
    "AssignBeam",
    "AssignPlate",
    "ButterflyHoleDecomposition",
    "Command",
    "CommandEvent",
    "CommandStack",
    "CompositeCommand",
    "CommitStructuredLayout",
    "CopyEntities",
    "CircularPattern",
    "DeleteEntity",
    "DeleteAttribute",
    "DeleteLoadCase",
    "DeleteOutputRequest",
    "DeleteFeature",
    "DeleteProjectRecord",
    "Extrude",
    "FragmentPlateOverlaps",
    "EditFeature",
    "EditAttribute",
    "EditOutputRequest",
    "LinearPattern",
    "JoinSheet",
    "MeasureGeometry",
    "MirrorEntities",
    "MovePoint",
    "NeutralTrimHole",
    "PunchHole",
    "RefineForImpact",
    "Revolve",
    "ReverseEntity",
    "SetAcceleration",
    "SetFaceCorners",
    "SetElementOrder",
    "SetFollowerPressure",
    "SetUnitProfile",
    "SplitEdge",
    "SplitFace",
    "StripFace",
    "SuppressFeature",
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


@dataclass(frozen=True)
class CommandEvent:
    """One successfully completed command-stack action.

    Action listeners are observational: a recorder or integration failure must
    never roll back a command that has already committed to the project.
    """

    action: str
    command: Command


class CompositeCommand(Command):
    """Several commands committed and undone as one user action.

    A failure rolls back every command that completed before it.  This makes a
    large section/load assignment both efficient and safe: the user gets one
    refresh and one undo item, and never a half-applied selection.
    """

    def __init__(self, commands: Sequence[Command], label: str = "batch edit") -> None:
        self.commands = tuple(commands)
        self.label = str(label)
        self._completed = 0

    def do(self, project: Project) -> List[Any]:
        results: List[Any] = []
        self._completed = 0
        try:
            for command in self.commands:
                results.append(command.do(project))
                self._completed += 1
        except BaseException:
            for command in reversed(self.commands[: self._completed]):
                command.undo(project)
            self._completed = 0
            raise
        return results

    def undo(self, project: Project) -> None:
        for command in reversed(self.commands[: self._completed]):
            command.undo(project)

    def redo(self, project: Project) -> List[Any]:
        results: List[Any] = []
        completed = 0
        try:
            for command in self.commands:
                results.append(command.redo(project))
                completed += 1
        except BaseException:
            for command in reversed(self.commands[:completed]):
                command.undo(project)
            raise
        self._completed = completed
        return results


def _contains_geometry_mutation(command: Command) -> bool:
    """Whether a command would alter editable geometry/feature intent."""

    if isinstance(command, CompositeCommand):
        return any(_contains_geometry_mutation(item) for item in command.commands)
    return isinstance(
        command, (GeometryCommand, FeatureCommand, MovePoint, DeleteEntity)
    )


class CommandStack:
    """Runs commands and keeps the undo and redo history."""

    def __init__(self, project: Project, selection: Any = None) -> None:
        self.project = project
        self.selection = selection
        self._done: List[Command] = []
        self._undone: List[Command] = []
        self._listeners: List[Callable[[], None]] = []
        self._action_listeners: List[Callable[[CommandEvent], None]] = []

    # ------------------------------------------------------------------
    def run(self, command: Command) -> Any:
        if (
            getattr(self.project, "geometry_editing_disabled_reason", None)
            and _contains_geometry_mutation(command)
        ):
            raise PermissionError(
                str(self.project.geometry_editing_disabled_reason)
            )
        selected_before = (
            list(self.selection.items) if self.selection is not None else None
        )
        result = command.do(self.project)
        geometry_edit = isinstance(
            command, (GeometryCommand, FeatureCommand, DeleteEntity)
        )
        if self.selection is not None and geometry_edit:
            replacements = (
                [(command.ref, ())]
                if isinstance(command, DeleteEntity)
                else (
                    command.replacements
                    if isinstance(command, (FeatureCommand, GeometryCommand))
                    else self.project.geometry.replacement_log()
                )
            )
            self.selection.apply_replacements(replacements)
            command._selection_before = selected_before
            command._selection_after = list(self.selection.items)
        self._done.append(command)
        # A new action invalidates the redo branch.
        self._undone.clear()
        self._notify_action("run", command)
        self._notify()
        return result

    def undo(self) -> bool:
        if not self._done:
            return False
        command = self._done.pop()
        command.undo(self.project)
        if self.selection is not None and hasattr(command, "_selection_before"):
            self.selection.restore(command._selection_before)
        self._undone.append(command)
        self._notify_action("undo", command)
        self._notify()
        return True

    def redo(self) -> bool:
        if not self._undone:
            return False
        command = self._undone.pop()
        command.redo(self.project)
        if self.selection is not None and hasattr(command, "_selection_after"):
            self.selection.restore(command._selection_after)
        self._done.append(command)
        self._notify_action("redo", command)
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

    def query(self, query: Any) -> Any:
        """Evaluate a read-only model query without creating an undo item.

        Measurements belong on the same public workflow surface as commands,
        but reading a length or area must not dirty the document or make the
        next Ctrl+Z appear to do nothing.  Query objects expose ``evaluate``
        and deliberately bypass the mutation history.
        """

        evaluate = getattr(query, "evaluate", None)
        if not callable(evaluate):
            raise TypeError("a command-stack query needs evaluate(project)")
        return evaluate(self.project)

    # ------------------------------------------------------------------
    def add_listener(self, callback: Callable[[], None]) -> None:
        self._listeners.append(callback)

    def remove_listener(self, callback: Callable[[], None]) -> None:
        """Detach a listener, e.g. when its widget is being destroyed."""

        while callback in self._listeners:
            self._listeners.remove(callback)

    def add_action_listener(
        self, callback: Callable[[CommandEvent], None]
    ) -> None:
        """Observe successful run/undo/redo actions without affecting them."""

        self._action_listeners.append(callback)

    def remove_action_listener(
        self, callback: Callable[[CommandEvent], None]
    ) -> None:
        while callback in self._action_listeners:
            self._action_listeners.remove(callback)

    def _notify_action(self, action: str, command: Command) -> None:
        event = CommandEvent(str(action), command)
        for callback in list(self._action_listeners):
            try:
                callback(event)
            except Exception:
                # Recording/telemetry is never part of the model transaction.
                continue

    def _notify(self) -> None:
        for callback in list(self._listeners):
            callback()


# ----------------------------------------------------------------------
# geometry
# ----------------------------------------------------------------------
class FeatureCommand(Command):
    """Atomically change feature intent and its current materialization.

    Regeneration happens on ANYgeometry's working clone.  This command adds
    the document-level inverse: a rejected definition is restored as well as
    the topology, and undo/redo restore exact design snapshots instead of
    replaying an operation into potentially different entity IDs.
    """

    def __init__(self) -> None:
        self._before: Mapping[str, object] | None = None
        self._after: Mapping[str, object] | None = None
        self._feature_id: int | None = None
        self._replacements: tuple[
            tuple[EntityRef, tuple[EntityRef, ...]], ...
        ] = ()

    @property
    def replacements(
        self,
    ) -> tuple[tuple[EntityRef, tuple[EntityRef, ...]], ...]:
        return self._replacements

    def change(self, project: Project) -> int:
        raise NotImplementedError

    def do(self, project: Project) -> Any:
        geometry = project.geometry
        self._before = geometry.design_snapshot()
        working_project = copy(project)
        working_project.geometry = geometry.clone(include_features=True)
        feature_id = int(self.change(working_project))
        report = working_project.regenerate_geometry_features()
        if not report.success:
            raise GeometryError(
                report.diagnostic or f"feature {feature_id} failed to regenerate"
            )
        staged = working_project.geometry.features.get(feature_id)
        allowed_states = {"ok"}
        if bool(getattr(staged, "suppressed", False)):
            allowed_states.add("suppressed")
        if str(getattr(staged, "state", "")) not in allowed_states:
            raise GeometryError(
                getattr(staged, "diagnostic", None)
                or f"feature {feature_id} did not produce a valid materialization"
            )

        # The entire feature + structural-owner result is now known valid.
        # Publish the detached design once; validation failures above have not
        # touched the live feature history, revision, or allocator state.
        geometry.restore_design(working_project.geometry.design_snapshot())
        committed = geometry.features.get(feature_id)
        self._feature_id = feature_id
        self._replacements = tuple(report.replacements)
        self._after = geometry.design_snapshot()
        return committed

    def undo(self, project: Project) -> None:
        if self._before is None:
            raise RuntimeError("feature command has not been applied")
        project.geometry.restore_design(self._before)

    def redo(self, project: Project) -> Any:
        if self._after is None or self._feature_id is None:
            return self.do(project)
        project.geometry.restore_design(self._after)
        return project.geometry.features.get(self._feature_id)


@dataclass(eq=False)
class AddFeature(FeatureCommand):
    """Create and materialize one versioned ANYgeometry feature."""

    kind: str
    name: str | None = None
    parameters: Mapping[str, Any] | None = None
    inputs: Mapping[str, Sequence[Any]] | None = None
    suppressed: bool = False
    kind_version: int = 1
    dependencies: Sequence[int] = ()
    label: str = "add feature"

    def __post_init__(self) -> None:
        FeatureCommand.__init__(self)

    def change(self, project: Project) -> int:
        geometry = project.geometry
        geometry.features.capture_baseline(geometry)
        record = geometry.features.append(
            self.kind,
            name=self.name,
            parameters=self.parameters,
            inputs=self.inputs,
            suppressed=self.suppressed,
            kind_version=self.kind_version,
            dependencies=self.dependencies,
        )
        return record.feature_id


@dataclass(eq=False)
class AddSketch(FeatureCommand):
    """Create one editable constrained sketch on a persistent support face."""

    support_face: EntityRef
    definition: SketchDefinition
    name: str = "Sketch"
    label: str = "add sketch"

    def __post_init__(self) -> None:
        FeatureCommand.__init__(self)
        if self.support_face.kind != "face":
            raise GeometryError("a sketch support must be a plate face")

    def change(self, project: Project) -> int:
        geometry = project.geometry
        geometry.features.capture_baseline(geometry)
        record = geometry.features.append(
            "geometry.sketch.extrude",
            name=self.name,
            parameters=self.definition.to_parameters(),
            inputs={
                "support_face": (
                    _feature_anchor(geometry, self.support_face),
                )
            },
        )
        return record.feature_id


@dataclass(eq=False)
class FragmentPlateOverlaps(FeatureCommand):
    """Partition coplanar selected plates into non-overlapping geometry cells.

    Face order resolves section ownership of common cells: the first selected
    face wins.  The geometric union is retained in full.
    """

    face_ids: Sequence[int]
    label: str = "fragment plate overlaps"

    def __post_init__(self) -> None:
        FeatureCommand.__init__(self)
        identifiers = tuple(dict.fromkeys(int(item) for item in self.face_ids))
        if len(identifiers) < 2:
            raise GeometryError("select at least two plates in ownership order")
        self.face_ids = identifiers

    def change(self, project: Project) -> int:
        geometry = project.geometry
        geometry.features.capture_baseline(geometry)
        record = geometry.features.append(
            "geometry.fragment.overlaps",
            name="Plate overlap fragmentation",
            inputs={
                "faces": tuple(
                    _feature_anchor(geometry, EntityRef("face", face_id))
                    for face_id in self.face_ids
                )
            },
        )
        return record.feature_id


@dataclass(eq=False)
class DeleteFeature(FeatureCommand):
    """Delete one feature without silently cascading into dependants."""

    feature_id: int
    label: str = "delete feature"

    def __post_init__(self) -> None:
        FeatureCommand.__init__(self)

    def do(self, project: Project) -> int:
        geometry = project.geometry
        self._before = geometry.design_snapshot()
        working_project = copy(project)
        working_project.geometry = geometry.clone(include_features=True)
        working_project.geometry.features.remove(
            int(self.feature_id), cascade=False
        )
        report = working_project.regenerate_geometry_features()
        if not report.success:
            raise GeometryError(
                report.diagnostic
                or f"feature {self.feature_id} failed to regenerate"
            )
        geometry.restore_design(working_project.geometry.design_snapshot())
        self._feature_id = int(self.feature_id)
        self._replacements = tuple(report.replacements)
        self._after = geometry.design_snapshot()
        return int(self.feature_id)

    def undo(self, project: Project) -> None:
        if self._before is None:
            raise RuntimeError("feature delete has not been applied")
        project.geometry.restore_design(self._before)

    def redo(self, project: Project) -> int:
        if self._after is None:
            return self.do(project)
        project.geometry.restore_design(self._after)
        return int(self.feature_id)


class AddStiffenedPanel(AddFeature):
    """Insert an editable ANYgeometry stiffened-panel generator feature."""

    def __init__(
        self,
        length: float,
        width: float,
        longitudinal_spacing: float,
        transverse_spacing: float | None = None,
        *,
        origin: Sequence[float] = (0.0, 0.0, 0.0),
        u_direction: Sequence[float] = (1.0, 0.0, 0.0),
        v_direction: Sequence[float] = (0.0, 1.0, 0.0),
        semantic_group: str = "shell",
        name: str = "Stiffened panel",
    ) -> None:
        parameters: Dict[str, Any] = {
            "length": float(length),
            "width": float(width),
            "longitudinal_spacing": float(longitudinal_spacing),
            "origin": tuple(float(item) for item in origin),
            "u_direction": tuple(float(item) for item in u_direction),
            "v_direction": tuple(float(item) for item in v_direction),
            "semantic_group": str(semantic_group),
        }
        if transverse_spacing is not None:
            parameters["transverse_spacing"] = float(transverse_spacing)
        super().__init__(
            "generator.stiffened_panel",
            name=name,
            parameters=parameters,
            label="add stiffened panel",
        )


class AddCylinder(AddFeature):
    """Insert an editable ANYgeometry structural-cylinder feature."""

    def __init__(
        self,
        radius: float,
        height: float,
        *,
        circumferential_segments: int = 12,
        longitudinal_spacing: float | None = None,
        ring_spacing: float | None = None,
        origin: Sequence[float] = (0.0, 0.0, 0.0),
        axis: Sequence[float] = (0.0, 0.0, 1.0),
        radial_direction: Sequence[float] = (1.0, 0.0, 0.0),
        name: str = "Cylinder",
    ) -> None:
        parameters: Dict[str, Any] = {
            "radius": float(radius),
            "height": float(height),
            "circumferential_segments": int(circumferential_segments),
            "origin": tuple(float(item) for item in origin),
            "axis": tuple(float(item) for item in axis),
            "radial_direction": tuple(float(item) for item in radial_direction),
        }
        if longitudinal_spacing is not None:
            parameters["longitudinal_spacing"] = float(longitudinal_spacing)
        if ring_spacing is not None:
            parameters["ring_spacing"] = float(ring_spacing)
        super().__init__(
            "generator.cylinder",
            name=name,
            parameters=parameters,
            label="add cylinder",
        )


class AddCone(AddFeature):
    """Insert an editable ANYgeometry structural-cone feature."""

    def __init__(
        self,
        radius_start: float,
        radius_end: float,
        height: float,
        *,
        circumferential_segments: int = 12,
        longitudinal_spacing: float | None = None,
        ring_spacing: float | None = None,
        origin: Sequence[float] = (0.0, 0.0, 0.0),
        axis: Sequence[float] = (0.0, 0.0, 1.0),
        radial_direction: Sequence[float] = (1.0, 0.0, 0.0),
        name: str = "Cone",
    ) -> None:
        parameters: Dict[str, Any] = {
            "radius_start": float(radius_start),
            "radius_end": float(radius_end),
            "height": float(height),
            "circumferential_segments": int(circumferential_segments),
            "origin": tuple(float(item) for item in origin),
            "axis": tuple(float(item) for item in axis),
            "radial_direction": tuple(float(item) for item in radial_direction),
        }
        if longitudinal_spacing is not None:
            parameters["longitudinal_spacing"] = float(longitudinal_spacing)
        if ring_spacing is not None:
            parameters["ring_spacing"] = float(ring_spacing)
        super().__init__(
            "generator.cone",
            name=name,
            parameters=parameters,
            label="add cone",
        )


@dataclass(eq=False)
class EditFeature(FeatureCommand):
    """Edit feature parameters/inputs and regenerate as one undo item."""

    feature_id: int
    name: str | None = None
    parameters: Mapping[str, Any] | None = None
    inputs: Mapping[str, Sequence[Any]] | None = None
    dependencies: Sequence[int] | None = None
    label: str = "edit feature"

    def __post_init__(self) -> None:
        FeatureCommand.__init__(self)

    def change(self, project: Project) -> int:
        project.geometry.features.update(
            self.feature_id,
            name=self.name,
            parameters=self.parameters,
            inputs=self.inputs,
            dependencies=self.dependencies,
        )
        return self.feature_id


@dataclass(eq=False)
class SuppressFeature(FeatureCommand):
    """Suppress or resume a feature without deleting dependent intent."""

    feature_id: int
    suppressed: bool = True
    label: str = "suppress feature"

    def __post_init__(self) -> None:
        FeatureCommand.__init__(self)
        if not self.suppressed and self.label == "suppress feature":
            self.label = "resume feature"

    def change(self, project: Project) -> int:
        project.geometry.features.set_suppressed(
            self.feature_id, self.suppressed
        )
        return self.feature_id


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
        self._after: Dict[str, object] | None = None
        self._after_attributes: Dict[str, Any] | None = None
        self._result: Any = None
        self._feature_id: int | None = None
        self._replacements: tuple[
            tuple[EntityRef, tuple[EntityRef, ...]], ...
        ] = ()

    @property
    def replacements(
        self,
    ) -> tuple[tuple[EntityRef, tuple[EntityRef, ...]], ...]:
        return self._replacements

    def operate(self, project: Project) -> Any:
        raise NotImplementedError

    def preflight(self, project: Project) -> None:
        """Qualify expected validation failures before any live bookkeeping."""

    def do(self, project: Project) -> Any:
        self._snapshot = project.geometry.design_snapshot()
        self._attributes = _attribute_snapshot(project)
        definition = _geometry_feature_definition(self, project.geometry)
        published = False
        try:
            if definition is None:
                self.preflight(project)
                published = True
                project.geometry.begin_replacement_log()
                result = self.operate(project)
                self._replacements = tuple(project.geometry.replacement_log())
            else:
                kind, parameters, inputs = definition
                working_project = copy(project)
                working_project.geometry = project.geometry.clone(
                    include_features=True
                )
                working_geometry = working_project.geometry
                working_geometry.features.capture_baseline(working_geometry)
                pending = working_geometry.features.append(
                    kind,
                    name=self.label.title(),
                    parameters=parameters,
                    inputs=inputs,
                )
                report = working_project.regenerate_geometry_features()
                if not report.success:
                    raise GeometryError(
                        report.diagnostic
                        or f"feature {pending.feature_id} failed to regenerate"
                    )
                self._feature_id = pending.feature_id
                self._replacements = tuple(report.replacements)
                staged = working_geometry.features.get(pending.feature_id)
                if str(getattr(staged, "state", "")) != "ok":
                    raise GeometryError(
                        getattr(staged, "diagnostic", None)
                        or f"feature {pending.feature_id} did not produce a valid materialization"
                    )
                project.geometry.restore_design(
                    working_geometry.design_snapshot()
                )
                published = True
                committed = project.geometry.features.get(pending.feature_id)
                result = _feature_command_result(self, project.geometry, committed)
            # Splitting a line that carries a load, or a plate that carries a
            # pressure, must not throw the attribute away: it follows onto
            # whatever replaced the entity.
            _apply_replacements(project, self._replacements)
            self._after = project.geometry.design_snapshot()
            self._after_attributes = _attribute_snapshot(project)
            self._result = result
            return result
        except BaseException:
            if published:
                project.geometry.restore_design(self._snapshot)
                _restore_attributes(project, self._attributes)
            raise

    def undo(self, project: Project) -> None:
        project.geometry.restore_design(self._snapshot)
        _restore_attributes(project, self._attributes)

    def redo(self, project: Project) -> Any:
        if self._after is None or self._after_attributes is None:
            return self.do(project)
        project.geometry.restore_design(self._after)
        _restore_attributes(project, self._after_attributes)
        return self._result


@dataclass(eq=False)
class CommitStructuredLayout(Command):
    """Commit one fresh ANYmesher preview as one exact frozen design feature."""

    plan: Any
    label: str = "commit structured mesh partitions"

    def __post_init__(self) -> None:
        self._before: Dict[str, object] | None = None
        self._before_attributes: Dict[str, Any] | None = None
        self._after: Dict[str, object] | None = None
        self._after_attributes: Dict[str, Any] | None = None
        self._report: Any = None

    def do(self, project: Project) -> Any:
        from anymesher import commit_structured_layout

        self._before = project.geometry.design_snapshot()
        self._before_attributes = _attribute_snapshot(project)
        try:
            working, report, _feature = commit_structured_layout(
                project.geometry,
                self.plan,
            )
            replacements = tuple(
                (
                    EntityRef("face", int(source)),
                    tuple(EntityRef("face", int(value)) for value in descendants),
                )
                for source, descendants in report.source_to_working_faces.items()
                if tuple(descendants) != (int(source),)
            )
            project.geometry.restore_design(working.design_snapshot())
            _apply_replacements(project, replacements)
            self._after = project.geometry.design_snapshot()
            self._after_attributes = _attribute_snapshot(project)
            self._report = report
            return report
        except BaseException:
            project.geometry.restore_design(self._before)
            _restore_attributes(project, self._before_attributes)
            raise

    def undo(self, project: Project) -> None:
        if self._before is None or self._before_attributes is None:
            return
        project.geometry.restore_design(self._before)
        _restore_attributes(project, self._before_attributes)

    def redo(self, project: Project) -> Any:
        if self._after is None or self._after_attributes is None:
            return self.do(project)
        project.geometry.restore_design(self._after)
        _restore_attributes(project, self._after_attributes)
        return self._report


def _attribute_snapshot(project: Project) -> Dict[str, Any]:
    """Cheap snapshot of everything attached to geometry."""

    return {
        "face_sections": dict(project.face_sections),
        "edge_sections": dict(project.edge_sections),
        "sheet_join_intents": dict(project.sheet_join_intents),
        "supports": list(project.supports),
        "masses": list(project.masses),
        "imperfections": list(project.imperfections),
        "refinements": list(project.refinements),
        "element_order": project.element_order,
        "loads": {
            name: (
                list(case.point_loads),
                list(case.pressures),
                list(case.line_loads),
                list(case.surface_tractions),
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
    project.sheet_join_intents.clear()
    project.sheet_join_intents.update(snapshot.get("sheet_join_intents", {}))
    project.supports[:] = list(snapshot["supports"])
    project.masses[:] = list(snapshot.get("masses", ()))
    project.imperfections[:] = list(snapshot.get("imperfections", ()))
    project.refinements[:] = list(snapshot.get("refinements", ()))
    project.element_order = snapshot.get("element_order", project.element_order)
    for name, loads in snapshot["loads"].items():
        points, pressures, lines, *optional = loads
        case = project.load_case(name)
        case.point_loads[:] = list(points)
        case.pressures[:] = list(pressures)
        case.line_loads[:] = list(lines)
        case.surface_tractions[:] = list(optional[0] if optional else ())


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

        masses: List[Mass] = []
        for mass in project.masses:
            if mass.ref != old:
                masses.append(mass)
                continue
            # ``Mass.value`` is a total, unlike pressure or traction intensity.
            # Divide it between descendants so a topology edit conserves mass.
            share = mass.value / len(replacements)
            masses.extend(
                replace(
                    mass,
                    ref=new,
                    value=share,
                    name=f"{mass.name}_{new.id}",
                )
                for new in replacements
            )
        project.masses[:] = masses

        imperfections: List[Imperfection] = []
        for imperfection in project.imperfections:
            if imperfection.ref != old:
                imperfections.append(imperfection)
                continue
            imperfections.extend(
                replace(
                    imperfection,
                    ref=new,
                    name=f"{imperfection.name}_{new.id}",
                )
                for new in replacements
            )
        project.imperfections[:] = imperfections

        refinements = []
        for refinement in project.refinements:
            if refinement.ref != old:
                refinements.append(refinement)
                continue
            refinements.extend(
                replace(
                    refinement,
                    ref=new,
                    name=f"{refinement.name}_{new.id}",
                )
                for new in replacements
            )
        project.refinements[:] = refinements

        for case in project.load_cases.values():
            _replace_loads(case.point_loads, old, replacements)
            _replace_loads(case.pressures, old, replacements)
            _replace_loads(case.line_loads, old, replacements)
            _replace_loads(case.surface_tractions, old, replacements)


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


def _feature_anchor(geometry, reference: EntityRef):
    """Prefer persistent feature-output identity for a topology reference.

    A long-running viewport task can legitimately retain an ``EntityRef``
    while an unrelated feature edit regenerates the model.  Regeneration
    allocates fresh materialized IDs, so first follow ANYgeometry's explicit
    replacement lineage when it resolves to one unambiguous survivor.  This
    is identity-based retargeting; deliberately do not guess by proximity.
    """

    from anygeometry.features import FeatureOutputRef

    candidates = geometry.resolve_ref(reference)
    if len(candidates) == 1:
        reference = candidates[0]
    for record in reversed(geometry.features.records):
        for key, output in record.outputs.items():
            if output == reference:
                return FeatureOutputRef(record.feature_id, key, output.kind)
    return reference


def _geometry_feature_definition(command: "GeometryCommand", geometry):
    """Translate established modelling commands to neutral feature intent."""

    anchor = lambda kind, value: _feature_anchor(
        geometry, EntityRef(kind, int(value))
    )
    if isinstance(command, AddPoint):
        return "geometry.point", {"position": (command.x, command.y, command.z)}, {}
    if isinstance(command, AddLine):
        return "geometry.line", {}, {
            "start": (anchor("vertex", command.start),),
            "end": (anchor("vertex", command.end),),
        }
    if isinstance(command, AddArc):
        return "geometry.arc", {}, {
            "start": (anchor("vertex", command.start),),
            "via": (anchor("vertex", command.via),),
            "end": (anchor("vertex", command.end),),
        }
    if isinstance(command, AddPolyline):
        return "geometry.polyline", {"close": bool(command.close)}, {
            "vertices": tuple(anchor("vertex", item) for item in command.vertex_ids)
        }
    if isinstance(command, AddFace):
        return "geometry.face", {"corners": command.corners}, {
            "edges": tuple(anchor("edge", item) for item in command.edge_ids)
        }
    if isinstance(command, AddPlate):
        return "geometry.plate", {}, {
            "vertices": tuple(anchor("vertex", item) for item in command.vertex_ids)
        }
    if isinstance(command, Extrude):
        return "geometry.extrude", {"vector": tuple(command.vector)}, {
            "edges": tuple(anchor("edge", item) for item in command.edge_ids)
        }
    if isinstance(command, Revolve):
        return "geometry.revolve", {
            "axis_point": tuple(command.axis_point),
            "axis_direction": tuple(command.axis_direction),
            "angle": float(command.angle),
            "segments": command.segments,
        }, {"edges": tuple(anchor("edge", item) for item in command.edge_ids)}
    if isinstance(command, SplitEdge):
        return "geometry.split_edge", {"fraction": float(command.fraction)}, {
            "edge": (anchor("edge", command.edge_id),)
        }
    if isinstance(command, SplitFace):
        return "geometry.split_face", {
            "axis": int(command.axis), "fraction": float(command.fraction)
        }, {"face": (anchor("face", command.face_id),)}
    if isinstance(command, StripFace):
        return "geometry.strip_face", {
            "axis": int(command.axis), "count": int(command.count)
        }, {"face": (anchor("face", command.face_id),)}
    if isinstance(command, NeutralTrimHole):
        return "geometry.trim_hole", {
            "centre": tuple(float(item) for item in command.centre),
            "radius": float(command.radius),
        }, {"face": (anchor("face", command.face_id),)}
    if isinstance(command, SetFaceCorners):
        return "geometry.set_face_corners", {"corners": tuple(command.corners)}, {
            "face": (anchor("face", command.face_id),)
        }
    if isinstance(command, CopyEntities):
        return "geometry.copy", {"matrix": command.affine_matrix().tolist()}, {
            "entities": tuple(_feature_anchor(geometry, item) for item in command.references)
        }
    if isinstance(command, MirrorEntities):
        return "geometry.mirror", {
            "plane_point": tuple(command.plane_point),
            "plane_normal": tuple(command.plane_normal),
        }, {
            "entities": tuple(_feature_anchor(geometry, item) for item in command.references)
        }
    if isinstance(command, LinearPattern):
        return "geometry.pattern.linear", {
            "direction": tuple(command.direction),
            "spacing": float(command.spacing),
            "count": int(command.count),
        }, {
            "entities": tuple(_feature_anchor(geometry, item) for item in command.references)
        }
    if isinstance(command, CircularPattern):
        return "geometry.pattern.circular", {
            "axis_point": tuple(command.axis_point),
            "axis_direction": tuple(command.axis_direction),
            "angle_step": float(command.angle_step),
            "count": int(command.count),
        }, {
            "entities": tuple(_feature_anchor(geometry, item) for item in command.references)
        }
    if isinstance(command, ReverseEntity):
        return "geometry.reverse", {}, {
            "entity": (_feature_anchor(geometry, command.reference),)
        }
    # Mesher decomposition commands deliberately stay out of design history:
    # they are element-control intent, not neutral geometric features.
    return None


def _numbered_outputs(
    outputs: Mapping[str, EntityRef], prefix: str
) -> list[EntityRef]:
    """Return semantic ``prefix/N`` outputs in numeric rather than text order."""

    made: list[tuple[int, EntityRef]] = []
    marker = f"{prefix}/"
    for key, reference in outputs.items():
        if not key.startswith(marker):
            continue
        suffix = key[len(marker):]
        if suffix.isdigit():
            made.append((int(suffix), reference))
    return [reference for _index, reference in sorted(made)]


def _insert_result(geometry, outputs: Mapping[str, EntityRef]) -> InsertResult:
    """Rebuild the established copy result from persistent semantic slots."""

    mapping: Dict[EntityRef, EntityRef] = {}
    values = set(outputs.values())
    for key, reference in outputs.items():
        parts = key.split("/")
        if len(parts) == 2 and parts[0] in ("vertex", "edge", "face"):
            try:
                mapping[EntityRef(parts[0], int(parts[1]))] = reference
            except ValueError:
                continue
    groups = {
        name: tuple(item for item in geometry.group(name) if item in values)
        for name in geometry.groups
        if any(item in values for item in geometry.group(name))
    }
    return InsertResult(mapping, groups, dict(outputs))


def _feature_command_result(command: "GeometryCommand", geometry, record) -> Any:
    """Preserve established command return types after feature regeneration."""

    outputs = record.outputs
    if isinstance(command, AddPoint):
        return outputs["point"].id
    if isinstance(command, (AddLine, AddArc)):
        return outputs["edge"].id
    if isinstance(command, AddPolyline):
        return [item.id for item in _numbered_outputs(outputs, "edge")]
    if isinstance(command, (AddFace, AddPlate)):
        return outputs["face"].id
    if isinstance(command, (Extrude, Revolve)):
        return [item.id for item in _numbered_outputs(outputs, "face")]
    if isinstance(command, SplitEdge):
        return outputs["point"].id
    if isinstance(command, SplitFace):
        return tuple(item.id for item in _numbered_outputs(outputs, "face"))
    if isinstance(command, StripFace):
        return (
            [item.id for item in _numbered_outputs(outputs, "face")],
            [item.id for item in _numbered_outputs(outputs, "divider")],
        )
    if isinstance(command, NeutralTrimHole):
        return (
            outputs["face"].id,
            tuple(item.id for item in _numbered_outputs(outputs, "boundary")),
        )
    if isinstance(command, SetFaceCorners):
        return None
    if isinstance(command, (CopyEntities, MirrorEntities)):
        return _insert_result(geometry, outputs)
    if isinstance(command, (LinearPattern, CircularPattern)):
        instances: Dict[int, Dict[str, EntityRef]] = {}
        for key, reference in outputs.items():
            parts = key.split("/", 2)
            if len(parts) != 3 or parts[0] != "instance" or not parts[1].isdigit():
                continue
            instances.setdefault(int(parts[1]), {})[parts[2]] = reference
        return PatternResult(
            tuple(
                _insert_result(geometry, instances[index])
                for index in sorted(instances)
            )
        )
    if isinstance(command, ReverseEntity):
        return outputs["entity"]
    raise GeometryError(
        f"feature result adapter is missing for {type(command).__name__}"
    )


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
class CopyEntities(GeometryCommand):
    """Copy a selected topology closure with an optional affine offset."""

    references: Sequence[EntityRef]
    translation: Sequence[float] = (0.0, 0.0, 0.0)
    matrix: Sequence[Sequence[float]] | None = None
    label: str = "copy geometry"

    def __post_init__(self) -> None:
        GeometryCommand.__init__(self)
        self.references = tuple(self.references)

    def affine_matrix(self) -> np.ndarray:
        affine = (
            np.eye(4, dtype=float)
            if self.matrix is None
            else np.asarray(self.matrix, dtype=float).copy()
        )
        offset = np.asarray(self.translation, dtype=float)
        if affine.shape != (4, 4) or not np.all(np.isfinite(affine)):
            raise GeometryError("copy transform must be a finite 4x4 matrix")
        if offset.shape != (3,) or not np.all(np.isfinite(offset)):
            raise GeometryError("copy translation must be a finite 3-vector")
        affine[:3, 3] += offset
        return affine

    def operate(self, project: Project) -> InsertResult:
        return copy_entities(
            project.geometry, self.references, matrix=self.affine_matrix()
        )


@dataclass(eq=False)
class MirrorEntities(GeometryCommand):
    """Make a reflected copy of the selected geometry."""

    references: Sequence[EntityRef]
    plane_point: Sequence[float] = (0.0, 0.0, 0.0)
    plane_normal: Sequence[float] = (1.0, 0.0, 0.0)
    label: str = "mirror geometry"

    def __post_init__(self) -> None:
        GeometryCommand.__init__(self)
        self.references = tuple(self.references)

    def operate(self, project: Project) -> InsertResult:
        return mirror_entities(
            project.geometry,
            self.references,
            self.plane_point,
            self.plane_normal,
        )


@dataclass(eq=False)
class LinearPattern(GeometryCommand):
    """Make equally spaced translated copies of selected geometry."""

    references: Sequence[EntityRef]
    direction: Sequence[float] = (1.0, 0.0, 0.0)
    spacing: float = 1.0
    count: int = 1
    label: str = "linear pattern"

    def __post_init__(self) -> None:
        GeometryCommand.__init__(self)
        self.references = tuple(self.references)

    def operate(self, project: Project) -> PatternResult:
        return linear_pattern(
            project.geometry,
            self.references,
            self.direction,
            self.spacing,
            self.count,
        )


@dataclass(eq=False)
class CircularPattern(GeometryCommand):
    """Make equally spaced rotated copies about an axis."""

    references: Sequence[EntityRef]
    axis_point: Sequence[float] = (0.0, 0.0, 0.0)
    axis_direction: Sequence[float] = (0.0, 0.0, 1.0)
    angle_step: float = np.pi / 2.0
    count: int = 1
    label: str = "circular pattern"

    def __post_init__(self) -> None:
        GeometryCommand.__init__(self)
        self.references = tuple(self.references)

    def operate(self, project: Project) -> PatternResult:
        return circular_pattern(
            project.geometry,
            self.references,
            self.axis_point,
            self.axis_direction,
            self.angle_step,
            self.count,
        )


@dataclass(eq=False)
class ReverseEntity(GeometryCommand):
    """Reverse an edge parameterization or a face's structural normal."""

    reference: EntityRef
    label: str = "reverse orientation"

    def __post_init__(self) -> None:
        GeometryCommand.__init__(self)

    def operate(self, project: Project) -> EntityRef:
        if self.reference.kind == "edge":
            return reverse_edge(project.geometry, self.reference.id)
        if self.reference.kind == "face":
            return reverse_face(project.geometry, self.reference.id)
        raise GeometryError("orientation can only be reversed for an edge or face")


@dataclass(frozen=True)
class _SheetJoinPlan:
    face_ids: tuple[int, ...]
    sheet_ids: tuple[int, ...]
    anchor_part_id: int
    removable_part_ids: tuple[int, ...]
    orientations: tuple[Orientation, ...]
    policy: Any


def _plan_sheet_join(geometry, references: Sequence[EntityRef]) -> _SheetJoinPlan:
    """Validate an exact, orientation-consistent replacement of singleton sheets."""

    made = tuple(references)
    if len(made) < 2:
        raise GeometryError("Join Sheet needs at least two selected plate faces")
    if any(not isinstance(reference, EntityRef) or reference.kind != "face" for reference in made):
        raise GeometryError("Join Sheet accepts only face EntityRefs")
    face_ids = tuple(sorted(reference.id for reference in made))
    if len(set(face_ids)) != len(face_ids):
        raise GeometryError("Join Sheet selection contains a duplicate face")
    for face_id in face_ids:
        if face_id not in geometry.faces:
            # Deliberately do not follow replacement history and never search
            # for a geometrically nearby face.  Joining changes ownership,
            # so its inputs must name exact current topology.
            raise GeometryError(f"Join Sheet selected missing face {face_id}")

    selected_uses: dict[int, Any] = {}
    selected_sheets: dict[int, Any] = {}
    for face_id in face_ids:
        uses = tuple(
            use for use in geometry.face_uses.values() if use.face_id == face_id
        )
        if len(uses) != 1:
            raise GeometryError(
                f"face {face_id} must belong to exactly one structural Sheet; "
                f"found {len(uses)} owners"
            )
        use = uses[0]
        sheet = geometry.sheets[use.sheet_id]
        if len(sheet.face_use_ids) != 1:
            owned = [
                geometry.face_uses[use_id].face_id
                for use_id in sheet.face_use_ids
            ]
            raise GeometryError(
                f"face {face_id} already belongs to joined Sheet {sheet.id} "
                f"with faces {owned}"
            )
        if sheet.id in selected_sheets:
            raise GeometryError(
                f"faces in Join Sheet must have independent singleton owners; "
                f"Sheet {sheet.id} was selected twice"
            )
        if sheet.declared_non_manifold_edges:
            raise GeometryError(
                f"Sheet {sheet.id} carries declared non-manifold intent that "
                "Join Sheet cannot discard"
            )
        if sheet.metadata or use.metadata or any(
            geometry.coedges[coedge_id].metadata for coedge_id in use.coedge_ids
        ):
            raise GeometryError(
                f"Sheet {sheet.id} carries owner metadata that Join Sheet "
                "cannot transfer losslessly"
            )
        selected_uses[face_id] = use
        selected_sheets[sheet.id] = sheet

    policies = {sheet.policy for sheet in selected_sheets.values()}
    if len(policies) != 1:
        raise GeometryError("selected Sheets use different topology policies")

    sheet_ids = tuple(sorted(selected_sheets))
    sheet_id_set = set(sheet_ids)
    part_ids = {sheet.part_id for sheet in selected_sheets.values()}
    for part_id in sorted(part_ids):
        part = geometry.parts[part_id]
        if part.name or part.metadata:
            raise GeometryError(
                f"Part {part_id} carries a name or metadata that Join Sheet "
                "cannot preserve through feature regeneration"
            )
        if set(part.sheet_ids) - sheet_id_set or part.member_ids:
            raise GeometryError(
                f"Part {part_id} owns unselected structural definitions; "
                "Join Sheet requires isolated singleton plate owners"
            )
        if any(
            attachment.part_id == part_id
            for attachment in geometry.attachments.values()
        ):
            raise GeometryError(
                f"cannot join Sheets while attachment references Part {part_id}"
            )
    for junction in geometry.junctions.values():
        touched = sheet_id_set.intersection(junction.sheet_ids)
        if touched:
            raise GeometryError(
                f"cannot join Sheet(s) {sorted(touched)} while junction "
                f"{junction.id} references them"
            )
    for attachment in geometry.attachments.values():
        touched_sheet = (
            attachment.sheet_id in sheet_id_set
            or attachment.source_key in {("sheet", item) for item in sheet_id_set}
            or attachment.target_key in {("sheet", item) for item in sheet_id_set}
        )
        if touched_sheet:
            raise GeometryError(
                f"cannot join Sheets while attachment {attachment.id} "
                "references a singleton Sheet owner"
            )

    # A face's coedge orientation is relative to the geometric edge.  Sheet
    # orientation multiplies that sign.  Solve the exact shared-edge graph so
    # the two effective directions are opposite at every internal boundary.
    edge_uses: dict[int, list[tuple[int, int]]] = {}
    for face_id, use in selected_uses.items():
        seen: set[int] = set()
        for coedge_id in use.coedge_ids:
            coedge = geometry.coedges[coedge_id]
            if coedge.edge_id in seen:
                raise GeometryError(
                    f"face {face_id} uses edge {coedge.edge_id} more than once; "
                    "sheet orientation is ambiguous"
                )
            seen.add(coedge.edge_id)
            edge_uses.setdefault(coedge.edge_id, []).append(
                (face_id, int(coedge.orientation))
            )

    adjacency: dict[int, list[tuple[int, int, int, int]]] = {
        face_id: [] for face_id in face_ids
    }
    for edge_id, uses in sorted(edge_uses.items()):
        if len(uses) > 2:
            raise GeometryError(
                f"selected faces create a non-manifold Sheet at edge {edge_id}"
            )
        if len(uses) == 2:
            (first, first_coedge), (second, second_coedge) = uses
            adjacency[first].append(
                (second, first_coedge, second_coedge, edge_id)
            )
            adjacency[second].append(
                (first, second_coedge, first_coedge, edge_id)
            )

    anchor = face_ids[0]
    solved: dict[int, int] = {
        anchor: int(selected_uses[anchor].orientation)
    }
    pending = [anchor]
    while pending:
        current = pending.pop()
        for neighbor, current_coedge, neighbor_coedge, edge_id in adjacency[current]:
            required = -current_coedge * solved[current] * neighbor_coedge
            existing = solved.get(neighbor)
            if existing is None:
                solved[neighbor] = required
                pending.append(neighbor)
            elif existing != required:
                raise GeometryError(
                    "selected faces are not consistently orientable; conflicting "
                    f"requirements meet at edge {edge_id}"
                )
    disconnected = sorted(set(face_ids) - set(solved))
    if disconnected:
        raise GeometryError(
            "selected faces do not share one connected exact-edge topology; "
            f"disconnected faces {disconnected}"
        )

    anchor_part_id = selected_sheets[selected_uses[anchor].sheet_id].part_id
    removable_parts: list[int] = []
    for part_id in sorted(part_ids - {anchor_part_id}):
        part = geometry.parts[part_id]
        remaining_sheets = set(part.sheet_ids) - sheet_id_set
        if not remaining_sheets and not part.member_ids:
            removable_parts.append(part_id)

    return _SheetJoinPlan(
        face_ids,
        sheet_ids,
        anchor_part_id,
        tuple(removable_parts),
        tuple(Orientation(solved[face_id]) for face_id in face_ids),
        next(iter(policies)),
    )


def _apply_sheet_join(geometry, plan: _SheetJoinPlan, name: str) -> int:
    """Apply a prevalidated owner replacement in one geometry transaction."""

    with geometry.transaction():
        for sheet_id in reversed(plan.sheet_ids):
            geometry.remove_sheet(sheet_id)
        for part_id in reversed(plan.removable_part_ids):
            geometry.remove_part(part_id)
        return geometry.add_sheet(
            plan.face_ids,
            part_id=plan.anchor_part_id,
            name=name,
            policy=plan.policy,
            orientations=plan.orientations,
        )


@dataclass(eq=False)
class JoinSheet(GeometryCommand):
    """Join exact selected singleton plate owners into one coherent Sheet.

    Geometry faces, feature outputs, regions, loads and section targets retain
    their IDs.  Only structural ownership changes.  Orientation is expressed
    through ``FaceUse`` values; geometric face loops/normals are never reversed.
    """

    references: Sequence[EntityRef]
    name: str = "Joined sheet"
    label: str = "join sheet"

    def __post_init__(self) -> None:
        GeometryCommand.__init__(self)
        self.references = tuple(self.references)

    def preflight(self, project: Project) -> None:
        plan = _plan_sheet_join(project.geometry, self.references)

        # Exercise the complete public owner mutation against a detached clone
        # before allocating any live structural IDs.  This catches name/policy
        # and whole-model validation failures without partially touching the
        # document.  The live application remains wrapped by GeometryCommand's
        # full design/attribute rollback as a final fail-closed guard.
        working = project.geometry.clone(include_features=False)
        working_plan = _plan_sheet_join(working, self.references)
        _apply_sheet_join(working, working_plan, str(self.name))

    def operate(self, project: Project) -> int:
        plan = _plan_sheet_join(project.geometry, self.references)

        sheet_id = _apply_sheet_join(project.geometry, plan, str(self.name))
        project.add_sheet_join_intent(
            SheetJoinIntent(
                name=str(self.name),
                anchors=join_anchors(
                    project.geometry,
                    tuple(
                        EntityRef("face", face_id)
                        for face_id in plan.face_ids
                    ),
                ),
                orientations=plan.orientations,
                policy=plan.policy,
            )
        )
        return sheet_id


@dataclass(frozen=True)
class MeasureGeometry:
    """Typed, read-only ANYgeometry measurement for ``CommandStack.query``."""

    references: EntityRef | Sequence[EntityRef]
    quantity: str = "auto"

    def evaluate(self, project: Project) -> Measurement:
        return measure(project.geometry, self.references, quantity=self.quantity)


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
    _snapshot: Dict[str, object] = field(default_factory=dict, init=False)
    _attributes: Dict[str, Any] = field(default_factory=dict, init=False)

    def do(self, project: Project) -> None:
        geometry = project.geometry
        # ``begin_replacement_log`` intentionally clears the current public
        # transaction log.  Qualify the complete owner-aware deletion on a
        # detached model first, so a normal rejected delete never touches that
        # caller-owned log (and never needs a private-store repair).
        working = geometry.clone(include_features=False)
        _delete_exact_geometry_ref(working, self.ref)
        # Public removals update semantic groups, tags and persistent
        # replacement history in addition to deleting the entity.  Capture the
        # complete owner topology so undo restores those annotations and the
        # current replacement transaction exactly; reinserting one entity in
        # the raw dictionary would leave the reference resolved as deleted.
        self._snapshot = geometry.topology_snapshot()
        geometry.begin_replacement_log()
        try:
            _delete_exact_geometry_ref(geometry, self.ref)
        except Exception:
            # ``begin_replacement_log`` is itself stateful.  A rejected delete
            # must not clear the caller's current transaction log.
            geometry.restore_topology(self._snapshot)
            raise
        self._attributes = _detach_attributes(project, self.ref)

    def undo(self, project: Project) -> None:
        project.geometry.restore_topology(self._snapshot)
        _reattach_attributes(project, self.ref, self._attributes)


def _delete_exact_geometry_ref(geometry, ref: EntityRef) -> None:
    """Delete one exact current topology reference through public owner APIs."""

    _detach_structural_owner(geometry, ref)
    if ref.kind == "face":
        geometry.remove_face(ref.id)
    elif ref.kind == "edge":
        geometry.remove_edge(ref.id)
    elif ref.kind == "vertex":
        geometry.remove_vertex(ref.id)
    else:  # Defensive for callers constructing EntityRef-like values dynamically.
        raise GeometryError(f"cannot delete unsupported geometry kind {ref.kind!r}")


def _detach_structural_owner(geometry, ref: EntityRef) -> None:
    """Remove singleton structural ownership before deleting its topology.

    Section assignment creates one Sheet per independently authored plate and
    one Member per assigned beam.  Those records deliberately protect their
    topology from accidental raw deletion.  A user-facing Delete command is
    explicit intent, so it removes the singleton owner and its connection
    records in dependency order.  Joined multi-face sheets and multi-edge
    members remain protected: engineers must split/unjoin them explicitly.
    """

    attachment_ids: set[int] = set()
    junction_ids: set[int] = set()
    owner_parts: set[int] = set()
    sheet_ids: tuple[int, ...] = ()
    member_ids: tuple[int, ...] = ()

    if ref.kind == "face":
        sheet_ids = tuple(
            sorted(
                {
                    use.sheet_id
                    for use in geometry.face_uses.values()
                    if use.face_id == ref.id
                }
            )
        )
        for sheet_id in sheet_ids:
            sheet = geometry.sheets[sheet_id]
            owned_faces = tuple(
                geometry.face_uses[use_id].face_id
                for use_id in sheet.face_use_ids
            )
            if owned_faces != (ref.id,):
                raise GeometryError(
                    f"cannot delete face {ref.id}: it belongs to joined sheet "
                    f"{sheet_id} with faces {list(owned_faces)}; unjoin the sheet first"
                )
            owner_parts.add(sheet.part_id)
            attachment_ids.update(geometry.attachments_for_sheet(sheet_id))
            junction_ids.update(
                junction.id
                for junction in geometry.junctions.values()
                if sheet_id in junction.sheet_ids
            )
        attachment_ids.update(geometry.attachments_for_source("face", ref.id))
        attachment_ids.update(geometry.attachments_for_face(ref.id))
    elif ref.kind == "edge":
        member_ids = tuple(geometry.members_using_edge(ref.id))
        for member_id in member_ids:
            member = geometry.members[member_id]
            owned_edges = tuple(
                geometry.member_edge_uses[use_id].edge_id
                for use_id in member.edge_use_ids
            )
            if owned_edges != (ref.id,):
                raise GeometryError(
                    f"cannot delete edge {ref.id}: it belongs to joined member "
                    f"{member_id} with edges {list(owned_edges)}; split the member first"
                )
            owner_parts.add(member.part_id)
            attachment_ids.update(geometry.attachments_for_member(member_id))
            junction_ids.update(
                junction.id
                for junction in geometry.junctions.values()
                if member_id in junction.member_ids
            )
        attachment_ids.update(geometry.attachments_for_source("edge", ref.id))
    else:
        attachment_ids.update(geometry.attachments_for_source("vertex", ref.id))

    junction_ids.update(
        junction.id
        for junction in geometry.junctions.values()
        if any(value in attachment_ids for value in junction.attachment_ids)
    )
    for junction_id in sorted(junction_ids, reverse=True):
        if junction_id in geometry.junctions:
            geometry.remove_junction(junction_id)
    for attachment_id in sorted(attachment_ids, reverse=True):
        if attachment_id in geometry.attachments:
            geometry.remove_attachment(attachment_id)
    for member_id in reversed(member_ids):
        if member_id in geometry.members:
            geometry.remove_member(member_id)
    for sheet_id in reversed(sheet_ids):
        if sheet_id in geometry.sheets:
            geometry.remove_sheet(sheet_id)
    for part_id in sorted(owner_parts, reverse=True):
        if part_id in geometry.parts:
            part = geometry.parts[part_id]
            if not part.sheet_ids and not part.member_ids:
                geometry.remove_part(part_id)


def _detach_attributes(project: Project, ref: EntityRef) -> Dict[str, Any]:
    """Remove and record everything that referenced a deleted entity."""

    removed: Dict[str, Any] = {
        "face_section": None,
        "edge_section": None,
        "supports": [],
        "masses": [],
        "imperfections": [],
        "refinements": [],
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

    for attribute in ("masses", "imperfections"):
        container = getattr(project, attribute)
        survivors = []
        for item in container:
            if item.ref == ref:
                removed[attribute].append(item)
            else:
                survivors.append(item)
        container[:] = survivors

    refinements = []
    for item in project.refinements:
        if item.ref == ref:
            removed["refinements"].append(item)
        else:
            refinements.append(item)
    project.refinements[:] = refinements

    for case in project.load_cases.values():
        for attribute in (
            "point_loads",
            "pressures",
            "line_loads",
            "surface_tractions",
        ):
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
    project.masses.extend(removed.get("masses", ()))
    project.imperfections.extend(removed.get("imperfections", ()))
    project.refinements.extend(removed.get("refinements", ()))


# ----------------------------------------------------------------------
# attributes
# ----------------------------------------------------------------------
@dataclass(eq=False)
class AssignPlate(Command):
    face_id: int
    section: str
    label: str = "assign plate section"
    _previous: Optional[str] = field(default=None, init=False)
    _captured: bool = field(default=False, init=False)
    _previous_assignments: Dict[str, Any] = field(default_factory=dict, init=False)
    _previous_region_ids: set[str] = field(default_factory=set, init=False)
    _previous_maps: Any = field(default=None, init=False)

    def do(self, project: Project) -> None:
        if not self._captured:
            self._previous = project.face_sections.get(self.face_id)
            self._previous_assignments = dict(project.section_assignments)
            self._previous_region_ids = {region.id for region in project.regions}
            self._previous_maps = project._section_compatibility_snapshot()
            self._captured = True
        project.assign_plate(self.face_id, self.section)

    def undo(self, project: Project) -> None:
        project.section_assignments.clear()
        project.section_assignments.update(self._previous_assignments)
        for region in tuple(project.regions):
            if region.id not in self._previous_region_ids:
                project.regions.remove(region.id)
        project._singleton_region_cache_size = -1
        project._restore_section_compatibility(self._previous_maps)


@dataclass(eq=False)
class AssignBeam(Command):
    edge_id: int
    section: str
    label: str = "assign beam section"
    _previous: Optional[str] = field(default=None, init=False)
    _captured: bool = field(default=False, init=False)
    _previous_assignments: Dict[str, Any] = field(default_factory=dict, init=False)
    _previous_region_ids: set[str] = field(default_factory=set, init=False)
    _previous_maps: Any = field(default=None, init=False)

    def do(self, project: Project) -> None:
        if not self._captured:
            self._previous = project.edge_sections.get(self.edge_id)
            self._previous_assignments = dict(project.section_assignments)
            self._previous_region_ids = {region.id for region in project.regions}
            self._previous_maps = project._section_compatibility_snapshot()
            self._captured = True
        project.assign_beam(self.edge_id, self.section)

    def undo(self, project: Project) -> None:
        project.section_assignments.clear()
        project.section_assignments.update(self._previous_assignments)
        for region in tuple(project.regions):
            if region.id not in self._previous_region_ids:
                project.regions.remove(region.id)
        project._singleton_region_cache_size = -1
        project._restore_section_compatibility(self._previous_maps)


@dataclass(eq=False)
class AddSupport(Command):
    support: Support
    label: str = "add support"
    _applied_support: Support | None = field(default=None, init=False)

    def do(self, project: Project) -> Support:
        if self._applied_support is None:
            self._applied_support = project.add_support(self.support)
        else:
            project.supports.append(self._applied_support)
        return self._applied_support

    def undo(self, project: Project) -> None:
        if self._applied_support is not None:
            project.supports.remove(self._applied_support)


_EDITABLE_ATTRIBUTE_TYPES = (
    Support,
    Mass,
    PointLoad,
    Pressure,
    LineLoad,
    SurfaceTraction,
    Imperfection,
)


def _attribute_container(
    project: Project, item_id: str
) -> tuple[tuple[str, ...], list[Any], int, Any]:
    """Locate one UUID-backed support, mass or load without using its label."""

    for attribute in ("supports", "masses", "imperfections"):
        container = getattr(project, attribute)
        for index, item in enumerate(container):
            if getattr(item, "id", None) == item_id:
                return (("project", attribute), container, index, item)
    for case in project.load_cases.values():
        for attribute in (
            "point_loads",
            "pressures",
            "line_loads",
            "surface_tractions",
        ):
            container = getattr(case, attribute)
            for index, item in enumerate(container):
                if getattr(item, "id", None) == item_id:
                    return (("case", case.id, attribute), container, index, item)
    raise ProjectError(f"attribute {item_id!r} does not exist")


def _attribute_container_at(project: Project, address: tuple[str, ...]) -> list[Any]:
    if address[0] == "project":
        return getattr(project, address[1])
    case_id, attribute = address[1], address[2]
    for case in project.load_cases.values():
        if case.id == case_id:
            return getattr(case, attribute)
    raise ProjectError(f"load case {case_id!r} no longer exists")


@dataclass(eq=False)
class EditAttribute(Command):
    """Replace one selected support/load/mass while retaining its UUID."""

    replacement: Any
    label: str = "edit model attribute"
    _previous: Any = field(default=None, init=False)
    _address: tuple[str, ...] | None = field(default=None, init=False)

    def do(self, project: Project) -> Any:
        if not isinstance(self.replacement, _EDITABLE_ATTRIBUTE_TYPES):
            raise TypeError("replacement must be a support, load or mass")
        address, container, index, previous = _attribute_container(
            project, self.replacement.id
        )
        if type(previous) is not type(self.replacement):
            raise TypeError("an attribute edit cannot change the attribute type")
        if self._previous is None:
            self._previous = previous
            self._address = address
        container[index] = self.replacement
        return self.replacement

    def undo(self, project: Project) -> None:
        if self._previous is None:
            return
        _address, container, index, _current = _attribute_container(
            project, self.replacement.id
        )
        container[index] = self._previous


@dataclass(eq=False)
class DeleteAttribute(Command):
    """Delete one UUID-backed support/load/mass and restore it exactly on undo."""

    item_id: str
    label: str = "delete model attribute"
    _item: Any = field(default=None, init=False)
    _address: tuple[str, ...] | None = field(default=None, init=False)
    _index: int = field(default=-1, init=False)

    def do(self, project: Project) -> Any:
        address, container, index, item = _attribute_container(project, self.item_id)
        if self._item is None:
            self._item = item
            self._address = address
            self._index = index
        container.pop(index)
        return item

    def undo(self, project: Project) -> None:
        if self._item is None or self._address is None:
            return
        container = _attribute_container_at(project, self._address)
        container.insert(min(self._index, len(container)), self._item)


@dataclass(eq=False)
class AddPointLoad(Command):
    ref: EntityRef
    force: Sequence[float] = (0.0, 0.0, 0.0)
    moment: Sequence[float] = (0.0, 0.0, 0.0)
    case: str = "default"
    coordinate_system_id: str = "global"
    distribution_policy: str = "per_target"
    label: str = "add point load"
    _load: Any = field(default=None, init=False)

    def do(self, project: Project) -> Any:
        case = project.load_case(self.case)
        if self._load is None:
            self._load = case.add_point_load(
                self.ref,
                self.force,
                self.moment,
                coordinate_system_id=self.coordinate_system_id,
                distribution_policy=self.distribution_policy,
            )
        else:
            case.point_loads.append(self._load)
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
        case = project.load_case(self.case)
        if self._load is None:
            self._load = case.add_pressure(self.ref, self.value)
        else:
            case.pressures.append(self._load)
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
        _edge, faces = split_face_at(
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
        strips, dividers = strip_face(
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


def _closest_face(
    geometry, face_ids: Sequence[int], point: np.ndarray
) -> Tuple[int, Tuple[float, float]]:
    """Which candidate face is closest, and its authoritative local UV."""

    references = [EntityRef("face", face_id) for face_id in face_ids]
    if not references:
        raise ValueError("no plate to refine: the model has no plates")
    reference, _closest, _distance = closest_point(
        geometry, np.asarray(point, dtype=float), references
    )
    _projected, uv, _gap = project_point(geometry, reference, point)
    return reference.id, uv


def _face_spans(geometry, face_id: int) -> Tuple[float, float]:
    """Arc length across a face in each parametric direction."""

    sides = geometry.faces[face_id].sides()
    return (
        sum(geometry.edge_length(item.edge) for item in sides[0]),
        sum(geometry.edge_length(item.edge) for item in sides[1]),
    )


def _face_axis_direction(geometry, face_id: int, axis: int) -> np.ndarray:
    """Unit chord direction of one mapped coordinate on a face fragment."""

    side = geometry.faces[face_id].sides()[axis]
    start = geometry.vertex_position(
        geometry.oriented_start_vertex(side[0])
    )
    end = geometry.vertex_position(
        geometry.oriented_end_vertex(side[-1])
    )
    direction = np.asarray(end, dtype=float) - np.asarray(start, dtype=float)
    length = float(np.linalg.norm(direction))
    if length <= 0.0:
        raise GeometryError(
            f"face {face_id} has a degenerate mapped axis {axis}"
        )
    return direction / length


def _cut_face_across_direction(
    geometry,
    face_id: int,
    point: np.ndarray,
    direction: np.ndarray,
    radius: float,
) -> Tuple[int, List[int]]:
    """Bracket ``point`` on one face along an inherited mapped direction."""

    patch = face_id
    produced = [face_id]
    for side in (-1.0, 1.0):
        local_directions = tuple(
            _face_axis_direction(geometry, patch, axis) for axis in (0, 1)
        )
        alignments = tuple(
            float(np.dot(candidate, direction))
            for candidate in local_directions
        )
        axis = int(np.argmax(np.abs(alignments)))
        orientation = 1.0 if alignments[axis] >= 0.0 else -1.0
        span = _face_spans(geometry, patch)[axis]
        if span <= 0.0:
            break
        _face, where = _closest_face(geometry, [patch], point)
        fraction = where[axis] + side * orientation * radius / span
        if not 0.02 < fraction < 0.98:
            continue
        before_cut = geometry.design_snapshot()
        try:
            _edge, pair = split_face_at(
                geometry, patch, axis, fraction
            )
        except GeometryError:
            # Keep a skipped, unrepresentable cut observationally atomic even
            # when paired with an older decomposition provider.
            geometry.restore_design(before_cut)
            continue
        produced.remove(patch)
        produced.extend(pair)
        patch, _where = _closest_face(geometry, list(pair), point)
    return patch, produced


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

    # Split the complete strip set in the second direction.  Cutting only the
    # central descendant leaves T-junctions and forces an abrupt 8:1 mapped
    # size jump at the contact patch.  A conforming grid lets the size field
    # grade through every shared edge and satisfy quality_v2.
    directions = tuple(
        _face_axis_direction(geometry, face_id, axis) for axis in (0, 1)
    )
    _middle, columns = _cut_face_across_direction(
        geometry, face_id, point, directions[0], radius
    )
    central_rows: List[int] = []
    produced: List[int] = []
    for column in columns:
        middle, rows = _cut_face_across_direction(
            geometry, column, point, directions[1], radius
        )
        central_rows.append(middle)
        produced.extend(rows)
    patch, _where = _closest_face(geometry, central_rows, point)
    return patch, produced


@dataclass(eq=False)
class TriangleToQuads(GeometryCommand):
    """Turn a three-sided region into three mapped plates."""

    edge_ids: Sequence[int] = ()
    label: str = "triangle to plates"

    def __post_init__(self) -> None:
        GeometryCommand.__init__(self)

    def operate(self, project: Project) -> List[int]:
        return triangle_to_quads(project.geometry, self.edge_ids)


@dataclass(eq=False)
class ButterflyHoleDecomposition(GeometryCommand):
    """Create mapped butterfly patches around a circular mesh opening."""

    face_id: int
    centre: Sequence[float] = (0.0, 0.0, 0.0)
    radius: float = 0.1
    label: str = "butterfly hole mesh decomposition"

    def __post_init__(self) -> None:
        GeometryCommand.__init__(self)

    def operate(self, project: Project) -> Tuple[List[int], List[int]]:
        return punch_circular_hole(
            project.geometry, self.face_id, self.centre, self.radius
        )


@dataclass(eq=False)
class NeutralTrimHole(GeometryCommand):
    """Create a true trimmed face hole without meshing decomposition.

    This wraps ANYgeometry's neutral feature.  The face keeps its identity,
    the circular arcs become an inner boundary, and no mapped-mesh patches are
    implied.  Its centre/radius and face anchor are persisted for regeneration.
    """

    face_id: int
    centre: Sequence[float] = (0.0, 0.0, 0.0)
    radius: float = 0.1
    label: str = "neutral trim hole"

    def __post_init__(self) -> None:
        GeometryCommand.__init__(self)

    def operate(self, project: Project) -> Tuple[int, Tuple[int, ...]]:
        return punch_hole(
            project.geometry, self.face_id, self.centre, self.radius
        )


# Historical scripting name.  The GUI and new code use the explicit meshing
# term above so this operation cannot be mistaken for ANYgeometry's neutral
# trim/hole feature.
PunchHole = ButterflyHoleDecomposition


# ----------------------------------------------------------------------
# loads, masses, combinations and imperfections
# ----------------------------------------------------------------------
@dataclass(eq=False)
class AddLineLoad(Command):
    ref: EntityRef
    force_per_length: Sequence[float] = (0.0, 0.0, 0.0)
    case: str = "default"
    coordinate_system_id: str = "global"
    label: str = "add line load"
    _load: Any = field(default=None, init=False)

    def do(self, project: Project) -> Any:
        case = project.load_case(self.case)
        if self._load is None:
            self._load = case.add_line_load(
                self.ref,
                self.force_per_length,
                coordinate_system_id=self.coordinate_system_id,
            )
        else:
            case.line_loads.append(self._load)
        return self._load

    def undo(self, project: Project) -> None:
        project.load_case(self.case).line_loads.remove(self._load)


@dataclass(eq=False)
class AddSurfaceTraction(Command):
    ref: EntityRef
    traction: Sequence[float] = (0.0, 0.0, 0.0)
    case: str = "default"
    coordinate_system_id: str = "global"
    label: str = "add surface traction"
    _load: Any = field(default=None, init=False)

    def do(self, project: Project) -> Any:
        case = project.load_case(self.case)
        if self._load is None:
            self._load = case.add_surface_traction(
                self.ref,
                self.traction,
                coordinate_system_id=self.coordinate_system_id,
            )
        else:
            case.surface_tractions.append(self._load)
        return self._load

    def undo(self, project: Project) -> None:
        project.load_case(self.case).surface_tractions.remove(self._load)


@dataclass(eq=False)
class AddMass(Command):
    ref: EntityRef
    value: float = 0.0
    name: str = "mass"
    distribution_policy: str = "total_distributed"
    label: str = "add mass"
    _mass: Any = field(default=None, init=False)

    def do(self, project: Project) -> Any:
        if self._mass is None:
            self._mass = project.add_mass(
                Mass(
                    ref=self.ref,
                    value=self.value,
                    name=self.name,
                    distribution_policy=self.distribution_policy,
                )
            )
        else:
            project.masses.append(self._mass)
        return self._mass

    def undo(self, project: Project) -> None:
        project.masses.remove(self._mass)


@dataclass(eq=False)
class SetAcceleration(Command):
    """Gravity or a design acceleration field on one case."""

    vector: Sequence[float] = (0.0, 0.0, -9.81)
    case: str = "default"
    coordinate_system_id: str = "global"
    label: str = "set acceleration"
    _previous: Any = field(default=None, init=False)
    _previous_system: str = field(default="global", init=False)
    _had: bool = field(default=False, init=False)

    def do(self, project: Project) -> None:
        case = project.load_case(self.case)
        self._had = case.gravity is not None
        self._previous = None if case.gravity is None else np.array(case.gravity)
        self._previous_system = case.gravity_coordinate_system_id
        case.set_acceleration(
            *(float(value) for value in self.vector),
            coordinate_system_id=self.coordinate_system_id,
        )

    def undo(self, project: Project) -> None:
        case = project.load_case(self.case)
        case.gravity = self._previous if self._had else None
        case.gravity_coordinate_system_id = self._previous_system


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


# ----------------------------------------------------------------------
# reusable model definitions
# ----------------------------------------------------------------------
@dataclass(eq=False)
class DeleteProjectRecord(Command):
    """Delete one non-topology tree record with dependency-aware undo.

    Sidecar files are deliberately retained.  Deleting a mesh, job or result
    removes its project reference and is therefore instant and undoable; a
    later project save may garbage-collect unreferenced artifacts explicitly.
    """

    kind: str
    identifier: str
    label: str = "delete project record"
    _value: Any = field(default=None, init=False)
    _auxiliary: Any = field(default=None, init=False)

    def do(self, project: Project) -> None:
        kind = str(self.kind)
        identifier = str(self.identifier)
        if kind == "mesh":
            self._value = _pop_record(project.mesh_records, identifier, "mesh")
            return
        if kind == "analysis":
            users = [
                job.name for job in project.jobs.values()
                if job.analysis_id == identifier
            ]
            if users:
                raise ProjectError(
                    f"analysis is used by job(s) {users}; delete those jobs first"
                )
            self._value = _pop_record(project.analyses, identifier, "analysis")
            return
        if kind == "job":
            job = _require_record(project.jobs, identifier, "job")
            status = str(getattr(getattr(job, "status", ""), "value", job.status))
            if status in {"queued", "running", "cancelling"}:
                raise ProjectError(
                    f"cannot delete a {status} job; cancel it and wait first"
                )
            self._value = project.jobs.pop(identifier)
            return
        if kind == "result":
            matches = [
                job for job in project.jobs.values()
                if job.result_artifact_id == identifier
            ]
            if not matches:
                raise ProjectError(f"no result with ID {identifier!r}")
            if len(matches) != 1:
                raise ProjectError(f"result ID {identifier!r} is not unique")
            self._value = matches[0]
            self._auxiliary = matches[0].result_artifact_id
            matches[0].result_artifact_id = None
            return
        if kind == "material":
            users = sorted(
                [
                    f"plate section {name!r}"
                    for name, section in project.plate_sections.items()
                    if section.material == identifier
                ]
                + [
                    f"beam section {name!r}"
                    for name, section in project.beam_sections.items()
                    if section.material == identifier
                ]
            )
            if users:
                raise ProjectError(
                    f"material {identifier!r} is used by {', '.join(users)}"
                )
            self._value = _pop_record(project.materials, identifier, "material")
            self._auxiliary = project.material_ids.pop(identifier, None)
            return
        if kind in {"plate_section", "beam_section"}:
            registry = (
                project.plate_sections
                if kind == "plate_section"
                else project.beam_sections
            )
            section = _require_record(registry, identifier, kind.replace("_", " "))
            direct = (
                [key for key, value in project.face_sections.items() if value == identifier]
                if kind == "plate_section"
                else [key for key, value in project.edge_sections.items() if value == identifier]
            )
            canonical = [
                item.name
                for item in project.section_assignments.values()
                if item.section_id == section.id
            ]
            if direct or canonical:
                targets = [str(value) for value in direct] + canonical
                raise ProjectError(
                    f"section {identifier!r} is assigned to {targets}; unassign it first"
                )
            self._value = registry.pop(identifier)
            return
        if kind == "coordinate":
            if identifier == "global":
                raise ProjectError("the reserved Global coordinate system cannot be deleted")
            users = _coordinate_system_users(project, identifier)
            if users:
                raise ProjectError(
                    f"coordinate system is used by {', '.join(users)}"
                )
            self._value = _pop_record(
                project.coordinate_systems, identifier, "coordinate system"
            )
            return
        if kind == "region":
            region = project.regions[identifier]
            users = _region_users(project, identifier)
            if users:
                raise ProjectError(f"region is used by {', '.join(users)}")
            self._value = region
            project.regions.remove(identifier, cascade=False)
            return
        raise ProjectError(f"tree item type {kind!r} is not deletable")

    def undo(self, project: Project) -> None:
        kind = str(self.kind)
        identifier = str(self.identifier)
        if kind == "mesh":
            project.mesh_records[identifier] = self._value
        elif kind == "analysis":
            project.analyses[identifier] = self._value
        elif kind == "job":
            project.jobs[identifier] = self._value
        elif kind == "result":
            self._value.result_artifact_id = self._auxiliary
        elif kind == "material":
            project.materials[identifier] = self._value
            if self._auxiliary is not None:
                project.material_ids[identifier] = self._auxiliary
        elif kind == "plate_section":
            project.plate_sections[identifier] = self._value
        elif kind == "beam_section":
            project.beam_sections[identifier] = self._value
        elif kind == "coordinate":
            project.coordinate_systems[identifier] = self._value
        elif kind == "region":
            project.regions.add(self._value)
        else:  # pragma: no cover - guarded by do
            raise RuntimeError(f"cannot undo tree item type {kind!r}")


def _require_record(registry: Mapping[str, Any], identifier: str, label: str) -> Any:
    try:
        return registry[identifier]
    except KeyError:
        raise ProjectError(f"no {label} with ID/name {identifier!r}") from None


def _pop_record(registry: dict[str, Any], identifier: str, label: str) -> Any:
    _require_record(registry, identifier, label)
    return registry.pop(identifier)


def _coordinate_system_users(project: Project, identifier: str) -> list[str]:
    users: list[str] = []
    for collection_name in ("supports", "masses"):
        for item in getattr(project, collection_name):
            if getattr(item, "coordinate_system_id", "global") == identifier:
                users.append(
                    f"{collection_name[:-1]} "
                    f"{getattr(item, 'name', getattr(item, 'id', '?'))}"
                )
    for case_name, case in project.load_cases.items():
        for collection_name in (
            "point_loads", "pressures", "line_loads", "surface_tractions"
        ):
            for item in getattr(case, collection_name):
                if getattr(item, "coordinate_system_id", "global") == identifier:
                    users.append(f"load in case {case_name!r}")
        if (
            case.gravity is not None
            and case.gravity_coordinate_system_id == identifier
        ):
            users.append(f"gravity in case {case_name!r}")
    for request in project.output_requests.values():
        if request.basis == identifier:
            users.append(f"output request {request.label!r}")
    return users


def _region_users(project: Project, identifier: str) -> list[str]:
    users = [
        f"region {value!r}" for value in project.regions.dependents(identifier)
    ]
    users.extend(
        f"section assignment {item.name!r}"
        for item in project.section_assignments.values()
        if item.region.id == identifier
    )
    for collection_name in ("supports", "masses"):
        for item in getattr(project, collection_name):
            if getattr(getattr(item, "region", None), "id", None) == identifier:
                users.append(
                    f"{collection_name[:-1]} "
                    f"{getattr(item, 'name', getattr(item, 'id', '?'))}"
                )
    for case_name, case in project.load_cases.items():
        for collection_name in (
            "point_loads", "pressures", "line_loads", "surface_tractions"
        ):
            for item in getattr(case, collection_name):
                if getattr(getattr(item, "region", None), "id", None) == identifier:
                    users.append(f"load in case {case_name!r}")
    users.extend(
        f"output request {request.label!r}"
        for request in project.output_requests.values()
        if request.region.id == identifier
    )
    return users


@dataclass(eq=False)
class AddMaterial(Command):
    """Add or replace one material while preserving its registry UUID."""

    material: MaterialSpec
    label: str = "add material"
    _captured: bool = field(default=False, init=False)
    _had_previous: bool = field(default=False, init=False)
    _previous: MaterialSpec | None = field(default=None, init=False)
    _previous_id: str | None = field(default=None, init=False)
    _material_id: str | None = field(default=None, init=False)

    def do(self, project: Project) -> MaterialSpec:
        if not self._captured:
            self._had_previous = self.material.name in project.materials
            self._previous = project.materials.get(self.material.name)
            self._previous_id = project.material_ids.get(self.material.name)
            self._captured = True
        project.add_material(self.material)
        if self._material_id is None:
            self._material_id = project.material_ids[self.material.name]
        else:
            project.material_ids[self.material.name] = self._material_id
        return self.material

    def undo(self, project: Project) -> None:
        if self._had_previous and self._previous is not None:
            project.materials[self.material.name] = self._previous
            if self._previous_id is not None:
                project.material_ids[self.material.name] = self._previous_id
            else:
                project.material_ids.pop(self.material.name, None)
        else:
            project.materials.pop(self.material.name, None)
            project.material_ids.pop(self.material.name, None)


@dataclass(eq=False)
class AddPlateSection(Command):
    """Atomically add a plate section and its optional material specification."""

    section: PlateSection
    material: MaterialSpec | None = None
    label: str = "add plate section"
    _captured: bool = field(default=False, init=False)
    _had_section: bool = field(default=False, init=False)
    _previous_section: PlateSection | None = field(default=None, init=False)
    _had_material: bool = field(default=False, init=False)
    _previous_material: MaterialSpec | None = field(default=None, init=False)
    _previous_material_id: str | None = field(default=None, init=False)
    _material_id: str | None = field(default=None, init=False)

    def do(self, project: Project) -> PlateSection:
        if self.material is not None and self.material.name != self.section.material:
            raise ProjectError("plate section and material names do not match")
        if not self._captured:
            self._had_section = self.section.name in project.plate_sections
            self._previous_section = project.plate_sections.get(self.section.name)
            if self.material is not None:
                self._had_material = self.material.name in project.materials
                self._previous_material = project.materials.get(self.material.name)
                self._previous_material_id = project.material_ids.get(self.material.name)
            self._captured = True
        if self.material is not None:
            project.add_material(self.material)
            if self._material_id is None:
                self._material_id = project.material_ids[self.material.name]
            else:
                project.material_ids[self.material.name] = self._material_id
        project.add_plate_section(
            self.section.name,
            self.section.thickness,
            self.section.material,
            id=self.section.id,
        )
        return project.plate_sections[self.section.name]

    def undo(self, project: Project) -> None:
        if self._had_section and self._previous_section is not None:
            project.plate_sections[self.section.name] = self._previous_section
        else:
            project.plate_sections.pop(self.section.name, None)
        if self.material is None:
            return
        if self._had_material and self._previous_material is not None:
            project.materials[self.material.name] = self._previous_material
            if self._previous_material_id is not None:
                project.material_ids[self.material.name] = self._previous_material_id
            else:
                project.material_ids.pop(self.material.name, None)
        else:
            project.materials.pop(self.material.name, None)
            project.material_ids.pop(self.material.name, None)


@dataclass(eq=False)
class AddRegion(Command):
    """Add one persistent reusable scope as a single undoable edit.

    The caller constructs the :class:`Region` once.  Redo inserts that same
    object again, so its UUID and Boolean dependency references never change.
    """

    region: Region
    label: str = "create region"

    def do(self, project: Project) -> Region:
        return project.regions.add(self.region)

    def undo(self, project: Project) -> None:
        project.regions.remove(self.region.id)


@dataclass(eq=False)
class AddCoordinateSystem(Command):
    """Add a validated named coordinate system without regenerating its ID."""

    system: CoordinateSystem
    label: str = "create coordinate system"

    def do(self, project: Project) -> CoordinateSystem:
        return project.add_coordinate_system(self.system)

    def undo(self, project: Project) -> None:
        if self.system.id == "global":
            raise ProjectError("the reserved Global coordinate system cannot be removed")
        project.coordinate_systems.pop(self.system.id)


@dataclass(eq=False)
class SetUnitProfile(Command):
    """Change entry/display units while keeping all stored values in SI."""

    profile: UnitProfile
    label: str = "change unit profile"
    _previous: UnitProfile | None = field(default=None, init=False)

    def do(self, project: Project) -> UnitProfile:
        self._previous = project.units
        project.units = self.profile
        return self.profile

    def undo(self, project: Project) -> None:
        if self._previous is None:  # pragma: no cover - command misuse guard
            raise RuntimeError("unit-profile command has not been run")
        project.units = self._previous


@dataclass(eq=False)
class AddMeshRecord(Command):
    """Register the mesh UUID needed by a mesh-native region.

    Normally meshing already creates this record.  Imported models can be
    scoped before their first save, so the Definitions task uses this command
    together with :class:`AddRegion` in one :class:`CompositeCommand`.
    """

    record: MeshRecord
    label: str = "register mesh"

    def do(self, project: Project) -> MeshRecord:
        return project.add_mesh_record(self.record)

    def undo(self, project: Project) -> None:
        project.mesh_records.pop(self.record.id)


@dataclass(eq=False)
class AddOutputRequest(Command):
    """Create one stable request and optionally attach it to named analyses."""

    request: OutputRequest
    analysis_ids: tuple[str, ...] = ()
    label: str = "create output request"
    _previous_analyses: dict[str, Any] = field(default_factory=dict, init=False)

    def do(self, project: Project) -> OutputRequest:
        identifiers = tuple(dict.fromkeys(str(value) for value in self.analysis_ids))
        analyses = {}
        for identifier in identifiers:
            try:
                analysis = project.analyses[identifier]
            except KeyError:
                raise ProjectError(f"unknown analysis ID {identifier!r}") from None
            problems = self.request.problems_for_analysis(analysis.type)
            if problems:
                raise ProjectError(
                    f"output request {self.request.label!r} cannot be attached "
                    f"to analysis {analysis.name!r}: {'; '.join(problems)}"
                )
            analyses[identifier] = analysis
        project.add_output_request(self.request)
        self._previous_analyses = analyses
        try:
            for identifier, analysis in analyses.items():
                project.analyses[identifier] = replace(
                    analysis,
                    output_request_ids=tuple(
                        dict.fromkeys(
                            analysis.output_request_ids + (self.request.id,)
                        )
                    ),
                )
        except BaseException:
            project.output_requests.pop(self.request.id, None)
            for identifier, analysis in analyses.items():
                project.analyses[identifier] = analysis
            raise
        return self.request

    def undo(self, project: Project) -> None:
        for identifier, analysis in self._previous_analyses.items():
            project.analyses[identifier] = analysis
        project.output_requests.pop(self.request.id, None)


@dataclass(eq=False)
class EditOutputRequest(Command):
    """Replace an immutable request while retaining its UUID and references."""

    request_id: str
    replacement: OutputRequest
    label: str = "edit output request"
    _previous: OutputRequest | None = field(default=None, init=False)

    def do(self, project: Project) -> OutputRequest:
        identifier = str(self.request_id)
        if self.replacement.id != identifier:
            raise ProjectError("editing an output request cannot change its UUID")
        try:
            previous = project.output_requests[identifier]
        except KeyError:
            raise ProjectError(f"unknown output-request ID {identifier!r}") from None
        for analysis in project.analyses.values():
            if identifier not in analysis.output_request_ids:
                continue
            problems = self.replacement.problems_for_analysis(analysis.type)
            if problems:
                raise ProjectError(
                    f"output request {self.replacement.label!r} is invalid for "
                    f"analysis {analysis.name!r}: {'; '.join(problems)}"
                )
        self._previous = previous
        return project.update_output_request(self.replacement)

    def undo(self, project: Project) -> None:
        if self._previous is None:  # pragma: no cover - command misuse guard
            raise RuntimeError("output-request edit has not been run")
        project.output_requests[self.request_id] = self._previous


@dataclass(eq=False)
class DeleteOutputRequest(Command):
    """Delete and detach one request as a single reversible edit."""

    request_id: str
    label: str = "delete output request"
    _request: OutputRequest | None = field(default=None, init=False)
    _previous_analyses: dict[str, Any] = field(default_factory=dict, init=False)

    def do(self, project: Project) -> None:
        identifier = str(self.request_id)
        self._previous_analyses = {
            analysis.id: analysis
            for analysis in project.analyses.values()
            if identifier in analysis.output_request_ids
        }
        self._request = project.remove_output_request(identifier, cascade=True)

    def undo(self, project: Project) -> None:
        if self._request is None:  # pragma: no cover - command misuse guard
            raise RuntimeError("output-request delete has not been run")
        project.add_output_request(self._request)
        for identifier, analysis in self._previous_analyses.items():
            project.analyses[identifier] = analysis
