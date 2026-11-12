"""Bind Results picks to the submitted ANYfem mesh and the ANYfatigue engine."""

from dataclasses import dataclass, field, replace
from numbers import Integral
from typing import Any, ClassVar

import numpy as np

from ..post.results import LinearBatchSolution, LinearSolution, TransientSolution, submitted_plate_thicknesses


def require_fatigue():
    """Check the optional producer/consumer contract only when fatigue is used."""
    try:
        from anyfatigue.extract import readout_preview
        from anyfatigue.model import SurfaceMesh
        from anyfatigue.plate import plate_stress_source
        if not hasattr(SurfaceMesh, "normals_for"):
            raise ImportError("the installed ANYfatigue predates on-demand shell readout")
    except ImportError as exc:
        raise ValueError(
            "Fatigue requires ANYfatigue with on-demand shell readout. Install the "
            "ANYfem[fatigue] extra or use the matching ANYfatigue source checkout."
        ) from exc


@dataclass(frozen=True)
class FatigueInput:
    source: Any
    mesh: Any
    face_thicknesses_m: dict[int, float] = field(default_factory=dict)

    def plate_scope_for(self, element_ids):
        """The submitted receiving face and all its shells, without reading stresses."""
        face = _plate_face(self.mesh, element_ids)
        return face, tuple(sorted(self.mesh.elements_of_face[face]))

    def for_cases(self, labels, *, cancelled=None):
        """Limit extraction to the frames referenced by the fatigue settings."""
        select = getattr(self.source, "select_cases", None)
        if select is None:  # An already materialized stress source.
            return self
        return replace(self, source=select(tuple(dict.fromkeys(labels)), cancelled=cancelled))

    def thickness_mm_for(self, element_ids):
        """Suggest a thickness only when every pick has the same submitted thickness."""
        selected = set(element_ids)
        if not selected:
            return None
        covered = set()
        thicknesses = set()
        for face, elements in self.mesh.elements_of_face.items():
            common = selected.intersection(elements)
            if common:
                covered.update(common)
                thicknesses.add(self.face_thicknesses_m.get(face))
        if covered == selected and None not in thicknesses and len(thicknesses) == 1:
            thickness = thicknesses.pop()
            if np.isfinite(thickness) and thickness > 0.0:
                return 1000 * thickness
        return None


@dataclass(frozen=True)
class FatigueMeshEdgeRef:
    """One submitted shell side, identified independently of viewing direction."""

    node_ids: tuple[int, int]
    mesh_id: str | None = None
    kind: ClassVar[str] = "mesh_edge"

    def __post_init__(self):
        if (len(self.node_ids) != 2 or self.node_ids[0] == self.node_ids[1]
                or not all(isinstance(node, Integral) for node in self.node_ids)):
            raise ValueError("A shell side needs two distinct corner node IDs.")
        object.__setattr__(self, "node_ids", tuple(sorted(map(int, self.node_ids))))


@dataclass(frozen=True)
class PreparedFatigue:
    source: Any
    locations: tuple
    stations: tuple
    element_ids: tuple[int, ...]
    face_id: int | None

    @property
    def location(self):
        """The first location, for callers preparing a single target."""
        return self.locations[0]


