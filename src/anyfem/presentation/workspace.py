"""Persisted, engineering-neutral presentation state and deterministic sampling."""
from __future__ import annotations
from copy import deepcopy
import json
import math
import numpy as np


def validate_presentation(value):
    if not isinstance(value, dict):
        raise ValueError('presentation must be an object')
    if not value:
        return {}
    if value.get('version') != 1:
        raise ValueError('unsupported presentation metadata version')
    if set(value) - {'version', 'views', 'annotations', 'workspace'}:
        raise ValueError('unknown presentation metadata member')
    for key in ('views', 'workspace'):
        if key in value and not isinstance(value[key], dict):
            raise ValueError(f'presentation.{key} must be an object')
    if not isinstance(value.get('annotations', []), list):
        raise ValueError('presentation.annotations must be a list')
    # JSON boundary refuses non-finite values, mutable runtime objects and cycles.
    return json.loads(json.dumps(value, allow_nan=False))


def store_presentation(session, change, label='Presentation settings'):
    """Use document transactions while preserving model/mesh/job identities."""
    with session.transaction(label, solver_affecting=False):
        state=deepcopy(session.project.presentation) or {'version': 1}
        change(state)
        session.project.presentation=validate_presentation(state)


def stable_sample_ids(ids, maximum=2000):
    if isinstance(maximum, bool) or not 1 <= int(maximum) <= 2000:
        raise ValueError('glyph sample count must be between 1 and 2000')
    ids=sorted(set(int(i) for i in ids))
    if len(ids) <= maximum:
        return tuple(ids)
    return tuple(ids[i] for i in np.linspace(0, len(ids)-1, int(maximum), dtype=int))


def annotation_stale(record, document_id, mesh_identity):
    return (not mesh_identity or not record.get('mesh_identity') or record.get('document_id') != document_id or record.get('mesh_identity') != mesh_identity)


def section_box_values(center, half_extents, orientation=(0,0,0,1)):
    c=np.asarray(center,dtype=float); h=np.asarray(half_extents,dtype=float);q=np.asarray(orientation,dtype=float)
    if c.shape!=(3,) or h.shape!=(3,) or q.shape!=(4,) or not all(np.isfinite(v).all() for v in (c,h,q)):
        raise ValueError('section box needs finite center xyz, positive half-extents xyz and quaternion xyzw')
    if np.any(h<=0) or np.linalg.norm(q)<=1e-12:
        raise ValueError('section box half-extents and quaternion norm must be positive')
    return c.tolist(),h.tolist(),(q/np.linalg.norm(q)).tolist()


def camera_record(canvas):
    state=canvas.export_view_state()
    def point(v):return [float(v.x),float(v.y),float(v.z)]
    return dict(position=point(state.camera_position),target=point(state.camera_target),
                up=point(state.camera_world_up),fov=state.fov,near=state.near,far=state.far)


def apply_camera(canvas, record):
    from dataclasses import replace
    from any3dview import Point3D
    state=canvas.export_view_state()
    canvas.apply_view_state(replace(state,camera_position=Point3D(*record['position']),
        camera_target=Point3D(*record['target']),camera_world_up=Point3D(*record['up']),
        fov=float(record['fov']),near=float(record['near']),far=float(record['far'])))
