"""Regional (per independent plate component) mesh update for ANYfem.

Three entry points, used by :mod:`anyfem.mesh_jobs` and the workflow:

``capture_regional_baseline(project, settings, mesh)``
    After a *full* generation and *after publication numbering*, record what a later
    update must prove unchanged: per independent component the face/edge/vertex
    ids, a digest of the **full authoritative ANYgeometry definition** of those
    entities (``anygeometry.to_dict`` records: surfaces, curves, loops, corners,
    vertices -- so same-bounds edits are detected, nothing is sampled), its local
    settings, and an exact binding of the component's mesh: numeric node ids and
    coordinates, element ids and connectivity, and the face/edge/vertex
    associations.  Global settings/model dependencies and the build pins
    (``regional_identity.build_identity``) are recorded too.  Returns a
    JSON-serialisable ``dict`` or ``None`` for a *known* refusal (ineligible
    model/mesh/metadata); programming errors propagate.

``plan_regional_update(project, settings, baseline)``
    Compare the current project with the baseline and classify every baseline
    component as retained / regenerated / removed, and every unclaimed whole new
    plate as added.  Independence is proven by the ANYgeometry owner partition at the
    baseline's separation.  A removal needs the old ids to be exactly absent from the
    current certified geometry; an addition needs fresh entity ids and its own
    certified component.  Anything that touches a potential contact, overlaps or
    reuses an identity, merges or splits components, leaves a dangling assignment or
    changes a global dependency raises :class:`RegionalUpdateUnsupported`.  A plate
    added or removed *alone* is assembled for real through the owner's explicit
    ``added_owners``/``removed_owners`` on :class:`CrossRevisionAuthority` (absence
    receipts bound to the source geometry of each side); a removal next to an
    addition over the same region is refused as ambiguous.  There is no global
    regeneration.

``generate_regional_update(project, settings, baseline, old_mesh, ...)``
    Mesh *only* the changed components (one owner call) and join them to the old
    mesh with ``anymesher.regional_update.assemble_regional_replacement`` under a
    :class:`CrossRevisionAuthority` assembled from the true source model/revision
    of the old mesh and of the replacement.  Receipt digests are computed from the
    definitions and mesh bindings above; nothing is stamped onto the replacement.
    An unchanged project is a managed no-op (the old mesh is returned as it is).

Scope: mapped strategy, or the Automatic strategy when the full generation
demonstrably selected the mapped route for every face; linear/quadratic plates on
four-sided hole-free faces; no beams/members/couplings/junctions; pairwise
independent components.  Interior patches are a separate integration: see
:data:`PATCH_SEAM` / :func:`_generate_component`.
"""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass, field, replace
from typing import Any, Callable, Mapping

import numpy as np

__all__ = [
    "RegionalPlan",
    "RegionalUpdateError",
    "RegionalUpdateResult",
    "RegionalUpdateUnsupported",
    "capture_regional_baseline",
    "explain_regional_ineligibility",
    "generate_regional_update",
    "plan_regional_update",
]

SCHEMA = "anyfem.regional-baseline"
# 3: per-component effective plate/edge section assignments live in the local
# fingerprint (they are no longer part of the global ``plate_model``).
VERSION = 3

# Seam for a separate integration (planar patches with holes): a callable
# ``(project, settings, record) -> anymesher Mesh`` keyed by a component kind may be
# registered here.  This slice only ever uses the mapped producer.
PATCH_SEAM: dict[str, Callable[[Any, Any, Mapping[str, Any]], Any]] = {}


class RegionalUpdateError(RuntimeError):
    """A regional update failed (inconsistent inputs or an owner gate refused)."""


class RegionalUpdateUnsupported(RegionalUpdateError):
    """The requested update is outside the supported regional scope."""


class _Ineligible(Exception):
    pass


def _refusals() -> tuple[type[BaseException], ...]:
    """The known qualification refusals; anything else is a programming error."""

    from anygeometry.errors import GeometryError
    from anymesher.errors import MeshError

    return (_Ineligible, RegionalUpdateUnsupported, GeometryError, MeshError)


# --------------------------------------------------------------------- helpers


def _hex(values: Any) -> list[str]:
    return [float(v).hex() for v in np.asarray(values, dtype=float).ravel()]


def _ints(values: Any) -> list[int]:
    return [int(v) for v in values]


def _digest(value: Any) -> str:
    text = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _jsonable(value: Any) -> Any:
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return float(value)
    if isinstance(value, (set, frozenset, tuple)):
        return list(value)
    return str(value)


def _plain(value: Any) -> Any:
    return json.loads(json.dumps(value, default=_jsonable))


def _check_fn(cancellation_check: Callable[[str], Any] | None) -> Callable[[str], None]:
    def check(stage: str) -> None:
        if cancellation_check is not None:
            cancellation_check(stage)

    return check


def _strategy(project: Any, settings: Any) -> str:
    token = settings.strategy
    if token is None:
        native = getattr(project, "native_mesh_settings", None)
        backend = "auto" if native is None else getattr(native.backend, "value", native.backend)
        token = {"automatic": "auto"}.get(str(backend), str(backend))
    return str(token)


def _pins() -> dict[str, str]:
    from .regional_identity import build_identity

    pins = {"baseline_schema": f"{SCHEMA}/{VERSION}"}
    for name in ("anyfem", "anymesher", "anygeometry"):
        try:
            pins[name] = build_identity(name)
        except (ImportError, ValueError) as error:
            raise _Ineligible(f"cannot identify the {name} build: {error}") from error
    return pins


def _document(geometry: Any) -> dict[str, Any]:
    """The owner's complete, validated geometry document, indexed by entity id."""

    from anygeometry import to_dict

    raw = to_dict(geometry)
    return {
        "raw": raw,
        "faces": {int(item["id"]): item for item in raw["faces"]},
        "edges": {int(item["id"]): item for item in raw["edges"]},
        "vertices": {int(item["id"]): item for item in raw["vertices"]},
        "frame": _digest({"coordinates": raw["coordinates"], "tolerance": raw["tolerance"]}),
    }


def _fixed_meshing_inputs(meshing):
    """Keep model meshing policies outside the explicit size and edge inputs."""
    fixed = dict(meshing)
    for key in ("target_size", "seeding_overrides"):
        fixed.pop(key, None)
    if fixed.get("native") is not None:
        # The Workbench stores the current global target here as well.
        # The per-control target sizes inside 'controls' remain model inputs.
        fixed["native"] = dict(fixed["native"])
        fixed["native"].pop("target_size", None)
    return fixed


def _surface_model_inputs(project):
    from .document import _model_payload
    from .io.project_file import project_to_dict

    document = project_to_dict(project)
    model = _model_payload(document)
    # Manual mesh regions are resolved against the producing artifact during
    # atomic publication. A retained pressure face changes its mesh UUID there.
    model["regions"] = [region for region in model.get("regions", ()) if region["domain"] != "mesh"]
    for key in ("generator_properties", "mesh_size_properties"):
        model[key] = document[key]
    return model


