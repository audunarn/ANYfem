"""ANYfem-owned extensions to ANYgeometry's public feature registry.

Neutral modelling features remain owned by ANYgeometry.  Consumer-specific
mesh preparation intent, such as the mapped butterfly around a circular
opening, is registered here so it can participate in the same detached,
atomic regeneration contract without adding an ANYfem dependency to
ANYgeometry.
"""

from __future__ import annotations

from dataclasses import replace

from anygeometry.errors import GeometryError
from anygeometry.features import (
    FeatureRegistry,
    FeatureTopologyRole,
    builtin_feature_registry,
)
from anymesher.decomposition import punch_circular_hole


BUTTERFLY_HOLE_KIND = "anyfem.mesh.butterfly_hole"
PLATE_CUT_KIND = "anyfem.plate_cut"


def restore_plate_cut_executor_state(geometry):
    """Recognize verified v1 consumer features after the neutral file loader.

    The neutral registry correctly freezes unavailable ANYfem executors. In an
    ANYfem command stage this executor is available again. Keep the exact saved
    checksum and outputs; an invalid materialization is never re-signed.
    """
    snapshot = geometry.features.snapshot()
    records = list(snapshot["records"])
    changed = False
    for index, record in enumerate(records):
        if record.kind != PLATE_CUT_KIND:
            continue
        if record.kind_version != 1:
            raise GeometryError("Unsupported plate-cut feature version; a compatible executor is required.")
        if record.state == "invalid":
            raise GeometryError(record.diagnostic or "Plate-cut feature materialization is invalid; repair is required.")
        if record.state != "frozen":
            continue
        diagnostic = geometry.features.validate_materialization(record, geometry)
        if diagnostic:
            raise GeometryError(diagnostic)
        records[index] = replace(record, state="ok", diagnostic=None)
        changed = True
    if changed:
        snapshot["records"] = records
        geometry.features.restore(snapshot)


def _plate_cut(geometry, feature, inputs):
    from .plate_cut import cut_plate

    if feature.kind_version != 1:
        raise GeometryError("Unsupported plate-cut feature version; a compatible executor is required.")
    faces = tuple(inputs.get("face", ()))
    cutters = tuple(inputs.get("cutter", ()))
    if len(faces) != 1 or faces[0].kind != "face":
        raise GeometryError("Cut target changed topology; select one plate face again.")
    if cutters and (len(cutters) != 1 or cutters[0].kind != "face"):
        raise GeometryError("Cutting plate changed topology; select one cutter face again.")
    working, result = cut_plate(
        geometry, faces[0].id,
        cutter_face_id=cutters[0].id if cutters else None,
        origin=feature.parameters.get("origin"),
        normal=feature.parameters.get("normal"),
    )
    geometry.restore_topology(working.topology_snapshot())
    return {
        **{f"target/{i}": geometry.entity_ref("face", value)
           for i, value in enumerate(result.target_faces)},
        **{f"cutter/{i}": geometry.entity_ref("face", value)
           for i, value in enumerate(result.cutter_faces)},
        **{f"trace/{i}": geometry.entity_ref("edge", value)
           for i, value in enumerate(result.trace_edges)},
    }


def _one_face(inputs):
    values = tuple(inputs.get("face", ()))
    if len(values) != 1 or values[0].kind != "face":
        raise GeometryError("a butterfly-hole feature needs one plate face")
    return values[0]


def _butterfly_hole(geometry, feature, inputs):
    face = _one_face(inputs)
    faces, arcs = punch_circular_hole(
        geometry,
        face.id,
        feature.parameters["centre"],
        float(feature.parameters["radius"]),
    )
    return {
        **{
            f"face/{index}": geometry.entity_ref("face", identifier)
            for index, identifier in enumerate(faces)
        },
        **{
            f"boundary/{index}": geometry.entity_ref("edge", identifier)
            for index, identifier in enumerate(arcs)
        },
    }


def anyfem_feature_registry() -> FeatureRegistry:
    """Return neutral executors plus ANYfem's versioned consumer features."""

    registry = builtin_feature_registry()
    registry.register(
        PLATE_CUT_KIND, _plate_cut, replay_mode="mutating",
        topology_role=FeatureTopologyRole.MODIFIER,
    )
    registry.register(
        BUTTERFLY_HOLE_KIND,
        _butterfly_hole,
        replay_mode="mutating",
        topology_role=FeatureTopologyRole.MODIFIER,
    )
    return registry


__all__ = ["BUTTERFLY_HOLE_KIND", "PLATE_CUT_KIND", "anyfem_feature_registry"]
