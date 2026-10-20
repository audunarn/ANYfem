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


def _polygon_identity(polygons):
    """Hash every polygon byte without serializing each tiny array separately.

    Blocks are transient and bounded to 8 MiB (a larger single polygon is
    hashed directly). Array subclasses stay on the conservative refusal path.
    This is content identity, never an identity-only cache of mutable arrays.
    """
    if type(polygons) not in (list, tuple):
        raise TypeError("unsupported polygon sequence")
    blocks = []
    start = 0
    while start < len(polygons):
        first = polygons[start]
        if type(first) is not np.ndarray or first.dtype.hasobject:
            raise TypeError("unsupported polygon array")
        dtype, shape = first.dtype, first.shape
        if (dtype.kind not in "biuf" or dtype.fields is not None
                or dtype.subdtype is not None or dtype.itemsize > 8):
            # Structured and extended-precision arrays may contain padding
            # that concatenation need not preserve. Keep the original
            # per-array reducer and its complete byte identity for this field.
            return polygons
        limit = max(1, min(4096, (8 * 1024 * 1024) // max(1, first.nbytes)))
        stop = start + 1
        while stop < min(len(polygons), start + limit):
            item = polygons[stop]
            if (type(item) is not np.ndarray or item.dtype != dtype
                    or item.shape != shape):
                break
            stop += 1
        if stop == start + 1 or not shape:
            # Scalar arrays cannot concatenate along axis zero.
            data = np.ascontiguousarray(first)
            stop = start + 1
        else:
            # Concatenate may otherwise preserve Fortran layout, which cannot
            # be exposed as one byte memoryview. Allocate the bounded block
            # explicitly in C order, avoiding a second normalization copy.
            data = np.empty((shape[0] * (stop - start), *shape[1:]), dtype=dtype)
            np.concatenate(polygons[start:stop], axis=0, out=data)
        raw = memoryview(data).cast("B") if data.size else b""
        blocks.append((dtype.descr, shape, stop - start,
                       hashlib.sha256(raw).digest()))
        start = stop
    return ("polygon-array-blocks", type(polygons).__name__, tuple(blocks))


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
            payload = tuple(
                _polygon_identity(value.polygons)
                if cls is FacePatch and field == "polygons"
                else getattr(value, field)
                for field in attributes
            )
            return tuple, ((name, payload),)
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