def _assignment_maps(project: Any) -> dict[str, dict[int, str | None]]:
    """The effective face/edge section assignments (canonical records folded in)."""

    resolve = getattr(project, "resolve_section_assignments", None)
    if resolve is not None:
        resolve(strict=False)
    return {
        "faces": {int(k): (None if v is None else str(v)) for k, v in project.face_sections.items()},
        "edges": {int(k): (None if v is None else str(v)) for k, v in project.edge_sections.items()},
    }


def _assignment_closure(assignments: Mapping[str, Mapping[int, Any]],
                        components: list[Mapping[str, Any]]) -> None:
    """Every effective assignment must target an entity of exactly one component."""

    faces = {int(f) for c in components for f in c["faces"]}
    edges = {int(e) for c in components for e in c["edges"]}
    dangling_faces = sorted(set(assignments["faces"]) - faces)
    dangling_edges = sorted(set(assignments["edges"]) - edges)
    if dangling_faces or dangling_edges:
        raise _Ineligible(
            f"section assignments reference entities outside the plate components "
            f"(faces {dangling_faces[:6]}, edges {dangling_edges[:6]})")


def _global_state(project: Any, settings: Any, strategy: str, document: Mapping[str, Any]) -> dict[str, Any]:
    """Dependencies shared by every component; any change refuses a regional update."""

    from .document import canonical_hash
    from .io.project_file import project_to_dict

    shared = replace(settings, overrides=(), regional=None, execution_policy=None)
    native = getattr(project, "native_mesh_settings", None)
    native_payload = None
    if native is not None:
        payload = dict(native.to_dict())
        native_payload = {key: payload[key] for key in ("controls", "parameters") if key in payload}
    # True globals only: material/section *definitions*.  The per-face/edge
    # assignments are local to a component (see ``_component_record``).
    model = {
        str(key): value for key, value in project_to_dict(project).items()
        if str(key) not in ("face_sections", "edge_sections")
        and any(token in str(key).lower() for token in ("section", "material", "plate"))
    }
    return {
        "strategy": strategy,
        "document_id": project.document_id,
        "target_size": float(settings.target_size).hex(),
        "element_order": str(settings.element_order),
        "settings": shared.input_hash,
        "native_settings": _digest(native_payload),
        "plate_model": canonical_hash(model),
        "geometry_frame": document["frame"],
    }


def _geometry_gate(geometry: Any) -> None:
    from anygeometry import CoonsSurface, Plane, Straight

    for name in ("members", "attachments", "junctions"):
        table = getattr(geometry, name, None)
        if table:
            raise _Ineligible(f"geometry has {name}; only plain plates are supported")
    if not len(geometry.faces):
        raise _Ineligible("geometry has no faces")
    for face_id, face in geometry.faces.items():
        if len(face.corners) != 4:
            raise _Ineligible(f"face {face_id} is not four-sided")
        if getattr(face, "holes", ()):
            raise _Ineligible(f"face {face_id} has holes (planar patch route not enabled)")
        if (len(face.loop) != 4 or face.parameterization is not None
                or not (type(face.surface) is Plane or
                        type(face.surface) is CoonsSurface and face.surface.bottom is None)
                or any(type(geometry.edges[use.edge].curve) is not Straight for use in face.loop)):
            raise _Ineligible(f"face {face_id} is outside the straight-sided plate route")
        xyz = np.array([geometry.vertex_position(geometry.oriented_start_vertex(use)) for use in face.loop])
        extent = float(np.linalg.norm(np.ptp(xyz, axis=0)))
        normal = np.cross(xyz[1] - xyz[0], xyz[2] - xyz[0])
        norm = np.linalg.norm(normal)
        if (norm <= geometry.tolerance.effective_area(extent)
                or abs(np.dot(xyz[3] - xyz[0], normal / norm)) > geometry.tolerance.effective_length(extent)):
            raise _Ineligible(f"face {face_id} does not define a planar plate")


def _auto_mappable(geometry: Any, faces: Any) -> bool:
    try:
        from anymesher.hybrid import _mappable
    except ImportError:
        return False
    return all(bool(_mappable(geometry, int(face))) for face in faces)


def _component_record(geometry: Any, document: Mapping[str, Any], faces: list[int],
                      settings: Any, assignments: Mapping[str, Mapping[int, Any]] | None = None,
                      *, sheets_in_scope=None
                      ) -> dict[str, Any]:
    face_set = set(faces)
    order = [int(faces[0])] if len(faces) == 1 else [int(f) for f in geometry.faces if int(f) in face_set]
    edges: set[int] = set()
    vertices: set[int] = set()
    for face_id in faces:
        face = geometry.faces[face_id]
        for loop in (face.loop, *getattr(face, "holes", ())):
            edges.update(int(use.edge) for use in loop)
        # Face corners are offsets into the oriented loop, whose endpoints are
        # collected below. They are already bound by the serialized face.
    points = []
    parameters = np.linspace(0.0, 1.0, 9)
    for edge_id in sorted(edges):
        edge = geometry.edges[edge_id]
        curve = edge.curve
        controls = []
        if getattr(curve, "via_vertex", None) is not None:
            controls.append(curve.via_vertex)
        controls.extend(getattr(curve, "control_vertices", ()) or ())
        vertices.update(int(v) for v in (edge.start, edge.end, *controls))
        points.append(np.asarray(geometry.sample_edge(edge_id, parameters), dtype=float).reshape(-1, 3))
    cloud = np.vstack(points)
    # The fingerprint is the complete serialized definition of every owned entity
    # (support surfaces, parameterizations, loops, corners, curves, vertices);
    # nothing is sampled, so an edit that keeps the bounds still changes it.
    definition = {
        "order": order,
        "faces": {str(f): document["faces"][f] for f in sorted(faces)},
        "edges": {str(e): document["edges"][e] for e in sorted(edges)},
        "vertices": {str(v): document["vertices"][v] for v in sorted(vertices)},
    }
    sheets = {}
    sheet_ids = geometry.sheets if sheets_in_scope is None else sheets_in_scope
    for sheet_id in sheet_ids:
        sheet = geometry.sheets[sheet_id]
        uses = [geometry.face_uses[use] for use in sheet.face_use_ids]
        covered = {int(use.face_id) for use in uses}
        if covered & face_set:
            if not covered <= face_set:
                raise _Ineligible(f"sheet {sheet_id} spans independent components")
            sheets[str(sheet_id)] = sorted(covered)
            definition.setdefault("sheet_uses", {})[str(sheet_id)] = [
                (use.id, use.face_id, use.orientation) for use in uses]
    overrides = sorted((int(e), int(n)) for e, n in settings.overrides if int(e) in edges)
    local: dict[str, Any] = {"overrides": [list(item) for item in overrides], "hash": _digest(overrides)}
    if assignments is not None:
        owned = {
            "faces": {str(f): assignments["faces"].get(int(f)) for f in sorted(faces)},
            "edges": {str(e): assignments["edges"].get(int(e)) for e in sorted(edges)},
        }
        local["assignments"] = owned
        local["hash"] = _digest({"overrides": overrides, "assignments": owned})
    return {
        "faces": [int(f) for f in sorted(faces)],
        "edges": sorted(edges),
        "sheets": sheets,
        "vertices": sorted(vertices),
        "face_order": order,
        "fingerprint": _digest(definition),
        "bounds": [float(v) for v in (*cloud.min(axis=0), *cloud.max(axis=0))],
        "local": local,
    }


