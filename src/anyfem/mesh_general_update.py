"""Update published source faces with live cell, interface and numbering proofs.

The mesher validates the complete source closure. Unchanged cells can keep
their permanent numbers after their oriented connectivity, coordinates and
source associations match the original publication.
"""
from __future__ import annotations

from contextlib import nullcontext
from collections import defaultdict
from dataclasses import dataclass, field, replace

import numpy as np

from . import regional_mesh as rm
from .mesh_connected_update import _published_mesh_digest
from .mesh_numbering import MeshNumbering, _fresh_ids, element_numbers, raise_high_water
from .mesh_whole_plate_update import _Cancellation


SCHEMA = "anyfem.regional-source-surfaces"
VERSION = 1


class GeneralSurfaceUnsupported(rm.RegionalUpdateUnsupported):
    """The full source owner, boundary or permanent identity could not be proved."""


def _require(condition, reason):
    if not condition:
        raise GeneralSurfaceUnsupported(reason)


def general_family(project):
    from anygeometry import CoonsSurface, Cone, Cylinder, ExtrudedSurface, RuledSurface
    from anygeometry.nurbs import NurbsSurface
    from .model.project import source_publication_required

    faces = project.geometry.faces
    return bool(faces) and (source_publication_required(project.geometry) or any(
        isinstance(f.surface, (ExtrudedSurface, RuledSurface, NurbsSurface))
        or isinstance(f.surface, CoonsSurface) and f.surface.has_boundaries
        or isinstance(f.surface, (Cylinder, Cone)) and (len(faces) > 1 or f.holes)
        for f in faces.values()))


def _face_groups(geometry):
    """Connected components of actual shared topological edges."""
    faces_of_edge = defaultdict(set)
    for face, surface in geometry.faces.items():
        for loop in (surface.loop, *surface.holes):
            for use in loop:
                faces_of_edge[use.edge].add(face)
    neighbours = {f: set() for f in geometry.faces}
    for edge, owners in faces_of_edge.items():
        _require(len(owners) <= 2, f"Source edge {edge} has more than two incident shell faces.")
        if len(owners) == 2:
            a, b = owners
            neighbours[a].add(b)
            neighbours[b].add(a)
    groups, remaining = [], set(neighbours)
    for face in sorted(neighbours):
        if face in remaining:
            stack, component = [face], set()
            remaining.remove(face)
            while stack:
                item = stack.pop()
                component.add(item)
                adjacent = neighbours[item] & remaining
                remaining -= adjacent
                stack.extend(adjacent)
            groups.append(sorted(component))
    return groups


def _inputs(project, settings):
    from .document import canonical_hash

    g = project.geometry
    _require(general_family(project), "No general authored surface in the selected source.")
    _require(not (g.members or g.junctions or g.attachments or project.beam_edges or project.beam_offsets
                  or project.read_only_reason or project.sheet_join_intent_problems()
                  or project._feature_materialization_problems()), "General surface update requires resolved shell owners.")
    _require(settings.element_order == "linear", "General surface source coverage requires linear shell elements.")
    records = []
    doc = rm._document(g)
    groups = _face_groups(g)
    group_of = {f: group for group in groups for f in group}
    sheet_faces = {}
    sheets_by_face = defaultdict(list)
    for sheet_id, sheet in g.sheets.items():
        covered = {g.face_uses[use].face_id for use in sheet.face_use_ids}
        _require(not covered or covered <= set(group_of[min(covered)]),
                 f"Source sheet {sheet_id} spans disconnected shell components.")
        sheet_faces[sheet_id] = sorted(covered)
        for face in covered:
            sheets_by_face[face].append(sheet_id)
    for face in g.faces:
        owned_sheets = sheets_by_face[face]
        record = rm._component_record(g, doc, [face], settings,
                                     sheets_in_scope=[s for s in owned_sheets if len(sheet_faces[s]) == 1])
        if len(group_of[face]) > 1:
            from anygeometry import source_tensor_chart
            from anygeometry.nurbs import NurbsSurface
            surface = g.faces[face]
            _require(not surface.holes and surface.parameterization is None and len(surface.loop) == 4
                     and len({u.edge for u in surface.loop}) == 4
                     and (type(surface.surface) is NurbsSurface or source_tensor_chart(g, face) is not None),
                     "Shared-edge source networks require four distinct outer edges and no inner loops.")
            record["network"] = group_of[face]
            record["sheets"] = {str(s): sheet_faces[s] for s in owned_sheets}
            record["loop"] = [(u.edge, u.forward, g.edges[u.edge].start, g.edges[u.edge].end) for u in surface.loop]
        records.append(record)
    for key, owner in (("edges", g.edges), ("vertices", g.vertices)):
        membership = {}
        for row in records:
            group = group_of[row["faces"][0]]
            _require(all(membership.setdefault(item, group) == group for item in row[key]),
                     "Source components must have disjoint complete edge and vertex ownership.")
        _require(set(membership) == set(owner), "Source components have unowned edges or vertices.")
    _require({e for e, _ in settings.overrides} <= set(g.edges), "A requested division names an unowned edge.")
    model = rm._surface_model_inputs(project)
    model.pop("geometry")
    # The exact serialized face, oriented trim, support, edges and control
    # vertices are bound per face below. Every other geometry input remains global.
    geometry_globals = {k: v for k, v in doc["raw"].items()
                         if k not in ("revision", "faces", "edges", "vertices", "id_state", "checksum")}
    structural = dict(geometry_globals["structural"])
    face_uses = structural.pop("face_uses")
    coedges = structural.pop("coedges")
    geometry_globals["structural"] = structural
    meshing = rm._fixed_meshing_inputs(model.pop("meshing"))
    zones = meshing.pop("refinements")
    fixed = {key: canonical_hash(value) for key, value in model.items()}
    fixed.update({f"geometry.{key}": canonical_hash(value) for key, value in geometry_globals.items()})
    fixed["meshing"] = canonical_hash(meshing)
    fixed["settings"] = replace(settings, regional=None, target_size=1., overrides=(), execution_policy=None).input_hash
    local = {}
    uses_by_face = defaultdict(list)
    face_by_use = {}
    coedges_by_face = defaultdict(list)
    for use in face_uses:
        uses_by_face[use["face_id"]].append(use)
        face_by_use[use["id"]] = use["face_id"]
    for edge in coedges:
        coedges_by_face[face_by_use[edge["face_use_id"]]].append(edge)
    for row in records:
        face, = row["faces"]
        occurrence = {"definition": row["fingerprint"], "face_uses": uses_by_face[face],
                      "coedges": coedges_by_face[face]}
        local[str(face)] = {"definition": canonical_hash(occurrence), "divisions": row["local"]["overrides"], "sizes": []}
    points = []
    for zone in zones:
        ref = zone.get("ref")
        if zone.get("mode") == "override" and ref and ref["kind"] == "face" and str(ref["id"]) in local:
            local[str(ref["id"])]["sizes"].append(zone)
        elif zone.get("mode", "refine") == "refine" and ref is None and zone.get("center") is not None:
            points.append(zone)
        else:
            raise GeneralSurfaceUnsupported("The refinement lacks a supported point or unique face owner.")
    network = {"components": groups} if any(len(group) > 1 for group in groups) else {}
    return records, {**network, "fixed": fixed, "document": str(project.document_id), "model": str(g.model_id),
                     "revision": g.revision, "target": float(settings.target_size).hex(), "faces": local,
                     "zones": points}


