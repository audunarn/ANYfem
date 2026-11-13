"""Phase 12: symmetry, the capacity workflow, and recovery/resource policy.

There is also a group here guarding something ANYfem does not itself offer:
the solver has orthotropic materials, and nothing in this layer may quietly
break them.  ANYfem models isotropic steel, but results, recovery and the
impact time step all pass through material objects, and each of those is a
place where reading an isotropic-only attribute would fail silently rather than
loudly.
"""

from __future__ import annotations

import numpy as np
import pytest

from anyfem import (
    Project,
    ShellFormulationPolicy,
    pinned,
    recovery_policy,
    resource_policy,
    solve_capacity,
    solve_linear_static,
    solve_nonlinear_static,
    steel,
    support,
)
from anyfem.model import ProjectError, antisymmetry, symmetry
from anyfem.post.fields import recover, reported_fields
from anyfem.solve import history_modes

SIDE, THICKNESS, PRESSURE = 2.0, 0.010, 10_000.0


def rectangle(x0: float, x1: float, y0: float, y1: float) -> tuple[Project, list]:
    project = Project(name="plate")
    project.add_material(steel("S355", THICKNESS))
    project.add_plate_section("plate", thickness=THICKNESS, material="S355")
    geometry = project.geometry
    points = geometry.add_points(
        [(x0, y0, 0), (x1, y0, 0), (x1, y1, 0), (x0, y1, 0)]
    )
    edges = geometry.add_polyline(points, close=True)
    face = geometry.add_face(edges)
    project.assign_plate(face, "plate")
    project.load_case().add_pressure(project.face(face), PRESSURE)
    return project, edges


def edge_midpoint(project: Project, edge_id: int) -> np.ndarray:
    edge = project.geometry.edges[edge_id]
    ends = [project.geometry.vertices[v].position for v in (edge.start, edge.end)]
    return 0.5 * (ends[0] + ends[1])


# ----------------------------------------------------------------------
# symmetry
# ----------------------------------------------------------------------


















# ----------------------------------------------------------------------
# capacity workflow
# ----------------------------------------------------------------------
def compressed_plate(nonlinear: bool = True) -> Project:
    """A plate strip in axial compression: buckling decides the capacity."""

    length, width, thickness = 1.0, 0.5, 0.006
    project = Project(name="capacity")
    project.add_material(steel("S355", thickness, nonlinear=nonlinear))
    project.add_plate_section("plate", thickness=thickness, material="S355")
    geometry = project.geometry
    points = geometry.add_points(
        [(0, 0, 0), (length, 0, 0), (length, width, 0), (0, width, 0)]
    )
    edges = geometry.add_polyline(points, close=True)
    face = geometry.add_face(edges)
    project.assign_plate(face, "plate")
    project.add_support(support(project.edge(edges[3]), ux=0.0, uy=0.0, uz=0.0))
    project.add_support(support(project.edge(edges[1]), uy=0.0, uz=0.0))
    for index in (0, 2):
        project.add_support(support(project.edge(edges[index]), uz=0.0))
    project.load_case().add_line_load(project.edge(edges[1]), (-1.0e6, 0, 0))
    return project


@pytest.fixture(scope="module")
def capacity_solution():
    """One representative workflow run serves its read-only result contracts."""

    project = compressed_plate()
    # This fixture checks orchestration and summaries. The updated qualified
    # plastic Q4 refuses the warped eigenmode imperfection; select the supported
    # historical Q4 route explicitly rather than changing the production default.
    project.shell_formulation_policy = ShellFormulationPolicy(
        q4="legacy", s3=project.shell_formulation_policy.s3)
    return solve_capacity(
        project, target_size=0.05, num_buckling_modes=3,
        imperfection_amplitude=1.0 / 500, num_steps=12,
    )


def test_the_capacity_workflow_runs_every_stage(capacity_solution):
    solution = capacity_solution
    assert solution.status == "completed"
    assert solution.critical_factor is not None and solution.critical_factor > 0
    assert solution.capacity_factor > 0
    assert len(solution.buckling) == 3
    assert solution.mesh_adequacy.get("status") == "ok"
    # It is a nonlinear result, so the nonlinear interface still works.
    assert len(solution.history()["load_factor"]) == len(solution.steps)


def test_the_capacity_ratio_compares_the_two_load_factors(capacity_solution):
    solution = capacity_solution
    assert solution.capacity_ratio == pytest.approx(
        solution.capacity_factor / solution.critical_factor
    )
    # A compressed plate has post-buckling reserve, so this one is above 1.
    # Asserted because it is the physics, not because above 1 is "good".
    assert solution.capacity_ratio > 1.0


def test_the_capacity_summary_names_both_load_factors(capacity_solution):
    solution = capacity_solution
    text = solution.summary()
    assert "capacity" in text
    assert "elastic critical" in text




# ----------------------------------------------------------------------
# recovery and resource policy
# ----------------------------------------------------------------------
def loaded_plate(nonlinear: bool = False) -> Project:
    project = Project(name="policy")
    project.add_material(steel("S355", 0.008, nonlinear=nonlinear))
    project.add_plate_section("plate", thickness=0.008, material="S355")
    geometry = project.geometry
    points = geometry.add_points([(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)])
    edges = geometry.add_polyline(points, close=True)
    face = geometry.add_face(edges)
    project.assign_plate(face, "plate")
    for edge_id in edges:
        project.add_support(pinned(project.edge(edge_id)))
    project.load_case().add_pressure(project.face(face), 50_000.0)
    return project














# ----------------------------------------------------------------------
# orthotropic materials must keep working
# ----------------------------------------------------------------------
def orthotropic():
    from anyfesolver import OrthotropicMaterial

    return OrthotropicMaterial(
        name="S355", elastic_modulus_1=120e9, elastic_modulus_2=180e9,
        elastic_modulus_3=90e9, poisson_ratio_12=0.28, poisson_ratio_13=0.28,
        poisson_ratio_23=0.30, shear_modulus_12=45e9, shear_modulus_13=40e9,
        shear_modulus_23=35e9, density=7850.0,
    )






