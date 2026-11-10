"""Update independent mapped plates with boundary beams or a moving transverse beam."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from . import regional_mesh as rm
from .mesh_controls import MeshControls


SCHEMA = "anyfem.regional-mapped-beam-shell"
VERSION = 1


class StructuralUpdateUnsupported(rm.RegionalUpdateUnsupported):
    """Source preparation or mesh ownership cannot establish this update."""


def _require(condition, reason):
    if not condition:
        raise StructuralUpdateUnsupported(reason)


def _context(project, settings, check):
    from anygeometry import plan_independent_components
    from anygeometry.curves import Straight
    from anygeometry.surfaces import CoonsSurface, Plane

    g = project.geometry
    _require(rm._strategy(project, settings) == "mapped" and settings.element_order == "linear"
             and settings.automation is None and not project.refinements and not project.beam_offsets,
             "Structural updates require linear mapped meshing, uniform sizing and zero beam offsets.")
    controls = settings.controls or MeshControls.from_settings(project.native_mesh_settings)
    _require(controls.certification_mode == "interactive" and not project.read_only_reason
             and not project.sheet_join_intent_problems() and not project._feature_materialization_problems(),
             "Structural regional preparation requires a resolved editable project in interactive mode.")
    native = project.native_mesh_settings
    if native:
        _require(not native.controls and not dict(native.parameters).get("geometry_approximation_error"),
                 "Scoped native controls or geometric approximation require full preparation.")
    beam_edges = set(project.beam_edges)
    _require(not g.attachments and not g.junctions,
             "Explicit junctions and attachments need source-transfer authority for their connections.")
    _require(all(len(f.loop) == 4 and len(f.corners) == 4 and not f.holes and f.parameterization is None
                 and (type(f.surface) is Plane or type(f.surface) is CoonsSurface and f.surface.bottom is None)
                 for f in g.faces.values()) and all(type(e.curve) is Straight for e in g.edges.values()),
             "Only straight four-sided mapped plate faces are supported.")
    free = len(beam_edges) == 1 and not g.faces_using_edge(next(iter(beam_edges)))
    _require(all((free or len(g.faces_using_edge(e)) == 1) and len(g.members_using_edge(e)) == 1 for e in beam_edges),
             "Beam connectivity must be proved by a source edge shared with exactly one plate. "
             "An interior or newly imprinted intersection requires a full rebuild.")
    beam_of_member = {next(iter(g.members_using_edge(e))): e for e in beam_edges}
    _require(set(beam_of_member) == set(g.members) and len(beam_of_member) == len(beam_edges)
             and all(len(m.edge_use_ids) == 1 for m in g.members.values()),
             "Each active beam must have one uniquely owned source edge.")
    partition = plan_independent_components(
        g, face_ids=tuple(g.faces), member_ids=tuple(g.members), edge_ids=sorted(beam_edges),
        separation=float(settings.target_size), allow_untrimmed_bilinear=True,
        cancellation_check=lambda stage: check(stage) or False)
    _require(partition.certified and len(partition.components) > 1,
             "ANYgeometry could not certify two independent structural components.")
    doc = rm._document(g)
    if free:
        return doc, _free_records(project, settings, doc, partition, beam_of_member)
    if not beam_edges:
        _require(sum(len(c.face_ids) == 2 for c in partition.components) == 1
                 and all(1 <= len(c.face_ids) <= 2 for c in partition.components),
                 "A T-junction update needs one isolated pair of source plates.")
    records = []
    for component in partition.components:
        faces = sorted(component.face_ids)
        _require(not beam_edges or len(faces) == 1, "Multiple interacting plates require a new source-connectivity proof.")
        record = rm._component_record(g, doc, faces, settings)
        if len(faces) == 2:
            _require(len(record["sheets"]) == 2 and all(len(fs) == 1 for fs in record["sheets"].values()),
                     "A T-junction needs two independently owned source sheets.")
            record["tee"] = True
        record["members"] = {str(m): int(beam_of_member[m]) for m in component.member_ids}
        _require(set(component.edge_ids) == set(record["members"].values())
                 and set(component.edge_ids) <= set(record["edges"]),
                 "The geometry partition did not include each beam with its owning plate.")
        records.append(record)
    covered_edges = {e for r in records for e in r["edges"]}
    _require(covered_edges == set(g.edges),
             "Loose construction edges have no prepared structural-component owner.")
    _require({e for e, _ in settings.overrides} <= covered_edges,
             "A division override names an edge outside the structural components.")
    return doc, sorted(records, key=lambda r: r["faces"])


def _free_records(project, settings, doc, partition, beam_of_member):
    """Record individual source owners so either revision's interaction can expand the cut."""
    g = project.geometry
    member, edge = next(iter(beam_of_member.items()))
    axis = g.edges[edge]
    ends = {axis.start, axis.end}
    _require(not settings.overrides and all(len(c.face_ids) <= 1 for c in partition.components),
             "Beam crossing updates require independent plates and uniform sizing.")
    group, = [c for c in partition.components if member in c.member_ids]
    records = []
    for face in sorted(g.faces):
        r = rm._component_record(g, doc, [face], settings)
        _require(ends.isdisjoint(r["vertices"]) and len(r["sheets"]) == 1,
                 "Beam endpoints must be independent of every source plate.")
        r["members"] = {}
        records.append(r)
    _require({e for r in records for e in r["edges"]} | {edge} == set(g.edges)
             and {v for r in records for v in r["vertices"]} | ends == set(g.vertices),
             "Loose construction entities have no structural owner.")
    fixed = {k: v for k, v in doc["raw"].items() if k not in ("revision", "checksum", "vertices")}
    fixed["vertices"] = [{k: v for k, v in item.items() if k != "position" or item["id"] not in ends}
                         for item in doc["raw"]["vertices"]]
    contact = None
    tolerance = g.tolerance.effective_length(np.linalg.norm(g.vertex_position(axis.end) - g.vertex_position(axis.start)))
    if group.face_ids:
        face, = group.face_ids
        corners = np.array([g.vertex_position(g.oriented_start_vertex(use)) for use in g.faces[face].loop])
        sides = np.roll(corners, -1, axis=0) - corners
        normal = np.cross(sides[0], sides[1])
        scale = max(np.linalg.norm(sides, axis=1))
        _require(np.linalg.norm(normal) > g.tolerance.effective_area(scale),
                 "The candidate crossing plate has no resolved plane.")
        normal /= np.linalg.norm(normal)
        tolerance = max(tolerance, g.tolerance.effective_length(scale))
        a, b = (g.vertex_position(v) for v in (axis.start, axis.end))
        h0, h1 = np.dot(a - corners[0], normal), np.dot(b - corners[0], normal)
        _require(np.max(abs((corners - corners[0]) @ normal)) <= tolerance
                 and min(h0, h1) < -tolerance and max(h0, h1) > tolerance,
                 "The member must cross the plate plane with both ends clear of it.")
        contact = a + (b - a) * (h0 / (h0 - h1))
        _require(np.all(np.cross(sides, contact - corners) @ normal > tolerance * np.linalg.norm(sides, axis=1)),
                 "The member must cross the interior of one convex mapped plate.")
        contact = contact.tolist()
    records.append(dict(faces=[], edges=[edge], vertices=sorted(ends), sheets={},
                        members={str(member): edge}, near=list(group.face_ids),
                        contact=contact, tolerance=float(tolerance),
                        fixed=rm._digest([fixed, rm._assignment_maps(project)]),
                        fingerprint=rm._digest([doc["vertices"][v] for v in sorted(ends)])))
    return records