def _mesh_owners(mesh, records):
    _require(not (mesh.beams or mesh.couplings or mesh.automatic_intersections or mesh.automatic_beam_connections
                  or mesh.automatic_shell_connections or mesh.declared_plate_junction_edges or mesh.offset_nodes_of_edge),
             "Structural connections or offset nodes require a distinct generation receipt.")
    rm._closure_gate(mesh, records)
    claimed_nodes, claimed_cells = {}, set()
    shared_boundaries, users, boundary_users = defaultdict(list), defaultdict(set), defaultdict(set)
    station_owner = {}
    for row in records:
        cells, nodes = rm._component_mesh(mesh, row["faces"])
        face, = row["faces"]
        group = row.get("network", [face])
        _require(all(claimed_nodes.setdefault(n, group) == group for n in nodes)
                 and claimed_cells.isdisjoint(cells), "Different source components share generated cells or nodes.")
        _require(all(set(mesh.nodes_of_edge.get(edge, ())) <= nodes for edge in row["edges"])
                 and all(mesh.nodes_of_edge.get(edge) for edge in row["edges"]),
                 "A source edge has missing or foreign shell stations.")
        if "network" in row:
            boundary = _cut_boundary(mesh, cells)
            expected = set()
            for edge, forward, start, end in row["loop"]:
                ids = mesh.nodes_of_edge[edge]
                _require(len(ids) >= 2 and len(set(ids)) == len(ids) and ids[0] == mesh.node_of_vertex.get(start)
                         and ids[-1] == mesh.node_of_vertex.get(end), f"Edge {edge} lacks ordered source stations.")
                sequence = ids if forward else ids[::-1]
                segments = set(zip(sequence, sequence[1:]))
                expected.update(segments)
                reverse = {(b, a) for a, b in segments}
                shared_boundaries[edge].append((face, boundary & (segments | reverse)))
                for index, node in enumerate(ids):
                    if index == 0 or index == len(ids) - 1:
                        source_station = ("vertex", start if index == 0 else end)
                    else:
                        source_station = ("edge", edge, index)
                    _require(station_owner.setdefault(node, source_station) == source_station,
                             f"Node {node} conflates different boundary stations.")
                    boundary_users[node].add(face)
            _require(expected == boundary or {(b, a) for a, b in expected} == boundary,
                     f"Face {face} boundary does not match its ordered source edge stations.")
            for n in nodes:
                users[n].add(face)
        claimed_cells.update(cells)
    interfaces = {}
    for edge, sides in shared_boundaries.items():
        if len(sides) == 2:
            first, second = sides[0][1], sides[1][1]
            _require(first == {(b, a) for a, b in second}, f"Shared edge {edge} has inconsistent shell orientation.")
            interfaces[str(edge)] = {"faces": sorted(face for face, _ in sides),
                                     "stations": len(mesh.nodes_of_edge[edge]), "segments": len(first)}
    _require(all(owners <= boundary_users[n] for n, owners in users.items() if len(owners) > 1),
             "A node joins source faces without a common authored station or vertex.")
    _require(set(claimed_nodes) == set(mesh.nodes) and claimed_cells == element_numbers(mesh),
             "A generated node or cell has no unambiguous source face.")
    return interfaces