def load_fatigue_input(*, solution=None, dataset=None, mesh, project=None):
    """Read the displayed job. The mesh must come from the submitted result."""
    require_fatigue()
    if (solution is None) == (dataset is None) or mesh is None:
        raise ValueError("Select a single result and its submitted mesh.")
    if dataset is not None:
        from anyfatigue import anyfem_artifacts

        identity = dataset.identity
        if identity.get("partial"):
            raise ValueError("A partial result cannot supply a fatigue assessment.")
        if project is not None and identity.get("document_id") != project.document_id:
            raise ValueError("The fatigue result belongs to a different document.")
        mesh_id = getattr(mesh, "_anyfem_mesh_record_id", None)
        if mesh_id and identity.get("mesh_id") not in (None, "", "active-mesh", mesh_id):
            raise ValueError("The picked mesh differs from the submitted result mesh.")
        times = None
        if identity.get("frame_kind") == "load_case" and "load_case_times_s" not in dataset.table_keys:
            # Older artifacts may predate the time table. Only the submitted
            # snapshot or a user-supplied block duration can establish exposure.
            submitted = dataset.metadata("provenance").get("submitted_inputs", {})
            by_name = {case["name"]: case.get("time") for case in submitted.get("load_cases", ())}
            names = dataset.table("load_case_names")
            times = [by_name.get(n.decode() if isinstance(n, bytes) else str(n)) for n in names]
        # Results already has a reader for this publication, often with its field
        # descriptors decoded. Older ANYfatigue releases accept only a path.
        read_dataset = getattr(anyfem_artifacts, "read_result_dataset", None)
        source = (read_dataset(dataset, times=times) if read_dataset is not None
                  else anyfem_artifacts.read_result(dataset.path, times=times))
        thicknesses = ({int(k): float(v) for k, v in dataset.table("shell_face_thickness_m").items()}
                       if "shell_face_thickness_m" in dataset.table_keys else {})
    else:
        from anyfatigue.anyfem_adapter import from_solutions

        if not isinstance(solution, (LinearSolution, LinearBatchSolution, TransientSolution)):
            raise ValueError("Select a linear-static shell result, load-case series or linear transient history.")
        built = solution.built
        if isinstance(solution, LinearBatchSolution) and (
            not solution.shapes or any(s.built.mesh is not built.mesh for s in solution.shapes)
        ):
            raise ValueError("All fatigue cases must belong to the same submitted mesh.")
        source = from_solutions(solution, project=built.project, lazy=True)
        thicknesses = submitted_plate_thicknesses(built)
    # A live job and its retained viewer can hold independent deserializations.
    # Match actual corner topology and undeformed coordinates before using the
    # viewer mesh's point, line and face ownership to interpret a fatigue pick.
    if dataset is not None or mesh is not solution.built.mesh:
        from anyfatigue.anyfem_adapter import surface_mesh_from_anyfem

        submitted, _, _ = surface_mesh_from_anyfem(mesh)
        if not all(np.array_equal(getattr(source.mesh, key), getattr(submitted, key))
                   for key in ("element_ids", "element_corner_ids", "node_ids", "node_xyz")):
            raise ValueError("The picked shell mesh does not match the stress result.")
    return FatigueInput(source, mesh, thicknesses)


def _plate_face(mesh, selected):
    owners = {eid: [] for eid in selected}
    for fid, elements in mesh.elements_of_face.items():
        for eid in owners.keys() & set(elements):
            owners[eid].append(fid)
    faces = {fid for hits in owners.values() for fid in hits}
    if len(faces) != 1 or any(len(hits) != 1 for hits in owners.values()):
        raise ValueError("Select shell elements belonging to one identifiable plate face for the hot spot.")
    return faces.pop()


def _weld_nodes(mesh, target, plate_nodes, corners, sides):
    # A midside node may be skipped for the engine's linear corner readout.
    # A corner on another plate must never be silently dropped from the weld.
    if target.kind == "mesh_edge":
        if frozenset(target.node_ids) not in sides:
            raise ValueError("The picked mesh segment must be a shell side on the selected plate.")
        return target.node_ids
    nodes = mesh.nodes_of_edge.get(target.id, ())
    if not nodes or not plate_nodes.issuperset(nodes):
        raise ValueError("The whole picked line must belong to the selected plate.")
    chain = tuple(int(n) for n in nodes if n in corners)
    if len(chain) < 2:
        raise ValueError("The weld line needs at least two shell corner nodes.")
    if any(frozenset(pair) not in sides for pair in zip(chain, chain[1:])):
        raise ValueError("The picked line does not follow connected shell edges on this plate.")
    return chain


def _point_weld_direction(mesh, node, reference_edge, plate_nodes, corners, sides):
    if reference_edge is None:
        candidates = [chain for chain in mesh.nodes_of_edge.values()
                      if node in chain and set(chain) <= plate_nodes]
        if len(candidates) != 1:
            raise ValueError("Pick a reference line through the point or give an in-plane readout direction.")
        chain = candidates[0]
    else:
        if reference_edge.kind not in ("edge", "mesh_edge"):
            raise ValueError("Pick a line to give the weld direction.")
        chain = _weld_nodes(mesh, reference_edge, plate_nodes, corners, sides)
        if reference_edge.kind == "edge":
            chain = mesh.nodes_of_edge[reference_edge.id]  # Tangent also exists at a midside node.
        if node not in chain:
            raise ValueError("The reference line must pass through the point on the selected plate.")
    if len(chain) < 2:
        raise ValueError("The reference line has no usable tangent at this point.")
    index = list(chain).index(node)
    if chain[0] == chain[-1] and index == 0:
        lo, hi = chain[-2], chain[1]
    else:
        lo, hi = chain[max(0, index - 1)], chain[min(len(chain) - 1, index + 1)]
    return tuple(np.asarray(mesh.nodes[hi]) - mesh.nodes[lo])