def _ownership(mesh, records):
    """Check element and node owners, including exact coincident beam/shell stations."""
    tee = next((r for r in records if r.get("tee")), None)
    _require(not mesh.couplings and not mesh.tris and not mesh.offset_nodes_of_edge
             and bool(mesh.declared_plate_junction_edges) == bool(tee) and not mesh.thickness_of_face
             and mesh.automatic_intersections == int(bool(tee)) and not mesh.automatic_shell_connections
             and all(v == 1.0 for v in mesh.activity.values()),
             "This mesh has a junction, offset, coupling, activity or shell family outside the proved scope.")
    if tee:
        _tee_joint(mesh, tee)
    faces, edges, sheets, members = {}, set(), {}, {}
    all_elements, all_nodes, owned = set(), set(), []
    for record in records:
        shell_ids, shell_nodes = rm._component_mesh(mesh, record["faces"])
        _require(all(e in mesh.quads for e in shell_ids), "Face is not entirely mapped quadrilaterals.")
        elements = set(shell_ids)
        faces.update({f: set(mesh.elements_of_face[f]) for f in record["faces"]})
        edges.update(record["edges"])
        for s, sheet_faces in record["sheets"].items():
            sheets[int(s)] = {e for f in sheet_faces for e in mesh.elements_of_face[f]}
        for label, edge in record["members"].items():
            member = int(label)
            beam_ids = set(mesh.elements_of_edge.get(edge, ()))
            beam_nodes = {n for e in beam_ids for n in mesh.beams.get(e, ())}
            edge_nodes = list(mesh.nodes_of_edge.get(edge, ()))
            axes = {tuple(sorted(mesh.beams[e])) for e in beam_ids if e in mesh.beams}
            segments = {tuple(sorted(pair)) for pair in zip(edge_nodes, edge_nodes[1:])}
            shell_sides = {tuple(sorted((a, b))) for e in shell_ids for a, b in
                           zip(mesh.quads[e], mesh.quads[e][1:] + mesh.quads[e][:1])}
            contact_ok = axes <= shell_sides and beam_nodes <= shell_nodes
            if "near" in record:
                overlaps = {f: beam_nodes & {n for e in elems for n in mesh.quads[e]}
                            for f, elems in faces.items()}
                contact_ok = (set(record["near"]) == {f for f, ns in overlaps.items() if ns}
                              and all(len(ns) <= 1 for ns in overlaps.values())
                              and all(np.linalg.norm(mesh.nodes[n] - record["contact"]) <= record["tolerance"]
                                      for ns in overlaps.values() for n in ns)
                              and mesh.automatic_beam_connections == len(record["near"]))
            _require(beam_ids and beam_ids == set(mesh.elements_of_member.get(member, ()))
                     and beam_ids <= mesh.beams.keys() and len(beam_ids) == len(segments)
                     and axes == segments and contact_ok
                     and set(mesh.nodes_of_member.get(member, ())) == beam_nodes,
                     f"Beam member {member} lacks a one-to-one shared source-edge discretization.")
            _require(elements.isdisjoint(beam_ids), "Two source owners claim a beam element.")
            elements |= beam_ids
            members[member] = edge
        _require(all_nodes.isdisjoint(shell_nodes) and all_elements.isdisjoint(elements),
                 "Components share mesh entities across the proposed cut.")
        if "near" in record:
            shell_nodes = beam_nodes
        all_nodes |= shell_nodes
        all_elements |= elements
        owned.append((elements, shell_nodes))
    _require(all_elements == mesh.quads.keys() | mesh.beams.keys() and all_nodes == set(mesh.nodes)
             and set(faces) == set(mesh.elements_of_face)
             and set(mesh.elements_of_edge) == set(members.values())
             and set(mesh.elements_of_member) == set(members) and set(mesh.nodes_of_member) == set(members)
             and {s: set(es) for s, es in mesh.elements_of_sheet.items()} == sheets
             and set(mesh.nodes_of_edge) == edges
             and set(mesh.node_of_vertex) == {v for r in records for v in r["vertices"]}
             and set(mesh.grid_of_face) <= set(faces) and set(mesh.block_grids_of_face) <= set(faces),
             "The published source associations do not exactly cover the selected structural owners.")
    return owned