def _source_proof(project, mesh):
    from anygeometry import to_dict
    from anymesher import requires_material_source_transfer

    g = project.geometry
    _require(str(mesh.geometry_model_id) == str(g.model_id) and mesh.geometry_revision == g.revision,
             "Mesh model/revision differs from the current source geometry.")
    _require(requires_material_source_transfer(g, mesh),
             "This ordinary full generator supplies no live material-cell proof for this surface or trim.")
    owner = getattr(getattr(getattr(mesh, "boundary_registry", None), "view", None), "source", None)
    _require(owner is not None and (owner is g or rm._digest(to_dict(owner)) == rm._digest(to_dict(g))),
             "General surface capture requires live source-cell publication for the exact source geometry.")
    try:
        from anymesher import validate_numbered_source_mesh
        validate_numbered_source_mesh(mesh, source_geometry=owner)
    except (ImportError, ValueError, RuntimeError) as error:
        raise GeneralSurfaceUnsupported(f"Live source-cell publication could not be verified: {error}") from error
    return owner


def _capture(project, settings, mesh):
    records, source = _inputs(project, settings)
    source_owner = _source_proof(project, mesh)
    shared = _mesh_owners(mesh, records)
    baseline = {"schema": SCHEMA, "version": VERSION, "pins": rm._pins(), "source": source,
                "mesh": _published_mesh_digest(mesh)}
    if shared:
        baseline["shared_interfaces"] = shared
    from anymesher.regional_source_faces import capture_reusable_source_faces
    proposals = capture_reusable_source_faces(source_owner, mesh)
    baseline["reusable_faces"] = proposals
    if proposals:
        from anymesher.regional_source_faces import capture_source_face_identity_tables
        tables = capture_source_face_identity_tables(source_owner, mesh, tuple(map(int, proposals)))
        shells = mesh.shells
        for key, identities in tables.items():
            _validate_parent_rows(mesh, int(key), proposals[key]["data"], identities, shells)
        if len(records) == 1:
            baseline["point_patch_parent"] = next(iter(tables.values()))
        else:
            baseline["patch_parents"] = tables
    return {**baseline, "digest": rm._digest(baseline)}


def capture_general_baseline(project, settings, mesh):
    try:
        return _capture(project, settings, mesh)
    except (GeneralSurfaceUnsupported, rm._Ineligible, KeyError, ImportError, ValueError):
        return None


@dataclass(frozen=True)
class GeneralPlan:
    description: str
    regenerated: tuple[int, ...]
    retained: tuple[int, ...]
    reuse_candidates: tuple[int, ...] = ()
    applicable: bool = True
    patched_face: int | None = None
    replaced_elements: tuple[int, ...] = ()
    interface_nodes: tuple[int, ...] = ()
    _patch: dict | None = field(default=None, repr=False, compare=False)
    components: tuple[tuple[int, ...], ...] = ()
    removes_material: bool = False
    hole_edges: tuple[int, ...] = ()

    @property
    def is_noop(self):
        return not self.regenerated and self.patched_face is None

    @property
    def impact(self):
        impact = {"regenerated": [[f] for f in self.regenerated], "retained": [[f] for f in self.retained],
                  "added": [], "removed": []}
        if self.patched_face is not None:
            impact.update(patched_face=self.patched_face, replaced_elements=list(self.replaced_elements),
                          interface_nodes=list(self.interface_nodes))
        if self.hole_edges:
            impact["hole_edges"] = list(self.hole_edges)
        if self.components:
            impact["regenerated"] = [list(c) for c in self.components if c[0] in self.regenerated]
            impact["retained"] = [list(c) for c in self.components if c[0] in self.retained]
        return impact


def _validate_parent_rows(mesh, face, data, identities, shells):
    """Check owner row correspondence against every oriented cell on the real parent."""
    nodes, cells = identities["nodes"], identities["cells"]
    face_cells, face_nodes = rm._component_mesh(mesh, [face])
    _require(len(nodes) == len(data["node_rows"]) == len(face_nodes) and set(nodes) == face_nodes
             and len(cells) == len(data["cells"]) == len(face_cells) and set(cells) == set(face_cells),
             "Point patch parent rows do not bijectively cover the published face.")
    _require(all(np.array_equal(mesh.nodes[node], row[1]) for node, row in zip(nodes, data["node_rows"]))
             and all(tuple(nodes[n] for n in conn) == shells[e] for conn, e in zip(data["cells"], cells)),
             "Point patch parent rows differ from the oriented source cells or their coordinates.")


def _point_plan(project, settings, baseline, current, controls, check):
    from anymesher.errors import MeshError
    from anymesher.regional_source_patch import plan_source_face_point_patch

    previous = baseline["source"]
    if (len(current["faces"]) != 1 or previous["zones"] or len(current["zones"]) != 1
            or any(previous[k] != current[k] for k in ("faces", "revision", "target"))
            or len(project.refinements) != 1 or "point_patch_parent" not in baseline):
        return None
    face, = project.geometry.faces
    identities = baseline["point_patch_parent"]
    try:
        proposal = baseline["reusable_faces"][str(face)]
        patch = plan_source_face_point_patch(
            project.geometry, proposal, project.refinements[0], target_size=settings.target_size,
            strategy=rm._strategy(project, settings), recombine=controls.recombine, cancellation_check=check)
        if patch is None:
            return None
        _require(patch["face"] == face, "Point patch belongs to a different source face.")
        retired = tuple(sorted(identities["cells"][r] for r in patch["replaced_cell_rows"]))
        loop = tuple(identities["nodes"][r] for r in patch["interface_rows"])
        _require(0 < len(retired) == len(set(retired)) < len(identities["cells"])
                 and len(loop) == len(set(loop)) >= 3, "Point patch has an invalid parent cut or interface.")
    except (MeshError, IndexError, KeyError, ValueError, TypeError) as error:
        raise GeneralSurfaceUnsupported(f"Point patch source or parent interface refused: {error}") from error
    return GeneralPlan(f"Replace {len(retired)} parent cells on face {face} for the point sizing change. "
                       f"Keep the {len(loop)} interface nodes and all cells outside the cut.",
                       (), (), patched_face=face, replaced_elements=retired, interface_nodes=loop, _patch=patch)