def prepare_fatigue_locations(data, element_ids, *, targets=(), surface="top",
                              method="hotspot_a", thickness_mm=None, side="auto", direction=None,
                              reference_edge=None):
    """Resolve all picked targets in order, sharing one receiving plate and stress source."""
    from anyfatigue.errors import FatigueError
    from anyfatigue.extract import ElementLocation, ReadOutStation
    from anyfatigue.plate import plate_stress_source

    mesh = data.mesh
    shells = mesh.shells
    selected = tuple(dict.fromkeys(element_ids))
    if not selected or not all(eid in shells for eid in selected):
        raise ValueError("Pick one or more shell elements on the displayed result.")
    targets = tuple(targets)
    if not targets:
        location = ElementLocation(selected, surfaces=(surface,), weld_direction=direction)
        rows = [data.source.mesh.element_row(e) for e in selected]
        centers = data.source.mesh.centroids()
        normals = data.source.mesh.normals_for(rows)
        stations = tuple(ReadOutStation(tuple(centers[r]), tuple(normal), None, ())
                         for r, normal in zip(rows, normals))
        return PreparedFatigue(data.source, (location,), stations, selected, None)

    mesh_id = getattr(mesh, "_anyfem_mesh_record_id", None)
    for ref in (*targets, reference_edge):
        if getattr(ref, "mesh_id", None) is not None and ref.mesh_id != mesh_id:
            raise ValueError("The picked target belongs to a different result mesh.")
    if method not in ("hotspot_a", "hotspot_b"):
        raise ValueError("Select a supported hot-spot readout method.")
    face, elements = data.plate_scope_for(selected)
    plate = plate_stress_source(data.source, elements)
    plate_nodes = {node for eid in elements for node in shells[eid]}
    corners = set(map(int, plate.mesh.node_ids))
    sides = set()
    for eid in elements:
        ring = mesh.corners_of(eid)
        sides.update(frozenset(pair) for pair in zip(ring, ring[1:] + ring[:1]))

    locations = []
    stations = []
    for target in targets:
        try:
            location = _hotspot_location(mesh, target, surface, reference_edge, direction,
                                         plate_nodes, corners, sides)
            location, preview = _resolve_readout(plate, location, method, thickness_mm, side, direction)
        except (ValueError, FatigueError) as exc:
            if len(targets) == 1:
                raise
            identity = target.node_ids if target.kind == "mesh_edge" else target.id
            raise ValueError(f"{target.kind} {identity}: {exc}") from exc
        locations.append(location)
        stations.extend(preview)
    return PreparedFatigue(plate, tuple(locations), tuple(stations), elements, face)


def _hotspot_location(mesh, target, surface, reference_edge, direction, plate_nodes, corners, sides):
    from anyfatigue.extract import LineLocation, PointLocation

    if target.kind in ("edge", "mesh_edge"):
        chain = _weld_nodes(mesh, target, plate_nodes, corners, sides)
        closed = chain[0] == chain[-1]
        name = (f"Shell side {chain[0]}–{chain[1]}" if target.kind == "mesh_edge"
                else f"Weld line {target.id}")
        return LineLocation(chain[:-1] if closed else chain, surfaces=(surface,), closed=closed, name=name)
    if target.kind in ("node", "vertex"):
        node = target.id if target.kind == "node" else mesh.node_of_vertex.get(target.id)
        if node not in plate_nodes:
            raise ValueError("The picked point must belong to the selected plate.")
        xyz = tuple(mesh.nodes[node])
        if reference_edge is not None and direction is not None:
            raise ValueError("Choose a reference line or a readout vector for the point.")
        tangent = (_point_weld_direction(mesh, node, reference_edge, plate_nodes, corners, sides)
                   if direction is None else None)
        return PointLocation((xyz,), surfaces=(surface,), weld_direction=tangent, name=f"Toe {target.kind} {target.id}")
    raise ValueError("Pick a weld line or a toe point on the result mesh.")


def _resolve_readout(plate, location, method, thickness_mm, side, direction):
    from anyfatigue.extract import ReadOut, readout_preview

    if side == "auto" and direction is None:
        from anyfatigue.errors import ExtractionError

        valid = []
        for candidate in (1, -1):
            trial = replace(location, readout=ReadOut(method, thickness_mm, side=candidate))
            try:
                stations = readout_preview(plate, trial)
            except ExtractionError:
                continue
            valid.append((trial, stations))
        if len(valid) != 1:
            raise ValueError("Both readout sides are available; select a side." if valid else
                             "Neither readout fits on the selected plate at every station.")
        location, stations = valid[0]
    else:
        location = replace(location, readout=ReadOut(method, thickness_mm, side=1 if side == "auto" else side,
                                                     direction=direction))
        stations = readout_preview(plate, location)
    return location, stations


def prepare_fatigue_location(data, element_ids, *, target=None, **kwargs):
    """Prepare a single point/line or a direct selection of shell elements."""
    return prepare_fatigue_locations(data, element_ids, targets=() if target is None else (target,), **kwargs)
