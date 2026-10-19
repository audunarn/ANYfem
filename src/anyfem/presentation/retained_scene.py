"""Conservative reuse of unchanged scene submissions through public viewer APIs.

Coordinates remain mutable at the scene boundary, so identity alone is unsafe.
Hash all displayed payloads rather than retaining another copy of their arrays.
An invisible, empty mesh handle witnesses the lifetime of the canvas contents:
the viewer removes it on clear, including clears performed outside this adapter.
"""
import hashlib
import io
import pickle
import struct
from dataclasses import fields

import numpy as np

from anygeometry.entities import EntityRef
from ..selection import MeshEntityRef
from .scene import Arrow, FacePatch, PointMarker, Polyline, Sphere
from .visualization import VisualizationStyle


_IDENTITY_FIELDS = {
    cls: (cls.__module__ + "." + cls.__qualname__, tuple(field.name for field in fields(cls)))
    for cls in (EntityRef, MeshEntityRef, Arrow, FacePatch, PointMarker,
                Polyline, Sphere, VisualizationStyle)
}
_NUMPY_SCALARS = frozenset(np.sctypeDict.values())


class _IdentityPickler(pickle.Pickler):
    """Serialize known draw data, never trust an external object's reducer."""

    def reducer_override(self, value):
        cls = type(value)
        if cls is np.ndarray:
            if value.dtype.hasobject:
                raise TypeError("object arrays cannot identify retained scene contents")
            array = np.ascontiguousarray(value)
            return tuple, (("array", array.dtype.descr, array.shape,
                            pickle.PickleBuffer(array)),)
        identity = _IDENTITY_FIELDS.get(cls)
        if identity is not None:
            name, attributes = identity
            return tuple, ((name, tuple(getattr(value, field) for field in attributes)),)
        if cls in _NUMPY_SCALARS and not value.dtype.hasobject:
            return tuple, (("scalar", value.dtype.descr, value.tobytes()),)
        if value is tuple:
            # The sole reconstruction callable emitted above; never executed.
            return NotImplemented
        raise TypeError(f"unsupported retained scene identity: {cls.__name__}")


def scene_key(viewport, scene):
    # Construction callbacks and formatters may change independently of scene
    # data. Rebuild those overlays normally. GPU result fields have their own
    # retained scalar/appearance update contract and must not enter this cache.
    if (scene.result_field is not None or viewport.construction_active
            or getattr(viewport, "plate_draft", None) is not None
            or not hasattr(viewport.canvas, "preselected_key")
            or not callable(getattr(viewport.canvas, "add_mesh_arrays", None))):
        return None
    digest = hashlib.sha256()
    buffers = []
    try:
        # C serialization encodes container lengths, types, ownership and array
        # dtype/shape without a Python call for every scalar. Protocol 5 exposes
        # contiguous NumPy storage as buffers, avoiding a second geometry copy.
        # This is an in-memory identity only: never persist or unpickle it.
        stream = io.BytesIO()
        _IdentityPickler(stream, protocol=5, buffer_callback=buffers.append).dump(
            (scene.faces, scene.lines, scene.points, scene.arrows,
             scene.spheres, scene.legend, viewport.visualization,
             viewport._commercial_selection),
        )
        metadata = stream.getbuffer()
    except (TypeError, AttributeError, pickle.PicklingError):
        # Third-party payloads remain drawable through the uncached route.
        return None
    digest.update(struct.pack("!Q", len(metadata)))
    digest.update(metadata)
    for buffer in buffers:
        data = buffer.raw()
        digest.update(struct.pack("!Q", data.nbytes))
        digest.update(data)
    return digest.digest()


def lifetime_guard(canvas):
    from any3dview import MeshArrays

    handle = canvas.add_mesh_arrays(
        MeshArrays(np.empty((0, 3)), np.empty((0, 3), dtype=np.uint32)),
        pickable=False,
    )
    if handle is None or not callable(getattr(handle, "set_visible", None)):
        return None
    handle.set_visible(False)
    return handle