def _patch_parent(baseline, face):
    single = baseline.get("point_patch_parent")
    return single if single is not None else baseline.get("patch_parents", {}).get(str(face))


def _trim_plan(project, settings, baseline, current, controls, check, face, kept):
    previous = baseline["source"]
    identity = _patch_parent(baseline, face)
    if (identity is None or previous["revision"] == current["revision"]
            or previous["target"] != current["target"] or previous["zones"] or current["zones"]
            or str(face) not in baseline["reusable_faces"]):
        return None
    holes = project.geometry.faces[face].holes
    if len(holes) != 1:
        return None
    key = str(face)
    former, now = previous["faces"][key], current["faces"][key]
    rim = {use.edge for use in holes[0]}
    if (former["sizes"] or now["sizes"] or dict(former["divisions"]) !=
            {e:n for e,n in now["divisions"] if e not in rim}):
        return None
    from anymesher.regional_source_trim import plan_source_face_trim
    from .mesh_controls import StructuredMeshControls

    stored = project.native_mesh_settings
    parameters = {} if stored is None else dict(stored.parameters)
    policy = (dict(settings.quality_policy) if settings.quality_policy_explicit else
              {k.removeprefix("mesh_quality_"): v for k,v in parameters.items() if k.startswith("mesh_quality_")})
    quality = {}
    if policy:
        controls_for_quality = settings.structured_controls or StructuredMeshControls.from_settings(stored)
        options = controls_for_quality.owner_options(preference=settings.structure_preference, quality_policy=policy)
        bounds = options.quality_policy
        quality = dict(min_scaled_jacobian=bounds.minimum_scaled_jacobian, max_aspect_ratio=bounds.maximum_aspect_ratio,
                       min_angle=bounds.minimum_angle, max_angle=bounds.maximum_angle, max_warpage=bounds.maximum_warpage,
                       max_element_growth=options.max_element_growth)
    patch = plan_source_face_trim(project.geometry, baseline["reusable_faces"][key],
        target_size=settings.target_size, overrides=settings.overrides, strategy=rm._strategy(project, settings),
        recombine=controls.recombine, quality=quality, cancellation_check=check)
    if patch is None:
        return None
    _require(patch["face"] == face and patch["proposal_digest"] == baseline["reusable_faces"][key]["digest"],
             "Trim proposal belongs to a different parent source.")
    retired = tuple(sorted(identity["cells"][r] for r in patch["replaced_cell_rows"]))
    loop = tuple(identity["nodes"][r] for r in patch["interface_rows"])
    _require(len(set(loop)) == len(loop) >= 3 and 0 < len(set(retired)) == len(retired) < len(identity["cells"]),
             "Trim proposal does not define an interior cut in the parent.")
    if patch.get("annulus") is True:
        description = (f"Replace {len(retired)} parent cells on face {face}. Remesh the material between "
                       f"the {len(loop)} fixed interface nodes and the new four-sided hole. "
                       "Keep every exterior cell.")
        hole_edges = tuple(sorted(rim))
    else:
        description = (f"Remove {len(retired)} complete parent cells inside the new hole on face {face}. "
                       f"Keep its {len(loop)} boundary nodes and every exterior cell.")
        hole_edges = ()
    if kept:
        description += f" Reuse the unchanged source faces {list(kept)}."
    return GeneralPlan(description,
                       (), kept, kept, patched_face=face, replaced_elements=retired, interface_nodes=loop,
                       _patch=patch, removes_material=True, hole_edges=hole_edges)