def _tee_joint(mesh, record):
    """The entire shared chain has two cell sides on the flange, one on its web."""
    incidence = {}
    face_nodes = []
    for face in record["faces"]:
        nodes = set()
        for element in mesh.elements_of_face.get(face, ()):
            quad = mesh.quads[element]
            nodes.update(quad)
            for a, b in zip(quad, quad[1:] + quad[:1]):
                incidence.setdefault(tuple(sorted((a, b))), []).append(face)
        face_nodes.append(nodes)
    junction = {tuple(sorted(pair)) for pair in mesh.declared_plate_junction_edges}
    _require(junction and junction == {p for p, fs in incidence.items() if len(fs) > 2}
             and all(len(incidence[p]) == 3 and set(incidence[p]) == set(record["faces"]) for p in junction)
             and face_nodes[0] & face_nodes[1] == {n for p in junction for n in p},
             "Declared junction segments must exactly cover the three-sided contact of both source sheets.")
    matches = []
    for edge in record["edges"]:
        chain = mesh.nodes_of_edge.get(edge, ())
        if {tuple(sorted(p)) for p in zip(chain, chain[1:])} == junction:
            matches.append(edge)
    _require(len(matches) == 1, "A T-junction must have one source edge spanning its declared mesh segments.")
    return matches[0]


