"""Phase 11: local refinement, graded seeding and quadratic elements.

The thread running through these is that a mapped mesh has a hard limit: the
interior of a plate is the transfinite blend of its boundary, so refining
inside one means decomposing it.  Several tests exist to pin that down rather
than to check a feature works, because it is the kind of limit that otherwise
gets rediscovered as a bug.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import numpy as np
import pytest

from anyfem import Project, fixed, pinned, solve_linear_static, steel, support
from anyfem.commands import (
    AddRefinement,
    CommandStack,
    RefineForImpact,
    SetElementOrder,
)
from anyfem.geometry import GeometryModel
from anyfem.mesh import MeshError, generate_mesh, refine_around, refine_at
from anyfem.mesh.refinement import Refinement, SizeField
from anyfem.mesh.seeding import edge_demand, solve_seeding
from anyfem.model import BeamSection, Collision, ProjectError


@pytest.fixture
def workspace():
    with tempfile.TemporaryDirectory() as directory:
        yield Path(directory)


def square(side: float = 4.0) -> tuple[GeometryModel, int]:
    model = GeometryModel()
    points = model.add_points(
        [(0, 0, 0), (side, 0, 0), (side, side, 0), (0, side, 0)]
    )
    return model, model.add_face(model.add_polyline(points, close=True))


def plate_project(side: float = 1.0, thickness: float = 0.010) -> Project:
    project = Project(name="plate")
    project.add_material(steel("S355", thickness))
    project.add_plate_section("plate", thickness=thickness, material="S355")
    geometry = project.geometry
    points = geometry.add_points(
        [(0, 0, 0), (side, 0, 0), (side, side, 0), (0, side, 0)]
    )
    edges = geometry.add_polyline(points, close=True)
    face = geometry.add_face(edges)
    project.assign_plate(face, "plate")
    for edge in edges:
        project.add_support(pinned(project.edge(edge)))
    project.load_case().add_pressure(project.face(face), 10_000.0)
    return project


# ----------------------------------------------------------------------
# the size field
# ----------------------------------------------------------------------












# ----------------------------------------------------------------------
# graded seeding
# ----------------------------------------------------------------------
















# ----------------------------------------------------------------------
# quadratic elements
# ----------------------------------------------------------------------
















def test_quadratic_shells_converge_faster_than_linear_ones():
    """The point of Q8: fewer elements for the same accuracy.

    Compared against each order's own converged answer rather than a thin-plate
    coefficient, because the two are not the same number -- the elements are
    Mindlin-Reissner and include transverse shear.
    """

    fine = plate_project()
    fine.set_element_order("quadratic")
    converged = solve_linear_static(fine, target_size=1 / 24).max_translation()[1]

    errors = {}
    for order in ("linear", "quadratic"):
        project = plate_project()
        project.set_element_order(order)
        coarse = solve_linear_static(project, target_size=1 / 4)
        assert len(coarse.built.mesh.quads) == 16
        errors[order] = abs(coarse.max_translation()[1] / converged - 1.0)

    assert errors["quadratic"] < 0.5 * errors["linear"]




# ----------------------------------------------------------------------
# refinement inside a plate needs decomposition
# ----------------------------------------------------------------------




# ----------------------------------------------------------------------
# impact refinement
# ----------------------------------------------------------------------
def struck_plate() -> Project:
    project = plate_project(side=1.0, thickness=0.008)
    return project


def sphere() -> Collision:
    return Collision(
        mass=200.0, radius=0.15, start=(0.5, 0.5, 0.6),
        direction=(0.0, 0.0, -1.0), speed=4.0,
    )




















# ----------------------------------------------------------------------
# commands and the project file
# ----------------------------------------------------------------------