def plan_general_update(project, settings, baseline, *, cancellation_check=None):
    check = rm._check_fn(cancellation_check)
    check("validate general surface update scope")
    _require(baseline.get("schema") == SCHEMA and baseline.get("version") == VERSION
             and baseline.get("digest") == rm._digest({k: v for k, v in baseline.items() if k != "digest"})
             and baseline.get("pins") == rm._pins(), "General surface baseline or owner build differs.")
    try:
        _, current = _inputs(project, settings)
    except (rm._Ineligible, KeyError, ValueError) as error:
        raise GeneralSurfaceUnsupported(f"General surface scope is unproved: {error}") from error
    prior = baseline["source"]
    differences = sorted(k for k in set(current["fixed"]) | set(prior["fixed"])
                         if current["fixed"].get(k) != prior["fixed"].get(k))
    _require(not differences, f"General surface update encountered changed global inputs {differences}.")
    fixed = ("document", "model")
    _require(all(current[k] == prior[k] for k in fixed) and current["faces"].keys() == prior["faces"].keys(),
             "General surface update requires the same named face owners, structural ownership and global settings.")
    groups = tuple(tuple(g) for g in current.get("components", ()))
    _require(current.get("components") == prior.get("components"),
             "The authored source network changed connectivity; regenerate the complete mesh.")
    global_change = any(current[k] != prior[k] for k in ("target", "zones"))
    changed = tuple(sorted(int(f) for f in current["faces"] if global_change or current["faces"][f] != prior["faces"][f]))
    definitions_changed = any(current["faces"][f]["definition"] != prior["faces"][f]["definition"]
                              for f in current["faces"])
    _require(not definitions_changed or current["revision"] != prior["revision"],
             "Authored geometry changed without a new source revision.")
    if current["revision"] != prior["revision"] and not definitions_changed:
        # A new revision with no localized geometric delta still needs current
        # generation authority. Its old mesh cannot be returned as a no-op.
        changed = tuple(sorted(project.geometry.faces))
    if groups:
        selected = set(changed)
        changed = tuple(sorted(f for group in groups if selected.intersection(group) for f in group))
    kept = tuple(sorted(set(project.geometry.faces) - set(changed)))
    if not changed:
        return GeneralPlan("No source or sizing change.", (), kept, components=groups)
    from anymesher.regional_source_faces import reusable_face_for_request
    from .mesh_controls import MeshControls
    controls = settings.controls or MeshControls.from_settings(project.native_mesh_settings)
    proposals = baseline.get("reusable_faces", {})
    candidates = tuple(f for f in kept if str(f) in proposals and reusable_face_for_request(
        proposals[str(f)], rm._strategy(project, settings), controls.recombine))
    if definitions_changed and len(changed) == 1 and candidates == kept:
        trim = _trim_plan(project, settings, baseline, current, controls, check, changed[0], kept)
        if trim is not None:
            return trim
    points_changed = current["zones"] != prior["zones"]
    if points_changed:
        point_plan = _point_plan(project, settings, baseline, current, controls, check)
        if point_plan is not None:
            return point_plan
    if candidates:
        operation = f"Reuse the stored cells of source faces {list(candidates)} after fresh owner validation."
    else:
        operation = "Regenerate the full source mesh."
    if points_changed:
        operation += (" A fixed cut requires one added point zone with certified size and influence "
                      "inside an unchanged source face's interior Q4 neighbourhood.")
    if groups:
        changed_groups = [list(g) for g in groups if g[0] in changed]
        operation += f" Expand the edit to complete shared-edge components {changed_groups}."
    return GeneralPlan(f"{operation} Replace whole faces {list(changed)} because no fixed interior cut is proved. "
                       f"Retain exact IDs on faces {list(kept)} after their cells and associations match.",
                       changed, kept, candidates, components=groups)

def _retained_ids(old, candidate, records, plan):
    nodes, elements = {}, {}
    old_shells, fresh_shells = old.shells, candidate.shells
    for record in records:
        face, = record["faces"]
        if face not in plan.retained:
            continue
        before = old.elements_of_face[face]
        after = candidate.elements_of_face[face]
        _require(len(before) == len(after), f"Retained face {face} changed cell count.")
        for new, previous in zip(after, before, strict=True):
            _require((previous in old.quads) == (new in candidate.quads), f"Retained face {face} changed cell type.")
            prev_conn = old_shells[previous]
            new_conn = fresh_shells[new]
            _require(len(prev_conn) == len(new_conn), f"Retained face {face} changed connectivity.")
            elements[new] = previous
            for a, b in zip(new_conn, prev_conn, strict=True):
                _require(nodes.setdefault(a, b) == b and np.array_equal(candidate.nodes[a], old.nodes[b]),
                         f"Retained face {face} changed oriented connectivity or coordinates.")
    _require(len(nodes) == len(set(nodes.values())), "Retained face nodes have ambiguous identity.")
    return nodes, elements


def _cut_boundary(mesh, elements):
    """Return the oriented edges of a cell patch and reject non-manifold interiors."""
    edges = defaultdict(list)
    shells = mesh.shells
    for element in elements:
        cell = shells[element]
        for a, b in zip(cell, cell[1:] + cell[:1]):
            edges[min(a, b), max(a, b)].append((a, b))
    boundary = set()
    for sides in edges.values():
        if len(sides) == 1:
            boundary.add(sides[0])
        else:
            _require(len(sides) == 2 and sides[0] == sides[1][::-1],
                     "Shell cells have overlapping or inconsistently oriented interior edges.")
    return boundary