def _mesh_digest(mesh):
    from anymesher.serialize import mesh_to_dict
    return rm._digest(mesh_to_dict(mesh))


def _capture(project, settings, mesh, check):
    doc, records = _context(project, settings, check)
    _require(str(mesh.geometry_model_id) == str(project.geometry.model_id)
             and mesh.geometry_revision == project.geometry.revision and mesh.order == "linear",
             "The parent mesh is not bound to the current source geometry revision.")
    mapped = rm._proven_mapped_faces(project.geometry, mesh)
    prepared_faces = (mesh.structural_preparation or {}).get("source_to_working_faces", {})
    working_faces = {w for f in project.geometry.faces for w in prepared_faces.get(str(f), ())}
    split = records[-1].get("near") or any(r.get("tee") for r in records)
    proved = mapped == set(project.geometry.faces) or (split and mapped
             and all(prepared_faces.get(str(f)) for f in project.geometry.faces) and mapped == working_faces)
    if split and not proved:
        # The initial hybrid record can start at the already imprinted working
        # model. Reconstruct the complete source-to-working step from this
        # exact source revision before interpreting its strategy_by_face keys.
        from .structural_preparation import source_work_mapping
        closure, _ = _prepare_closure(project, check)
        mapping = source_work_mapping(closure)
        leaves = [mapping.get(f"face:{face}", ()) for face in project.geometry.faces]
        proved = all(leaves) and {f"face:{f}" for f in mapped} == {w for row in leaves for w in row}
    _require(proved,
             "Could not verify mapped generation for every published source plate.")
    _ownership(mesh, records)
    rm._admissible_metadata(mesh)
    baseline = dict(schema=SCHEMA, version=VERSION, pins=rm._pins(),
                    geometry=doc["raw"]["checksum"]["value"],
                    dependencies=rm._global_state(project, settings, "mapped", doc),
                    mesh_digest=_mesh_digest(mesh), components=records)
    baseline["receipt"] = rm._digest(baseline)
    return baseline


def capture_structural_baseline(project, settings, mesh, *, cancellation_check=None):
    try:
        return _capture(project, settings, mesh, rm._check_fn(cancellation_check))
    except rm._refusals():
        return None


@dataclass(frozen=True)
class StructuralPlan:
    description: str
    records: tuple
    changed: tuple

    @property
    def is_noop(self):
        return not self.changed


def plan_structural_update(project, settings, baseline, *, cancellation_check=None):
    _require(isinstance(baseline, dict) and baseline.get("schema") == SCHEMA and baseline.get("version") == VERSION
             and baseline.get("pins") == rm._pins()
             and rm._digest({k: v for k, v in baseline.items() if k != "receipt"}) == baseline.get("receipt"),
             "Structural baseline identity or content changed.")
    doc, records = _context(project, settings, rm._check_fn(cancellation_check))
    if "near" in records[-1] and "near" in baseline["components"][-1]:
        old = baseline["components"]
        axis = records[-1]
        _require(rm._global_state(project, settings, "mapped", doc) == baseline["dependencies"]
                 and old[-1]["fixed"] == axis["fixed"] and old[:-1] == records[:-1],
                 "Only the two endpoints of one free beam may move during a crossing update.")
        if doc["raw"]["checksum"]["value"] == baseline["geometry"]:
            return StructuralPlan("Retain all source owners", tuple(records), ())
        impacted = set(axis["near"]) | set(old[-1]["near"])
        _require(len(impacted) == 1 and old[-1]["near"] != axis["near"]
                 and old[-1]["fingerprint"] != axis["fingerprint"],
                 "The source change must create or remove one isolated beam/plate intersection.")
        face, = impacted
        changes = tuple(i for i, r in enumerate(records) if not r["faces"] or face in r["faces"])
        return StructuralPlan(f"Regenerate plate {face} and beam member {next(iter(axis['members']))} "
                              "across the changed intersection. Retain all other plates.", tuple(records), changes)
    _require(doc["raw"]["checksum"]["value"] == baseline["geometry"],
             "The source geometry changed. Added or removed intersections require a full rebuild.")
    _require(rm._global_state(project, settings, "mapped", doc) == baseline["dependencies"],
             "The structural meshing dependencies changed globally.")
    changes = []
    descriptions = []
    for index, (record, saved) in enumerate(zip(records, baseline["components"], strict=True)):
        _require(record["fingerprint"] == saved["fingerprint"] and record["members"] == saved["members"],
                 "The owner partition or membership changed.")
        changed = record["local"] != saved["local"]
        if changed:
            changes.append(index)
        target = (f"T-junction plates {record['faces']}" if record.get("tee") else
                  f"plate {record['faces'][0]} and beam members {sorted(int(m) for m in record['members'])}")
        descriptions.append(f"{'Regenerate' if changed else 'Retain'} {target}")
    _require(not any(r.get("tee") for r in records) or not changes or
             any(records[i].get("tee") for i in changes),
             "This structural route needs the T-junction component in its changed region.")
    beam_groups = {i for i, r in enumerate(records) if r["members"]}
    _require(not (beam_groups & set(changes)) or beam_groups <= set(changes),
             "A subset of beam-bearing owners needs per-owner connection action receipts.")
    return StructuralPlan(". ".join(descriptions), tuple(records), tuple(changes))


