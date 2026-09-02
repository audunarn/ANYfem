"""Acceptance workflows for the commercial-style headless model API.

These tests intentionally address modelling intent by feature, semantic group,
and named region.  Numeric topology IDs are an implementation detail of the
geometry/mesh hand-off, not something the engineer has to manage.
"""

from __future__ import annotations

import numpy as np
import pytest

from anyfem import (
    BeamSection,
    DocumentSession,
    Project,
    Region,
    RegionRef,
    Support,
    solve_buckling,
    solve_linear_static,
    steel,
)
from anyfem import commands as cmd
from anyfem.model import GeometryGroupRef, ManualRegion, RegionStatus
from anygeometry import FeatureOutputRef
from anygeometry import GeometryError


def _feature_resolver(project: Project):
    return lambda anchor: project.geometry.features.resolve(
        anchor, project.geometry
    )


def _named_group_region(
    project: Project, name: str, group: str, kind: str
) -> Region:
    return project.regions.add(
        Region(
            name,
            "geometry",
            kind,
            ManualRegion((GeometryGroupRef(group, kind),)),
        )
    )


def test_stiffened_panel_runs_linear_and_buckling_through_semantic_scopes():
    project = Project("stiffened panel")
    session = DocumentSession(project)
    feature = session.execute(
        cmd.AddFeature(
            "generator.stiffened_panel",
            name="Main stiffened panel",
            parameters={
                "length": 2.0,
                "width": 1.0,
                "longitudinal_spacing": 0.34,
                "transverse_spacing": 1.0,
                "semantic_group": "deck",
            },
        )
    )

    assert feature.state == "ok"
    assert project.geometry_group("deck", kind="face")
    assert project.geometry_group("longitudinal_stiffeners", kind="edge")

    project.add_material(steel("S355", 0.010))
    project.add_plate_section("deck plating", 0.010, "S355")
    project.add_beam_section(
        BeamSection(
            "flat bars",
            "Flatbar",
            "S355",
            flange_width=0.10,
            flange_thickness=0.010,
            web_direction=(0.0, 0.0, 1.0),
        )
    )
    project.assign_plate_group("deck", "deck plating")
    project.assign_beam_group("longitudinal_stiffeners", "flat bars")
    project.assign_beam_group("transverse_stiffeners", "flat bars")

    deck = _named_group_region(project, "Deck plates", "deck", "face")
    boundary = _named_group_region(
        project, "Panel boundary", "boundaries", "edge"
    )
    region_context = {
        "geometry": project.geometry,
        "feature_resolver": _feature_resolver(project),
    }
    deck_members = project.regions.resolve(deck.id, **region_context)
    boundary_members = project.regions.resolve(boundary.id, **region_context)

    # One object owns the engineering condition.  Its current materialized
    # reference is retained only for backward compatibility; the named region
    # is the canonical multi-entity scope.
    project.add_support(
        Support(
            "Clamped panel boundary",
            boundary_members[0],
            {name: 0.0 for name in ("ux", "uy", "uz", "rx", "ry", "rz")},
            region=RegionRef(boundary.id),
        )
    )
    project.load_case("design").add_pressure(
        deck_members[0],
        10_000.0,
        region=RegionRef(deck.id),
    )

    mesh = project.generate_mesh(0.25)
    linear = solve_linear_static(project, mesh=mesh, load_case="design")
    buckling = solve_buckling(
        project, mesh=mesh, load_case="design", num_modes=1
    )

    assert mesh.nodes
    assert mesh.shells
    assert mesh.beams
    assert np.isfinite(linear.max_translation()[1])
    assert "nodes" in linear.summary()
    assert buckling.status == "ok"
    assert len(buckling) == 1