def _patch_ids(old, fresh, baseline, plan, stats):
    """Translate receipt rows only after proving both sides of the advertised fixed cut."""
    face = plan.patched_face
    identity = _patch_parent(baseline, face)
    old_nodes, old_cells = identity["nodes"], identity["cells"]
    data = baseline["reusable_faces"][str(face)]["data"]
    issued = stats.get("trimmed_faces" if plan.removes_material else "patched_faces", {})
    _require(set(issued) == {face}, "Fixed patch received no current final owner proof for the selected face.")
    receipt = issued[face]
    cut = set(plan._patch["replaced_cell_rows"])
    _require(cut == set(receipt["replaced_cell_rows"])
             and tuple(receipt["interface_rows"]) == tuple(plan._patch["interface_rows"]),
             "Issued point patch differs from the previewed cells or interface.")
    kept = set(range(len(old_cells))) - cut
    rows, surviving = receipt["kept_cell_rows"], receipt["old_node_rows"]
    # A station solely inside the retired cells gets a fresh permanent number
    # even when the patch re-emits its exact source position.
    shared = {n for r in kept for n in data["cells"][r]}
    if plan.hole_edges:
        # A grown cut can include part of the original outer rim. Its fixed
        # stations still belong to the exterior even without an uncut cell.
        shared.update(plan._patch["interface_rows"])
    _require(set(rows) == kept and shared <= set(surviving) <= set(range(len(old_nodes)))
             and len(set(rows.values())) == len(rows) and len(set(surviving.values())) == len(surviving),
             "Point patch receipt lacks a unique record for every retained row.")
    nodes = {surviving[r]: old_nodes[r] for r in shared}
    cells = {new: old_cells[r] for r, new in rows.items()}
    old_shells, new_shells = old.shells, fresh.shells
    fresh_elements = set(fresh.elements_of_face[face])
    _require(set(cells) <= fresh_elements and all(np.array_equal(fresh.nodes[a], old.nodes[b])
             for a, b in nodes.items()), "Point patch moved a retained source node or cell to another owner.")
    _require(all(tuple(nodes[n] for n in new_shells[a]) == old_shells[b]
                 and fresh.activity.get(a) == old.activity.get(b) for a, b in cells.items()),
             "Point patch changed a retained oriented source cell or its activity.")
    before = _cut_boundary(old, plan.replaced_elements)
    original = _cut_boundary(old, old.elements_of_face[face])
    if plan.hole_edges:
        added = fresh_elements - set(cells)
        new_rows = receipt["new_node_rows"]
        hole_rim = plan._patch["hole_rim"]
        _require(receipt.get("annulus") is True and added and len(added) == len(receipt["new_cell_ids"])
                 and added == set(receipt["new_cell_ids"]) and set(new_rows.values()).isdisjoint(surviving.values())
                 and len(set(new_rows.values())) == len(new_rows) and all(r >= len(old_nodes) for r in new_rows)
                 and sorted(e for e, _ in hole_rim) == list(plan.hole_edges)
                 and all([new_rows[r] for _, r in stations] == fresh.nodes_of_edge[e] for e, stations in hole_rim),
                 "Trim annulus receipt differs from its new cells or authored hole stations.")
        side = _cut_boundary(fresh, added)
        after = {(nodes[a], nodes[b]) for a,b in side if a in nodes and b in nodes}
        hole_stations = {n for e in plan.hole_edges for n in fresh.nodes_of_edge[e]}
        _require(all((a in nodes and b in nodes) or (a in hole_stations and b in hole_stations)
                     for a,b in side), "Trim annulus exposes an edge outside the fixed interface and hole.")
    elif plan.removes_material:
        _require(fresh_elements == set(cells), "The trim generator emitted cells inside the proposed cut.")
        boundary = {(nodes[a], nodes[b]) for a,b in _cut_boundary(fresh, fresh_elements)}
        _require(boundary == original | {(b,a) for a,b in before},
                 "The trimmed face does not have exactly its original boundary and the new hole.")
        after = {(b,a) for a,b in boundary - original}
    else:
        after = {(nodes[a], nodes[b]) for a, b in _cut_boundary(fresh, fresh_elements - set(cells))}
    loop = plan.interface_nodes
    ring = set(zip(loop, loop[1:] + loop[:1]))
    inner_cut = before - original if plan.hole_edges else before
    _require((before == ring or before == {(b, a) for a, b in ring}) and after == before
             and {(b, a) for a, b in inner_cut} <= _cut_boundary(old, list(cells.values())),
             "Point patch did not close against the complete oriented retained interface.")
    return nodes, cells


def _verify_trim_associations(geometry, old, mesh, plan, baseline):
    hole, = geometry.faces[plan.patched_face].holes
    hole_edges = {u.edge for u in hole}
    hole_vertices = {geometry.oriented_start_vertex(u) for u in hole}
    for parent, child, allowed in ((old.nodes_of_edge, mesh.nodes_of_edge, hole_edges),
                                   (old.elements_of_edge, mesh.elements_of_edge, hole_edges),
                                   (old.node_of_vertex, mesh.node_of_vertex, hole_vertices)):
        _require(all(k in child and child[k] == value for k, value in parent.items())
                 and child.keys() - parent.keys() <= allowed,
                 "Trim publication changed an association outside the authored hole.")
    directed = set()
    ordered_nodes = []
    for use in hole:
        edge = geometry.edges[use.edge]
        chain = mesh.nodes_of_edge[edge.id]
        _require(len(chain) >= 2 and chain[0] == mesh.node_of_vertex[edge.start]
                 and chain[-1] == mesh.node_of_vertex[edge.end],
                 "New trim edge is missing its endpoint ownership.")
        ordered = chain if use.forward else chain[::-1]
        directed.update(zip(ordered, ordered[1:]))
        ordered_nodes.extend(ordered[:-1])
    _require(len(set(ordered_nodes)) == len(ordered_nodes) == len(directed)
             and directed == set(zip(ordered_nodes, ordered_nodes[1:] + ordered_nodes[:1])),
             "Authored trim edge stations do not make one simple closed loop.")
    if plan.hole_edges:
        from fractions import Fraction

        def oriented_area(loop):
            return sum(Fraction(a[0]) * Fraction(b[1]) - Fraction(a[1]) * Fraction(b[0])
                       for a,b in zip(loop, loop[1:] + loop[:1]))

        data = baseline["reusable_faces"][str(plan.patched_face)]["data"]
        outer_cell = data["cells"][plan._patch["replaced_cell_rows"][0]]
        parent_uv = [data["node_rows"][row][0] for row in outer_cell]
        hole_uv = [geometry.edges[u.edge].curve.pcurve.exact_point(0. if u.forward else 1.) for u in hole]
        winding = oriented_area(parent_uv) * oriented_area(hole_uv)
        hole_sides = directed if winding < 0 else {(b,a) for a,b in directed}
        exterior = _cut_boundary(old, old.elements_of_face[plan.patched_face])
        boundary = _cut_boundary(mesh, mesh.elements_of_face[plan.patched_face])
        _require(winding != 0 and old.nodes.keys().isdisjoint(ordered_nodes) and boundary == exterior | hole_sides,
                 "Trim annulus does not have exactly its preserved exterior and oriented new material boundary.")
        return
    cut = _cut_boundary(old, plan.replaced_elements)
    _require(directed == {(b,a) for a,b in cut} or directed == cut,
             "The authored trim stations differ from the previewed parent cell cut.")


