"""Staged feature regeneration and engineering-attachment projections."""
from __future__ import annotations
from copy import copy, deepcopy
from dataclasses import replace
from typing import Any, Dict, TYPE_CHECKING
from anygeometry import EntityRef, GeometryError
if TYPE_CHECKING:
    from .project import Project


def _stage_feature_project(project: Project) -> Project:
    working_project = copy(project)
    working_project.geometry = project.geometry.clone(include_features=True)
    # Projection rebuilding and canonical legacy adoption must remain on
    # independent containers until the complete edit is known valid.
    for name in ("face_sections", "edge_sections", "face_assignment_ids", "edge_assignment_ids",
                 "section_assignments", "sheet_join_intents"):
        setattr(working_project, name, dict(getattr(project, name)))
    working_project.regions = deepcopy(project.regions)
    working_project._singleton_region_cache = {}
    working_project._singleton_region_cache_size = -1
    for name in ("supports", "masses", "imperfections", "refinements"):
        setattr(working_project, name, list(getattr(project, name)))
    working_project.load_cases = {name: copy(case) for name, case in project.load_cases.items()}
    for case in working_project.load_cases.values():
        for name in ("point_loads", "pressures", "line_loads", "surface_tractions"):
            setattr(case, name, list(getattr(case, name)))
        case._region_factory = working_project.singleton_region
    return working_project


def _attribute_snapshot(project: Project) -> Dict[str, Any]:
    """Cheap snapshot of everything attached to geometry."""

    return {
        "face_sections": dict(project.face_sections),
        "edge_sections": dict(project.edge_sections),
        "face_assignment_ids": dict(project.face_assignment_ids),
        "edge_assignment_ids": dict(project.edge_assignment_ids),
        "section_assignments": dict(project.section_assignments),
        "regions": deepcopy(project.regions),
        "sheet_join_intents": dict(project.sheet_join_intents),
        "supports": list(project.supports),
        "masses": list(project.masses),
        "imperfections": list(project.imperfections),
        "refinements": list(project.refinements),
        "element_order": project.element_order,
        "loads": {
            name: (
                list(case.point_loads),
                list(case.pressures),
                list(case.line_loads),
                list(case.surface_tractions),
            )
            for name, case in project.load_cases.items()
        },
    }


def _restore_attributes(project: Project, snapshot: Dict[str, Any]) -> None:
    if not snapshot:
        return
    project.face_sections.clear()
    project.face_sections.update(snapshot["face_sections"])
    project.edge_sections.clear()
    project.edge_sections.update(snapshot["edge_sections"])
    for name in ("face_assignment_ids", "edge_assignment_ids", "section_assignments"):
        if name in snapshot:
            getattr(project, name).clear()
            getattr(project, name).update(snapshot[name])
    if "regions" in snapshot:
        project.regions = deepcopy(snapshot["regions"])
        project._singleton_region_cache = {}
        project._singleton_region_cache_size = -1
    project.sheet_join_intents.clear()
    project.sheet_join_intents.update(snapshot.get("sheet_join_intents", {}))
    project.supports[:] = list(snapshot["supports"])
    project.masses[:] = list(snapshot.get("masses", ()))
    project.imperfections[:] = list(snapshot.get("imperfections", ()))
    project.refinements[:] = list(snapshot.get("refinements", ()))
    project.element_order = snapshot.get("element_order", project.element_order)
    for name, loads in snapshot["loads"].items():
        points, pressures, lines, *optional = loads
        case = project.load_case(name)
        case.point_loads[:] = list(points)
        case.pressures[:] = list(pressures)
        case.line_loads[:] = list(lines)
        case.surface_tractions[:] = list(optional[0] if optional else ())


def _rebind_feature_attachments(project: Project, log) -> None:
    """Refresh compatibility refs from authoritative regions/owner lineage.

    A representative may follow one exact output, but may not be guessed when
    an edit removes it or makes its replacement ambiguous. Canonical region
    scope, UUIDs and all engineering quantities remain unchanged.
    """
    replacements = dict(log)
    def rebind(item):
        reference = getattr(item, "ref", None)
        if not isinstance(reference, EntityRef):
            return item
        region = getattr(item, "region", None)
        if region is not None:
            try:
                targets = project.regions.resolve(
                    region.id, geometry=project.geometry,
                    feature_resolver=lambda anchor: project.geometry.features.resolve(anchor, project.geometry),
                )
            except (KeyError, ValueError) as error:
                raise GeometryError(f"feature edit cannot uniquely rebind {reference} for {type(item).__name__}: {error}") from error
        else:
            targets = replacements.get(reference, project.geometry.resolve_ref(reference))
        targets = tuple(dict.fromkeys(targets))
        if reference in targets:
            target = reference
        elif len(targets) == 1 and targets[0].kind == reference.kind:
            target = targets[0]
        else:
            raise GeometryError(f"feature edit cannot uniquely rebind {reference} for {type(item).__name__}")
        if target not in project.geometry.resolve_ref(target):
            raise GeometryError(f"feature edit resolved an unavailable attachment {target}")
        return item if target == reference else replace(item, ref=target)

    for name in ("supports", "masses", "imperfections", "refinements"):
        getattr(project, name)[:] = [rebind(item) for item in getattr(project, name)]
    for case in project.load_cases.values():
        for name in ("point_loads", "pressures", "line_loads", "surface_tractions"):
            getattr(case, name)[:] = [rebind(item) for item in getattr(case, name)]
    # Preserve inactive/unresolved section intent (e.g. feature suppression).
    # Its diagnostic continues to block mesh/solve through the project gate.
    project.resolve_section_assignments()


