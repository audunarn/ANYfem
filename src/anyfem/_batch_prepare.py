"""Plan/apply core of structural preparation, shared by the in-process path and the thread-route child.

This module imports only the standard library and ``anygeometry`` (no ``anyfem`` package import), because the free-threaded
child interpreter loads it by file path and must not import the full application.  The code is the code that used to sit inline
in ``structural_preparation.prepare_structural_connectivity``; moving it here changes no behaviour of the route-off path.

Two phases, because the caller maps their errors differently:

* ``plan_apply_phase``  plan + apply the batch; a ``GeometryError`` here is reported as a structural preparation error;
* ``report_rows_phase`` derive the report rows from the applied geometry; errors here propagate unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping

import anygeometry
from anygeometry import ConnectionIntent, IntersectionBatchPolicy
from anygeometry.entities import EntityRef

__all__ = [
    "BatchRows",
    "BatchState",
    "face_sheet_map",
    "handle_label",
    "make_policy",
    "plan_apply_phase",
    "plan_only",
    "resolved_route",
    "report_rows_phase",
    "rows_to_json",
]


def handle_label(handle) -> str:
    return f"{handle.kind}:{handle.id}"


def face_sheet_map(geometry) -> dict[int, int]:
    return {
        geometry.face_uses[use_id].face_id: sheet.id
        for sheet in geometry.sheets.values()
        for use_id in sheet.face_use_ids
    }


@dataclass
class BatchState:
    application: Any
    pairs: set
    original_faces: tuple
    face_label: Callable[[int], str]


@dataclass(frozen=True)
class BatchRows:
    pairs: frozenset
    pair_relations: Mapping[tuple, tuple]
    relations: tuple
    reused: bool


def _check(cancellation_check, phase: str) -> None:
    if cancellation_check is not None:
        cancellation_check(phase)


# The owner entry points are looked up on the ``anygeometry`` module at call time (as the pre-route code did with a
# function-local import): tests and diagnostics that replace ``anygeometry.plan_intersections`` / ``apply_intersections`` must see the call.


def make_policy(cancellation_check=None) -> IntersectionBatchPolicy:
    """The ONE policy of a preparation call (parent: with its cancellation check; thread-route child: without)."""
    def cancelled():
        _check(cancellation_check, "structural intersection batch")
        return False
    return IntersectionBatchPolicy(ConnectionIntent.CONNECT,
        cancellation_check=None if cancellation_check is None else cancelled)


def resolved_route() -> list:
    """The planning mode and seam policy the OWNER resolves for this process (environment included), as strings."""
    from anygeometry.batch_intersections import resolve_planning_mode, resolve_seam_policy
    policy = make_policy(None)
    return [str(resolve_planning_mode(policy)), str(resolve_seam_policy(policy))]


def plan_only(geometry, operands):
    """Plan stage alone (what the thread-route child runs); the parent adopts and applies the plan."""
    return anygeometry.plan_intersections(geometry, operands, policy=make_policy(None))


def plan_apply_phase(geometry, operands, *, cancellation_check=None, record_plan=None, plan_source=None) -> BatchState:
    """``plan_source(policy)`` may supply an adopted plan instead of planning here; None (or a None result) plans serially."""
    policy = make_policy(cancellation_check)
    # Report physical design-owner connections rather than one event per
    # cylindrical panel. Topology still retains every exact panel relation.
    sheets = face_sheet_map(geometry)
    original_faces = tuple(sorted(geometry.faces))
    representatives = {}
    for face in sorted(geometry.faces):
        key = ("sheet", sheets[face]) if face in sheets else ("face", face)
        representatives.setdefault(key, face)
    def face_label(face):
        key = ("sheet", sheets[face]) if face in sheets else ("face", face)
        return f"face:{representatives[key]}"
    plan = plan_source(policy) if plan_source is not None else None
    if plan is None:
        plan = anygeometry.plan_intersections(geometry, operands, policy=policy)
    if record_plan is not None:
        record_plan(plan)
    pairs = set()
    for arrangement in plan.arrangements:
        for path in arrangement.paths:
            for index, first in enumerate(path.owners):
                for second in path.owners[index+1:]:
                    labels = (face_label(first), face_label(second))
                    if labels[0] != labels[1]:
                        pairs.add(tuple(sorted(labels)))
            for member in path.member_ids:
                pairs.add((face_label(arrangement.face_id), f"member:{member}"))
    for contact in plan.contacts:
        members = sorted({member for member, _parameter in contact.member_parameters})
        if contact.face_id is not None:
            pairs.update((face_label(contact.face_id), f"member:{member}") for member in members)
        for index, first in enumerate(members):
            pairs.update((f"member:{first}", f"member:{second}") for second in members[index+1:])
    application = anygeometry.apply_intersections(geometry, plan, policy=policy)
    return BatchState(application, pairs, original_faces, face_label)


def report_rows_phase(geometry, state: BatchState) -> BatchRows:
    application, pairs, original_faces, face_label = (
        state.application, state.pairs, state.original_faces, state.face_label)
    # Some certified owners publish a complete application without legacy
    # ArrangementPath records. Report only owners that actually share one of
    # its issued joint edges, using explicit face replacement lineage.
    descendant_owners = {}
    for parent in original_faces:
        descendants = tuple(geometry.resolve_ref(EntityRef('face', parent)))
        if not descendants and parent in geometry.faces:
            descendants = (EntityRef('face', parent),)
        for descendant in descendants:
            if descendant.kind == 'face' and descendant.id in geometry.faces:
                descendant_owners.setdefault(descendant.id, set()).add(parent)
    pair_relations = {}
    for edge in application.joint_edges:
        incident = geometry.faces_using_edge(edge.id)
        labels = sorted({face_label(parent) for face in incident
                         for parent in descendant_owners.get(face, ())})
        # This combination is local to one actual shared edge, not the global
        # operand set. Faces of the same original Sheet share one label.
        for index, first in enumerate(labels):
            for second in labels[index+1:]:
                pair = (first, second)
                pairs.add(pair)
                pair_relations.setdefault(pair, []).append(handle_label(edge))
    relations = tuple(handle_label(handle) for handle in application.joint_edges)
    return BatchRows(frozenset(pairs), pair_relations, relations, application.reused)


def rows_to_json(rows: BatchRows) -> dict:
    return {
        "pairs": [list(pair) for pair in sorted(rows.pairs)],
        "pair_relations": [[first, second, list(labels)]
                           for (first, second), labels in sorted(rows.pair_relations.items())],
        "relations": list(rows.relations),
        "reused": bool(rows.reused),
    }


def source_digest(anygeometry_dir, anyfem_files) -> str:
    """sha256 over the anygeometry package sources and the named ANYfem files (relative names + bytes, sorted)."""
    import hashlib
    from pathlib import Path
    digest = hashlib.sha256()
    base = Path(anygeometry_dir)
    for path in sorted(base.rglob("*.py")):
        digest.update(path.relative_to(base).as_posix().encode() + b"\0")
        digest.update(path.read_bytes() + b"\0")
    for path in sorted(Path(item) for item in anyfem_files):
        digest.update(b"anyfem/" + path.name.encode() + b"\0")
        digest.update(path.read_bytes() + b"\0")
    return digest.hexdigest()


def loaded_modules(package_dir) -> list:
    """Relative paths of the ``.py`` files of every loaded ``anygeometry*`` module (child side).  A module that was not
    loaded from a plain ``.py`` file under ``package_dir`` (extension, bytecode only, other tree) is an error."""
    import sys
    from pathlib import Path
    base = Path(package_dir).resolve()
    found = set()
    for name, module in list(sys.modules.items()):
        if name == "anygeometry" or name.startswith("anygeometry."):
            path = getattr(module, "__file__", None)
            if not path or not str(path).endswith(".py"):
                raise ValueError("module %s is not loaded from a .py file" % name)
            found.add(Path(path).resolve().relative_to(base).as_posix())
    return sorted(found)


def files_digest(package_dir, relative_paths) -> str:
    """sha256 over (relative name, bytes) of exactly the named ``.py`` files under ``package_dir``."""
    import hashlib
    from pathlib import Path, PurePosixPath
    base = Path(package_dir).resolve()
    digest = hashlib.sha256()
    for name in relative_paths:
        pure = PurePosixPath(name)
        if pure.is_absolute() or ".." in pure.parts or pure.suffix != ".py":
            raise ValueError("unacceptable module path %r" % (name,))
        digest.update(name.encode() + b"\0")
        digest.update((base / pure).read_bytes() + b"\0")
    return digest.hexdigest()


SMOKE_DIGEST = "161bbdbb399dcdd190eba88964f91a06670f04b0468aea9e7687f0fed04b4b4f"  # frozen: identical on CPython 3.14.2 and 3.14.10t


def smoke_digest() -> str:
    """Tiny exact plan: two crossing plates, plan + apply, sha256 of the prepared document (fixed model id)."""
    import hashlib
    import json
    from anygeometry import GeometryModel, to_dict
    model = GeometryModel(model_id="12345678-1234-5678-1234-567812345678")
    first = model.add_plate(model.add_points(((0, 0, 0), (4, 0, 0), (4, 4, 0), (0, 4, 0))))
    second = model.add_plate(model.add_points(((2, 0, -1), (2, 4, -1), (2, 4, 1), (2, 0, 1))))
    state = plan_apply_phase(model, (model.handle("face", first), model.handle("face", second)))
    rows = report_rows_phase(model, state)
    payload = json.dumps([to_dict(model), rows_to_json(rows)], sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()