def _partition(geometry: Any, separation: float, check: Callable[[str], None]) -> list[list[int]]:
    """Owner-certified independent face groups, or refuse."""

    from anygeometry import plan_independent_components

    def cancel(stage: str) -> bool:
        check(stage)
        return False  # a boolean answer; a real cancellation raised in ``check``

    faces = sorted(int(f) for f in geometry.faces)
    partition = plan_independent_components(
        geometry, face_ids=faces, member_ids=(), separation=float(separation),
        cancellation_check=cancel, allow_untrimmed_bilinear=True,
    )
    if not partition.certified or not partition.components:
        reasons = "; ".join(item.reason for item in partition.refusals) or "no components"
        raise _Ineligible(f"ANYgeometry owner partition refused: {reasons}")
    groups = [sorted(int(i) for i in component.face_ids) for component in partition.components]
    flat = [face for group in groups for face in group]
    if sorted(flat) != faces or len(set(flat)) != len(flat):
        raise _Ineligible("owner partition does not cover each face exactly once")
    return sorted(groups)


def _component_mesh(mesh: Any, faces: list[int]) -> tuple[list[int], set[int]]:
    elements: list[int] = []
    for face_id in faces:
        ids = mesh.elements_of_face.get(face_id)
        if not ids:
            raise _Ineligible(f"face {face_id} has no mesh elements")
        elements.extend(int(e) for e in ids)
    if len(set(elements)) != len(elements):
        raise _Ineligible("an element is owned by two faces")
    nodes: set[int] = set()
    for element in elements:
        connectivity = mesh.quads.get(element)
        if connectivity is None:
            connectivity = mesh.tris.get(element)
        if connectivity is None:
            raise _Ineligible(f"element {element} is not a plate element")
        nodes.update(int(n) for n in connectivity)
    return elements, nodes


def _grid(value: Any) -> Any:
    return None if value is None else np.asarray(value).astype(np.int64).tolist()


def _binding(mesh: Any, record: Mapping[str, Any], elements: list[int], nodes: set[int]) -> dict[str, Any]:
    """Exact numeric identity of one component's mesh (ids, coordinates, topology)."""

    payload = {
        "nodes": {str(n): _hex(mesh.nodes[n]) for n in sorted(nodes)},
        "elements": {
            str(e): ["quad", _ints(mesh.quads[e])] if e in mesh.quads else ["tri", _ints(mesh.tris[e])]
            for e in sorted(elements)
        },
        "faces": {
            str(f): {
                "elements": _ints(mesh.elements_of_face.get(f, ())),
                "grid": _grid(mesh.grid_of_face.get(f)),
                "blocks": [_grid(b) for b in mesh.block_grids_of_face.get(f, ())],
            } for f in record["faces"]
        },
        "edges": {
            str(e): {
                "elements": _ints(mesh.elements_of_edge.get(e, ())),
                "nodes": _ints(mesh.nodes_of_edge.get(e, ())),
                "offset": _ints(mesh.offset_nodes_of_edge.get(e, ())),
            } for e in record["edges"]
        },
        "vertices": {
            str(v): (int(mesh.node_of_vertex[v]) if v in mesh.node_of_vertex else None)
            for v in record["vertices"]
        },
        "activity": {str(e): float(mesh.activity[e]).hex() for e in sorted(elements) if e in mesh.activity},
    }
    return {
        "elements": len(elements), "nodes": len(nodes),
        "node_ids": sorted(int(n) for n in nodes), "element_ids": sorted(int(e) for e in elements),
        "digest": _digest(payload),
    }


def _component_digest(fingerprint: str, local_hash: str, binding_digest: str) -> str:
    return _digest({"definition": fingerprint, "local": local_hash, "mesh": binding_digest})


def _closure_gate(mesh: Any, components: list[Mapping[str, Any]]) -> None:
    """Every association of the mesh must belong to exactly one component."""

    keys = {
        "faces": {f for c in components for f in c["faces"]},
        "edges": {e for c in components for e in c["edges"]},
        "vertices": {v for c in components for v in c["vertices"]},
    }
    for name, kind in (("elements_of_face", "faces"), ("grid_of_face", "faces"),
                       ("block_grids_of_face", "faces"), ("thickness_of_face", "faces"),
                       ("elements_of_edge", "edges"), ("nodes_of_edge", "edges"),
                       ("offset_nodes_of_edge", "edges"), ("node_of_vertex", "vertices")):
        extra = {int(k) for k in getattr(mesh, name)} - keys[kind]
        if extra:
            raise _Ineligible(f"mesh.{name} has entries outside the components: {sorted(extra)[:6]}")
    expected_sheets = {
        int(sheet): sorted(e for face in faces for e in mesh.elements_of_face[face])
        for component in components for sheet, faces in component["sheets"].items()
    }
    if expected_sheets != {int(sheet): sorted(ids) for sheet, ids in mesh.elements_of_sheet.items()}:
        raise _Ineligible("sheet element membership differs from the source face uses")
    for name in ("elements_of_member", "nodes_of_member"):
        if getattr(mesh, name):
            raise _Ineligible(f"mesh.{name} is not empty; only plain plates are supported")
    elements = {int(e) for e in (*mesh.quads, *mesh.tris)}
    if {int(e) for e in mesh.activity} - elements:
        raise _Ineligible("mesh.activity names elements that are not plate elements")


def _admissible_metadata(mesh: Any) -> None:
    """Only metadata that the owner can carry through a regional assembly is eligible."""

    from anymesher.regional_update import remap_mesh_ids

    identity_nodes = {int(n): int(n) for n in mesh.nodes}
    identity_elements = {int(e): int(e) for e in (*mesh.quads, *mesh.tris, *mesh.beams, *mesh.couplings)}
    remap_mesh_ids(mesh, identity_nodes, identity_elements)  # MeshError: not admissible


def _proven_mapped_faces(geometry: Any, mesh: Any) -> set[int]:
    diagnostics = getattr(mesh, "hybrid_diagnostics", None) or {}
    chosen = diagnostics.get("strategy_by_face") if isinstance(diagnostics, Mapping) else None
    if not isinstance(chosen, Mapping):
        return set()
    return {int(face) for face, value in chosen.items()
            if str(getattr(value, "value", value)).lower() == "mapped"}


