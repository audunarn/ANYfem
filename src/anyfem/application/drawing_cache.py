"""Per-call memo for pure drawing computations (thickness sides, plate contours, curved strips).

A drawing call (one view, one takeoff export, one publish) recomputes the same
Sheet contours and thickness sides many times: every plate sheet view resolves
the sides of every Part and rebuilds each neighbour's contour.  Inside one
:func:`drawing_scope` such results are computed once.

The memo lives only while the outermost scope of the current thread is open,
so nothing is reused across calls: a workbench edit (geometry, thickness side,
datum, extent) between two drawing calls always recomputes.  Inside a scope
the drawing code only reads the project.  Keys hold the identity of the
geometry/project objects (kept alive by the memo, so an ID cannot be reused)
plus every argument the result depends on.  Cached arrays are made read-only,
and a cached refusal is re-raised as a copy with its type, message and cause.
"""
from __future__ import annotations

import copy
import threading
from contextlib import contextmanager
from dataclasses import fields, is_dataclass
from functools import wraps

import numpy as np

_state = threading.local()


@contextmanager
def drawing_scope():
    """Open a memo for this thread unless one is already open; clear it on exit of the outermost scope."""
    if getattr(_state, "memo", None) is not None:
        yield
        return
    _state.memo = {}
    try:
        yield
    finally:
        _state.memo = None


def scoped(function):
    """Run ``function`` inside a :func:`drawing_scope`."""
    @wraps(function)
    def wrapper(*args, **kwargs):
        with drawing_scope():
            return function(*args, **kwargs)
    return wrapper


def _freeze(value):
    if isinstance(value, np.ndarray):
        value.flags.writeable = False
    elif isinstance(value, (tuple, list)):
        for item in value:
            _freeze(item)
    elif is_dataclass(value) and not isinstance(value, type):
        for item in fields(value):
            _freeze(getattr(value, item.name))
    return value


def memo(kind, key, compute, *, owners=(), freeze=True):
    """Return ``compute()``, computed once per ``(kind, owner identities, key)`` in the open scope.

    Without an open scope this is ``compute()``.  ``owners`` are the objects
    the result depends on by identity (geometry, project); they are retained
    with the entry.  ``key`` must capture every other input.
    """
    store = getattr(_state, "memo", None)
    if store is None:
        return compute()
    full = (kind, tuple(id(owner) for owner in owners), key)
    hit = store.get(full)
    if hit is None:
        try:
            value = compute()
        except (ValueError, ImportError) as error:
            store[full] = (owners, False, error)
            raise
        store[full] = (owners, True, _freeze(value) if freeze else value)
        return value
    _, ok, value = hit
    if not ok:
        raise _replay(value)
    return value


def _replay(error):
    """A copy of a cached refusal with its type, message, cause, context and traceback."""
    try:
        again = copy.copy(error)
    except Exception:  # an exception type that cannot be copied is raised as the same instance
        return error
    again.__cause__ = error.__cause__
    again.__context__ = error.__context__
    again.__suppress_context__ = error.__suppress_context__
    return again.with_traceback(error.__traceback__)


def thawed(snapshot):
    """``snapshot.thaw()``, once per snapshot inside the open scope.

    A publish exports the takeoff and then draws the plate sheets from the same
    snapshot; sharing one thawed project lets the sheets reuse the export's
    contours and sides.  Apart from one refresh the drawing code only reads the
    project: ``resolve_section_assignments(strict=True)`` in the plate profiles
    rewrites the section compatibility maps, either refusing (which ends the
    publish) or writing the values a freshly loaded project already holds,
    since loading resolves them.
    """
    return memo("thaw", (), snapshot.thaw, owners=(snapshot,), freeze=False)
