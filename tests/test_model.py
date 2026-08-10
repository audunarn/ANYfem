"""Materials, sections, and attribute binding to geometry."""

from __future__ import annotations

import numpy as np
import pytest
from anymaterial import MaterialSpec

from anyfem import Project, fixed, simply_supported, steel, support
from anyfem.model import BeamSection, Material, ProjectError
from anyfem.model.sections import PROFILES, rectangular_bar


def test_steel_comes_from_the_solver_table():
    material = steel("S355", thickness=0.010)
    assert material.elastic_modulus == pytest.approx(210.0e9)
    assert material.yield_stress == pytest.approx(357.0e6)
    assert material.poisson_ratio == pytest.approx(0.3)


def test_steel_fails_closed_outside_the_table():
    with pytest.raises(Exception):
        steel("S355", thickness=10.0)


def test_material_rejects_impossible_properties():
    with pytest.raises(ValueError, match="Poisson"):
        Material(name="bad", elastic_modulus=210e9, poisson_ratio=0.7)
    with pytest.raises(ValueError, match="elastic modulus"):
        Material(name="bad", elastic_modulus=0.0, poisson_ratio=0.3)


def test_material_from_external_spec_keeps_compatibility_properties():
    external = MaterialSpec(
        name="editor steel",
        constants={"elastic_modulus": 200.0e9, "poisson_ratio": 0.29},
        density=7800.0,
    )

    material = Material.from_dict(external.to_dict())

    assert material.elastic_modulus == pytest.approx(200.0e9)
    assert material.poisson_ratio == pytest.approx(0.29)
    assert material.to_dict() == external.to_dict()


def test_rectangular_bar_properties_match_hand_calculation():
    width, thickness = 0.05, 0.02
    section = rectangular_bar("bar", width, thickness, "S355")
    properties = section.properties()

    assert properties["area"] == pytest.approx(width * thickness)
    assert properties["Iy"] == pytest.approx(width * thickness**3 / 12.0)
    assert properties["Iz"] == pytest.approx(thickness * width**3 / 12.0)


def test_only_solver_supported_profiles_are_accepted():
    """An unsupported profile would silently become a bare web, so reject it."""

    with pytest.raises(ValueError, match="unknown profile"):
        BeamSection(
            name="box",
            profile="Box",
            material="S355",
            web_height=0.2,
            web_thickness=0.01,
        )
    assert "T-bar" in PROFILES


def test_profile_dimensions_are_validated():
    with pytest.raises(ValueError, match="flange_width"):
        BeamSection(name="fb", profile="Flatbar", material="S355")
    with pytest.raises(ValueError, match="web_height"):
        BeamSection(name="t", profile="T-bar", material="S355")


def test_section_dimensions_must_be_finite():
    from anyfem.model import PlateSection

    with pytest.raises(ValueError, match="finite and positive"):
        PlateSection(name="plate", thickness=np.nan, material="S355")
    with pytest.raises(ValueError, match="invalid flange_width"):
        BeamSection(
            name="fb", profile="Flatbar", material="S355",
            flange_width=np.inf, flange_thickness=0.01,
        )


@pytest.mark.parametrize("direction", [(0.0, 1.0), (0.0, 0.0, 0.0), (0.0, np.nan, 1.0)])
def test_web_direction_must_be_a_finite_nonzero_vector(direction):
    with pytest.raises(ValueError, match="web_direction needs three finite"):
        BeamSection(
            name="t", profile="T-bar", material="S355",
            web_height=0.2, web_thickness=0.01,
            flange_width=0.1, flange_thickness=0.012,
            web_direction=direction,
        )


def test_web_direction_reaches_the_solver_as_orientation():
    section = BeamSection(
        name="t",
        profile="T-bar",
        material="S355",
        web_height=0.2,
        web_thickness=0.01,
        flange_width=0.1,
        flange_thickness=0.012,
        web_direction=(0.0, 0.0, 1.0),
    )
    assert np.allclose(section.properties()["orientation"], [0.0, 0.0, 1.0])


def test_assignment_requires_a_defined_section():
    project = Project()
    geometry = project.geometry
    points = geometry.add_points([(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)])
    edges = geometry.add_polyline(points, close=True)
    face = geometry.add_face(edges)

    with pytest.raises(ProjectError, match="no plate section"):
        project.assign_plate(face, "missing")


def test_section_requires_a_defined_material():
    project = Project()
    with pytest.raises(ProjectError, match="no material"):
        project.add_plate_section("plate", thickness=0.01, material="missing")


def test_support_rejects_unknown_degrees_of_freedom():
    project = Project()
    vertex = project.geometry.add_point(0, 0, 0)
    with pytest.raises(ValueError, match="unknown degrees of freedom"):
        support(project.point(vertex), uq=0.0)


def test_support_helpers_constrain_what_they_claim():
    project = Project()
    vertex = project.geometry.add_point(0, 0, 0)
    ref = project.point(vertex)

    assert set(fixed(ref).constraints) == {"ux", "uy", "uz", "rx", "ry", "rz"}
    assert set(simply_supported(ref).constraints) == {"uz"}
    assert set(support(ref, ux=0.0, rz=0.0).constraints) == {"ux", "rz"}


def test_attributes_reference_geometry_that_must_exist():
    project = Project()
    from anyfem.geometry.entities import EntityRef

    with pytest.raises(Exception):
        project.add_support(fixed(EntityRef("vertex", 999)))


def test_plate_sections_assign_from_shared_geometry_groups():
    project = Project(name="semantic groups")
    project.add_material(steel("S355", 0.010))
    project.add_plate_section("deck plate", thickness=0.010, material="S355")
    points = project.geometry.add_points(
        [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0),
         (1.0, 1.0, 0.0), (0.0, 1.0, 0.0)]
    )
    face = project.geometry.add_face(
        project.geometry.add_polyline(points, close=True)
    )
    project.geometry.add_to_group("deck", [project.face(face)])

    project.assign_plate_group("deck", "deck plate")

    assert project.face_sections == {face: "deck plate"}


def test_load_case_is_created_on_first_use():
    project = Project()
    first = project.load_case("dead")
    second = project.load_case("dead")
    assert first is second
    assert first.is_empty()

    vertex = project.geometry.add_point(0, 0, 0)
    first.add_point_load(project.point(vertex), force=(0, 0, -1000.0))
    assert not first.is_empty()


def test_gravity_defaults_to_downward():
    project = Project()
    case = project.load_case()
    case.set_gravity()
    assert np.allclose(case.gravity, [0.0, 0.0, -9.81])


def test_project_reports_every_problem_at_once():
    """Validation should not make the user fix one thing at a time."""

    project = Project(name="empty")
    geometry = project.geometry
    points = geometry.add_points([(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)])
    edges = geometry.add_polyline(points, close=True)
    geometry.add_face(edges)

    with pytest.raises(ProjectError) as excinfo:
        project.validate()
    message = str(excinfo.value)
    assert message.count("- ") >= 3


def test_project_validation_reports_stale_attribute_references():
    from anyfem.geometry import EntityRef

    project = Project(name="stale")
    project.load_case().add_point_load(
        EntityRef("vertex", 999), force=(0.0, 0.0, -1.0)
    )

    with pytest.raises(ProjectError, match="point load 0 references missing vertex999"):
        project.validate(require_supports=False)