def _strategy_gate(settings: Any, strategy: str) -> None:
    from anymesher.recovery import MeshAutomationOptions

    if settings.automation not in (None, MeshAutomationOptions()):
        raise _Ineligible("custom method recovery requires full owner generation")
    if strategy == "mapped":
        return
    if strategy == "auto":
        if settings.quad_options is not None:
            raise _Ineligible("automatic strategy with quad options is not regional")
        return
    raise _Ineligible(f"strategy {strategy!r}: only mapped (or automatic resolving to mapped) is supported")


# --------------------------------------------------------------------- capture


def _capture(project: Any, settings: Any, mesh: Any, check: Callable[[str], None],
             proven_mapped: set[int] | None = None) -> dict[str, Any]:
    from .mesh_controls import MeshControls
    controls = settings.controls or MeshControls.from_settings(project.native_mesh_settings)
    if controls.certification_mode != "interactive":
        raise _Ineligible("the requested certification requires full owner generation")
    if project.refinements:
        raise _Ineligible("component generation requires a uniform size field")
    strategy = _strategy(project, settings)
    _strategy_gate(settings, strategy)
    if not all(hasattr(mesh, name) for name in ("elements_of_face", "quads", "tris", "nodes")):
        raise _Ineligible("mesh carries no face provenance")
    if mesh.beams or mesh.couplings:
        raise _Ineligible("mesh has beams or couplings")
    if (mesh.declared_plate_junction_edges or mesh.automatic_intersections
            or mesh.automatic_beam_connections or mesh.automatic_shell_connections):
        raise _Ineligible("mesh has structural connections")
    if mesh.order != settings.element_order:
        raise _Ineligible("mesh order differs from the settings")
    geometry = project.geometry
    _geometry_gate(geometry)
    if {int(f) for f in mesh.elements_of_face} != {int(f) for f in geometry.faces}:
        raise _Ineligible("mesh does not cover exactly the geometry's faces")
    if mesh.geometry_model_id is None or mesh.geometry_revision is None:
        raise _Ineligible("mesh carries no geometry model/revision identity")
    # The mesh keeps its own true source identity (a detached job clone may carry
    # another model id than the live document); it is recorded, never rewritten.
    route = None
    if strategy == "auto" or settings.automation is not None:
        if settings.automation is not None and proven_mapped is None:
            from anymesher.recovery import AUTOMATION_CONTRACT_ID
            report = mesh.hybrid_diagnostics.get("automation", {})
            if (report.get("contract_id") != AUTOMATION_CONTRACT_ID or report.get("status") != "ready"
                    or report.get("solver_admission") != "ADMITTED"
                    or report.get("selected_method") not in ("mapped", "auto")):
                raise _Ineligible("method recovery did not admit a mapped plate mesh")
        faces_now = {int(f) for f in geometry.faces}
        proven = _proven_mapped_faces(geometry, mesh) if proven_mapped is None else set(proven_mapped)
        if proven != faces_now:
            raise _Ineligible("the automatic route did not demonstrably select mapped for every face")
        route = {"mapped_faces": sorted(faces_now)}
    _admissible_metadata(mesh)
    document = _document(geometry)
    separation = float(settings.target_size)
    groups = _partition(geometry, separation, check)
    assignments = _assignment_maps(project)
    components, seen_nodes, seen_elements = [], set(), set()
    for faces in groups:
        record = _component_record(geometry, document, faces, settings, assignments)
        elements, nodes = _component_mesh(mesh, faces)
        if nodes & seen_nodes:
            raise _Ineligible("components share mesh nodes")
        seen_nodes |= nodes
        seen_elements |= set(elements)
        record["mesh"] = _binding(mesh, record, elements, nodes)
        components.append(record)
    if seen_nodes != {int(n) for n in mesh.nodes}:
        raise _Ineligible("mesh has nodes outside the components")
    if seen_elements != {int(e) for e in (*mesh.quads, *mesh.tris)}:
        raise _Ineligible("mesh has elements outside the components")
    _closure_gate(mesh, components)
    _assignment_closure(assignments, components)
    baseline = {
        "schema": SCHEMA,
        "version": VERSION,
        "pins": _pins(),
        "global": _global_state(project, settings, strategy, document),
        "separation": separation,
        "geometry_order": [int(f) for f in geometry.faces],
        "mesh_identity": {"model_id": str(mesh.geometry_model_id), "revision": int(mesh.geometry_revision)},
        "original_geometry": _plain(document["raw"]),
        "components": components,
    }
    if route is not None:
        baseline["route"] = route
    return baseline


def explain_regional_ineligibility(project: Any, settings: Any, mesh: Any) -> str | None:
    """Why :func:`capture_regional_baseline` returns ``None`` (``None`` if eligible)."""

    try:
        _capture(project, settings, mesh, _check_fn(None))
    except _refusals() as error:
        return f"{type(error).__name__}: {error}"
    return None


def capture_regional_baseline(project: Any, settings: Any, mesh: Any, *,
                              cancellation_check: Callable[[str], Any] | None = None
                              ) -> dict[str, Any] | None:
    """Serializable baseline for a published full mesh, or ``None`` on a known refusal.

    Only the known qualification refusals are swallowed; a programming error raises.
    """

    try:
        return _capture(project, settings, mesh, _check_fn(cancellation_check))
    except _refusals():
        return None


# ------------------------------------------------------------------------ plan


@dataclass(frozen=True, eq=False)
class RegionalPlan:
    retained: tuple[int, ...]
    changed: tuple[tuple[int, tuple[int, ...]], ...]
    deleted: tuple[int, ...]
    reasons: Mapping[int, str]
    records: Mapping[int, dict]
    description: str
    fingerprints: Mapping[int, Mapping[str, str]] = field(default_factory=dict)
    # Whole-plate additions (current records, fresh entity ids) and the faces of the
    # removed baseline components; ``deleted`` holds the removed baseline indices.
    added: tuple[tuple[int, ...], ...] = ()
    added_records: tuple[dict, ...] = ()
    removed_faces: tuple[tuple[int, ...], ...] = ()
    retained_faces: tuple[tuple[int, ...], ...] = ()
    groups: tuple[tuple[int, ...], ...] = ()

    @property
    def is_noop(self) -> bool:
        return not self.changed and not self.deleted and not self.added

    @property
    def impact(self) -> dict[str, Any]:
        """What the update keeps, regenerates, adds and removes (for the preview)."""

        return {
            "retained": [list(f) for f in self.retained_faces],
            "regenerated": [list(f) for _, f in self.changed],
            "added": [list(f) for f in self.added],
            "removed": [list(f) for f in self.removed_faces],
        }


