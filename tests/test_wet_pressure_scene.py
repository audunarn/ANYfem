"""Wet surfaces and fluid pressure as drawn in the viewport overlay."""

from __future__ import annotations

import numpy as np
import pytest

from anyfem import Project, pinned, steel
from anyfem.model import WetSurface
from anyfem.ui.scene import COLOR_WET, OVERLAY_SYMBOL_LIMIT, build_attribute_overlay


def _wall(height=3.0):
    """A 2 m wide vertical plate in the plane y = 0, pinned all round."""

    project = Project(name="wall")
    project.add_material(steel("S355", 0.010))
    project.add_plate_section("plate", thickness=0.010, material="S355")
    geometry = project.geometry
    ids = geometry.add_points([(0, 0, 0), (2, 0, 0), (2, 0, height), (0, 0, height)])
    edges = geometry.add_polyline(ids, close=True)
    face = geometry.add_face(edges)
    project.assign_plate(face, "plate")
    for edge in edges:
        project.add_support(pinned(project.edge(edge)))
    return project, face


def _wet_arrows(scene):
    return [a for a in scene.arrows if a.color == COLOR_WET]


def _wet_lines(scene):
    return [line for line in scene.lines if line.color == COLOR_WET]


def test_a_wet_plate_is_outlined_in_the_wet_colour_without_stealing_clicks():
    project, face = _wall()
    project.add_wet_surface(WetSurface(ref=project.face(face)))
    scene = build_attribute_overlay(project, case_name=None)
    lines = _wet_lines(scene)
    assert lines
    assert all(line.ref is None and line.annotation for line in lines)
    outline = max(lines, key=lambda line: line.width).points
    assert np.allclose(outline[0], outline[-1]), "the outline closes on itself"
    assert outline[:, 1] == pytest.approx(0.0)
    assert outline[:, 0].min() == pytest.approx(0.0) and outline[:, 0].max() == pytest.approx(2.0)
    assert outline[:, 2].max() == pytest.approx(3.0)


def test_no_wet_surface_draws_nothing_extra():
    project, face = _wall()
    scene = build_attribute_overlay(project, case_name="default")
    assert not _wet_lines(scene) and not _wet_arrows(scene)


def test_the_loads_toggle_hides_the_wet_overlay():
    project, face = _wall()
    project.add_wet_surface(WetSurface(ref=project.face(face)))
    project.load_case().add_wet_pressure(value=1000.0)
    scene = build_attribute_overlay(project, case_name="default", show_loads=False)
    assert not _wet_lines(scene) and not _wet_arrows(scene)


@pytest.mark.parametrize("meshed", [False, True])
def test_arrows_point_into_the_plate_from_the_fluid_side(meshed):
    project, face = _wall()
    # The interior point is on the +y side, so the fluid is at -y and pushes +y.
    project.add_wet_surface(
        WetSurface(
            ref=project.face(face),
            fluid_side="away_from_point",
            interior_point=(1.0, 5.0, 1.0),
        )
    )
    project.load_case().add_wet_pressure(value=1000.0)
    mesh = project.generate_mesh(0.5) if meshed else None
    arrows = _wet_arrows(build_attribute_overlay(project, case_name="default", mesh=mesh))
    assert arrows
    for arrow in arrows:
        push = arrow.end - arrow.start
        assert push[1] > 0.0 and abs(push[0]) < 1e-9 and abs(push[2]) < 1e-9
        assert arrow.end[1] == pytest.approx(0.0), "the arrow ends on the plate"


def test_a_negative_pressure_reverses_the_arrows():
    project, face = _wall()
    project.add_wet_surface(
        WetSurface(
            ref=project.face(face),
            fluid_side="away_from_point",
            interior_point=(1.0, 5.0, 1.0),
        )
    )
    project.load_case().add_wet_pressure(value=-1000.0)
    for arrow in _wet_arrows(build_attribute_overlay(project, case_name="default")):
        assert (arrow.end - arrow.start)[1] < 0.0


@pytest.mark.parametrize("meshed", [False, True])
def test_hydrostatic_arrows_are_longer_deeper_and_absent_above_the_waterline(meshed):
    project, face = _wall()
    project.add_wet_surface(WetSurface(ref=project.face(face), fluid_side="negative_normal"))
    case = project.load_case()
    case.set_parameter("draft", 2.5)
    case.add_wet_pressure(expression="10000 * max(0, draft - z)")
    mesh = project.generate_mesh(0.25) if meshed else None
    arrows = _wet_arrows(build_attribute_overlay(project, case_name="default", mesh=mesh))
    assert arrows
    assert all(arrow.end[2] < 2.5 for arrow in arrows), "dry elements are not drawn"
    by_depth = sorted(arrows, key=lambda arrow: arrow.end[2])
    lengths = [float(np.linalg.norm(arrow.end - arrow.start)) for arrow in by_depth]
    assert lengths == sorted(lengths, reverse=True)
    assert lengths[0] > lengths[-1]


def test_the_overlay_shows_the_requested_case_only():
    project, face = _wall()
    project.add_wet_surface(WetSurface(ref=project.face(face), fluid_side="negative_normal"))
    project.load_case("a").add_wet_pressure(value=1000.0)
    project.load_case("b").add_wet_pressure(value=1000.0)
    project.load_case("b").add_wet_pressure(value=500.0)
    a = _wet_arrows(build_attribute_overlay(project, case_name="a"))
    none = _wet_arrows(build_attribute_overlay(project, case_name="missing"))
    assert a and not none


def test_a_mapping_by_element_is_drawn_on_the_mesh_and_skipped_before_it():
    project, face = _wall()
    project.add_wet_surface(WetSurface(ref=project.face(face), fluid_side="negative_normal"))
    mesh = project.generate_mesh(0.5)
    element = sorted(mesh.elements_of_face[face])[0]
    project.load_case().add_wet_pressure(mapping={element: 1000.0}, mapping_key="element")
    assert not _wet_arrows(build_attribute_overlay(project, case_name="default"))
    arrows = _wet_arrows(build_attribute_overlay(project, case_name="default", mesh=mesh))
    assert len(arrows) == 1


def test_a_dense_mesh_stays_within_the_symbol_budget():
    project, face = _wall()
    project.add_wet_surface(WetSurface(ref=project.face(face), fluid_side="negative_normal"))
    project.load_case().add_wet_pressure(value=1000.0)
    mesh = project.generate_mesh(0.05)
    assert len(mesh.elements_of_face[face]) > 3 * OVERLAY_SYMBOL_LIMIT
    arrows = _wet_arrows(build_attribute_overlay(project, case_name="default", mesh=mesh))
    assert 0 < len(arrows) <= OVERLAY_SYMBOL_LIMIT


def test_an_unresolvable_wet_pressure_does_not_break_drawing():
    project, face = _wall()
    project.add_wet_surface(WetSurface(ref=project.face(face)))
    project.load_case().add_wet_pressure(expression="undefined_name * z")
    mesh = project.generate_mesh(0.5)
    scene = build_attribute_overlay(project, case_name="default", mesh=mesh)
    assert not _wet_arrows(scene)
    scene = build_attribute_overlay(project, case_name="default")
    assert not _wet_arrows(scene)
