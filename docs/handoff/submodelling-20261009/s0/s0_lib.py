"""Shared helpers for the S0 submodelling spike (scratch code, not an owner contract).

Everything here uses public owner APIs from the HEAD snapshots selected by
``s0_env``.  Nothing here is a proposed implementation; it exists to find out
which assumptions of PLAN.md hold.
"""

from __future__ import annotations

import s0_env  # noqa: F401  (must be first)

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Mapping, Sequence

import numpy as np
from scipy import sparse
from scipy.sparse.linalg import LinearOperator, onenormest, splu

from anyfem import Project, steel
from anyfem.mesh_controls import MeshControls
from anyfem.post.results import LinearSolution
from anyfem.solve.build import build_fe_model
from anymesher.mesh_bvh import MeshElementBVH
from anysolver import BoundaryCondition, solve_linear
from anysolver.analysis_session import AnalysisSession
from anysolver.assembly import compute_constraint_force_diagnostics

DOF_NAMES = ("ux", "uy", "uz", "rx", "ry", "rz")
EPS = float(np.finfo(float).eps)
RESULTS = Path(__file__).with_name("results")
RESULTS.mkdir(exist_ok=True)

THICKNESS = 0.010


# ----------------------------------------------------------------------
# model building
# ----------------------------------------------------------------------
def new_project(name: str) -> Project:
    project = Project(name=name)
    project.add_material(steel("S355", THICKNESS))
    project.add_plate_section("plate", thickness=THICKNESS, material="S355")
    return project


def rectangle_faces(
    project: Project, xs: Sequence[float], ys: Sequence[float], z: float = 0.0, *, skip=(), assign=True
) -> dict[tuple[int, int], int]:
    """A conforming grid of rectangular faces; returns (i, j) -> face id.

    ``skip`` lists (i, j) cells left empty (their boundary edges are still created).
    """

    g = project.geometry
    vid = {}
    for j, y in enumerate(ys):
        for i, x in enumerate(xs):
            vid[i, j] = g.add_point(float(x), float(y), z)
    hedge = {}
    vedge = {}
    for j in range(len(ys)):
        for i in range(len(xs) - 1):
            hedge[i, j] = g.add_line(vid[i, j], vid[i + 1, j])
    for j in range(len(ys) - 1):
        for i in range(len(xs)):
            vedge[i, j] = g.add_line(vid[i, j], vid[i, j + 1])
    faces = {}
    for j in range(len(ys) - 1):
        for i in range(len(xs) - 1):
            if (i, j) in skip:
                continue
            loop = [hedge[i, j], vedge[i + 1, j], hedge[i, j + 1], vedge[i, j]]
            face = g.add_face(loop)
            if assign:
                project.assign_plate(face, "plate")
            faces[i, j] = face
    return faces


def boundary_edges(project: Project, faces: Iterable[int]) -> list[int]:
    """Edges used by exactly one of the given faces (the region boundary)."""

    g = project.geometry
    count: dict[int, int] = {}
    for face in faces:
        for oriented in g.faces[face].loop:
            count[oriented.edge] = count.get(oriented.edge, 0) + 1
        for hole in g.faces[face].holes:
            for oriented in hole:
                count[oriented.edge] = count.get(oriented.edge, 0) + 1
    return sorted(edge for edge, n in count.items() if n == 1)


def edge_midpoint(project: Project, edge_id: int) -> np.ndarray:
    g = project.geometry
    edge = g.edges[edge_id]
    return 0.5 * (
        np.asarray(g.vertex_position(edge.start)) + np.asarray(g.vertex_position(edge.end))
    )


def mesh(project: Project, size: float, *, triangles: bool = False, overrides=None):
    kwargs = {}
    if triangles:
        kwargs = {"strategy": "native", "mesh_controls": MeshControls(recombine=False)}
    return project.generate_mesh(size, overrides=overrides, **kwargs)