def _validate_baseline(baseline: Mapping[str, Any]) -> None:
    if not isinstance(baseline, Mapping) or baseline.get("schema") != SCHEMA:
        raise RegionalUpdateUnsupported("not a regional baseline")
    if baseline.get("version") != VERSION:
        raise RegionalUpdateUnsupported("unsupported regional baseline version")
    try:
        current = _pins()
    except _Ineligible as error:
        raise RegionalUpdateUnsupported(str(error)) from error
    if dict(baseline.get("pins", {})) != current:
        raise RegionalUpdateUnsupported(
            "build pins changed (anyfem/anymesher/anygeometry); a full rebuild is required"
        )


def plan_regional_update(project: Any, settings: Any, baseline: Mapping[str, Any],
                         *, cancellation_check: Callable[[str], Any] | None = None) -> RegionalPlan:
    check = _check_fn(cancellation_check)
    _validate_baseline(baseline)
    if project.refinements:
        raise RegionalUpdateUnsupported("component generation requires a uniform size field")
    strategy = _strategy(project, settings)
    try:
        _strategy_gate(settings, strategy)
    except _Ineligible as error:
        raise RegionalUpdateUnsupported(str(error)) from error
    geometry = project.geometry
    try:
        _geometry_gate(geometry)
        document = _document(geometry)
    except (_Ineligible,) + tuple(_refusals()[1:]) as error:
        raise RegionalUpdateUnsupported(str(error)) from error
    now = _global_state(project, settings, strategy, document)
    before = dict(baseline["global"])
    differing = sorted(key for key in set(now) | set(before) if now.get(key) != before.get(key))
    if differing:
        raise RegionalUpdateUnsupported(
            "global dependencies changed (" + ", ".join(differing) + "); not a regional edit"
        )
    try:
        check("regional component partition")
        groups = _partition(geometry, float(baseline["separation"]), check)
    except _Ineligible as error:
        raise RegionalUpdateUnsupported(str(error)) from error
    base = list(baseline["components"])
    owner = {int(face): index for index, item in enumerate(base) for face in item["faces"]}
    claimed: dict[int, list[tuple[int, ...]]] = {}
    records: dict[int, dict] = {}
    reasons: dict[int, str] = {}
    fingerprints: dict[int, dict[str, str]] = {}
    retained: list[int] = []
    assignments = _assignment_maps(project)
    added_records: list[dict] = []
    base_entities = {
        "faces": {int(f) for item in base for f in item["faces"]},
        "edges": {int(e) for item in base for e in item["edges"]},
        "vertices": {int(v) for item in base for v in item["vertices"]},
        "sheets": {int(s) for item in base for s in item["sheets"]},
    }
    for faces in groups:
        sources = {owner[f] for f in faces if f in owner}
        if not sources:
            # A whole new plate: every face id is fresh.  It is only an addition when
            # nothing of it is an entity the baseline already knew (no id reuse, no
            # coincident stand-in for a deleted plate) and it is meshable as before.
            record = _component_record(geometry, document, list(faces), settings, assignments)
            reused = {
                kind: sorted(set(record[kind]) & base_entities[kind])
                for kind in ("edges", "vertices")
            }
            reused["sheets"] = sorted({int(s) for s in record["sheets"]} & base_entities["sheets"])
            if any(reused.values()):
                raise RegionalUpdateUnsupported(
                    f"new plate {list(faces)} reuses baseline entities "
                    f"{ {k: v for k, v in reused.items() if v} }; it is ambiguous whether it is the "
                    "same plate, so a full rebuild is required")
            if strategy == "auto" and not _auto_mappable(geometry, record["faces"]):
                raise RegionalUpdateUnsupported(
                    f"new plate {record['faces']} would not be meshed by the mapped route under "
                    "the Automatic strategy; a full rebuild is required")
            added_records.append(record)
            continue
        if any(f not in owner for f in faces):
            raise RegionalUpdateUnsupported(
                f"faces {sorted(f for f in faces if f not in owner)} join existing component(s) "
                f"{sorted(sources)}: a new face on an existing plate (merge/overlap) is ambiguous"
            )
        if len(sources) != 1:
            raise RegionalUpdateUnsupported(
                f"components {sorted(sources)} are now one independent group: motion near a "
                "potential contact or a merge"
            )
        index = next(iter(sources))
        claimed.setdefault(index, []).append(tuple(faces))
        record = _component_record(geometry, document, list(faces), settings, assignments)
        fingerprints[index] = {"definition": record["fingerprint"], "local": record["local"]["hash"]}
        old = base[index]
        if list(faces) == list(old["faces"]) and record["fingerprint"] == old["fingerprint"] \
                and record["local"]["hash"] == old["local"]["hash"]:
            retained.append(index)
            continue
        if list(faces) != list(old["faces"]):
            reasons[index] = "face set changed"
        elif record["fingerprint"] != old["fingerprint"]:
            reasons[index] = "geometry changed"
        elif record["local"]["overrides"] != old["local"].get("overrides"):
            reasons[index] = "local edge divisions changed"
        else:
            reasons[index] = "local section assignment changed"
        if strategy == "auto" and not _auto_mappable(geometry, record["faces"]):
            raise RegionalUpdateUnsupported(
                f"faces {record['faces']} would no longer be meshed by the mapped route "
                "under the Automatic strategy; a full rebuild is required"
            )
        records[index] = record
    split = sorted(index for index, parts in claimed.items() if len(parts) > 1)
    if split:
        raise RegionalUpdateUnsupported(f"component(s) {split} split into several groups")
    removed = [index for index in range(len(base)) if index not in claimed]
    for index in removed:
        _prove_absent(base[index], geometry, document, assignments)
        reasons[index] = "removed"
    if removed or added_records:
        # A structural change must leave a closed, unambiguous model: the current
        # components own every edge/vertex exactly once and every assignment.
        current = [_component_record(geometry, document, list(g), settings, assignments) for g in groups]
        try:
            _structural_closure(document, assignments, current)
        except _Ineligible as error:
            raise RegionalUpdateUnsupported(str(error)) from error
        # A plate that disappears while another appears over the same material cannot
        # be told apart from an edit of one plate: refuse unless the regions are apart.
        for record in added_records:
            for index in removed:
                old_box, new_box = base[index]["bounds"], record["bounds"]
                if all(new_box[k] <= old_box[k + 3] and old_box[k] <= new_box[k + 3] for k in range(3)):
                    raise RegionalUpdateUnsupported(
                        f"new plate {record['faces']} overlaps the region of the removed plate "
                        f"{list(base[index]['faces'])}; whether it is the same material is ambiguous, "
                        "so a full rebuild is required")
    changed = tuple((index, tuple(records[index]["faces"])) for index in sorted(records))
    added = tuple(tuple(record["faces"]) for record in added_records)
    removed_faces = tuple(tuple(int(f) for f in base[i]["faces"]) for i in removed)
    retained_faces = tuple(tuple(int(f) for f in base[i]["faces"]) for i in sorted(retained))
    parts = [f"{len(groups)} independent component(s) now", f"{len(retained)} retained exactly"]
    for index, faces in changed:
        parts.append(f"regenerate faces {list(faces)} ({reasons[index]})")
    for faces in added:
        parts.append(f"add new plate faces {list(faces)} (fresh numbers)")
    for faces in removed_faces:
        parts.append(f"remove plate faces {list(faces)} (retired numbers)")
    return RegionalPlan(tuple(sorted(retained)), changed, tuple(removed), reasons, records,
                        "; ".join(parts), fingerprints, added, tuple(added_records), removed_faces,
                        retained_faces, tuple(tuple(int(f) for f in g) for g in groups))