def _verify_sizes(project, old_mesh, mesh, settings, records, prior, check, plan):
    from anymesher.refinement import SizeField

    g = project.geometry
    for edge, count in settings.overrides:
        _require(len(mesh.nodes_of_edge.get(edge, ())) == count + 1,
                 f"Requested divisions for edge {edge} were not achieved.")
    field = SizeField(g, settings.target_size, project.refinements, active_faces=tuple(g.faces))
    # Measure actual physical edge lengths. The target is nominal for unconstrained
    # triangles; certify its achieved local scale, in addition to each exact override.
    measured = {}
    met_zones = set()
    active_zones = {i for i, zone in enumerate(project.refinements)
                    if zone.center is not None and zone.size < settings.target_size}
    for record in records:
        face, = record["faces"]
        check("verify achieved general surface sizing")
        view = field.face_view(face)
        centers, edges = [], []
        for eid in mesh.elements_of_face[face]:
            xyz = np.asarray([mesh.nodes[n] for n in mesh.corners_of(eid)])
            centers.append(np.mean(xyz, axis=0))
            edges.append(np.max(np.linalg.norm(xyz - np.roll(xyz, 1, axis=0), axis=1)))
        sizes = np.asarray(edges)
        tol = g.tolerance.effective_length(float(max(edges)))
        generated = sizes
        if plan.hole_edges and face == plan.patched_face:
            old_ids = old_mesh.shells.keys()
            generated = sizes[[e not in old_ids for e in mesh.elements_of_face[face]]]
        pure_removal = plan.removes_material and not plan.hole_edges and plan.patched_face == face
        if face not in plan.retained and not pure_removal:
            _require(np.any(generated <= view.target_size + tol),
                     f"No generated cell on face {face} meets its requested {view.target_size:g} size.")
        previous = prior["faces"][str(face)]["sizes"]
        previous_target = (min(float(r["size"]) for r in previous) if previous else float.fromhex(prior["target"]))
        if view.target_size < previous_target and max(sizes) > view.target_size + tol:
            before = []
            for e in old_mesh.elements_of_face[face]:
                xyz = np.asarray([old_mesh.nodes[n] for n in old_mesh.corners_of(e)])
                before.append(float(np.max(np.linalg.norm(xyz - np.roll(xyz, 1, axis=0), axis=1))))
            _require(np.mean(sizes) + tol < np.mean(before),
                     f"Face {face} did not get finer after reducing its nominal target.")
        measured[str(face)] = {"target": view.target_size, "cells": len(sizes),
                               "mean_longest_edge": float(np.mean(sizes)), "max_edge": float(np.max(sizes))}
        if plan.hole_edges and face == plan.patched_face:
            measured[str(face)]["new_cells"] = len(generated)
            measured[str(face)]["min_longest_new_edge"] = float(np.min(generated))
        for index in sorted(active_zones):
            zone = project.refinements[index]
            distances = np.linalg.norm(np.asarray(centers) - np.asarray(zone.center), axis=1)
            # Credit only a cell whose center is inside the actual constant-size
            # core. A missed footprint, including a point-only core, fails closed.
            core = distances <= zone.radius + tol
            if np.any(core):
                achieved = float(np.min(sizes[core]))
                _require(achieved <= zone.size + tol,
                         f"No generated cell in the point zone on face {face} achieves the requested size.")
                measured[str(face)].setdefault("point_zones", []).append({
                    "index": index, "requested_size": zone.size, "radius": zone.radius,
                    "cells_in_core": int(np.count_nonzero(core)), "min_longest_edge": achieved,
                })
                met_zones.add(index)
    _require(met_zones == active_zones, "No generated cell meets the requested point-zone footprint and size.")
    return measured


@dataclass(frozen=True)
class GeneralUpdateResult:
    mesh: object
    baseline: dict
    retained_nodes: frozenset[int]
    retained_elements: frozenset[int]
    plan: GeneralPlan
    provenance: dict


