"""Build-boundary checks for persistent scopes and named coordinates."""

from __future__ import annotations

import numpy as np
import pytest

from anyfem import Project, steel
from anyfem.model import CoordinateSystem, ManualRegion, Region, RegionRef, Support
from anyfem.solve.build import build_fe_model


def _plate():
    project = Project("coordinate scopes")
    project.add_material(steel("S355", 0.01))
    project.add_plate_section("plate", thickness=0.01, material="S355")
    points = project.geometry.add_points(
        ((1.0, 0.0, 0.0), (2.0, 0.0, 0.0), (2.0, 2.0, 0.0), (0.0, 2.0, 0.0))
    )
    face = project.geometry.add_face(project.geometry.add_polyline(points, close=True))
    project.assign_plate(face, "plate")
    return project, points


def test_cylindrical_point_load_is_resolved_per_scoped_node():
    project, points = _plate()
    cylindrical = project.add_coordinate_system(
        CoordinateSystem("Pipe", kind="cylindrical")
    )
    region = project.regions.add(
        Region(
            "two radial points",
            "geometry",
            "vertex",
            ManualRegion((project.point(points[0]), project.point(points[3]))),
        )
    )
    case = project.load_case()
    case.add_point_load(
        project.point(points[0]),
        force=(100.0, 0.0, 0.0),
        region=RegionRef(region.id),
        coordinate_system_id=cylindrical.id,
        distribution_policy="total_distributed",
    )

    mesh = project.generate_mesh(0.5)
    built = build_fe_model(project, mesh, require_supports=False)
    loads = built.load_case.nodal_loads
    first = mesh.node_of_vertex[points[0]]
    second = mesh.node_of_vertex[points[3]]
    assert loads[first][:3] == pytest.approx((50.0, 0.0, 0.0))
    assert loads[second][:3] == pytest.approx((0.0, 50.0, 0.0))


def test_cylindrical_prescribed_support_becomes_affine_global_equation():
    project, points = _plate()
    cylindrical = project.add_coordinate_system(
        CoordinateSystem("Pipe", kind="cylindrical")
    )
    project.add_support(
        Support(
            "radial displacement",
            project.point(points[3]),
            {"ux": 0.002},
            coordinate_system_id=cylindrical.id,
        )
    )

    mesh = project.generate_mesh(0.5)
    built = build_fe_model(project, mesh, require_loads=False)
    equations = built.fe_model.constraint_equations
    assert len(equations) == 1
    equation = equations[0]
    node = mesh.node_of_vertex[points[3]]
    dofs = built.fe_model.mesh.dof_manager.get_node_dofs(node)
    assert equation.rhs == pytest.approx(0.002)
    assert dict(equation.terms).get(dofs[1], 0.0) == pytest.approx(1.0)
    assert abs(dict(equation.terms).get(dofs[0], 0.0)) <= 1.0e-12


def test_unknown_coordinate_system_blocks_build():
    project, points = _plate()
    project.load_case().add_point_load(
        project.point(points[0]),
        force=(1.0, 0.0, 0.0),
        coordinate_system_id="missing",
    )
    mesh = project.generate_mesh(0.5)
    with pytest.raises(ValueError, match="does not exist"):
        build_fe_model(project, mesh, require_supports=False)

