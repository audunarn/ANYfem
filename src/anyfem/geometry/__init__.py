"""Compatibility namespace for shared geometry and mapped decomposition."""

from importlib import import_module as _import_module
from typing import TYPE_CHECKING as _TYPE_CHECKING

if _TYPE_CHECKING:
    from .curves import (
        Arc,
        ArcFrame,
        CurveShape,
        DegenerateArcError,
        Spline,
        Straight,
        arc_frame,
    )
    from .entities import Edge, EntityKind, EntityRef, Face, OrientedEdge, Vertex
    from .model import GeometryError, GeometryModel
    from .operations import (
        MappabilityReport,
        check_mappable,
        punch_circular_hole,
        split_face_at,
        split_face_between,
        strip_face,
        surface_point,
        triangle_to_quads,
    )
    from .surfaces import CoonsSurface, Cone, Cylinder, Plane, RuledSurface
    from .construction import (
        ConstructionMode,
        ConstructionResult,
        ConstructionTask,
        CoordinateConstruction,
    )
    from .snapping import (
        GeometrySnapData,
        SnapCandidate,
        SnapEngine,
        SnapKind,
        SnapPoint,
        SnapResult,
        SnapSegment,
        geometry_snap_data,
    )
    from .sketching import FaceSketchTask

__all__ = [
    'Arc',
    'ArcFrame',
    'CoonsSurface',
    'ConstructionMode',
    'ConstructionResult',
    'ConstructionTask',
    'CoordinateConstruction',
    'Cone',
    'CurveShape',
    'Cylinder',
    'DegenerateArcError',
    'Edge',
    'EntityKind',
    'EntityRef',
    'Face',
    'FaceSketchTask',
    'GeometryError',
    'GeometryModel',
    'GeometrySnapData',
    'MappabilityReport',
    'OrientedEdge',
    'Plane',
    'RuledSurface',
    'Spline',
    'SnapCandidate',
    'SnapEngine',
    'SnapKind',
    'SnapPoint',
    'SnapResult',
    'SnapSegment',
    'Straight',
    'Vertex',
    'arc_frame',
    'check_mappable',
    'geometry_snap_data',
    'punch_circular_hole',
    'split_face_at',
    'split_face_between',
    'strip_face',
    'surface_point',
    'triangle_to_quads',
]

_EXPORTS = {
    'Arc': ('.curves', 'Arc'),
    'ArcFrame': ('.curves', 'ArcFrame'),
    'CurveShape': ('.curves', 'CurveShape'),
    'DegenerateArcError': ('.curves', 'DegenerateArcError'),
    'Spline': ('.curves', 'Spline'),
    'Straight': ('.curves', 'Straight'),
    'arc_frame': ('.curves', 'arc_frame'),
    'Edge': ('.entities', 'Edge'),
    'EntityKind': ('.entities', 'EntityKind'),
    'EntityRef': ('.entities', 'EntityRef'),
    'Face': ('.entities', 'Face'),
    'OrientedEdge': ('.entities', 'OrientedEdge'),
    'Vertex': ('.entities', 'Vertex'),
    'GeometryError': ('.model', 'GeometryError'),
    'GeometryModel': ('.model', 'GeometryModel'),
    'MappabilityReport': ('.operations', 'MappabilityReport'),
    'check_mappable': ('.operations', 'check_mappable'),
    'punch_circular_hole': ('.operations', 'punch_circular_hole'),
    'split_face_at': ('.operations', 'split_face_at'),
    'split_face_between': ('.operations', 'split_face_between'),
    'strip_face': ('.operations', 'strip_face'),
    'surface_point': ('.operations', 'surface_point'),
    'triangle_to_quads': ('.operations', 'triangle_to_quads'),
    'CoonsSurface': ('.surfaces', 'CoonsSurface'),
    'Cone': ('.surfaces', 'Cone'),
    'Cylinder': ('.surfaces', 'Cylinder'),
    'Plane': ('.surfaces', 'Plane'),
    'RuledSurface': ('.surfaces', 'RuledSurface'),
    'ConstructionMode': ('.construction', 'ConstructionMode'),
    'ConstructionResult': ('.construction', 'ConstructionResult'),
    'ConstructionTask': ('.construction', 'ConstructionTask'),
    'CoordinateConstruction': ('.construction', 'CoordinateConstruction'),
    'GeometrySnapData': ('.snapping', 'GeometrySnapData'),
    'SnapCandidate': ('.snapping', 'SnapCandidate'),
    'SnapEngine': ('.snapping', 'SnapEngine'),
    'SnapKind': ('.snapping', 'SnapKind'),
    'SnapPoint': ('.snapping', 'SnapPoint'),
    'SnapResult': ('.snapping', 'SnapResult'),
    'SnapSegment': ('.snapping', 'SnapSegment'),
    'geometry_snap_data': ('.snapping', 'geometry_snap_data'),
    'FaceSketchTask': ('.sketching', 'FaceSketchTask'),
}

_SUBMODULES = ('construction', 'curves', 'entities', 'model', 'operations', 'sketching', 'snapping', 'surfaces')

def __getattr__(name):
    target = _EXPORTS.get(name)
    if target is not None:
        module, attribute = target
        value = getattr(_import_module(module, __name__), attribute)
        globals()[name] = value
        return value
    if name in _SUBMODULES:
        value = _import_module('.' + name, __name__)
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__():
    return sorted(set(globals()) | set(__all__) | set(_SUBMODULES))