def _prove_absent(item: Mapping[str, Any], geometry: Any, document: Mapping[str, Any],
                  assignments: Mapping[str, Mapping[int, Any]]) -> None:
    """Current certified geometry proves the baseline component is exactly gone.

    Nothing of it may survive under the same id (a surviving id would be a different
    plate wearing an old identity), and nothing live may still point at it.
    """

    live = {
        "faces": sorted(int(f) for f in item["faces"] if int(f) in document["faces"]),
        "edges": sorted(int(e) for e in item["edges"] if int(e) in document["edges"]),
        "vertices": sorted(int(v) for v in item["vertices"] if int(v) in document["vertices"]),
        "sheets": sorted(int(s) for s in item["sheets"] if int(s) in geometry.sheets),
    }
    if any(live.values()):
        raise RegionalUpdateUnsupported(
            f"plate faces {list(item['faces'])} are not exactly absent: geometry still holds "
            f"{ {k: v for k, v in live.items() if v} }; a partial removal is ambiguous"
        )
    dangling = (sorted(set(assignments["faces"]) & {int(f) for f in item["faces"]})
                + sorted(set(assignments["edges"]) & {int(e) for e in item["edges"]}))
    if dangling:
        raise RegionalUpdateUnsupported(
            f"section assignments still reference the removed plate entities {dangling}")


def _structural_closure(document: Mapping[str, Any], assignments: Mapping[str, Mapping[int, Any]],
                        records: list[Mapping[str, Any]]) -> None:
    edges = [int(e) for r in records for e in r["edges"]]
    vertices = [int(v) for r in records for v in r["vertices"]]
    if len(set(edges)) != len(edges) or len(set(vertices)) != len(vertices):
        raise _Ineligible("independent components share an edge or vertex")
    if set(edges) != {int(e) for e in document["edges"]} or \
            set(vertices) != {int(v) for v in document["vertices"]}:
        raise _Ineligible("geometry holds edges or vertices outside the plate components")
    _assignment_closure(assignments, records)


# -------------------------------------------------------------------- generate


@dataclass(frozen=True, eq=False)
class RegionalUpdateResult:
    mesh: Any
    baseline: dict | None
    retained_nodes: frozenset
    retained_elements: frozenset
    plan: RegionalPlan


def _decode(old_mesh: Any) -> Any:
    if isinstance(old_mesh, Mapping):
        from anymesher.serialize import mesh_from_dict

        return mesh_from_dict(dict(old_mesh))
    return old_mesh


def _generate_component(project: Any, settings: Any, record: Mapping[str, Any]) -> Any:
    """The single seam that invokes the mesher for the changed faces.

    ``record`` carries ``faces`` and ``local.overrides``.  A separate integration
    for planar patches would dispatch through :data:`PATCH_SEAM` here.
    """

    import anymesher.mapped as mapped

    overrides = {int(e): int(n) for e, n in record["local"]["overrides"]}
    mesh = mapped.generate_mesh(
        project.geometry, target_size=float(settings.target_size), overrides=overrides,
        face_ids=tuple(int(f) for f in record["faces"]), order=settings.element_order,
    )
    for sheet_id, sheet in project.geometry.sheets.items():
        elements = sorted({e for use in sheet.face_use_ids
                           for e in mesh.elements_of_face.get(project.geometry.face_uses[use].face_id, ())})
        if elements:
            mesh.elements_of_sheet[int(sheet_id)] = elements
    return mesh


def _validate_quality(project: Any, mesh: Any, settings: Any, cancellation_check=None) -> None:
    """Owner mandatory validity, plus the policy only when it was explicitly requested."""

    from anymesher.errors import MeshError
    from anymesher.hybrid import validate_mesh_quality

    from .mesh_controls import StructuredMeshControls

    controls = settings.structured_controls or StructuredMeshControls()
    options = controls.owner_options(preference=settings.structure_preference,
                                     quality_policy=dict(settings.quality_policy))
    stored = dict(getattr(getattr(project, "native_mesh_settings", None), "parameters", ()) or ())
    if not settings.quality_policy_explicit:
        policy = {k.removeprefix("mesh_quality_"): v for k, v in stored.items()
                  if k.startswith("mesh_quality_")}
        options = controls.owner_options(preference=settings.structure_preference,
                                         quality_policy=policy) if policy else None
    try:
        validate_mesh_quality(project.geometry, mesh, quality_options=options,
                              cancellation_check=cancellation_check)
    except MeshError as error:
        raise RegionalUpdateError(f"the updated mesh failed owner quality validation: {error}") from error


