"""Automatic stiffener offset: the profile foot stands on the plate surface.

Owner decision 2026-11-24: with ``offset_mode="automatic"`` the stiffener foot
sits on the attached plate's surface, not on its mid-surface.  The neutral-axis
offset is therefore the profile centroid above its foot plus half the plate
thickness along the plate normal on the stiffener's side.  Manual and
centerline offsets are unchanged.

The end-to-end check compares a simply supported T-bar stiffened strip with
transformed-section (composite beam) theory, as in the independent ANYfeSolver
spot check of 2026-11-24.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from anyfem import Project, pinned, solve_linear_static, steel, support
from anyfem.geometry.operations import strip_face
from anyfem.model import BeamSection

E, NU = 210.0e9, 0.3
G = E / (2.0 * (1.0 + NU))
L, W, T = 5.0, 0.4, 0.010
HW, TW, BF, TF = 0.25, 0.008, 0.10, 0.012


def _tbar(**kwargs) -> BeamSection:
    values = dict(
        name="bar", profile="T-bar", material="S355", web_height=HW,
        web_thickness=TW, flange_width=BF, flange_thickness=TF,
        offset_mode="automatic",
    )
    values.update(kwargs)
    return BeamSection(**values)


def _strip(section: BeamSection, *, plate_section: bool = True, loads: bool = False):
    project = Project(name="strip")
    project.add_material(steel("S355", T))
    project.add_plate_section("plate", thickness=T, material="S355")
    project.add_beam_section(section)
    g = project.geometry
    pts = g.add_points([(0, 0, 0), (L, 0, 0), (L, W, 0), (0, W, 0)])
    face = g.add_face(g.add_polyline(pts, close=True))
    if plate_section:
        project.assign_plate(face, "plate")
    strips, dividers = strip_face(g, face, axis=1, count=2)
    if plate_section:
        project.assign_plates(strips, "plate")
    project.assign_beam(dividers[0], "bar")
    if loads:
        for eid in list(g.edges):
            edge = g.edges[eid]
            xs = [g.vertices[v].position[0] for v in (edge.start, edge.end)]
            if max(abs(x) for x in xs) < 1e-9:
                project.add_support(pinned(project.edge(eid)))
            elif max(abs(x - L) for x in xs) < 1e-9:
                project.add_support(support(project.edge(eid), uy=0.0, uz=0.0))
        for s in strips:
            project.load_case().add_pressure(project.face(s), 1000.0)
    return project, dividers[0]


def _profile_centroid() -> float:
    """T-bar centroid above its foot, by hand."""

    rects = [(TW, HW, 0.0), (BF, TF, HW)]
    area = sum(b * h for b, h, _ in rects)
    return sum(b * h * (z0 + h / 2.0) for b, h, z0 in rects) / area


@pytest.mark.parametrize("web_direction", [(0.0, 0.0, 1.0), None])
def test_automatic_offset_is_centroid_plus_half_plate_thickness(web_direction):
    project, edge = _strip(_tbar(web_direction=web_direction))
    offset = np.asarray(project.beam_offset_vector(edge))
    expected = _profile_centroid() + T / 2.0
    assert offset[2] == pytest.approx(expected, rel=1e-12, abs=1e-15)
    assert abs(offset[0]) < 1e-15 and abs(offset[1]) < 1e-15
    assert project.beam_offsets[edge] == pytest.approx((0.0, 0.0, expected))


def test_back_side_offset_goes_through_the_other_surface():
    project, edge = _strip(_tbar(attachment_side="back"))
    offset = np.asarray(project.beam_offset_vector(edge))
    assert offset[2] == pytest.approx(-(_profile_centroid() + T / 2.0), rel=1e-12)


def test_rotated_section_adds_half_thickness_along_the_plate_normal():
    angle = 30.0
    project, edge = _strip(_tbar(rotation_deg=angle))
    _axis, local_y, local_z = project.beam_section_frame(edge)
    y, z = project.beam_section_of(edge).centroid_from_attachment()
    offset = np.asarray(project.beam_offset_vector(edge))
    expected = y * local_y + z * local_z + np.array([0.0, 0.0, T / 2.0])
    np.testing.assert_allclose(offset, expected, rtol=0.0, atol=1e-15)
    # The web is rotated, the half thickness is not.
    assert local_z[2] == pytest.approx(math.cos(math.radians(angle)))


def test_plate_without_section_and_free_beam_add_nothing():
    project, edge = _strip(_tbar(), plate_section=False)
    assert project.beam_offset_vector(edge)[2] == pytest.approx(
        _profile_centroid(), rel=1e-12
    )

    free = Project(name="free")
    free.add_material(steel("S355", T))
    free.add_beam_section(_tbar(web_direction=(0.0, 0.0, 1.0)))
    g = free.geometry
    a, b = g.add_points([(0, 0, 0), (L, 0, 0)])
    line = g.add_line(a, b)
    free.assign_beam(line, "bar")
    assert free.beam_offset_vector(line)[2] == pytest.approx(
        _profile_centroid(), rel=1e-12
    )


@pytest.mark.parametrize(
    "mode, eccentricity, expected",
    [("manual", 0.123, (0.0, 0.0, 0.123)), ("centerline", 0.123, (0.0, 0.0, 0.0))],
)
def test_manual_and_centerline_offsets_are_unchanged(mode, eccentricity, expected):
    project, edge = _strip(
        _tbar(offset_mode=mode, eccentricity=eccentricity, web_direction=(0.0, 0.0, 1.0))
    )
    assert project.beam_offset_vector(edge) == expected


def test_tbar_strip_deflection_matches_foot_on_surface_theory():
    section = _tbar(web_direction=(0.0, 0.0, 1.0))
    project, edge = _strip(section, loads=True)
    mesh = project.generate_mesh(0.1)
    solution = solve_linear_static(project, mesh=mesh)

    rects = [(TW, HW, 0.0), (BF, TF, HW)]
    area = sum(b * h for b, h, _ in rects)
    zc = _profile_centroid()
    ib = sum(b * h ** 3 / 12 + b * h * (z0 + h / 2 - zc) ** 2 for b, h, z0 in rects)
    plate_area = W * T
    e = T / 2.0 + zc
    na = area * e / (plate_area + area)
    inertia = W * T ** 3 / 12 + plate_area * na ** 2 + ib + area * (e - na) ** 2
    q = 1000.0 * W
    props = section.properties()
    kga = props["shear_factor_z"] * props["area"] * G
    reference = 5 * q * L ** 4 / (384 * E * inertia) + q * L ** 2 / (8 * kga)

    nodes = mesh.nodes_of_edge[edge]
    xs = np.array([mesh.nodes[n][0] for n in nodes])
    middle = nodes[int(np.argmin(np.abs(xs - L / 2)))]
    deflection = abs(solution.node_displacement(middle)[2])
    assert deflection == pytest.approx(reference, rel=0.01)


def test_declared_attachments_give_the_same_plate_as_the_surface_search():
    """The barge's stiffeners are members declared on their plates.

    The fast declared route must pick the plate the full surface search picks,
    and every automatic offset must be the centroid plus half that plate's
    thickness along its normal on the stiffener side.
    """

    from anyfem.application.examples import floating_barge_section

    project = floating_barge_section()
    offsets = project.beam_offsets
    for edge_id in sorted(project.edge_sections):
        section = project.beam_section_of(edge_id)
        assert section.offset_mode == "automatic"
        samples = np.asarray(project.geometry.sample_edge(edge_id, np.asarray((0.0, 1.0))))
        searched = project._beam_attached_face_ids(edge_id, samples)
        declared = project._beam_declared_face_ids(edge_id)
        assert declared[:1] == searched[:1], edge_id
        axis, local_y, local_z = project.beam_section_frame(edge_id)
        y, z = section.centroid_from_attachment()
        face = searched[0]
        thickness = project.plate_section_of(face).thickness
        normal = np.asarray(project.geometry.face_normal(face, 0.5, 0.5), dtype=float)
        normal -= float(normal @ axis) * axis
        normal /= np.linalg.norm(normal)
        if normal @ local_z < 0.0:
            normal = -normal
        expected = y * local_y + z * local_z + 0.5 * thickness * normal
        np.testing.assert_allclose(offsets[edge_id], expected, rtol=0.0, atol=1e-15)
        assert project.beam_offset_vector(edge_id) == offsets[edge_id]
