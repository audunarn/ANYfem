"""Mesh submissions retain the opt-in residual-triangle warning policy."""

import pytest

from anyfem.mesh_jobs import MeshSettings
from anyfem.model.materials import steel
from anyfem.model.project import Project


def _settings(**options):
    return MeshSettings.create(
        0.5, element_order="quadratic", strategy="quad_first", **options,
    )


def test_quad_triangle_angle_policy_changes_mesh_identity():
    strict = _settings()
    warning = _settings(quad_t3_minimum_angle=12.0, quad_t3_angle_action="warning")
    assert strict.quad_t3_minimum_angle == 15.0
    assert strict.quad_t3_angle_action == "error"
    assert warning.input_hash != strict.input_hash
    assert warning.quad_t3_minimum_angle == 12.0
    assert warning.quad_t3_angle_action == "warning"


@pytest.mark.parametrize("angle", [0.0, 60.0, float("nan")])
def test_quad_triangle_angle_policy_rejects_invalid_threshold(angle):
    with pytest.raises(ValueError, match="finite angle"):
        _settings(quad_t3_minimum_angle=angle)


def test_warning_policy_is_not_accepted_for_nonquad_strategy():
    with pytest.raises(ValueError, match="require strategy"):
        MeshSettings.create(
            0.5, element_order="linear", strategy="auto",
            quad_t3_angle_action="warning",
        )


def test_project_quad_mesh_records_effective_warning_policy():
    project = Project(name="quad angle")
    project.add_material(steel("S355", 0.010))
    project.add_plate_section("plate", thickness=0.010, material="S355")
    geometry = project.geometry
    points = geometry.add_points(
        ((0.0, 0.0, 0.0), (2.0, 0.0, 0.0),
         (2.0, 1.0, 0.0), (0.0, 1.0, 0.0)),
    )
    project.assign_plate(geometry.add_face(geometry.add_polyline(points, close=True)),
                         "plate")
    mesh = project.generate_mesh(
        0.5, order="quadratic", strategy="quad_first",
        quad_t3_minimum_angle=12.0, quad_t3_angle_action="warning",
    )
    assert mesh.hybrid_diagnostics["t3_angle_rule"] == {
        "minimum_angle": 12.0, "action": "warning", "warning_count": 0,
    }
