"""Module-level job functions for the result-transport tests.

A worker process imports its job function by name, so these cannot be
closures defined inside a test.  Deliberately no pytest import: the worker
loads this module.

The results built here are transport fixtures, not scientific evidence: the
displacement arrays are controlled fakes (no solve, no recovery kernel ran),
while the model carries a real, admitted qualified S3 V2D element -- the
element family whose live objects refuse pickle by contract -- constructed
through its public constructor.  The mesh is hand-assembled; no mesher ran.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Mapping

import numpy as np

#: Controlled fake displacement vector: 3 nodes x 6 DOFs, DOF order fixed by
#: the DOF manager (ux, uy, uz, rx, ry, rz per node, nodes added 1, 2, 3).
DISPLACEMENTS = 0.001 * np.arange(1.0, 19.0)

#: The triangle's nodes, in the same order the FE model registers them.
NODES = {
    1: (0.0, 0.0, 0.0),
    2: (0.5, 0.0, 0.0),
    3: (0.25, 0.4, 0.0),
}

#: Controlled provenance carried with every fake recovered-stress cache: it
#: must survive the transfer like the values, never be invented on decode.
STRESS_PROVENANCE = {"recovery": "controlled-fixture", "kernel": "none"}


@dataclass(frozen=True)
class ControlledStressResult:
    """A picklable stand-in for a solver recovered-stress result.

    Production consumers (``LinearSolution.face_von_mises``,
    ``io/result_artifact.py``) read the cache through its ``element_stresses``
    attribute and its ``provenance`` mapping, so the fixture uses the same
    public shape instead of a bare dict.
    """

    element_stresses: Mapping[int, Mapping[str, np.ndarray]]
    provenance: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ControlledPatchResult:
    """A picklable stand-in for a solver patch-recovery result.

    ``io/result_artifact.py`` reads patch results through ``nodal_stresses``
    (a mapping with ``nodal`` values and ``node_diagnostics``) and the shared
    ``provenance`` mapping.
    """

    nodal_stresses: Mapping[str, Any]
    provenance: Mapping[str, Any] = field(default_factory=dict)


def controlled_stress_cache() -> ControlledStressResult:
    """The controlled recovered-stress cache: one element, one component."""

    return ControlledStressResult(
        element_stresses={101: {"von_mises": np.array([1.0, 2.0])}},
        provenance=dict(STRESS_PROVENANCE),
    )


def controlled_global_stress() -> ControlledStressResult:
    """The controlled global-surface stress cache requested by the workflow."""

    return ControlledStressResult(
        element_stresses={
            101: {"global_xx_top": np.array([3.0]), "global_yy_bot": np.array([4.0])}
        },
        provenance={**STRESS_PROVENANCE, "requested": "global"},
    )


def controlled_patch_stress() -> ControlledPatchResult:
    """The controlled patch-recovery cache requested by the workflow."""

    return ControlledPatchResult(
        nodal_stresses={
            "nodal": {
                1: {"von_mises": 5.0},
                2: {"von_mises": 6.0},
                3: {"von_mises": 7.0},
            },
            "node_diagnostics": {node: {"status": "qualified"} for node in (1, 2, 3)},
        },
        provenance={**STRESS_PROVENANCE, "requested": "patch"},
    )


class UnpicklableCache:
    """A cache that refuses pickle, like solver state bound to live elements."""

    def __reduce_ex__(self, protocol):
        raise RuntimeError("fake unpicklable cache")


def _build_built(name: str = "result transport"):
    """A BuiltModel around one real qualified S3 V2D shell element.

    The FE model carries controlled instances of every model field the
    transport must preserve: materials, a support, a constraint equation, a
    load case, a point mass, element activity and mesh revisions.
    """

    from anyfem.mesh.mapped import Mesh
    from anyfem.model.project import Project
    from anyfem.solve.build import BuiltModel
    from anysolver import BoundaryCondition, LoadCase
    from anysolver.e4_pl_s3_v2d_element import NativeParityE4PLS3V2DShellElement
    from anysolver.fe_core import FEModel

    project = Project(name)
    mesh = Mesh()
    for node_id, (x, y, z) in NODES.items():
        mesh.nodes[node_id] = np.array([x, y, z])
    mesh.tris[101] = (1, 2, 3)

    fe_model = FEModel(name=name)
    fe_model.add_material("steel", 210e9, 0.3, 7850.0)
    for node_id, (x, y, z) in NODES.items():
        fe_model.add_node(node_id, x, y, z)
    element = NativeParityE4PLS3V2DShellElement(
        101, (1, 2, 3), "steel", thickness=0.02, reference_normal=(0.0, 0.0, 1.0)
    )
    fe_model.add_element(101, element)
    fe_model.add_boundary_condition(
        BoundaryCondition(
            "support", [1], {"ux": 0.0, "uy": 0.0, "uz": 0.0, "rx": 0.0, "ry": 0.0, "rz": 0.0}
        )
    )
    # Global DOFs: node 1 ux is DOF 0, node 2 ux is DOF 6.
    fe_model.add_constraint_equation(terms=((0, 1.0), (6, -1.0)), rhs=0.0, source_id="fixture")
    fe_model.add_load_case(LoadCase(name="dead"))
    fe_model.add_point_mass(2, 3.5)
    from anysolver.activity import ElementActivity

    fe_model.set_element_activity(ElementActivity([101], 1.0))
    fe_model.mesh.revisions["mesh"] = 4
    return BuiltModel(fe_model=fe_model, load_case=None, mesh=mesh, project=project)


def s3_linear_result(*, project, progress, cancellation_token=None):
    """A completed linear static result whose model holds a live S3 element."""

    from anyfem.post.results import LinearSolution

    built = _build_built()
    progress("linear_static.complete")
    return LinearSolution(
        displacements=DISPLACEMENTS.copy(),
        built=built,
        info={"status": "fake-complete", "element": "NativeParityE4PLS3V2DShellElement"},
        label="static",
    )


def s3_linear_result_with_stress(*, project, progress, cancellation_token=None):
    """A completed result whose recovered-stress cache is carried, not dropped."""

    from anyfem.post.results import LinearSolution

    solution = s3_linear_result(project=project, progress=progress)
    solution._stress = controlled_stress_cache()
    return solution


def s3_linear_result_with_requested_stresses(*, project, progress, cancellation_token=None):
    """A completed result carrying the workflow's two requested stress caches.

    ``application/workflow.py`` attaches ``_requested_global_stress`` and
    ``_requested_patch_stress`` to a completed ``LinearSolution`` in the
    worker before returning it, and ``io/result_artifact.py`` reads them
    back by those exact names.  The transport must carry both.
    """

    solution = s3_linear_result(project=project, progress=progress)
    solution._requested_global_stress = controlled_global_stress()
    solution._requested_patch_stress = controlled_patch_stress()
    return solution


def s3_linear_result_with_unpicklable_requested_stress(
    *, project, progress, cancellation_token=None
):
    """A completed result whose requested global stress refuses pickle."""

    solution = s3_linear_result(project=project, progress=progress)
    solution._requested_global_stress = UnpicklableCache()
    return solution


def s3_linear_result_with_unpicklable_stress(*, project, progress, cancellation_token=None):
    """A completed result whose stress cache refuses pickle: carry it or fail."""

    solution = s3_linear_result(project=project, progress=progress)
    solution._stress = UnpicklableCache()
    return solution


def s3_linear_result_after_ready(*, project, progress, cancellation_token=None):
    """A completed S3 result preceded by a visible ready point.

    The ready message lets a test cancel the job before the result exists, so
    the cancellation-during-transfer branch is reached deterministically.
    """

    progress("ready")
    time.sleep(0.5)
    return s3_linear_result(project=project, progress=progress)


def s3_batch_result(*, project, progress, cancellation_token=None):
    """A completed linear batch result sharing one S3 model across cases."""

    from dataclasses import replace

    from anyfem.post.results import LinearBatchSolution, LinearSolution
    from anysolver import LoadCase

    built = _build_built()
    names = ("dead", "live")
    shapes = []
    for index, name in enumerate(names):
        shapes.append(
            LinearSolution(
                displacements=(DISPLACEMENTS * (index + 1)).copy(),
                built=replace(built, load_case=LoadCase(name=name)),
                info={"batch_case": name},
                label=f"static: {name}",
                value=float(index),
            )
        )
    return LinearBatchSolution(
        built=built,
        shapes=shapes,
        status="fake-ok",
        info={"status": "fake-batch"},
        case_names=names,
    )


def s3_batch_distinct_built_result(*, project, progress, cancellation_token=None):
    """A batch whose shapes carry genuinely different built models.

    Each shape keeps its own project, mesh and FE model, so the transport must
    preserve per-shape built models rather than assume one shared model.
    """

    from anyfem.post.results import LinearBatchSolution, LinearSolution

    names = ("alpha", "beta")
    shapes = []
    for index, name in enumerate(names):
        shapes.append(
            LinearSolution(
                displacements=(DISPLACEMENTS * (index + 1)).copy(),
                built=_build_built(f"result transport {name}"),
                info={"batch_case": name},
                label=f"static: {name}",
                value=float(index),
            )
        )
    return LinearBatchSolution(
        built=_build_built("result transport batch"),
        shapes=shapes,
        status="fake-ok",
        info={"status": "fake-distinct-batch"},
        case_names=names,
    )


def s3_nonlinear_result(*, project, progress, cancellation_token=None):
    """A nonlinear-family result: outside the plain-data transport by design.

    The stateless element serialization cannot carry nonlinear accepted state
    or history, so the transport must refuse it rather than strip it.
    """

    from anyfem.post.results import NonlinearSolution

    built = _build_built()
    progress("nonlinear.complete")
    return NonlinearSolution(
        displacements=DISPLACEMENTS.copy(),
        built=built,
        steps=[{"load_factor": 1.0}],
        status="fake-converged",
        info={"status": "fake-nonlinear"},
    )


def s3_linear_subclass_result(*, project, progress, cancellation_token=None):
    """A LinearSolution subclass carrying an extra field: refused, not reduced."""

    from dataclasses import dataclass, field

    from anyfem.post.results import LinearSolution

    @dataclass
    class ExtraFieldSolution(LinearSolution):
        extra_provenance: dict = field(default_factory=dict)

    return ExtraFieldSolution(
        displacements=DISPLACEMENTS.copy(),
        built=_build_built(),
        info={"status": "fake-subclass"},
        label="static",
        extra_provenance={"owner": "fixture"},
    )


def s3_built_subclass_result(*, project, progress, cancellation_token=None):
    """A BuiltModel subclass carrying an extra field: refused, not reduced.

    This mirrors a concurrent owner extending the built model (for example a
    connections field): the transport must fail closed instead of silently
    dropping the extra field.
    """

    from dataclasses import dataclass, field

    from anyfem.post.results import LinearSolution
    from anyfem.solve.build import BuiltModel

    @dataclass
    class ExtendedBuiltModel(BuiltModel):
        connections: dict = field(default_factory=dict)

    return LinearSolution(
        displacements=DISPLACEMENTS.copy(),
        built=ExtendedBuiltModel(
            fe_model=_build_built().fe_model,
            load_case=None,
            mesh=_build_built().mesh,
            project=_build_built().project,
            connections={"coupling": 1},
        ),
        info={"status": "fake-built-subclass"},
        label="static",
    )


def picklable_linear_result(*, project, progress, cancellation_token=None):
    """A completed linear result with no refusing element: the direct path.

    Its model holds no live S3 element, so the result pickles as itself and
    the plain-data transport must not be involved.
    """

    from anyfem.mesh.mapped import Mesh
    from anyfem.model.project import Project
    from anyfem.post.results import LinearSolution
    from anyfem.solve.build import BuiltModel
    from anysolver.fe_core import FEModel

    fe_model = FEModel(name="picklable")
    fe_model.add_material("steel", 210e9, 0.3, 7850.0)
    for node_id, (x, y, z) in NODES.items():
        fe_model.add_node(node_id, x, y, z)
    mesh = Mesh()
    for node_id, (x, y, z) in NODES.items():
        mesh.nodes[node_id] = np.array([x, y, z])
    progress("linear_static.complete")
    return LinearSolution(
        displacements=DISPLACEMENTS.copy(),
        built=BuiltModel(
            fe_model=fe_model, load_case=None, mesh=mesh, project=Project("picklable")
        ),
        info={"status": "fake-picklable"},
        label="static",
    )