def test_segmented_automatic_offset_stiffener_has_one_mpc_owner_per_station():
    """A continuous stiffener split by panel bays must solve as one member."""

    project = Project("segmented automatic-offset stiffener")
    session = DocumentSession(project)
    session.execute(
        cmd.AddFeature(
            "generator.stiffened_panel",
            name="Main stiffened panel",
            parameters={
                "length": 4.0,
                "width": 2.0,
                "longitudinal_spacing": 0.5,
                "transverse_spacing": 1.0,
                "semantic_group": "shell",
            },
        )
    )
    project.add_material(steel("S355", 0.010))
    project.add_plate_section("plate", 0.010, "S355")
    project.add_beam_section(
        BeamSection(
            "stiffener",
            "T-bar",
            "S355",
            web_height=0.200,
            web_thickness=0.010,
            flange_width=0.100,
            flange_thickness=0.012,
            offset_mode="automatic",
        )
    )
    project.assign_plate_group("shell", "plate")

    def endpoints(edge_id: int) -> tuple[np.ndarray, np.ndarray]:
        edge = project.geometry.edges[edge_id]
        return (
            np.asarray(project.geometry.vertices[edge.start].position),
            np.asarray(project.geometry.vertices[edge.end].position),
        )

    stiffener_edges = []
    for edge_id in project.geometry.edges:
        start, end = endpoints(edge_id)
        if np.allclose((start[1], end[1]), (1.0, 1.0)):
            stiffener_edges.append(edge_id)
            project.assign_beam(edge_id, "stiffener")
        if np.allclose((start[0], end[0]), (0.0, 0.0)):
            project.add_support(
                Support(
                    f"left edge {edge_id}",
                    project.edge(edge_id),
                    {"ux": 0.0, "uy": 0.0, "uz": 0.0},
                )
            )
        elif np.allclose((start[0], end[0]), (4.0, 4.0)):
            project.add_support(
                Support(
                    f"right edge {edge_id}",
                    project.edge(edge_id),
                    {"uy": 0.0, "uz": 0.0},
                )
            )
    for face_id in project.geometry.faces:
        project.load_case().add_pressure(project.face(face_id), 10_000.0)

    assert len(stiffener_edges) == 4
    mesh = project.generate_mesh(0.25)
    solution = solve_linear_static(project, mesh=mesh)

    unique_coupled_stations = {
        int(coupling.beam_node) for coupling in mesh.couplings.values()
    }
    assert len(unique_coupled_stations) < len(mesh.couplings)
    assert solution.info["constraint_info"]["num_mpc_constraints"] == (
        6 * len(unique_coupled_stations)
    )
    assert np.isfinite(solution.max_translation()[1])


def test_feature_edit_suppress_undo_redo_preserves_scope_identity():
    project = Project("feature identity")
    # A nearby baseline plate proves that a missing feature output is not
    # silently replaced by whichever geometry happens to be closest.
    project.geometry.add_plate(
        project.geometry.add_points(
            ((10.0, 0.0, 0.0), (11.0, 0.0, 0.0),
             (11.0, 1.0, 0.0), (10.0, 1.0, 0.0))
        )
    )
    session = DocumentSession(project)
    feature = session.execute(
        cmd.AddFeature(
            "generator.plate",
            parameters={
                "length": 2.0,
                "width": 1.0,
                "semantic_group": "loaded deck",
            },
        )
    )
    output = FeatureOutputRef(feature.feature_id, "face/1", "face")
    region = project.regions.add(
        Region(
            "Feature-owned plate",
            "geometry",
            "face",
            ManualRegion((output,)),
        )
    )
    original_face = project.regions.resolve(
        region.id,
        geometry=project.geometry,
        feature_resolver=_feature_resolver(project),
    )[0]
    pressure = project.load_case().add_pressure(
        original_face, 5_000.0, region=RegionRef(region.id)
    )
    pressure_id = pressure.id

    before_failed_edit = session.snapshot().document
    with pytest.raises(GeometryError, match="positive"):
        session.execute(
            cmd.EditFeature(
                feature.feature_id,
                parameters={
                    "length": -3.0,
                    "width": 1.0,
                    "semantic_group": "loaded deck",
                },
            )
        )
    assert session.snapshot().document == before_failed_edit

    edited = session.execute(
        cmd.EditFeature(
            feature.feature_id,
            parameters={
                "length": 3.0,
                "width": 1.0,
                "semantic_group": "loaded deck",
            },
        )
    )
    edited_face = project.regions.resolve(
        region.id,
        geometry=project.geometry,
        feature_resolver=_feature_resolver(project),
    )[0]
    assert edited.parameters["length"] == 3.0
    assert edited_face != original_face
    assert project.load_case().pressures[0].id == pressure_id
    assert project.load_case().pressures[0].region == RegionRef(region.id)

    assert session.undo()
    restored_face = project.regions.resolve(
        region.id,
        geometry=project.geometry,
        feature_resolver=_feature_resolver(project),
    )[0]
    assert restored_face == original_face
    assert session.redo()
    assert project.regions.resolve(
        region.id,
        geometry=project.geometry,
        feature_resolver=_feature_resolver(project),
    ) == (edited_face,)

    session.execute(cmd.SuppressFeature(feature.feature_id))
    assert project.regions.status(
        region.id,
        geometry=project.geometry,
        feature_resolver=_feature_resolver(project),
    ) is RegionStatus.UNRESOLVED
    assert len(project.geometry.faces) == 1  # only the unrelated baseline face
    assert project.load_case().pressures[0].id == pressure_id
    assert project.load_case().pressures[0].region == RegionRef(region.id)

    assert session.undo()
    assert project.regions.status(
        region.id,
        geometry=project.geometry,
        feature_resolver=_feature_resolver(project),
    ) is RegionStatus.VALID
    assert session.redo()
    assert project.regions.status(
        region.id,
        geometry=project.geometry,
        feature_resolver=_feature_resolver(project),
    ) is RegionStatus.UNRESOLVED