def _prepare_closure(project, check):
    from .structural_preparation import prepare_structural_connectivity
    g = project.geometry
    handles = [g.handle(kind, i) for kind, ids in (("face", g.faces), ("sheet", g.sheets),
               ("member", g.members), ("edge", project.beam_edges)) for i in ids]
    closure = g.extract_model_closure(handles, include_structural_closure=True, include_features=False)
    working = closure.working_model
    _require(closure.source_revision == g.revision, "The extracted structural closure is stale.")
    preparation = prepare_structural_connectivity(
        working, source_model_id=str(closure.source_model_id), source_revision=closure.source_revision,
        member_ids=tuple(working.members), cancellation_check=check)
    return closure, preparation


def _selected_mesh(project, settings, changed, check):
    from anymesher.hybrid import generate_hybrid_mesh_result
    from .structural_preparation import remap_mesh_to_source, source_edge_paths, source_work_mapping

    g = project.geometry
    closure, preparation = _prepare_closure(project, check)
    working = closure.working_model
    axis = next((r for r in changed if "near" in r), None)
    tee = next((r for r in changed if r.get("tee")), None)
    near = axis["near"] if axis else []
    relation_edge = None
    if tee:
        rows = preparation.to_dict()["connections"]
        pair = {f"face:{closure.source_to_work[g.handle('face', f)].id}" for f in tee["faces"]}
        _require(len(rows) == 1 and {rows[0]["first"], rows[0]["second"]} == pair
                 and rows[0]["intersection"] == "intersection_curve" and rows[0]["operation"] == "batch_arrangement"
                 and not rows[0]["reused"] and len(rows[0]["relations"]) == 1
                 and rows[0]["relations"][0].startswith("edge:") and len(working.junctions) == 1,
                 "Full preparation did not establish one new T-junction and one common edge.")
        relation_edge = int(rows[0]["relations"][0].split(":")[1])
        junction = next(iter(working.junctions.values()))
        sheets = {closure.source_to_work[g.handle("sheet", int(s))].id for s in tee["sheets"]}
        _require(set(junction.sheet_ids) == sheets and len(junction.attachment_ids) == 2
                 and str(junction.kind.value) == "sheet_joint" and str(junction.connection_intent.value) == "connect",
                 "Prepared sheet joint does not name both selected source owners and attachments.")
    elif near:
        work_face = closure.source_to_work[g.handle("face", near[0])].id
        work_member = closure.source_to_work[g.handle("member", int(next(iter(axis["members"]))))].id
        rows = preparation.to_dict()["connections"]
        _require(len(rows) == 1 and {rows[0]["first"], rows[0]["second"]} ==
                 {f"face:{work_face}", f"member:{work_member}"} and not rows[0]["relations"]
                 and rows[0]["operation"] == "batch_arrangement",
                 "Current owner preparation did not establish a unique beam/plate intersection.")
    else:
        _require(not preparation.connections and not preparation.created_count,
                 "Fresh preparation found a connection requiring intersection or junction source transfer.")
    paths = source_edge_paths(closure)
    mapping = source_work_mapping(closure, edge_paths=paths)

    def occurrences(kind, i):
        if not (near or tee) or kind not in ("face", "edge"):
            return [work(kind, i)]
        labels = mapping.get(f"{kind}:{i}", ())
        _require(labels and all(l.startswith(kind + ":") for l in labels),
                 f"Missing prepared occurrences for {kind} {i}.")
        ids = [int(label.split(":")[1]) for label in labels]
        if kind == "edge":
            _require(set(ids) == {e for e, _ in paths[i]}, f"Edge {i} lacks an ordered prepared path.")
        return ids

    def work(kind, identifier):
        target = closure.source_to_work[g.handle(kind, identifier)]
        _require(mapping.get(f"{kind}:{identifier}") == (f"{kind}:{target.id}",),
                 f"The prepared {kind} {identifier} does not have a unique unchanged source occurrence.")
        return target.id

    # The generic planar source remap translates faces, vertices and edges.
    # Validate sheets and members explicitly before publishing their associations.
    source_faces = {f for r in changed for f in r["faces"]}
    members = {int(m): edge for r in changed for m, edge in r["members"].items()}
    sheets = {int(s) for r in changed for s in r["sheets"]}
    edges = {e for r in changed for e in r["edges"]}
    for kind, ids in (("face", source_faces), ("edge", edges), ("member", members), ("sheet", sheets)):
        for i in ids:
            occurrences(kind, i)
    work_faces = {w for f in source_faces for w in occurrences("face", f)}
    selected = generate_hybrid_mesh_result(
        working, target_size=settings.target_size, strategy="mapped", order="linear",
        overrides={work("edge", e): n for e, n in settings.overrides if e in edges},
        face_ids=sorted(work_faces),
        member_ids=sorted(work("member", m) for m in members),
        beam_edges=sorted(w for e in members.values() for w in occurrences("edge", e)),
        native_backend=project.native_triangulation_backend,
        structural_preparation={"automatic_face_connections": False, "automatic_member_connections": False,
                                "automatic_member_sheet_connections": False, "declare_missing_owners": True},
        mutation_policy="working_copy", cancellation_check=check).mesh
    _require(rm._proven_mapped_faces(working, selected) == work_faces,
             "The selected owner generation did not map every requested face.")
    _require(str(selected.geometry_model_id) == str(working.model_id) and selected.geometry_revision == working.revision,
             "The selected fragment does not name the prepared geometry that produced it.")
    for name, kind, ids in (("elements_of_sheet", "sheet", sheets), ("elements_of_member", "member", members),
                            ("nodes_of_member", "member", members)):
        table = getattr(selected, name)
        inverse = {work(kind, i): i for i in ids}
        _require(set(table) == set(inverse), f"Selected fragment has incomplete {name} associations.")
        setattr(selected, name, {inverse[i]: value for i, value in table.items()})
    if selected.block_grids_of_face:
        inverse = {work("face", f): f for f in source_faces}
        _require(set(selected.block_grids_of_face) <= inverse.keys(), "Unexpected prepared block grid owner.")
        selected.block_grids_of_face = {inverse[f]: grid for f, grid in selected.block_grids_of_face.items()}
    remap_mesh_to_source(selected, closure, edge_paths=paths, source_geometry=g, cancellation_check=check)
    _require(str(selected.geometry_model_id) == str(g.model_id) and selected.geometry_revision == g.revision,
             "Fresh full preparation could not publish the selected fragment to the current source revision.")
    if tee:
        selected.automatic_intersections = len(preparation.connections)
        source_edge = _tee_joint(selected, tee)
        _require(occurrences("edge", source_edge) == [relation_edge],
                 "The declared segment chain does not match the prepared source junction edge.")
    _ownership(selected, changed)
    # The full source preparation ran on every active owner, even though only
    # the selected faces and members went through element generation.
    prepared = {"schema": SCHEMA, "status": "prepared",
                "source_to_working_faces": {str(f): occurrences("face", f) for f in g.faces},
                "source_to_working_edges": {str(e): occurrences("edge", e) for e in g.edges}}
    return selected, prepared


