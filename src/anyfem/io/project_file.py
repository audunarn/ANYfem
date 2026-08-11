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
import os
from dataclasses import replace
from pathlib import Path
import tempfile
from typing import Any, Dict, List, Mapping
from uuid import NAMESPACE_URL, uuid4, uuid5

import numpy as np
from anygeometry.curves import Arc, Straight
from anygeometry.entities import Edge, EntityRef, Face, OrientedEdge, Vertex
from anygeometry.errors import GeometryError
from anygeometry.model import GeometryModel
from anygeometry.serialization import from_dict as geometry_from_dict
from anygeometry.serialization import to_dict as geometry_to_dict

from ..model.attributes import (
    LoadCase,
    Mass,
    Support,
)
from ..model.regions import RegionRef
from ..mesh.refinement import Refinement
from ..model.imperfections import Imperfection
from ..model.materials import Material
from ..model.project import Project
from ..model.sections import BeamSection, SectionAssignment
from ..model.coordinates import CoordinateSystem, GLOBAL_COORDINATES
from ..model.records import (
    AnalysisDefinition,
    ArtifactRef,
    JobRecord,
    MeshRecord,
    OutputRequest,
)
from ..model.regions import RegionRegistry, region_from_dict
from ..model.units import UnitProfile, unit_profile

__all__ = [
    "FORMAT_VERSION",
    "ProjectFileError",
    "load_project",
    "project_from_dict",
    "project_to_dict",
    "save_project",
]

FORMAT_VERSION = 4
SUFFIX = ".anyfem"


class ProjectFileError(ValueError):
    """Raised when a project file cannot be read."""


