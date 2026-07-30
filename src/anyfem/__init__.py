"""ANYfem -- a shell and beam finite element application.

Geometry modelling, mapped meshing, loads and boundary conditions, solving and
postprocessing, built on ``anysolver`` for the analysis and ``anytk3d`` for
the viewport.

Modelling is bottom-up and point-driven::

    from anyfem import Project, steel

    project = Project(name="plate")
    project.add_material(steel("S355", thickness=0.010))
    project.add_plate_section("deck", thickness=0.010, material="S355")

    points = project.geometry.add_points(
        [(0, 0, 0), (2, 0, 0), (2, 1, 0), (0, 1, 0)]
    )
    face = project.geometry.add_plate(points)
    project.assign_plate(face, "deck")

Everything in this package works headless; the 3D viewport lives in
``anyfem.ui`` and is the only part that needs Tk.
"""

from .geometry import (
    Edge,
    EntityRef,
    Face,
    GeometryError,
    GeometryModel,
    OrientedEdge,
    Vertex,
)
from .mesh import Mesh, MeshError, Seeding, SeedingConflict, generate_mesh, solve_seeding
from .model import (
    BeamSection,
    LoadCase,
    Material,
    PlateSection,
    Project,
    ProjectError,
    Support,
    fixed,
    pinned,
    antisymmetry,
    simply_supported,
    symmetry,
    steel,
    support,
)
from .post import (
    BucklingSolution,
    LinearSolution,
    ModalSolution,
    NonlinearSolution,
    ShapeView,
    TransientSolution,
)
from .solve import (
    BuiltModel,
    build_fe_model,
    eigenmode_imperfection,
    preflight,
    solve_arc_length,
    recovery_policy,
    resource_policy,
    solve_buckling,
    solve_capacity,
    solve_linear_static,
    solve_modal,
    solve_nonlinear_static,
    solve_transient,
)

__version__ = "0.0.1"

__all__ = [
    "BeamSection",
    "BucklingSolution",
    "BuiltModel",
    "Edge",
    "EntityRef",
    "Face",
    "GeometryError",
    "GeometryModel",
    "LinearSolution",
    "LoadCase",
    "Material",
    "Mesh",
    "MeshError",
    "ModalSolution",
    "NonlinearSolution",
    "OrientedEdge",
    "PlateSection",
    "Project",
    "ProjectError",
    "Seeding",
    "SeedingConflict",
    "ShapeView",
    "Support",
    "TransientSolution",
    "Vertex",
    "__version__",
    "build_fe_model",
    "eigenmode_imperfection",
    "fixed",
    "generate_mesh",
    "pinned",
    "preflight",
    "simply_supported",
    "solve_arc_length",
    "recovery_policy",
    "resource_policy",
    "solve_buckling",
    "solve_capacity",
    "solve_linear_static",
    "solve_modal",
    "solve_nonlinear_static",
    "solve_seeding",
    "solve_transient",
    "steel",
    "antisymmetry",
    "support",
    "symmetry",
]