def generate_structural_update(project, settings, baseline, old_mesh, *, node_high_water,
                               element_high_water, cancellation_check=None):
    from anymesher.regional_update import RegionalQualityLimits, assemble_regional_replacement
    from anymesher.seeding import solve_seeding
    from anymesher.serialize import mesh_to_dict

    check = rm._check_fn(cancellation_check)
    plan = plan_structural_update(project, settings, baseline, cancellation_check=cancellation_check)
    old = rm._decode(old_mesh)
    _require(_mesh_digest(old) == baseline["mesh_digest"], "Published parent differs from its structural baseline.")
    saved = baseline["components"]
    owned = _ownership(old, saved)
    if plan.is_noop:
        return rm.RegionalUpdateResult(old, baseline, frozenset(old.nodes), frozenset(old.quads.keys() | old.beams.keys()), plan)
    check("prepare structural mesh update")
    changed = [plan.records[i] for i in plan.changed]
    rep, prepared = _selected_mesh(project, settings, changed, check)
    affected = set().union(*(owned[i][0] for i in plan.changed))
    old_elems = old.quads.keys() | old.beams.keys()
    nh, eh = max(node_high_water, max(old.nodes)), max(element_high_water, max(old_elems))
    # A saved mesh has no live seed field. Where present, compute and verify the
    # complete current source seeding after the splice before exposing it.
    # Detached hybrid results can carry working-model runtime objects. The
    # owner's neutral codec carries mesh semantics and auditable diagnostics.
    rep = rm._decode(mesh_to_dict(rep))
    parent = rm._decode(mesh_to_dict(old)) if old.seeding is not None else old
    authority = {}
    if "near" in plan.records[-1]:
        from anymesher.regional_update import CrossRevisionAuthority, OwnerBaselineReceipt

        def receipts(rows, mesh):
            return {i: OwnerBaselineReceipt(i, str(mesh.geometry_model_id), mesh.geometry_revision,
                                             rm._digest(row), True) for i, row in enumerate(rows)}

        unchanged = [r for i, r in enumerate(saved) if i not in plan.changed]
        keys = {kind: frozenset(int(key) for r in unchanged for key in r[field])
                for kind, field in (("edge", "edges"), ("face", "faces"), ("member", "members"),
                                    ("sheet", "sheets"), ("vertex", "vertices"))}
        authority = dict(owner_of_element={e: i for i, (elems, _) in enumerate(owned) for e in elems},
                         cross_revision=CrossRevisionAuthority(
                             receipts(saved, parent), receipts(plan.records, rep), frozenset(plan.changed), keys,
                             {e: i for i, r in enumerate(saved) for e in r["edges"]},
                             replacement_element_owner={e: i for i, (elems, _) in
                                                        zip(plan.changed, _ownership(rep, changed), strict=True)
                                                        for e in elems}))
    check("assemble structural mesh update")
    assembled = assemble_regional_replacement(
        parent, rep, affected_element_ids=affected, interface_node_ids={},
        node_ids={n: nh + i for i, n in enumerate(sorted(rep.nodes), 1)},
        element_ids={e: eh + i for i, e in enumerate(sorted(rep.quads.keys() | rep.beams.keys()), 1)},
        quality=RegionalQualityLimits(None, None, None), discard_stale_diagnostics=True, **authority)
    final = assembled.mesh
    final.structural_preparation = prepared
    final.automatic_intersections = rep.automatic_intersections
    # The plan admits regeneration of all beam-bearing components together or
    # retention of every beam. Therefore the owner's action count is complete.
    final.automatic_beam_connections = (rep.automatic_beam_connections if any(r["members"] for r in changed)
                                        else old.automatic_beam_connections)
    final.hybrid_diagnostics = {"strategy_by_face": {str(f): "mapped" for r in plan.records for f in r["faces"]},
                                "requested_target_size": float(settings.target_size),
                                "geometry_model_id": str(project.geometry.model_id),
                                "geometry_revision": int(project.geometry.revision)}
    _ownership(final, plan.records)
    if old.seeding is not None:
        seeding = solve_seeding(project.geometry, target_size=settings.target_size, overrides=dict(settings.overrides))
        _require(all(seeding.divisions[e] == len(chain) - 1 for e, chain in final.nodes_of_edge.items()),
                 "Global mapped seeding did not reproduce the published structural edge stations.")
        final.seeding = seeding
    rm._validate_quality(project, final, settings, cancellation_check)
    keep_nodes, keep_elements = frozenset(assembled.retained_node_ids), frozenset(assembled.retained_element_ids)
    _require(keep_elements == old_elems - affected
             and all(np.array_equal(old.nodes[n], final.nodes[n]) for n in keep_nodes),
             "Retained structural mesh identities differ from the planned owners.")
    check("verify structural publication")
    result_baseline = _capture(project, settings, final, check)
    return rm.RegionalUpdateResult(final, result_baseline, keep_nodes, keep_elements, plan)