def generate_general_update(project, settings, baseline, old_mesh, *, node_high_water, element_high_water,
                            cancellation_check=None, mesh_cancellation=None):
    from anymesher import verify_mesh_quality
    from anymesher.errors import MeshError
    from .document import DocumentSession
    from .mesh_jobs import run_mesh_job
    from .mesh_numbering import number_mesh_with_maps

    check = rm._check_fn(cancellation_check)
    plan = plan_general_update(project, settings, baseline, cancellation_check=check)
    old = rm._decode(old_mesh)
    _require(_published_mesh_digest(old) == baseline["mesh"], "Parent content differs from its captured surface mesh.")
    if plan.is_noop:
        # Reopening the existing publication is handled by the session. A newly
        # published result still requires live material authority.
        _source_proof(project, old)
        return GeneralUpdateResult(old, dict(baseline), frozenset(old.nodes), frozenset(old.shells), plan, {})
    check("generate selected source cells")
    snapshot = DocumentSession(project).snapshot()
    detached = snapshot.thaw()
    try:
        if plan.patched_face is not None:
            proposal = baseline["reusable_faces"][str(plan.patched_face)]
            _validate_parent_rows(old, plan.patched_face, proposal["data"], _patch_parent(baseline, plan.patched_face),
                                  old.shells)
        reuse = nullcontext({"reused_faces": set()})
        if plan.reuse_candidates or plan.patched_face is not None:
            from anymesher.regional_source_faces import reuse_source_faces
            candidates = {str(f): baseline["reusable_faces"][str(f)] for f in plan.reuse_candidates}
            options = {}
            if plan.patched_face is not None:
                key = str(plan.patched_face)
                candidates[key] = baseline["reusable_faces"][key]
                options["trim_patches" if plan.removes_material else "point_patches"] = {key: plan._patch}
            reuse = reuse_source_faces(detached.geometry, candidates, **options)
        with reuse as stats:
            fresh = run_mesh_job(detached, snapshot.revision.model_hash, replace(settings, regional=None),
                                 lambda *_: None, mesh_cancellation if mesh_cancellation is not None
                                 else _Cancellation(check),
                                 **({"_regional_stage_check": check} if mesh_cancellation is not None else {})).mesh
        reused = set(stats["reused_faces"])
        _require(reused == set(plan.reuse_candidates), "The owner could not reuse every planned unchanged source face.")
        _require(fresh.hybrid_diagnostics.get("automation", {}).get("status") != "inspection_only",
                 "The full surface generator produced an inspection-only mesh.")
        records, _ = _inputs(detached, settings)
        _mesh_owners(fresh, records)
        source = _source_proof(detached, fresh)
        kept_nodes, kept_elems = _retained_ids(old, fresh, records, plan)
        if plan.patched_face is not None:
            patch_nodes, patch_elems = _patch_ids(old, fresh, baseline, plan, stats)
            for retained, patched in ((kept_nodes, patch_nodes), (kept_elems, patch_elems)):
                _require(retained.keys().isdisjoint(patched) and set(retained.values()).isdisjoint(patched.values()),
                         "An independent trim patch overlaps a retained source face.")
                retained.update(patched)
        allocation = raise_high_water(MeshNumbering(node_high_water, element_high_water), old)
        nodes = {**kept_nodes, **_fresh_ids(fresh.nodes.keys() - kept_nodes.keys(), allocation.node_high_water, False)}
        cells = {**kept_elems, **_fresh_ids(fresh.shells.keys() - kept_elems.keys(), allocation.element_high_water, False)}
        # The owner has certified each station and its parameter on the current
        # edge. Carry its exact order through permanent numbering, including
        # every interior station between the two topological vertices.
        numbered_rim = {e: [nodes[n] for n in fresh.nodes_of_edge[e]] for e in plan.hole_edges}
        numbered = number_mesh_with_maps(fresh, nodes, cells, source_geometry=source)
        if plan.patched_face is not None:
            if plan.removes_material:
                _verify_trim_associations(detached.geometry, old, numbered, plan, baseline)
                _require(all(numbered.nodes_of_edge[e] == chain for e, chain in numbered_rim.items()),
                         "Permanent trim station order differs from the source owner's certified edge.")
            else:
                _require(old.nodes_of_edge == numbered.nodes_of_edge
                         and old.elements_of_edge == numbered.elements_of_edge
                         and old.node_of_vertex == numbered.node_of_vertex,
                         "Numbered point patch changed a boundary or vertex association.")
            _require(set(old.shells) - set(numbered.shells) == set(plan.replaced_elements)
                     and set(old.nodes) & set(numbered.nodes) == set(kept_nodes.values())
                     and old.thickness_of_face == numbered.thickness_of_face,
                     "Numbered point patch changed a parent cell, boundary or association outside its preview.")
        for record in records:
            if record["faces"][0] in plan.retained:
                old_ids = rm._component_mesh(old, record["faces"])
                new_ids = rm._component_mesh(numbered, record["faces"])
                _require(rm._binding(old, record, *old_ids) == rm._binding(numbered, record, *new_ids)
                         and old.thickness_of_face.get(record["faces"][0]) == numbered.thickness_of_face.get(record["faces"][0]),
                         "A retained face changed mesh ownership, activity, stations, grid or thickness.")
        measured = _verify_sizes(detached, old, numbered, settings, records, baseline["source"], check, plan)
        quality = verify_mesh_quality(numbered)
        _require(quality.num_shell_elements == len(numbered.shells) and quality.min_scaled_jacobian > 0
                 and np.isfinite(quality.max_aspect_ratio), "Whole-mesh shell validity failed.")
        rm._validate_quality(detached, numbered, settings, check)
        updated = _capture(detached, settings, numbered)
    except (MeshError, rm._Ineligible, KeyError, IndexError, ValueError, TypeError) as error:
        raise GeneralSurfaceUnsupported(f"Source surface generation or validation refused: {error}") from error
    check("complete general surface update")
    provenance = {"generation_scope": "selected faces" if reused else "full source mesh",
                  "generated_faces": sorted(set(detached.geometry.faces) - reused),
                  "reused_source_faces": sorted(reused), "impact": plan.impact, "sizing": measured, "quality": quality.as_dict()}
    if plan.patched_face is not None:
        provenance.update(generation_scope="fixed interior trim" if plan.removes_material else "fixed interior cell patch", generated_faces=[],
                          patched_face=plan.patched_face, new_elements=sorted(numbered.shells.keys() - old.shells.keys()))
        if plan.hole_edges:
            provenance["generation_scope"] = "fixed trim annulus"
    if "shared_interfaces" in updated:
        provenance["shared_interfaces"] = updated["shared_interfaces"]
    updated["last_update"] = provenance
    updated["digest"] = rm._digest({k: v for k, v in updated.items() if k != "digest"})
    return GeneralUpdateResult(numbered, updated, frozenset(kept_nodes.values()), frozenset(kept_elems.values()),
                              plan, provenance)
