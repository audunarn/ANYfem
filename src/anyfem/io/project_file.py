"""Saving and loading a project.

The file stores the model, not its consequences: geometry, sections, supports,
loads, combinations and imperfections.  The mesh and the results are left out
because they are regenerable, and storing them would make a saved file go
stale the moment the model changed.

**Entity IDs are part of the data.**  Loads, supports and sections reference
geometry by ID, so a load cycle that renumbered anything would silently
re-target them.  The ID counters are saved too, so entities created after a
reload cannot collide with entities that already exist.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Mapping

import numpy as np

from ..geometry.curves import Arc, Straight
from ..geometry.entities import Edge, EntityRef, Face, OrientedEdge, Vertex
from ..geometry.model import GeometryModel
from ..model.attributes import (
    LoadCase,
    Mass,
    Support,
)
from ..mesh.refinement import Refinement
from ..model.imperfections import Imperfection
from ..model.materials import Material
from ..model.project import Project
from ..model.sections import BeamSection

__all__ = [
    "FORMAT_VERSION",
    "ProjectFileError",
    "load_project",
    "project_from_dict",
    "project_to_dict",
    "save_project",
]

FORMAT_VERSION = 2
SUFFIX = ".anyfem"


class ProjectFileError(ValueError):
    """Raised when a project file cannot be read."""


# ----------------------------------------------------------------------
# writing
# ----------------------------------------------------------------------
def project_to_dict(project: Project) -> Dict[str, Any]:
    """The whole model as plain data."""

    geometry = project.geometry
    return {
        "anyfem": {"format": FORMAT_VERSION},
        "name": project.name,
        "geometry": {
            "vertices": [
                {"id": vertex.id, "position": _vector(vertex.position)}
                for vertex in _sorted(geometry.vertices)
            ],
            "edges": [_edge_to_dict(edge) for edge in _sorted(geometry.edges)],
            "faces": [_face_to_dict(face) for face in _sorted(geometry.faces)],
            "next_id": dict(geometry.id_state()),
        },
        "materials": [material.to_dict() for material in _by_name(project.materials)],
        "plate_sections": [
            {
                "name": section.name,
                "thickness": section.thickness,
                "material": section.material,
            }
            for section in _by_name(project.plate_sections)
        ],
        "beam_sections": [
            {
                "name": section.name,
                "profile": section.profile,
                "material": section.material,
                "web_height": section.web_height,
                "web_thickness": section.web_thickness,
                "flange_width": section.flange_width,
                "flange_thickness": section.flange_thickness,
                "web_direction": (
                    None
                    if section.web_direction is None
                    else _vector(section.web_direction)
                ),
                "eccentricity": section.eccentricity,
            }
            for section in _by_name(project.beam_sections)
        ],
        "face_sections": {str(k): v for k, v in sorted(project.face_sections.items())},
        "edge_sections": {str(k): v for k, v in sorted(project.edge_sections.items())},
        "supports": [
            {
                "name": support.name,
                "ref": _ref(support.ref),
                "constraints": {
                    key: float(value) for key, value in support.constraints.items()
                },
            }
            for support in project.supports
        ],
        "masses": [
            {"name": mass.name, "ref": _ref(mass.ref), "value": mass.value}
            for mass in project.masses
        ],
        "load_cases": [
            _load_case_to_dict(case) for case in _by_name(project.load_cases)
        ],
        "combinations": [
            {"name": item.name, "factors": dict(item.factors)}
            for item in _by_name(project.combinations)
        ],
        "imperfections": [
            {
                "name": item.name,
                "ref": _ref(item.ref),
                "kind": item.kind,
                "amplitude": item.amplitude,
                "direction": _vector(item.direction),
                "waves": [int(item.waves[0]), int(item.waves[1])],
                "axes": [int(item.axes[0]), int(item.axes[1])],
            }
            for item in project.imperfections
        ],
        # Meshing controls. They do not change the model, only the mesh made
        # from it -- but a project reopened without them meshes differently
        # from the one that was saved, which is not something a file format
        # should let happen quietly.
        "meshing": {
            "element_order": project.element_order,
            "refinements": [
                {
                    "name": item.name,
                    "size": float(item.size),
                    "radius": float(item.radius),
                    "growth": float(item.growth),
                    "ref": None if item.ref is None else _ref(item.ref),
                    "center": (
                        None if item.center is None else _vector(item.center)
                    ),
                }
                for item in project.refinements
            ],
        },
    }


def save_project(project: Project, path: str | Path) -> Path:
    """Write a project to a file."""

    destination = Path(path)
    if not destination.suffix:
        destination = destination.with_suffix(SUFFIX)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(project_to_dict(project), indent=2), encoding="utf-8"
    )
    return destination


# ----------------------------------------------------------------------
# reading
# ----------------------------------------------------------------------
def project_from_dict(data: Mapping[str, Any]) -> Project:
    """Rebuild a project and report malformed serialized data consistently."""

    if not isinstance(data, Mapping):
        raise ProjectFileError("an ANYfem project must be a JSON object")
    try:
        return _project_from_dict(data)
    except ProjectFileError:
        raise
    except (
        AttributeError,
        KeyError,
        TypeError,
        ValueError,
        IndexError,
        OverflowError,
    ) as error:
        detail = str(error) or type(error).__name__
        raise ProjectFileError(f"invalid ANYfem project data: {detail}") from None


def _project_from_dict(data: Mapping[str, Any]) -> Project:
    """Rebuild a project from plain data, IDs and all."""

    header = data.get("anyfem")
    if not isinstance(header, Mapping) or "format" not in header:
        raise ProjectFileError(
            "this does not look like an ANYfem project file: no format header"
        )
    version = int(header["format"])
    if version < 1:
        raise ProjectFileError(f"unsupported ANYfem project format {version}")
    if version > FORMAT_VERSION:
        raise ProjectFileError(
            f"the file is format {version} but this ANYfem reads up to "
            f"{FORMAT_VERSION}; upgrade ANYfem to open it"
        )

    project = Project(name=str(data.get("name", "model")))
    _geometry_from_dict(project.geometry, data.get("geometry", {}))

    for entry in data.get("materials", ()):
        if "constants" in entry or "symmetry" in entry:
            material = Material.from_dict(entry)
        else:
            # Version 1 stored isotropic constants at the top level and a DNV
            # descriptor as a three-item JSON list.
            material = Material(
                name=str(entry["name"]),
                elastic_modulus=float(entry["elastic_modulus"]),
                poisson_ratio=float(entry["poisson_ratio"]),
                density=float(entry.get("density", 0.0)),
                yield_stress=float(entry.get("yield_stress", 0.0)),
                hardening=entry.get("hardening"),
            )
        project.add_material(material)
    for entry in data.get("plate_sections", ()):
        project.add_plate_section(
            name=str(entry["name"]),
            thickness=float(entry["thickness"]),
            material=str(entry["material"]),
        )
    for entry in data.get("beam_sections", ()):
        project.add_beam_section(
            BeamSection(
                name=str(entry["name"]),
                profile=str(entry["profile"]),
                material=str(entry["material"]),
                web_height=float(entry.get("web_height", 0.0)),
                web_thickness=float(entry.get("web_thickness", 0.0)),
                flange_width=float(entry.get("flange_width", 0.0)),
                flange_thickness=float(entry.get("flange_thickness", 0.0)),
                web_direction=entry.get("web_direction"),
                eccentricity=float(entry.get("eccentricity", 0.0)),
            )
        )

    for face_id, section in data.get("face_sections", {}).items():
        project.assign_plate(int(face_id), str(section))
    for edge_id, section in data.get("edge_sections", {}).items():
        project.assign_beam(int(edge_id), str(section))

    for entry in data.get("supports", ()):
        project.add_support(
            Support(
                name=entry["name"],
                ref=_existing_ref(project, entry["ref"], "support.ref"),
                constraints={
                    key: float(value)
                    for key, value in entry["constraints"].items()
                },
            )
        )
    for entry in data.get("masses", ()):
        project.add_mass(
            Mass(
                ref=_existing_ref(project, entry["ref"], "mass.ref"),
                value=float(entry["value"]),
                name=entry.get("name", "mass"),
            )
        )

    for entry in data.get("load_cases", ()):
        _load_case_from_dict(project, entry)
    for entry in data.get("combinations", ()):
        project.add_combination(
            name=str(entry["name"]),
            factors={str(k): float(v) for k, v in entry["factors"].items()},
        )
    for entry in data.get("imperfections", ()):
        project.add_imperfection(
            Imperfection(
                ref=_existing_ref(
                    project, entry["ref"], "imperfection.ref"
                ),
                kind=entry.get("kind", "auto"),
                amplitude=entry.get("amplitude"),
                direction=tuple(entry.get("direction", (0.0, 0.0, 1.0))),
                waves=tuple(entry.get("waves", (1, 1))),
                axes=tuple(entry.get("axes", (0, 1))),
                name=entry.get("name", "imperfection"),
            )
        )

    meshing = data.get("meshing")
    if isinstance(meshing, Mapping):
        # Absent in files written before meshing controls existed, which is
        # what the defaults are for.
        project.set_element_order(str(meshing.get("element_order", "linear")))
        for entry in meshing.get("refinements", ()):
            center = entry.get("center")
            reference = entry.get("ref")
            project.add_refinement(
                Refinement(
                    size=float(entry["size"]),
                    radius=float(entry.get("radius", 0.0)),
                    growth=float(entry.get("growth", 1.5)),
                    ref=(
                        None
                        if reference is None
                        else _existing_ref(project, reference, "refinement.ref")
                    ),
                    center=None if center is None else tuple(center),
                    name=entry.get("name", "refinement"),
                )
            )
    return project


def load_project(path: str | Path) -> Project:
    """Read a project from a file."""

    source = Path(path)
    try:
        raw = source.read_text(encoding="utf-8")
    except OSError as error:
        raise ProjectFileError(f"cannot read {source}: {error}") from None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as error:
        raise ProjectFileError(f"{source} is not valid JSON: {error}") from None
    return project_from_dict(data)


# ----------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------
def _sorted(mapping):
    return [mapping[key] for key in sorted(mapping)]


def _by_name(mapping):
    return [mapping[key] for key in sorted(mapping)]


def _vector(values) -> List[float]:
    return [float(value) for value in np.asarray(values, dtype=float).ravel()]


def _ref(ref: EntityRef) -> Dict[str, Any]:
    return {"kind": ref.kind, "id": int(ref.id)}


def _ref_from(data: Mapping[str, Any]) -> EntityRef:
    return EntityRef(str(data["kind"]), int(data["id"]))  # type: ignore[arg-type]


def _existing_ref(
    project: Project, data: Mapping[str, Any], context: str
) -> EntityRef:
    """Decode one serialized reference and prove its target exists."""

    try:
        ref = _ref_from(data)
        return project.geometry.entity_ref(ref.kind, ref.id)
    except (KeyError, TypeError, ValueError) as error:
        raise ProjectFileError(f"{context}: {error}") from None


def _edge_to_dict(edge: Edge) -> Dict[str, Any]:
    curve: Dict[str, Any] = {"kind": "line"}
    if isinstance(edge.curve, Arc):
        curve = {"kind": "arc", "via": int(edge.curve.via_vertex)}
    return {"id": edge.id, "start": edge.start, "end": edge.end, "curve": curve}


def _face_to_dict(face: Face) -> Dict[str, Any]:
    return {
        "id": face.id,
        "loop": [[int(item.edge), bool(item.forward)] for item in face.loop],
        "corners": [int(corner) for corner in face.corners],
    }


def _geometry_from_dict(geometry: GeometryModel, data: Mapping[str, Any]) -> None:
    if not isinstance(data, Mapping):
        raise ProjectFileError("geometry must be a JSON object")
    for entry in data.get("vertices", ()):
        vertex_id = int(entry["id"])
        if vertex_id <= 0 or vertex_id in geometry.vertices:
            raise ProjectFileError(
                f"geometry.vertices[{vertex_id}].id must be unique and positive"
            )
        position = np.asarray(entry["position"], dtype=float)
        if position.shape != (3,) or not np.all(np.isfinite(position)):
            raise ProjectFileError(
                f"geometry.vertices[{vertex_id}].position needs three finite "
                "components"
            )
        geometry.vertices[vertex_id] = Vertex(
            id=vertex_id,
            position=position.copy(),
        )
    for entry in data.get("edges", ()):
        curve_data = entry.get("curve", {"kind": "line"})
        edge_id = int(entry["id"])
        if edge_id <= 0 or edge_id in geometry.edges:
            raise ProjectFileError(
                f"geometry.edges[{edge_id}].id must be unique and positive"
            )
        start = int(entry["start"])
        end = int(entry["end"])
        for field, vertex_id in (("start", start), ("end", end)):
            if vertex_id not in geometry.vertices:
                raise ProjectFileError(
                    f"geometry.edges[{edge_id}].{field} references missing "
                    f"vertex {vertex_id}"
                )
        if start == end:
            raise ProjectFileError(
                f"geometry.edges[{edge_id}] needs two distinct end vertices"
            )
        curve_kind = curve_data.get("kind", "line")
        if curve_kind == "arc":
            via = int(curve_data["via"])
            if via not in geometry.vertices:
                raise ProjectFileError(
                    f"geometry.edges[{edge_id}].curve.via references missing "
                    f"vertex {via}"
                )
            if len({start, via, end}) != 3:
                raise ProjectFileError(
                    f"geometry.edges[{edge_id}] arc needs three distinct vertices"
                )
            curve = Arc(via_vertex=via)
        elif curve_kind == "line":
            curve = Straight()
        else:
            raise ProjectFileError(
                f"geometry.edges[{edge_id}].curve.kind {curve_kind!r} is unknown"
            )
        geometry.edges[edge_id] = Edge(
            id=edge_id,
            start=start,
            end=end,
            curve=curve,
        )
    for entry in data.get("faces", ()):
        face_id = int(entry["id"])
        if face_id <= 0 or face_id in geometry.faces:
            raise ProjectFileError(
                f"geometry.faces[{face_id}].id must be unique and positive"
            )
        loop_items = []
        for item in entry["loop"]:
            if len(item) != 2 or not isinstance(item[1], bool):
                raise ProjectFileError(
                    f"geometry.faces[{face_id}].loop entries need an edge ID "
                    "and a boolean direction"
                )
            edge_id = int(item[0])
            if edge_id not in geometry.edges:
                raise ProjectFileError(
                    f"geometry.faces[{face_id}].loop references missing edge "
                    f"{edge_id}"
                )
            loop_items.append(OrientedEdge(edge_id, item[1]))
        loop = tuple(loop_items)
        if len(loop) < 4:
            raise ProjectFileError(
                f"geometry.faces[{face_id}].loop needs at least four edges"
            )

        def start_vertex(item: OrientedEdge) -> int:
            edge = geometry.edges[item.edge]
            return edge.start if item.forward else edge.end

        def end_vertex(item: OrientedEdge) -> int:
            edge = geometry.edges[item.edge]
            return edge.end if item.forward else edge.start

        for current, following in zip(loop, loop[1:] + loop[:1]):
            if end_vertex(current) != start_vertex(following):
                raise ProjectFileError(
                    f"geometry.faces[{face_id}].loop is not continuous at "
                    f"edge {following.edge}"
                )
        corners = tuple(int(corner) for corner in entry["corners"])
        if (
            len(corners) != 4
            or len(set(corners)) != 4
            or any(not 0 <= corner < len(loop) for corner in corners)
            or tuple(sorted(corners)) != corners
        ):
            raise ProjectFileError(
                f"geometry.faces[{face_id}].corners must be four distinct loop "
                "positions in order"
            )
        geometry.faces[face_id] = Face(
            id=face_id,
            loop=loop,
            corners=corners,
        )

    counters = data.get("next_id")
    if counters:
        state = {str(k): int(v) for k, v in counters.items()}
        for kind, store in (
            ("vertex", geometry.vertices),
            ("edge", geometry.edges),
            ("face", geometry.faces),
        ):
            minimum = max(store, default=0) + 1
            if kind not in state or state[kind] < minimum:
                raise ProjectFileError(
                    f"geometry.next_id.{kind} must be at least {minimum}"
                )
        geometry.restore_id_state(state)
    else:
        # An older file without counters: continue past whatever it holds, so
        # a new entity can never collide with a saved one.
        geometry.restore_id_state(
            {
                "vertex": max(geometry.vertices, default=0) + 1,
                "edge": max(geometry.edges, default=0) + 1,
                "face": max(geometry.faces, default=0) + 1,
            }
        )


def _load_case_to_dict(case: LoadCase) -> Dict[str, Any]:
    return {
        "name": case.name,
        "follower_pressure": bool(case.follower_pressure),
        "gravity": None if case.gravity is None else _vector(case.gravity),
        "point_loads": [
            {
                "ref": _ref(load.ref),
                "force": _vector(load.force),
                "moment": _vector(load.moment),
            }
            for load in case.point_loads
        ],
        "pressures": [
            {"ref": _ref(load.ref), "value": float(load.value)}
            for load in case.pressures
        ],
        "line_loads": [
            {"ref": _ref(load.ref), "force_per_length": _vector(load.force_per_length)}
            for load in case.line_loads
        ],
        "surface_tractions": [
            {"ref": _ref(load.ref), "traction": _vector(load.traction)}
            for load in case.surface_tractions
        ],
    }


def _load_case_from_dict(project: Project, data: Mapping[str, Any]) -> LoadCase:
    case = project.load_case(str(data["name"]))
    follower = data.get("follower_pressure", False)
    if not isinstance(follower, bool):
        raise ProjectFileError(
            f"load_cases[{case.name!r}].follower_pressure must be true or false"
        )
    case.follower_pressure = follower
    gravity = data.get("gravity")
    if gravity is None:
        case.gravity = None
    else:
        vector = np.asarray(gravity, dtype=float)
        if vector.shape != (3,):
            raise ProjectFileError(
                f"load_cases[{case.name!r}].gravity needs three finite components"
            )
        case.set_acceleration(*vector)

    for index, entry in enumerate(data.get("point_loads", ())):
        case.add_point_load(
            ref=_existing_ref(
                project,
                entry["ref"],
                f"load_cases[{case.name!r}].point_loads[{index}].ref",
            ),
            force=entry["force"],
            moment=entry["moment"],
        )
    for index, entry in enumerate(data.get("pressures", ())):
        case.add_pressure(
            ref=_existing_ref(
                project,
                entry["ref"],
                f"load_cases[{case.name!r}].pressures[{index}].ref",
            ),
            value=entry["value"],
        )
    for index, entry in enumerate(data.get("line_loads", ())):
        case.add_line_load(
            ref=_existing_ref(
                project,
                entry["ref"],
                f"load_cases[{case.name!r}].line_loads[{index}].ref",
            ),
            force_per_length=entry["force_per_length"],
        )
    for index, entry in enumerate(data.get("surface_tractions", ())):
        case.add_surface_traction(
            ref=_existing_ref(
                project,
                entry["ref"],
                f"load_cases[{case.name!r}].surface_tractions[{index}].ref",
            ),
            traction=entry["traction"],
        )
    return case