# ----------------------------------------------------------------------
# transfer
# ----------------------------------------------------------------------
@dataclass
class Transfer:
    values: dict[int, np.ndarray]  # fine node -> 6 driven values
    element: dict[int, int]  # fine node -> source element
    residual: dict[int, float]  # inverse-map residual (m)
    weights_sum_error: float
    candidates_max: int
    ambiguous: list[int] = field(default_factory=list)


def transfer(
    source_mesh,
    source_values: Mapping[int, np.ndarray],
    target_mesh,
    target_nodes: Iterable[int],
    *,
    element_ids: Iterable[int] | None = None,
    tolerance: float = 1.0e-9,
) -> Transfer:
    """Interpolate a nodal six-vector field from the source mesh at target nodes.

    Uses ANYmesher's element BVH and the hit's own interpolation weights.  A
    node found in several elements (a shared edge or vertex) is evaluated in
    every hit; disagreement beyond roundoff is reported as ambiguous.
    """

    bvh = MeshElementBVH(source_mesh, element_ids=element_ids, tolerance=tolerance)
    values: dict[int, np.ndarray] = {}
    element: dict[int, int] = {}
    residual: dict[int, float] = {}
    worst_weight = 0.0
    most = 0
    ambiguous = []
    for node in target_nodes:
        point = np.asarray(target_mesh.nodes[node], dtype=float)
        hits = bvh.locate_all(point)
        if not hits:
            raise RuntimeError(f"fine node {node} at {point} not located in the source mesh")
        most = max(most, len(hits))
        evaluated = []
        for hit in hits:
            weights = np.asarray(hit.weights, dtype=float)
            worst_weight = max(worst_weight, abs(float(weights.sum()) - 1.0))
            nodal = np.array([source_values[int(n)] for n in hit.node_ids])
            evaluated.append(weights @ nodal)
        stack = np.array(evaluated)
        spread = float(np.max(np.ptp(stack, axis=0))) if len(stack) > 1 else 0.0
        scale = max(float(np.max(np.abs(stack))), 1.0e-300)
        if spread > 1.0e-12 * scale:
            ambiguous.append(int(node))
        values[int(node)] = stack[0]
        element[int(node)] = int(hits[0].element_id)
        residual[int(node)] = float(hits[0].residual)
    return Transfer(values, element, residual, worst_weight, most, ambiguous)


# ----------------------------------------------------------------------
# solving
# ----------------------------------------------------------------------
@dataclass
class Solve:
    built: object
    u: np.ndarray
    info: dict
    seconds: float

    @property
    def backward_error(self) -> float:
        return float(self.info["convergence_info"]["relative_backward_error"])

    def node_values(self, node: int) -> np.ndarray:
        dofs = self.built.fe_model.mesh.dof_manager.get_node_dofs(node)
        return np.asarray(self.u[dofs], dtype=float)

    def solution(self) -> LinearSolution:
        return LinearSolution(displacements=self.u, built=self.built, info=self.info, label="s0")


def build(project: Project, mesh_, *, require_loads=True, require_supports=True):
    return build_fe_model(
        project, mesh_, require_loads=require_loads, require_supports=require_supports
    )


def drive(built, values: Mapping[int, np.ndarray], dofs: Sequence[str] = DOF_NAMES) -> None:
    """One ANYsolver BoundaryCondition per driven node (S0 stand-in for G2a)."""

    for node, six in sorted(values.items()):
        constraints = {name: float(six[DOF_NAMES.index(name)]) for name in dofs}
        built.fe_model.add_boundary_condition(
            BoundaryCondition(name=f"driven_{node}", node_ids=[int(node)], dof_constraints=constraints)
        )


def solve(built) -> Solve:
    start = time.perf_counter()
    u, info = solve_linear(built.fe_model, built.load_case)
    seconds = time.perf_counter() - start
    status = info.get("convergence_info", {}).get("status")
    if status not in (None, "converged"):
        raise RuntimeError(f"solve failed: {info.get('convergence_info')}")
    return Solve(built, np.asarray(u, dtype=float), info, seconds)