def generate_regional_update(project: Any, settings: Any, baseline: Mapping[str, Any], old_mesh: Any,
                             *, node_high_water: int, element_high_water: int,
                             cancellation_check: Callable[[str], Any] | None = None
                             ) -> RegionalUpdateResult:
    from anymesher.errors import MeshError
    from anymesher.regional_update import (
        CrossRevisionAuthority,
        OwnerBaselineReceipt,
        RegionalQualityLimits,
        assemble_regional_replacement,
    )

    check = _check_fn(cancellation_check)
    plan = plan_regional_update(project, settings, baseline, cancellation_check=cancellation_check)
    old = _decode(old_mesh)
    base = list(baseline["components"])
    identity = dict(baseline.get("mesh_identity") or {})
    if old.geometry_model_id is None or str(old.geometry_model_id) != identity.get("model_id") \
            or old.geometry_revision != identity.get("revision"):
        raise RegionalUpdateError("old mesh geometry model/revision differs from the baseline's")
    if old.order != settings.element_order:
        raise RegionalUpdateError("old mesh order differs from the settings")
    owned: dict[int, tuple[list[int], set[int]]] = {}
    for index, item in enumerate(base):
        try:
            elements, nodes = _component_mesh(old, list(item["faces"]))
            bound = _binding(old, item, elements, nodes)
        except (_Ineligible, KeyError) as error:
            raise RegionalUpdateError(f"old mesh does not match the baseline: {error}") from error
        if bound != item["mesh"]:
            raise RegionalUpdateError(
                f"old mesh does not match the baseline for faces {item['faces']}")
        owned[index] = (elements, nodes)
    if set().union(*(n for _, n in owned.values())) != {int(n) for n in old.nodes} or \
            {e for els, _ in owned.values() for e in els} != {int(e) for e in (*old.quads, *old.tris)}:
        raise RegionalUpdateError("old mesh has entities outside the baseline components")
    all_old_elements = {int(e) for e in (*old.quads, *old.tris, *old.beams, *old.couplings)}

    retained_nodes_all = frozenset(int(n) for n in old.nodes)
    if plan.is_noop:
        # Managed no-op: nothing is regenerated and the old mesh (with its own
        # metadata and numbers, all of which stay true) is returned as it is.
        return RegionalUpdateResult(old, copy.deepcopy(dict(baseline)), retained_nodes_all,
                                    frozenset(all_old_elements), plan)

    changed_indices = [index for index, _ in plan.changed]
    deleted_indices = [int(i) for i in plan.deleted]
    added_records = list(plan.added_records)
    for index in changed_indices:
        stale = [f for f in base[index]["faces"] if f in old.thickness_of_face]
        if stale:
            raise RegionalUpdateUnsupported(
                f"faces {stale} carry mesh thickness; production meshes keep thickness on the "
                "section assignment, so this mesh cannot be updated regionally")
    # An added plate must not collide with anything the old mesh already associates.
    for record in added_records:
        clash = (
            {f for f in record["faces"] if f in old.elements_of_face or f in old.grid_of_face}
            | {e for e in record["edges"] if e in old.nodes_of_edge or e in old.elements_of_edge}
            | {v for v in record["vertices"] if v in old.node_of_vertex}
            | {int(s) for s in record["sheets"] if int(s) in old.elements_of_sheet}
        )
        if clash:
            raise RegionalUpdateError(f"added plate {record['faces']} reuses ids of the old mesh: {sorted(clash)}")

    geometry = project.geometry
    regenerated = [*(plan.records[i] for i in changed_indices), *added_records]
    union = {
        "faces": sorted(f for record in regenerated for f in record["faces"]),
        "local": {"overrides": [item for record in regenerated for item in record["local"]["overrides"]]},
    }
    if union["faces"]:
        check("regional generate component")
        replacement = _generate_component(project, settings, union)
    else:
        # Pure removal: no generator call.  The replacement is genuinely empty and
        # bound to the current source model/revision.
        from anymesher.mesh import Mesh

        replacement = Mesh(geometry_model_id=geometry.model_id, geometry_revision=geometry.revision,
                           order=settings.element_order)

    if replacement.geometry_model_id is None or str(replacement.geometry_model_id) != str(geometry.model_id) \
            or replacement.geometry_revision != geometry.revision:
        raise RegionalUpdateError("the replacement was not built from the current geometry model/revision")
    new_digest: dict[int, str] = {}
    added_digest: list[str] = []
    seen_nodes: set[int] = set()
    seen_elements: set[int] = set()
    replacement_owner: dict[int, Any] = {}
    for key, record in [*((i, plan.records[i]) for i in changed_indices),
                        *((None, r) for r in added_records)]:
        try:
            elements, nodes = _component_mesh(replacement, list(record["faces"]))
        except _Ineligible as error:
            raise RegionalUpdateError(f"replacement does not cover faces {record['faces']}: {error}") from error
        if nodes & seen_nodes:
            raise RegionalUpdateError("replacement components share nodes")
        seen_nodes |= nodes
        seen_elements |= set(elements)
        binding = _binding(replacement, record, elements, nodes)
        digest = _component_digest(record["fingerprint"], record["local"]["hash"], binding["digest"])
        owner_key = key if key is not None else ("added", len(added_digest))
        for element in elements:
            replacement_owner[int(element)] = owner_key
        if key is None:
            added_digest.append(digest)
        else:
            new_digest[key] = digest
    if seen_nodes != {int(n) for n in replacement.nodes} or \
            seen_elements != {int(e) for e in (*replacement.quads, *replacement.tris,
                                               *replacement.beams, *replacement.couplings)}:
        raise RegionalUpdateError("replacement carries mesh outside the changed components")
    new_faces = {f for record in regenerated for f in record["faces"]}
    new_edges = {e for record in regenerated for e in record["edges"]}
    new_vertices = {v for record in regenerated for v in record["vertices"]}
    for name, allowed in (("elements_of_face", new_faces), ("grid_of_face", new_faces),
                          ("block_grids_of_face", new_faces), ("elements_of_edge", new_edges),
                          ("nodes_of_edge", new_edges), ("offset_nodes_of_edge", new_edges),
                          ("node_of_vertex", new_vertices)):
        if {int(k) for k in getattr(replacement, name)} - allowed:
            raise RegionalUpdateError(f"replacement.{name} has entries outside the changed components")

    # Seeding: absence is preserved; a stale previous seed is never kept.
    if old.seeding is None:
        replacement.seeding = None
    elif replacement.seeding is None and union["faces"]:
        raise RegionalUpdateUnsupported("the replacement carries no seeding to supersede the previous seeds")

    # Receipts bind real content on the true source model/revision of each side.
    old_model, old_revision = str(old.geometry_model_id), old.geometry_revision
    new_model, new_revision = str(replacement.geometry_model_id), replacement.geometry_revision
    old_receipts, new_receipts = {}, {}
    for index, item in enumerate(base):
        before = _component_digest(item["fingerprint"], item["local"]["hash"], item["mesh"]["digest"])
        if index in deleted_indices:
            # The owner is gone from the current revision: its new-side statement is
            # the absence of its definition from this very model/revision.
            after = _digest({"absent": [int(f) for f in item["faces"]], "definition": item["fingerprint"],
                             "model": new_model, "revision": new_revision,
                             "geometry": _digest(_document(geometry)["raw"])})
        elif index in new_digest:
            after = new_digest[index]
        else:
            current = plan.fingerprints[index]
            after = _component_digest(current["definition"], current["local"], item["mesh"]["digest"])
        old_receipts[index] = OwnerBaselineReceipt(index, old_model, old_revision, before, True)
        new_receipts[index] = OwnerBaselineReceipt(
            index, new_model, new_revision, after, True,
            "absent" if index in deleted_indices else "present")
    added_keys = []
    for number, record in enumerate(added_records):
        key = ("added", number)
        added_keys.append(key)
        # Old side: the component is absent from the previous source geometry (every
        # face id is unclaimed by the baseline and no entity id is reused; see the plan).
        absent = _digest({"absent": [int(f) for f in record["faces"]], "definition": record["fingerprint"],
                          "model": old_model, "revision": old_revision,
                          "geometry": _digest(baseline["original_geometry"])})
        old_receipts[key] = OwnerBaselineReceipt(key, old_model, old_revision, absent, True, "absent")
        new_receipts[key] = OwnerBaselineReceipt(key, new_model, new_revision, added_digest[number], True)
    retained_indices = [i for i in range(len(base)) if i not in new_digest and i not in deleted_indices]
    unchanged = {
        "face": frozenset(f for i in retained_indices for f in base[i]["faces"]),
        "edge": frozenset(e for i in retained_indices for e in base[i]["edges"]),
        "vertex": frozenset(v for i in retained_indices for v in base[i]["vertices"]),
        "sheet": frozenset(int(s) for i in retained_indices for s in base[i]["sheets"]),
    }
    edge_owner = {int(e): i for i, item in enumerate(base) for e in item["edges"]}
    edge_owner.update({int(e): i for i in changed_indices for e in plan.records[i]["edges"]})
    retired: dict[str, set[int]] = {"face": set(), "edge": set(), "vertex": set(), "sheet": set()}
    for index in changed_indices + deleted_indices:
        item = base[index]
        record = plan.records[index] if index in plan.records else {
            "faces": [], "edges": [], "vertices": [], "sheets": {}}
        retired["face"] |= {f for f in set(item["faces"]) - set(record["faces"]) if f in old.elements_of_face}
        retired["edge"] |= {e for e in set(item["edges"]) - set(record["edges"])
                            if e in old.nodes_of_edge or e in old.elements_of_edge
                            or e in old.offset_nodes_of_edge}
        retired["vertex"] |= {v for v in set(item["vertices"]) - set(record["vertices"])
                              if v in old.node_of_vertex}
        if index in deleted_indices:
            retired["sheet"] |= {int(s) for s in item["sheets"] if int(s) in old.elements_of_sheet}
    added_edges = {e for record in added_records for e in record["edges"]}
    for key, record in zip(added_keys, added_records):
        edge_owner.update({int(e): key for e in record["edges"]})
    authority = CrossRevisionAuthority(
        old_receipts=old_receipts, new_receipts=new_receipts,
        changed_owners=frozenset(changed_indices), unchanged_geometry_keys=unchanged,
        edge_owner=edge_owner, added_owners=frozenset(added_keys),
        removed_owners=frozenset(deleted_indices), replacement_element_owner=dict(replacement_owner),
    )

    next_node = max(int(node_high_water), max((int(n) for n in old.nodes), default=0))
    next_element = max(int(element_high_water), max(all_old_elements, default=0))
    node_map, element_map = {}, {}
    for temp in sorted(replacement.nodes):
        next_node += 1
        node_map[int(temp)] = next_node
    for temp in sorted(int(e) for e in (*replacement.quads, *replacement.tris,
                                        *replacement.beams, *replacement.couplings)):
        next_element += 1
        element_map[temp] = next_element
    owner_of_element = {int(e): i for i, (els, _) in owned.items() for e in els}
    check("regional assemble")
    try:
        assembled = assemble_regional_replacement(
            old, replacement,
            quality=RegionalQualityLimits(None, None, None),  # advisory: the reported quality is kept
            node_ids=node_map, element_ids=element_map,
            owner_of_element=owner_of_element, cross_revision=authority,
            retired_geometry_keys={k: sorted(v) for k, v in retired.items() if v},
            discard_stale_diagnostics=True,
            seed_changed_edges=sorted(added_edges) or None,
        )
    except MeshError as error:
        raise RegionalUpdateError(f"ANYmesher refused the regional splice: {error}") from error
    final = assembled.mesh
    check("regional quality validation")
    _validate_quality(project, final, settings, cancellation_check)

    retained_nodes = frozenset(int(n) for n in assembled.retained_node_ids)
    retained_elements = frozenset(int(e) for e in assembled.retained_element_ids)
    for node in retained_nodes:
        if not np.array_equal(np.asarray(old.nodes[node]), np.asarray(final.nodes[node])):
            raise RegionalUpdateError(f"retained node {node} moved")
    for element in retained_elements:
        before = old.quads.get(element) or old.tris.get(element)
        after = final.quads.get(element) or final.tris.get(element)
        if tuple(before) != tuple(after):
            raise RegionalUpdateError(f"retained element {element} changed connectivity")
    expected = {e for i in plan.retained for e in owned[i][0]}
    if expected != set(retained_elements):
        raise RegionalUpdateError("retained elements differ from the planned retained components")

    # Removed plates: nothing of their mesh or associations survives, and their
    # numbers are retired, never handed out again (fresh ids are above the high water).
    for index in deleted_indices:
        elements, nodes = owned[index]
        if nodes & {int(n) for n in final.nodes} or set(elements) & {int(e) for e in (*final.quads, *final.tris)}:
            raise RegionalUpdateError(f"removed plate {base[index]['faces']} left mesh behind")
        item = base[index]
        for name, keys in (("elements_of_face", item["faces"]), ("grid_of_face", item["faces"]),
                           ("elements_of_edge", item["edges"]), ("nodes_of_edge", item["edges"]),
                           ("node_of_vertex", item["vertices"]), ("elements_of_sheet", item["sheets"])):
            left = [k for k in keys if int(k) in {int(x) for x in getattr(final, name)}]
            if left:
                raise RegionalUpdateError(f"removed plate {item['faces']} left {name} entries {left}")
    # Added plates: fresh numbers only, above everything the old mesh ever used.
    fresh_nodes = {int(n) for n in final.nodes} - retained_nodes
    fresh_elements = {int(e) for e in (*final.quads, *final.tris)} - set(retained_elements)
    if fresh_nodes and min(fresh_nodes) <= max(next_node - len(node_map), 0):
        raise RegionalUpdateError("a new node reuses a number that was already allocated")
    if fresh_elements and min(fresh_elements) <= max(next_element - len(element_map), 0):
        raise RegionalUpdateError("a new element reuses a number that was already allocated")

    proven = None
    if _strategy(project, settings) == "auto" or settings.automation is not None:
        proven = {f for i in retained_indices for f in base[i]["faces"]} | new_faces
    try:
        updated = _capture(project, settings, final, check, proven_mapped=proven)
    except _refusals() as error:
        raise RegionalUpdateError(
            f"the updated mesh is no longer regional-eligible: {type(error).__name__}: {error}") from error
    # Every unaffected component must come back bit-for-bit: same faces, same
    # definition and local settings, same node/element numbers, coordinates and cells.
    by_faces = {tuple(c["faces"]): c for c in updated["components"]}
    for index in retained_indices:
        item = base[index]
        now = by_faces.get(tuple(item["faces"]))
        if now is None or now["fingerprint"] != item["fingerprint"] \
                or now["local"]["hash"] != item["local"]["hash"] or now["mesh"] != item["mesh"]:
            raise RegionalUpdateError(f"retained plate {item['faces']} is not identical after the update")
    expected_faces = {tuple(g) for g in plan.groups}
    if set(by_faces) != expected_faces:
        raise RegionalUpdateError("the updated mesh does not consist of exactly the current plates")
    for record in added_records:
        now = by_faces.get(tuple(record["faces"]))
        if now is None or now["fingerprint"] != record["fingerprint"] \
                or set(now["mesh"]["node_ids"]) & retained_nodes:
            raise RegionalUpdateError(f"added plate {record['faces']} is not an independent new component")
    return RegionalUpdateResult(final, updated, retained_nodes, retained_elements, plan)