# ----------------------------------------------------------------------
# writing
# ----------------------------------------------------------------------
def project_to_dict(project: Project) -> Dict[str, Any]:
    """The whole model as plain data."""

    # Fold any direct edits through the historical assignment dictionaries
    # into canonical region-backed records before taking the persisted view.
    project.resolve_section_assignments(strict=False)
    geometry = project.geometry
    return {
        "anyfem": {
            "schema": "anyfem.project",
            "format": FORMAT_VERSION,
            "document_id": project.document_id,
            "artifact_root": f"{project.name}.anyfem-data",
        },
        "name": project.name,
        # Imported solver models are deliberately mesh-native.  Keeping this
        # state explicit prevents an empty geometry model from being mistaken
        # for an unfinished modelled project when the document is reopened.
        "mesh_only": bool(project.mesh_only),
        "imported_format": project.imported_format,
        "imported_semantics_artifact_id": (
            project.imported_semantics_artifact_id
        ),
        "units": project.units.to_dict(),
        "coordinate_systems": [
            system.to_dict()
            for system in sorted(
                project.coordinate_systems.values(), key=lambda item: item.id
            )
        ],
        "regions": project.regions.to_list(),
        "output_requests": [
            item.to_dict()
            for item in sorted(
                project.output_requests.values(), key=lambda value: value.id
            )
        ],
        "geometry": geometry_to_dict(geometry),
        "materials": [
            {**material.to_dict(), "id": project.material_ids.get(material.name)}
            for material in _by_name(project.materials)
        ],
        "plate_sections": [
            {
                "id": section.id,
                "name": section.name,
                "thickness": section.thickness,
                "material": section.material,
            }
            for section in _by_name(project.plate_sections)
        ],
        "beam_sections": [
            {
                "id": section.id,
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
        "assignment_ids": {
            "faces": {
                str(key): value
                for key, value in sorted(project.face_assignment_ids.items())
            },
            "edges": {
                str(key): value
                for key, value in sorted(project.edge_assignment_ids.items())
            },
        },
        "assignments": {
            "sections": [
                item.to_dict()
                for item in sorted(
                    project.section_assignments.values(),
                    key=lambda value: value.id,
                )
            ]
        },
        "supports": [
            {
                "id": support.id,
                "name": support.name,
                "ref": _ref(support.ref),
                "region": _region_ref(support.region),
                "coordinate_system_id": support.coordinate_system_id,
                "constraints": {
                    key: float(value) for key, value in support.constraints.items()
                },
            }
            for support in project.supports
        ],
        "masses": [
            {
                "id": mass.id,
                "name": mass.name,
                "ref": _ref(mass.ref),
                "value": mass.value,
                "region": _region_ref(mass.region),
                "distribution_policy": mass.distribution_policy,
            }
            for mass in project.masses
        ],
        "load_cases": [
            _load_case_to_dict(case) for case in _by_name(project.load_cases)
        ],
        "combinations": [
            {"id": item.id, "name": item.name, "factors": dict(item.factors)}
            for item in _by_name(project.combinations)
        ],
        "imperfections": [
            {
                "id": item.id,
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
            "target_size": project.target_size,
            "seeding_overrides": {
                str(key): int(value)
                for key, value in sorted(project.seeding_overrides.items())
            },
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
        "analyses": [
            item.to_dict()
            for item in sorted(project.analyses.values(), key=lambda value: value.id)
        ],
        "mesh_records": [
            item.to_dict()
            for item in sorted(project.mesh_records.values(), key=lambda value: value.id)
        ],
        "jobs": [
            item.to_dict()
            for item in sorted(project.jobs.values(), key=lambda value: value.id)
        ],
        "artifacts": [
            item.to_dict()
            for item in sorted(project.artifacts.values(), key=lambda value: value.id)
        ],
    }


def save_project(project: Project, path: str | Path) -> Path:
    """Atomically write a project, preserving the prior valid file."""

    destination = Path(path)
    if not destination.suffix:
        destination = destination.with_suffix(SUFFIX)
    destination.parent.mkdir(parents=True, exist_ok=True)
    document = project_to_dict(project)
    document["anyfem"]["artifact_root"] = f"{destination.name}-data"
    payload = json.dumps(document, indent=2, allow_nan=False)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        # Prove the complete bytes are readable before replacing a user's
        # last valid project.
        project_from_dict(json.loads(temporary.read_text(encoding="utf-8")))
        if destination.exists():
            backup = destination.with_suffix(destination.suffix + ".bak")
            backup.write_bytes(destination.read_bytes())
        os.replace(temporary, destination)
    finally:
        if temporary.exists():
            temporary.unlink()
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

    document_id = str(header.get("document_id", ""))
    if not document_id:
        # Legacy files had no document identity.  Derive it from their exact
        # persisted semantics so repeated v1-v3 migrations produce identical
        # hidden regions and assignment UUIDs instead of fresh random IDs.
        legacy_payload = json.dumps(
            data,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        document_id = str(
            uuid5(NAMESPACE_URL, f"anyfem:legacy-document:{legacy_payload}")
        )
    if version >= 4:
        mesh_only_value = data.get("mesh_only", False)
        if not isinstance(mesh_only_value, bool):
            raise ProjectFileError("mesh_only must be true or false")
        mesh_only = mesh_only_value
        imported_format = _optional_text(
            data.get("imported_format"), "imported_format"
        )
        imported_artifact_id = _optional_text(
            data.get("imported_semantics_artifact_id"),
            "imported_semantics_artifact_id",
        )
    else:
        # Versions 1--3 predate imported-document persistence.  Their empty
        # geometry still means an ordinary model, exactly as it did then.
        mesh_only = False
        imported_format = None
        imported_artifact_id = None
    units_data = data.get("units")
    units = (
        UnitProfile.from_dict(units_data)
        if isinstance(units_data, Mapping)
        else unit_profile()
    )

    geometry_data = data.get("geometry", {})
    if not isinstance(geometry_data, Mapping):
        raise ProjectFileError("geometry must be a JSON object")
    if geometry_data.get("schema") == "anygeometry":
        try:
            geometry = geometry_from_dict(geometry_data)
        except GeometryError as error:
            raise ProjectFileError(f"geometry: {error}") from None
        project = Project(
            name=str(data.get("name", "model")),
            geometry=geometry,
            units=units,
            mesh_only=mesh_only,
            imported_format=imported_format,
            imported_semantics_artifact_id=imported_artifact_id,
            **({"document_id": document_id} if document_id else {}),
        )
    else:
        # Formats 1 and 2 embedded the original ANYmesher-era topology schema.
        # Keep reading those files, then write the owner codec on the next save.
        project = Project(
            name=str(data.get("name", "model")),
            units=units,
            mesh_only=mesh_only,
            imported_format=imported_format,
            imported_semantics_artifact_id=imported_artifact_id,
            **({"document_id": document_id} if document_id else {}),
        )
        _geometry_from_dict(project.geometry, geometry_data)

    coordinate_data = data.get("coordinate_systems", ())
    if coordinate_data:
        if not isinstance(coordinate_data, list):
            raise ProjectFileError("coordinate_systems must be a list")
        project.coordinate_systems.clear()
        for entry in coordinate_data:
            system = CoordinateSystem.from_dict(entry)
            if system.id in project.coordinate_systems:
                raise ProjectFileError(
                    f"duplicate coordinate-system ID {system.id!r}"
                )
            project.coordinate_systems[system.id] = system
        project.coordinate_systems.setdefault("global", GLOBAL_COORDINATES)

    regions_data = data.get("regions", ())
    if regions_data:
        if not isinstance(regions_data, list):
            raise ProjectFileError("regions must be a list")
        project.regions = RegionRegistry(region_from_dict(entry) for entry in regions_data)

    output_request_data = data.get("output_requests", ())
    if output_request_data:
        if not isinstance(output_request_data, list):
            raise ProjectFileError("output_requests must be a list")
        for index, entry in enumerate(output_request_data):
            if not isinstance(entry, Mapping):
                raise ProjectFileError(
                    f"output_requests[{index}] must be a JSON object"
                )
            try:
                project.add_output_request(OutputRequest.from_dict(entry))
            except (TypeError, ValueError) as error:
                raise ProjectFileError(
                    f"output_requests[{index}]: {error}"
                ) from None

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
        if entry.get("id"):
            project.material_ids[material.name] = str(entry["id"])
        else:
            project.material_ids[material.name] = str(
                uuid5(NAMESPACE_URL, f"{project.document_id}:material:{material.name}")
            )
    for entry in data.get("plate_sections", ()):
        project.add_plate_section(
            name=str(entry["name"]),
            thickness=float(entry["thickness"]),
            material=str(entry["material"]),
            id=None if entry.get("id") is None else str(entry["id"]),
        )
    for entry in data.get("beam_sections", ()):
        project.add_beam_section(
            BeamSection(
                id=str(entry.get("id")) if entry.get("id") else str(uuid4()),
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
        _restore_section_assignment(
            project, "face", face_id, section, "face_sections"
        )
    for edge_id, section in data.get("edge_sections", {}).items():
        _restore_section_assignment(
            project, "edge", edge_id, section, "edge_sections"
        )
    assignment_ids = data.get("assignment_ids", {})
    if isinstance(assignment_ids, Mapping):
        faces = assignment_ids.get("faces", {})
        edges = assignment_ids.get("edges", {})
        if isinstance(faces, Mapping):
            for key, value in faces.items():
                identifier = int(key)
                if identifier in project.face_sections:
                    project.face_assignment_ids[identifier] = str(value)
        if isinstance(edges, Mapping):
            for key, value in edges.items():
                identifier = int(key)
                if identifier in project.edge_sections:
                    project.edge_assignment_ids[identifier] = str(value)
    for identifier in project.face_sections:
        project.face_assignment_ids.setdefault(
            identifier,
            str(uuid5(NAMESPACE_URL, f"{project.document_id}:face-assignment:{identifier}")),
        )
    for identifier in project.edge_sections:
        project.edge_assignment_ids.setdefault(
            identifier,
            str(uuid5(NAMESPACE_URL, f"{project.document_id}:edge-assignment:{identifier}")),
        )

    assignments_data = data.get("assignments", {})
    if assignments_data is None:
        assignments_data = {}
    if not isinstance(assignments_data, Mapping):
        raise ProjectFileError("assignments must be a JSON object")
    section_assignment_data = assignments_data.get(
        "sections", data.get("section_assignments", ())
    )
    if not isinstance(section_assignment_data, list) and not isinstance(
        section_assignment_data, tuple
    ):
        raise ProjectFileError("assignments.sections must be a list")
    if section_assignment_data:
        for index, entry in enumerate(section_assignment_data):
            if not isinstance(entry, Mapping):
                raise ProjectFileError(
                    f"assignments.sections[{index}] must be a JSON object"
                )
            assignment = SectionAssignment.from_dict(entry)
            if assignment.id in project.section_assignments:
                raise ProjectFileError(
                    f"duplicate section assignment ID {assignment.id!r}"
                )
            try:
                project._require_section_id(  # type: ignore[attr-defined]
                    assignment.kind, assignment.section_id
                )
                project._require_assignment_region(  # type: ignore[attr-defined]
                    assignment
                )
            except ValueError as error:
                raise ProjectFileError(
                    f"assignments.sections[{index}]: {error}"
                ) from None
            project.section_assignments[assignment.id] = assignment
        # Canonical records win over their redundant compatibility cache.  An
        # unresolved feature output remains represented and diagnostic; it is
        # never replaced with the old numeric target.
        project.resolve_section_assignments(strict=False, adopt_legacy=False)
    elif not project.mesh_only:
        # Sequential v1-v3 migration (and early v4 files): preserve established
        # IDs when present, otherwise derive deterministic UUIDs and singleton
        # regions from the document identity and topology reference.
        project.resolve_section_assignments(strict=False, adopt_legacy=True)

    for entry in data.get("supports", ()):
        support = Support(
            id=str(entry.get("id")) if entry.get("id") else str(uuid4()),
            name=entry["name"],
            ref=_existing_ref(project, entry["ref"], "support.ref"),
            constraints={
                key: float(value)
                for key, value in entry["constraints"].items()
            },
            region=_region_ref_from(project, entry.get("region"), "support.region"),
            coordinate_system_id=str(entry.get("coordinate_system_id", "global")),
        )
        if project.mesh_only:
            project.supports.append(support)
        else:
            project.add_support(support)
    for entry in data.get("masses", ()):
        mass = Mass(
            id=str(entry.get("id")) if entry.get("id") else str(uuid4()),
            ref=_existing_ref(project, entry["ref"], "mass.ref"),
            value=float(entry["value"]),
            name=entry.get("name", "mass"),
            region=_region_ref_from(project, entry.get("region"), "mass.region"),
            distribution_policy=str(entry.get("distribution_policy", "total_distributed")),
        )
        if project.mesh_only:
            project.masses.append(mass)
        else:
            project.add_mass(mass)

    for entry in data.get("load_cases", ()):
        _load_case_from_dict(project, entry)
    for entry in data.get("combinations", ()):
        combination = project.add_combination(
            name=str(entry["name"]),
            factors={str(k): float(v) for k, v in entry["factors"].items()},
        )
        if entry.get("id"):
            project.combinations[combination.name] = replace(
                combination, id=str(entry["id"])
            )
    for index, entry in enumerate(data.get("imperfections", ())):
        imperfection = Imperfection(
            ref=_existing_ref(
                project, entry["ref"], "imperfection.ref"
            ),
            kind=entry.get("kind", "auto"),
            amplitude=entry.get("amplitude"),
            direction=tuple(entry.get("direction", (0.0, 0.0, 1.0))),
            waves=tuple(entry.get("waves", (1, 1))),
            axes=tuple(entry.get("axes", (0, 1))),
            name=entry.get("name", "imperfection"),
            id=str(
                entry.get("id")
                or uuid5(
                    NAMESPACE_URL,
                    f"{project.document_id}:imperfection:{index}",
                )
            ),
        )
        if project.mesh_only:
            project.imperfections.append(imperfection)
        else:
            project.add_imperfection(imperfection)

    meshing = data.get("meshing")
    if isinstance(meshing, Mapping):
        # Absent in files written before meshing controls existed, which is
        # what the defaults are for.
        project.set_element_order(str(meshing.get("element_order", "linear")))
        target_size = meshing.get("target_size")
        project.target_size = None if target_size is None else float(target_size)
        overrides = meshing.get("seeding_overrides", {})
        if not isinstance(overrides, Mapping):
            raise ProjectFileError("meshing.seeding_overrides must be an object")
        project.seeding_overrides = {
            int(key): int(value) for key, value in overrides.items()
        }
        for entry in meshing.get("refinements", ()):
            center = entry.get("center")
            reference = entry.get("ref")
            refinement = Refinement(
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
            if project.mesh_only:
                project.refinements.append(refinement)
            else:
                project.add_refinement(refinement)

    for index, entry in enumerate(data.get("analyses", ())):
        if not isinstance(entry, Mapping):
            raise ProjectFileError(f"analyses[{index}] must be a JSON object")
        analysis = AnalysisDefinition.from_dict(entry)
        analysis = _migrate_legacy_output_requests(project, analysis)
        project.add_analysis(analysis)
    for entry in data.get("mesh_records", ()):
        project.add_mesh_record(MeshRecord.from_dict(entry))
    for entry in data.get("jobs", ()):
        project.add_job(JobRecord.from_dict(entry))
    for entry in data.get("artifacts", ()):
        project.add_artifact(ArtifactRef.from_dict(entry))
    if (
        project.imported_semantics_artifact_id is not None
        and project.imported_semantics_artifact_id not in project.artifacts
    ):
        raise ProjectFileError(
            "imported_semantics_artifact_id names no project artifact: "
            f"{project.imported_semantics_artifact_id!r}"
        )
    if project.imported_semantics_artifact_id is not None:
        imported_artifact = project.artifacts[
            project.imported_semantics_artifact_id
        ]
        if imported_artifact.kind != "mesh":
            raise ProjectFileError(
                "imported_semantics_artifact_id must name a mesh artifact"
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
def _migrate_legacy_output_requests(
    project: Project, analysis: AnalysisDefinition
) -> AnalysisDefinition:
    """Upgrade only legacy requests that carry complete, explicit intent.

    A pre-v4 dictionary such as ``{"stress": True}`` says nothing about the
    required scope or result location.  It remains in the compatibility field
    and validation blocks a solve until an engineer resolves it; migration
    never invents a node/element scope.  Complete entries receive UUID5 IDs so
    repeated migration of the same file is byte-for-byte deterministic.
    """

    legacy = dict(analysis.output_requests)
    if not legacy:
        return analysis

    candidates: list[tuple[str, Mapping[str, Any]]] = []
    structured = (
        ("quantity_keys" in legacy or "quantities" in legacy)
        and ("region" in legacy or "region_id" in legacy)
        and "location" in legacy
    )
    if structured:
        candidates.append(("request", legacy))
    else:
        raw_requests = legacy.get("requests")
        if isinstance(raw_requests, list):
            for index, value in enumerate(raw_requests):
                if isinstance(value, Mapping):
                    candidates.append((f"requests:{index}", value))
        for key, value in legacy.items():
            if key == "requests" or not isinstance(value, Mapping):
                continue
            entry = dict(value)
            entry.setdefault("quantity_keys", (str(key),))
            entry.setdefault("label", str(key).replace("_", " ").title())
            candidates.append((f"key:{key}", entry))

    migrated_markers: set[str] = set()
    identifiers = list(analysis.output_request_ids)
    for marker, value in candidates:
        entry = dict(value)
        if not (
            (entry.get("quantity_keys") is not None or entry.get("quantities") is not None)
            and (entry.get("region") is not None or entry.get("region_id") is not None)
            and entry.get("location") is not None
        ):
            continue
        canonical = json.dumps(
            entry, sort_keys=True, separators=(",", ":"), allow_nan=False
        )
        entry.setdefault(
            "id",
            str(
                uuid5(
                    NAMESPACE_URL,
                    f"{project.document_id}:output-request:{analysis.id}:"
                    f"{marker}:{canonical}",
                )
            ),
        )
        entry.setdefault("label", f"{analysis.name} output")
        try:
            request = OutputRequest.from_dict(entry)
        except (TypeError, ValueError):
            # Preserve malformed/incomplete legacy data for an explicit user
            # decision.  The analysis validator will report that it remains.
            continue
        if request.region.id not in project.regions:
            continue
        existing = project.output_requests.get(request.id)
        if existing is None:
            project.add_output_request(request)
        elif existing.semantic_dict() != request.semantic_dict():
            raise ProjectFileError(
                f"legacy output request {request.id!r} conflicts with the "
                "typed output-request registry"
            )
        if request.id not in identifiers:
            identifiers.append(request.id)
        migrated_markers.add(marker)

    if structured and "request" in migrated_markers:
        remaining: dict[str, Any] = {}
    else:
        remaining = dict(legacy)
        for marker in migrated_markers:
            if marker.startswith("key:"):
                remaining.pop(marker.split(":", 1)[1], None)
        if "requests" in remaining:
            values = remaining.get("requests")
            if isinstance(values, list):
                incomplete = [
                    value
                    for index, value in enumerate(values)
                    if f"requests:{index}" not in migrated_markers
                ]
                if incomplete:
                    remaining["requests"] = incomplete
                else:
                    remaining.pop("requests", None)
    return replace(
        analysis,
        output_request_ids=tuple(identifiers),
        output_requests=remaining,
    )


def _by_name(mapping):
    return [mapping[key] for key in sorted(mapping)]


def _vector(values) -> List[float]:
    return [float(value) for value in np.asarray(values, dtype=float).ravel()]


def _ref(ref: EntityRef) -> Dict[str, Any]:
    return {"kind": ref.kind, "id": int(ref.id)}


def _region_ref(reference: RegionRef | None) -> str | None:
    return None if reference is None else reference.id


def _region_ref_from(
    project: Project, value: object, context: str
) -> RegionRef | None:
    if value is None:
        return None
    identifier = str(value.get("id")) if isinstance(value, Mapping) else str(value)
    if identifier not in project.regions:
        raise ProjectFileError(f"{context} references missing region {identifier!r}")
    return RegionRef(identifier)


def _ref_from(data: Mapping[str, Any]) -> EntityRef:
    if not isinstance(data, Mapping):
        raise ValueError("an entity reference must be an object")
    kind = str(data["kind"])
    if kind not in ("vertex", "edge", "face"):
        raise ValueError(
            f"unknown entity kind {kind!r}; expected vertex, edge or face"
        )
    raw_id = data["id"]
    if isinstance(raw_id, bool):
        raise ValueError("an entity reference ID must be a positive integer")
    identifier = int(raw_id)
    if identifier <= 0 or (
        isinstance(raw_id, float) and not raw_id.is_integer()
    ):
        raise ValueError("an entity reference ID must be a positive integer")
    return EntityRef(kind, identifier)  # type: ignore[arg-type]


def _existing_ref(
    project: Project, data: Mapping[str, Any], context: str
) -> EntityRef:
    """Decode one serialized reference and prove its target exists."""

    try:
        ref = _ref_from(data)
        if project.mesh_only:
            # Imported groups use the EntityRef vocabulary but intentionally
            # have no ANYgeometry entity behind them.  Their existence is
            # proved against the restored mesh association later.
            return ref
        return project.geometry.entity_ref(ref.kind, ref.id)
    except (KeyError, TypeError, ValueError) as error:
        raise ProjectFileError(f"{context}: {error}") from None


def _optional_text(value: object, context: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ProjectFileError(f"{context} must be a non-empty string or null")
    return value


def _restore_section_assignment(
    project: Project,
    kind: str,
    raw_id: object,
    raw_section: object,
    context: str,
) -> None:
    try:
        if isinstance(raw_id, bool):
            raise ValueError
        identifier = int(raw_id)
    except (TypeError, ValueError):
        raise ProjectFileError(f"{context} IDs must be positive integers") from None
    if identifier <= 0:
        raise ProjectFileError(f"{context} IDs must be positive integers")
    section = str(raw_section)
    available = project.plate_sections if kind == "face" else project.beam_sections
    if section not in available:
        label = "plate" if kind == "face" else "beam"
        raise ProjectFileError(f"no {label} section named {section!r}")
    if not project.mesh_only:
        try:
            project.geometry.entity_ref(kind, identifier)
        except (KeyError, TypeError, ValueError) as error:
            raise ProjectFileError(f"{context}: {error}") from None
    target = project.face_sections if kind == "face" else project.edge_sections
    target[identifier] = section


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
        "id": case.id,
        "name": case.name,
        "follower_pressure": bool(case.follower_pressure),
        "gravity": None if case.gravity is None else _vector(case.gravity),
        "gravity_coordinate_system_id": case.gravity_coordinate_system_id,
        "point_loads": [
            {
                "id": load.id,
                "ref": _ref(load.ref),
                "region": _region_ref(load.region),
                "force": _vector(load.force),
                "moment": _vector(load.moment),
                "coordinate_system_id": load.coordinate_system_id,
                "distribution_policy": load.distribution_policy,
            }
            for load in case.point_loads
        ],
        "pressures": [
            {
                "id": load.id, "ref": _ref(load.ref),
                "region": _region_ref(load.region), "value": float(load.value),
            }
            for load in case.pressures
        ],
        "line_loads": [
            {
                "id": load.id, "ref": _ref(load.ref),
                "region": _region_ref(load.region),
                "coordinate_system_id": load.coordinate_system_id,
                "force_per_length": _vector(load.force_per_length),
            }
            for load in case.line_loads
        ],
        "surface_tractions": [
            {
                "id": load.id, "ref": _ref(load.ref),
                "region": _region_ref(load.region),
                "coordinate_system_id": load.coordinate_system_id,
                "traction": _vector(load.traction),
            }
            for load in case.surface_tractions
        ],
    }


def _load_case_from_dict(project: Project, data: Mapping[str, Any]) -> LoadCase:
    case = project.load_case(str(data["name"]))
    if data.get("id"):
        case.id = str(data["id"])
    case.gravity_coordinate_system_id = str(
        data.get("gravity_coordinate_system_id", "global")
    )
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
        load = case.add_point_load(
            ref=_existing_ref(
                project,
                entry["ref"],
                f"load_cases[{case.name!r}].point_loads[{index}].ref",
            ),
            force=entry["force"],
            moment=entry["moment"],
            region=_region_ref_from(
                project, entry.get("region"),
                f"load_cases[{case.name!r}].point_loads[{index}].region",
            ),
            coordinate_system_id=str(entry.get("coordinate_system_id", "global")),
            distribution_policy=str(entry.get("distribution_policy", "per_target")),
        )
        if entry.get("id"):
            case.point_loads[-1] = replace(load, id=str(entry["id"]))
    for index, entry in enumerate(data.get("pressures", ())):
        load = case.add_pressure(
            ref=_existing_ref(
                project,
                entry["ref"],
                f"load_cases[{case.name!r}].pressures[{index}].ref",
            ),
            value=entry["value"],
            region=_region_ref_from(
                project, entry.get("region"),
                f"load_cases[{case.name!r}].pressures[{index}].region",
            ),
        )
        if entry.get("id"):
            case.pressures[-1] = replace(load, id=str(entry["id"]))
    for index, entry in enumerate(data.get("line_loads", ())):
        load = case.add_line_load(
            ref=_existing_ref(
                project,
                entry["ref"],
                f"load_cases[{case.name!r}].line_loads[{index}].ref",
            ),
            force_per_length=entry["force_per_length"],
            region=_region_ref_from(
                project, entry.get("region"),
                f"load_cases[{case.name!r}].line_loads[{index}].region",
            ),
            coordinate_system_id=str(entry.get("coordinate_system_id", "global")),
        )
        if entry.get("id"):
            case.line_loads[-1] = replace(load, id=str(entry["id"]))
    for index, entry in enumerate(data.get("surface_tractions", ())):
        load = case.add_surface_traction(
            ref=_existing_ref(
                project,
                entry["ref"],
                f"load_cases[{case.name!r}].surface_tractions[{index}].ref",
            ),
            traction=entry["traction"],
            region=_region_ref_from(
                project, entry.get("region"),
                f"load_cases[{case.name!r}].surface_tractions[{index}].region",
            ),
            coordinate_system_id=str(entry.get("coordinate_system_id", "global")),
        )
        if entry.get("id"):
            case.surface_tractions[-1] = replace(load, id=str(entry["id"]))
    return case