def condition_estimate(built) -> dict:
    """1-norm condition estimate of the reduced operator (S0 prototype of G2e)."""

    model = built.fe_model
    with AnalysisSession(model) as session:
        plan = session.constraint_plan()
        k = sparse.csc_matrix(plan.K_red, dtype=float)
    lu = splu(k)
    n = k.shape[0]
    inverse = LinearOperator(
        (n, n), matvec=lu.solve, rmatvec=lambda x: lu.solve(x, trans="T"), dtype=float
    )
    norm_k = float(abs(k).sum(axis=0).max())
    norm_inv = float(onenormest(inverse))
    # Diagonally scaled variant: the solver equilibrates before factorizing.
    d = 1.0 / np.sqrt(np.abs(k.diagonal()))
    ks = sparse.diags(d) @ k @ sparse.diags(d)
    lus = splu(sparse.csc_matrix(ks))
    inverse_s = LinearOperator(
        (n, n), matvec=lus.solve, rmatvec=lambda x: lus.solve(x, trans="T"), dtype=float
    )
    norm_ks = float(abs(ks).sum(axis=0).max())
    return {
        "n": n,
        "norm1_K": norm_k,
        "kappa1": norm_k * norm_inv,
        "kappa1_scaled": norm_ks * float(onenormest(inverse_s)),
    }


def interface_forces(built, u: np.ndarray, nodes: Iterable[int]) -> dict[int, np.ndarray]:
    """Fixed-DOF residuals K u - f at the given nodes (support bucket only)."""

    diagnostics = compute_constraint_force_diagnostics(built.fe_model, u, built.load_case)
    support = diagnostics["support_reactions"]
    if diagnostics.get("mpc_slave_forces"):
        raise RuntimeError("S0 interface forces assume no MPC slaves at the cut")
    return {int(n): np.asarray(support.get(int(n), np.zeros(6)), dtype=float) for n in nodes}


def resultant(forces: Mapping[int, np.ndarray], positions: Mapping[int, np.ndarray], about) -> np.ndarray:
    """Force and moment resultant (6) of nodal force/moment vectors about a point."""

    about = np.asarray(about, dtype=float)
    total = np.zeros(6)
    for node, f in forces.items():
        total[:3] += f[:3]
        total[3:] += f[3:] + np.cross(np.asarray(positions[node]) - about, f[:3])
    return total


def field_at(nodes: Mapping[int, np.ndarray], function: Callable[[np.ndarray], np.ndarray]) -> dict[int, np.ndarray]:
    return {int(n): np.asarray(function(np.asarray(x, dtype=float)), dtype=float) for n, x in nodes.items()}


def errors(computed: Mapping[int, np.ndarray], exact: Mapping[int, np.ndarray], nodes: Iterable[int]) -> dict:
    nodes = list(nodes)
    c = np.array([computed[n] for n in nodes])
    e = np.array([exact[n] for n in nodes])
    d = np.abs(c - e)
    t_scale = max(float(np.max(np.abs(e[:, :3]))), 1.0e-300)
    r_scale = max(float(np.max(np.abs(e[:, 3:]))), 1.0e-300)
    return {
        "max_abs_translation_error": float(d[:, :3].max()),
        "max_abs_rotation_error": float(d[:, 3:].max()),
        "rel_translation_error": float(d[:, :3].max()) / t_scale,
        "rel_rotation_error": float(d[:, 3:].max()) / r_scale if r_scale > 1.0e-300 else None,
        "translation_scale": t_scale,
        "rotation_scale": r_scale,
        "per_component_max_abs": [float(v) for v in d.max(axis=0)],
    }


def save(name: str, payload: dict) -> Path:
    path = RESULTS / f"{name}.json"
    payload = {"environment": s0_env.verify(), **payload}
    path.write_text(json.dumps(payload, indent=2, default=_jsonable))
    return path


def _jsonable(value):
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, (np.floating, np.integer)):
        return value.item()
    return str(value)
