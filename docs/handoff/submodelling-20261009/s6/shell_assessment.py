"""S6 shell-submodel assessment input builder: A0, A1, A2 and A7 (development candidate).

This is a *candidate* executable input for the independent assessor.  It is not frozen,
not self-approving, and it has not been run by its author (the author had no shell).
Every number below that is called an "allowance" is the one the public ANYsolver API
returns for the actual solve; no fixed tolerance stands in for it.

Usage (one case per process, single thread, new output file only)::

    python shell_assessment.py --inventory
    python shell_assessment.py --case A1-Q4 --output <new-result.json>

Exit codes: 0 pass, 1 failed, 2 inconclusive, 3 invocation error (nothing executed;
for example the output exists), 4 harness error (the output is still written and keeps
the traceback as failed evidence).

Provenance of the local fixtures.  The plate/grid, the exact fields, the MPC example and
the element-aligned A1 restriction are adapted from ``ANYsolver/tests/test_submodel_transfer.py``
(plate, affine, rigid, g_field, line_loaded) and from the historical
``s0``/``s1s3`` scripts (``integration_a1.py``, ``e1_a2_exact.py``, ``e3_a2_bend.py``).
Those scripts are not imported: they prepend historical source trees to ``sys.path`` and write
into their own result folders.  The adapters here import only the installed public
``anysolver`` modules, keep their own locator (``locate_in_grid``; this is NOT the ANYmesh
correspondence API) and write only the requested exclusive output.

Limits that are visible in the JSON, not hidden:
  * the point locator is a local regular-grid helper, not ``anymesher.locate_for_transfer``;
  * A1 is solver-level (matched nodes, tributary pressure partition), not the ANYfem
    cut/job route; the ANYfem rows are listed as pending external tests;
  * the public API always drives the rotations, so the translations-only control of the
    historical E3 script is reported unavailable;
  * the numerical (PL/hourglass/drilling) energy split of D3 is not exposed by the public
    API and is reported unavailable (informational, it does not change the grade).
"""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import math
import os
import platform
import sys
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

# Determinism: one BLAS/OpenMP thread.  Effective only if numpy is not yet imported.
_NUMPY_PRELOADED = "numpy" in sys.modules
for _name in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS",
              "NUMEXPR_NUM_THREADS", "VECLIB_MAXIMUM_THREADS"):
    os.environ[_name] = "1"

import numpy as np  # noqa: E402

SCHEMA = "anyfem.s6.shell_assessment/1"
HERE = Path(__file__).resolve().parent
HANDOFF_ROOT = HERE.parent
#: Historical evidence folders that must never receive output.
PROTECTED_DIRS = ("s0", "s1s3", "c0")

PASS, FAIL, INCONCLUSIVE, REPORTED = "pass", "fail", "inconclusive", "reported"
EXIT_CODES = {"pass": 0, "failed": 1, "inconclusive": 2, "invocation_error": 3, "harness_error": 4}
DOF_NAMES = ("ux", "uy", "uz", "rx", "ry", "rz")
EPS = float(np.finfo(float).eps)

E_MOD, NU, THK = 210.0e9, 0.3, 0.01
SIGMA = 100.0e6          # A2 tension, Pa
KAPPA = 1.0e-3           # A2 bend curvature, 1/m
SEED = 7

#: Registered constants.  Everything graded is "metric / actual allowance <= RATIO_MAX".
REGISTERED = {
    "ratio_max": 1.0,
    "ratio_definition": "max over components of |error| / (allowance returned by the public API for that solve)",
    "inconclusive_rule": "a check whose solve reports StaticErrorAllowance.inconclusive, or that measured nothing, is "
                         "inconclusive (neither passed nor failed)",
    "grade_rule": "failed if any graded check failed; else inconclusive if any graded check is inconclusive, any "
                  "graded-inventory row is pending/unavailable, or nothing was graded; else pass",
    "seed": SEED,
    "a1": {"grid_cells": 8, "block_cells": "i,j in 2..5", "pressure_Pa": 1.0e3, "policies": ["drive_all (graded)",
           "release_normal_rotation (reported)"]},
    "a2_exact": {"source_cells": 2, "target_cells": 3, "fields": ["tension", "affine_membrane_rotation", "rigid"],
                 "policies": ["drive_all", "release_normal_rotation"]},
    "a2_bend": {"kappa": KAPPA, "parent_sizes_m": [0.25, 0.125, 0.0625, 0.03125], "child_size_m": 0.025,
                "criterion": "graded: driven values equal the independently interpolated coarse field within the "
                             "interpolation allowance; convergence orders and D3 dispositions are reported"},
}


# ----------------------------------------------------------------------------------------
# result records and aggregation (pure Python; no solver needed)
# ----------------------------------------------------------------------------------------
@dataclass
class Check:
    id: str
    metric: str
    value: Any
    threshold: Any
    threshold_source: str     # actual_allowance | expected_code | exact_expectation | registered_constant | none
    status: str               # pass | fail | inconclusive | reported
    sensor: str = ""
    detail: Dict[str, Any] = field(default_factory=dict)


def ratio(error: float, allowance: float) -> float:
    """error / allowance with the zero-allowance conventions: 0/0 -> 0, e/0 -> inf, nonfinite -> inf."""

    if not (math.isfinite(error) and math.isfinite(allowance)):
        return math.inf
    if allowance > 0.0:
        return abs(error) / allowance
    return 0.0 if error == 0.0 else math.inf


def worst_ratio(errors: Sequence[float], allowances: Sequence[float], labels: Sequence[str]) -> Dict[str, Any]:
    e = np.abs(np.asarray(errors, dtype=float)).ravel()
    a = np.asarray(allowances, dtype=float).ravel()
    if e.size == 0 or e.size != a.size or len(labels) != e.size:
        return {"ratio": math.nan, "sensor": "", "error": math.nan, "allowance": math.nan,
                "max_abs_error": math.nan, "max_allowance": math.nan, "count": 0}
    r = np.array([ratio(float(x), float(y)) for x, y in zip(e, a)])
    k = int(np.argmax(r))
    return {"ratio": float(r[k]), "sensor": str(labels[k]), "error": float(e[k]), "allowance": float(a[k]),
            "max_abs_error": float(np.max(e)) if np.all(np.isfinite(e)) else math.inf,
            "max_allowance": float(np.max(a)) if np.all(np.isfinite(a)) else math.inf, "count": int(e.size)}


def ratio_check(check_id: str, metric: str, worst: Dict[str, Any], *, inconclusive: bool = False,
                graded: bool = True, **detail: Any) -> Check:
    value = worst["ratio"]
    if worst["count"] == 0 or (isinstance(value, float) and math.isnan(value)):
        status = INCONCLUSIVE if graded else REPORTED
    elif not graded:
        status = REPORTED
    elif inconclusive:
        status = INCONCLUSIVE
    else:
        status = PASS if value <= REGISTERED["ratio_max"] else FAIL
    body = {k: worst[k] for k in ("error", "allowance", "max_abs_error", "max_allowance", "count")}
    body.update(detail)
    if inconclusive:
        body["solve_reported_inconclusive"] = True
    return Check(check_id, metric, value, REGISTERED["ratio_max"], "actual_allowance", status, worst["sensor"], body)


def bool_check(check_id: str, metric: str, ok: bool, expectation: str, **detail: Any) -> Check:
    return Check(check_id, metric, bool(ok), expectation, "exact_expectation", PASS if ok else FAIL, "", detail)


def expect_code(check_id: str, fn: Callable[[], Any], code: str, expected_type: str = "SubmodelTransferError",
                **detail: Any) -> Check:
    try:
        fn()
    except Exception as error:  # noqa: BLE001 - the exception type is the measurement
        got = getattr(error, "code", type(error).__name__)
        ok = got == code and type(error).__name__ == expected_type
        return Check(check_id, "refusal_code", got, code, "expected_code", PASS if ok else FAIL, "",
                     {"exception": type(error).__name__, "message": str(error)[:300], **detail})
    return Check(check_id, "refusal_code", "no_exception", code, "expected_code", FAIL, "", dict(detail))


def grade_checks(checks: Sequence[Check], *, pending: int = 0, unavailable: int = 0) -> str:
    """Typed grade: failed / inconclusive / pass.  ``reported`` checks never decide."""

    statuses = [c.status for c in checks]
    if FAIL in statuses:
        return "failed"
    if INCONCLUSIVE in statuses or pending or unavailable:
        return "inconclusive"
    if PASS not in statuses:
        return "inconclusive"
    return "pass"


def observed_orders(sizes: Sequence[float], errors: Sequence[float]) -> List[Optional[float]]:
    """log(e_i/e_{i+1}) / log(h_i/h_{i+1}) for successive refinements; None if undefined."""

    out: List[Optional[float]] = []
    for k in range(len(sizes) - 1):
        e0, e1, h0, h1 = errors[k], errors[k + 1], sizes[k], sizes[k + 1]
        if all(math.isfinite(v) for v in (e0, e1, h0, h1)) and e0 > 0.0 and e1 > 0.0 and h0 > h1 > 0.0:
            out.append(math.log(e0 / e1) / math.log(h0 / h1))
        else:
            out.append(None)
    return out


def metric_disposition(p1: float, p2: float, tolerance: float) -> str:
    """P1 vs P2 on one metric; a difference inside the combined allowance is a tie."""

    if not all(math.isfinite(v) for v in (p1, p2, tolerance)):
        return "inconclusive"
    if abs(p1 - p2) <= tolerance:
        return "tie"
    return "P1_preferred" if p1 < p2 else "P2_preferred"


def overall_disposition(per_metric: Dict[str, str]) -> str:
    """D3 outcomes are P1 preferred, P2 preferred or inconclusive (ties and mixes are inconclusive)."""

    votes = {v for v in per_metric.values() if v in ("P1_preferred", "P2_preferred")}
    if "inconclusive" in per_metric.values() or len(votes) != 1:
        return "inconclusive"
    return votes.pop()


def clean(obj: Any) -> Any:
    """JSON-safe copy: numpy scalars/arrays converted, non-finite floats written as strings."""

    if isinstance(obj, dict):
        return {str(k): clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [clean(v) for v in obj]
    if isinstance(obj, np.ndarray):
        return clean(obj.tolist())
    if isinstance(obj, (bool, np.bool_)):
        return bool(obj)
    if isinstance(obj, (int, np.integer)):
        return int(obj)
    if isinstance(obj, (float, np.floating)):
        f = float(obj)
        if math.isfinite(f):
            return f
        return "nan" if math.isnan(f) else ("inf" if f > 0 else "-inf")
    if isinstance(obj, Check):
        return clean(obj.__dict__)
    return obj


# ----------------------------------------------------------------------------------------
# solver access, call accounting and ceilings
# ----------------------------------------------------------------------------------------
_SDK: Optional[SimpleNamespace] = None


def _sdk() -> SimpleNamespace:
    global _SDK
    if _SDK is None:
        import anysolver
        import anysolver.submodel_transfer as st
        from anysolver import BoundaryCondition, LoadCase, solve_linear
        from anysolver.analysis_session import AnalysisSession
        from anysolver.e4_pl_s3_v2d_element import NativeParityE4PLS3V2DShellElement
        from anysolver.elements import ShellElement, create_shell_element
        from anysolver.fe_core import FEModel
        from anysolver.matrix_assembly import assemble_load_vector
        _SDK = SimpleNamespace(
            anysolver=anysolver, st=st, BoundaryCondition=BoundaryCondition, LoadCase=LoadCase,
            solve_linear=solve_linear, AnalysisSession=AnalysisSession, S3=NativeParityE4PLS3V2DShellElement,
            ShellElement=ShellElement, create_shell_element=create_shell_element, FEModel=FEModel,
            assemble_load_vector=assemble_load_vector)
    return _SDK


class ResourceCeiling(Exception):
    """A registered per-case ceiling was reached; the case is reported inconclusive."""


class CallLog:
    """Counts every solve and public-API evaluation and enforces the registered ceilings."""

    def __init__(self, solve_ceiling: int, wall_ceiling_s: float):
        self.solve_ceiling = solve_ceiling
        self.wall_ceiling_s = wall_ceiling_s
        self.start = time.perf_counter()
        self.solves: List[Dict[str, Any]] = []
        self.solve_wall_s: List[float] = []
        self.counts = {"solves": 0, "allowance_estimates": 0, "interface_force_evaluations": 0,
                       "interpolation_calls": 0, "trace_builds": 0}

    def _guard(self, adding_solve: bool = False) -> None:
        if time.perf_counter() - self.start > self.wall_ceiling_s:
            raise ResourceCeiling(f"wall ceiling {self.wall_ceiling_s} s reached")
        if adding_solve and self.counts["solves"] >= self.solve_ceiling:
            raise ResourceCeiling(f"solve ceiling {self.solve_ceiling} reached")

    def solve(self, label: str, model: Any, case: Any) -> Tuple[np.ndarray, Any]:
        self._guard(True)
        S = _sdk()
        t0 = time.perf_counter()
        u, _info = S.solve_linear(model, case)
        u = np.asarray(u, dtype=float)
        self.counts["solves"] += 1
        allowance = S.st.estimate_static_error_allowance(model, u, case)
        self.counts["allowance_estimates"] += 1
        self.solve_wall_s.append(time.perf_counter() - t0)
        self.solves.append({
            "label": label, "dofs": int(model.mesh.dof_manager.total_dofs), "elements": len(model.mesh.elements),
            "eta_scaled": float(allowance.eta_scaled), "kappa_scaled": float(allowance.kappa_scaled),
            "kappa_times_eta": float(allowance.kappa_scaled * allowance.eta_scaled),
            "inconclusive": bool(allowance.inconclusive),
            "max_displacement_allowance": float(np.max(allowance.displacement)) if allowance.displacement.size else 0.0,
        })
        return u, allowance

    def interpolate(self, model: Any, u: np.ndarray, points: Sequence[Any]) -> Dict[int, Any]:
        self._guard()
        self.counts["interpolation_calls"] += 1
        return _sdk().st.interpolate_nodal_field(model, u, points)

    def trace(self, model: Any, values: Dict[int, Any], **kw: Any) -> Any:
        self._guard()
        self.counts["trace_builds"] += 1
        return _sdk().st.build_driven_trace(model, values, **kw)

    def forces(self, model: Any, u: np.ndarray, case: Any, trace: Any, **kw: Any) -> Any:
        self._guard()
        self.counts["interface_force_evaluations"] += 1
        return _sdk().st.interface_forces(model, u, case, trace, **kw)


@dataclass
class CaseOutcome:
    checks: List[Check] = field(default_factory=list)
    pending_external: List[Dict[str, Any]] = field(default_factory=list)   # graded inventory run elsewhere
    unavailable: List[Dict[str, Any]] = field(default_factory=list)        # graded inventory, cannot be run here
    informational_unavailable: List[Dict[str, Any]] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    data: Dict[str, Any] = field(default_factory=dict)


# ----------------------------------------------------------------------------------------
# local fixtures (adapted from ANYsolver tests / historical s0 helpers; see module docstring)
# ----------------------------------------------------------------------------------------
@dataclass
class Grid:
    model: Any
    n: int
    kind: str
    h: float
    size: float
    cells: List[Tuple[int, int]]
    cell_elements: Dict[Tuple[int, int], List[int]]
    nodes: Dict[int, Tuple[int, int]]          # node id -> (i, j)


def node_id(n: int, i: int, j: int) -> int:
    return j * (n + 1) + i + 1


def grid_model(n: int, kind: str, *, cells: Optional[Sequence[Tuple[int, int]]] = None, membrane: bool = False,
               name: str = "plate", size: float = 1.0) -> Grid:
    """[0,size]^2 plate in the xy-plane, ``kind`` = Q4 (qualified E4-PL) or S3 (V2D triangles).

    Node ids are global grid ids, so sub-models over a cell subset share ids with the full grid.
    """

    if kind not in ("Q4", "S3"):
        raise ValueError(f"unknown element kind {kind!r}")
    S = _sdk()
    model = S.FEModel(name)
    model.add_material("steel", E_MOD, NU, density=7850.0)
    h = size / n
    cell_list = sorted(cells, key=lambda c: (c[1], c[0])) if cells is not None else \
        [(i, j) for j in range(n) for i in range(n)]
    used = set()
    for i, j in cell_list:
        used.update({(i, j), (i + 1, j), (i + 1, j + 1), (i, j + 1)})
    nodes: Dict[int, Tuple[int, int]] = {}
    for i, j in sorted(used, key=lambda p: (p[1], p[0])):
        nid = node_id(n, i, j)
        model.add_node(nid, i * h, j * h, 0.0)
        nodes[nid] = (i, j)
    cell_elements: Dict[Tuple[int, int], List[int]] = {}
    eid = 1
    for i, j in cell_list:
        a, b, c, d = node_id(n, i, j), node_id(n, i + 1, j), node_id(n, i + 1, j + 1), node_id(n, i, j + 1)
        if kind == "S3":
            for ids in ((a, b, c), (a, c, d)):
                model.add_element(eid, S.S3(eid, ids, "steel", thickness=THK, reference_normal=(0.0, 0.0, 1.0)))
                cell_elements.setdefault((i, j), []).append(eid)
                eid += 1
        else:
            model.add_element(eid, S.create_shell_element(eid, [a, b, c, d], "steel", thickness=THK))
            cell_elements.setdefault((i, j), []).append(eid)
            eid += 1
    if membrane:
        model.add_boundary_condition(S.BoundaryCondition(
            name="membrane", node_ids=sorted(nodes), dof_constraints={"uz": 0.0, "rx": 0.0, "ry": 0.0}))
    return Grid(model, n, kind, h, size, list(cell_list), cell_elements, nodes)


def xyz(model: Any, node: int) -> np.ndarray:
    return np.asarray(model.mesh.get_node(node).coords(), dtype=float)


def dofs(model: Any, node: int) -> np.ndarray:
    return np.asarray(model.mesh.dof_manager.get_node_dofs(int(node)), dtype=int)


def nodal_vector(model: Any, fn: Callable[[np.ndarray], np.ndarray]) -> np.ndarray:
    u = np.zeros(model.mesh.dof_manager.total_dofs)
    for node in model.mesh.nodes:
        u[dofs(model, node)] = fn(xyz(model, node))
    return u


def f_tension(x: np.ndarray) -> np.ndarray:
    eps = SIGMA / E_MOD
    return np.array([eps * x[0], -NU * eps * x[1], 0.0, 0.0, 0.0, 0.0])


def f_affine(x: np.ndarray, omega: float = 2.0e-4) -> np.ndarray:
    # eps_x=1e-4, eps_y=-3e-5, gamma=5e-5, rotation omega (PLAN A2-exact (b))
    return np.array([1.0e-4 * x[0] + (2.5e-5 - omega) * x[1], (2.5e-5 + omega) * x[0] - 3.0e-5 * x[1],
                     0.0, 0.0, 0.0, omega])


A_RIGID = np.array([1.0e-3, -2.0e-3, 3.0e-3])
OM_RIGID = np.array([1.0e-3, -5.0e-4, 2.0e-3])


def f_rigid(x: np.ndarray) -> np.ndarray:
    return np.concatenate([A_RIGID + np.cross(OM_RIGID, x), OM_RIGID])


def f_bend(x: np.ndarray) -> np.ndarray:
    # w = kappa x^2 / 2; right-handed rotation vector: theta_y = -dw/dx
    return np.array([0.0, 0.0, 0.5 * KAPPA * x[0] ** 2, 0.0, -KAPPA * x[0], 0.0])


def f_gfield(x: np.ndarray) -> np.ndarray:
    return np.array([1.0e-4 * x[0] + 2.0e-5 * x[1] + 1.0e-4, -3.0e-5 * x[1] + 5.0e-5 * x[0], 0.0, 0.0, 0.0, 1.5e-5])


FIELDS: Dict[str, Callable[[np.ndarray], np.ndarray]] = {
    "tension": f_tension, "affine_membrane_rotation": f_affine, "rigid": f_rigid}

_Q4_NATURAL = [(-1.0, -1.0), (1.0, -1.0), (1.0, 1.0), (-1.0, 1.0)]
_S3_NATURAL = [(0.0, 0.0), (1.0, 0.0), (0.0, 1.0)]


def corner_natural(kind: str, local_index: int) -> Tuple[float, float]:
    return (_Q4_NATURAL if kind == "Q4" else _S3_NATURAL)[local_index]


def locate_in_grid(g: Grid, xy: Sequence[float]) -> Tuple[int, Tuple[float, float]]:
    """LOCAL regular-grid locator (not the ANYmesh API): element id and natural coordinates."""

    h, n = g.h, g.n
    x, y = float(xy[0]), float(xy[1])
    ci = min(max(int(math.floor(x / h + 1.0e-12)), 0), n - 1)
    cj = min(max(int(math.floor(y / h + 1.0e-12)), 0), n - 1)
    elements = g.cell_elements[(ci, cj)]
    u, v = (x - ci * h) / h, (y - cj * h) / h
    if g.kind == "Q4":
        return elements[0], (2.0 * u - 1.0, 2.0 * v - 1.0)
    tol = 1.0e-12
    r1, s1 = u - v, v                       # triangle (a, b, c)
    if r1 >= -tol and s1 >= -tol and u <= 1.0 + tol:
        return elements[0], (r1, s1)
    return elements[1], (u, v - u)          # triangle (a, c, d)


def transfer_points(S: SimpleNamespace, src: Grid, targets: Dict[int, np.ndarray]) -> List[Any]:
    points = []
    for node, point in sorted(targets.items()):
        eid, natural = locate_in_grid(src, point[:2])
        element = src.model.mesh.elements[eid]
        w = S.st.element_interpolation_weights(element, natural)
        x_p = w @ np.array([xyz(src.model, nn) for nn in element.node_ids])
        residual = float(np.linalg.norm(x_p - np.asarray(point, float)))
        points.append(S.st.TransferPoint(int(node), int(eid), tuple(float(v) for v in natural),
                                         tuple(float(v) for v in point), inverse_residual=residual))
    return points


def boundary_nodes(g: Grid) -> List[int]:
    n = g.n
    return sorted(nid for nid, (i, j) in g.nodes.items() if i in (0, n) or j in (0, n))


def line_load_case(S: SimpleNamespace, n: int, q: Tuple[float, float] = (2.0e5, 3.0e4)) -> Any:
    case = S.LoadCase("line")
    for j in range(n + 1):
        share = (0.5 if j in (0, n) else 1.0) / n
        case.add_nodal_load(node_id(n, n, j), np.array([q[0] * share, q[1] * share, 0.0]))
    return case


def stiffness_and_load(S: SimpleNamespace, model: Any, case: Any) -> Tuple[Any, np.ndarray]:
    with S.AnalysisSession(model) as session:
        k = session.stiffness_plan().matrix
    f = np.asarray(S.assemble_load_vector(model, case)[0], dtype=float)
    return k, f


def comp_labels(prefix: str, model: Any, node_list: Sequence[int], comps: Sequence[int]) -> List[str]:
    return [f"{prefix}|node {n}|{DOF_NAMES[c]}" for n in node_list for c in comps]


# ----------------------------------------------------------------------------------------
# A0
# ----------------------------------------------------------------------------------------
def run_a0_interp(kind: str, log: CallLog) -> CaseOutcome:
    S = _sdk()
    out = CaseOutcome()
    g = grid_model(2, kind)
    model = g.model
    rng = np.random.default_rng(SEED)
    for fname, fn in FIELDS.items():
        u = nodal_vector(model, fn)
        points, k = [], 0
        for eid in sorted(model.mesh.elements):
            element = model.mesh.elements[eid]
            for _ in range(3):
                if kind == "S3":
                    r, s = rng.uniform(0.05, 0.45, size=2)
                    natural = (float(r), float(s))
                else:
                    natural = tuple(float(v) for v in rng.uniform(-0.95, 0.95, size=2))
                w = S.st.element_interpolation_weights(element, natural)
                x_p = w @ np.array([xyz(model, nn) for nn in element.node_ids])
                # 1 cm off the surface: exact only through the rigid transport rule (D4).
                points.append(S.st.TransferPoint(1000 + k, eid, natural, tuple(x_p + np.array([0.0, 0.0, 0.01])),
                                                 tuple(w)))
                k += 1
        values = log.interpolate(model, u, points)
        errs, alls, labels = [], [], []
        for p in points:
            v = values[p.target_node]
            exact = fn(np.asarray(p.target_point))
            allow = S.st.interpolation_allowance(v, model, u)
            errs.extend(np.abs(v.values - exact))
            alls.extend(allow)
            labels.extend(f"{fname}|pt {p.target_node} elem {p.element_id}|{c}" for c in DOF_NAMES)
        out.checks.append(ratio_check(f"A0.interp.{kind}.{fname}", "max |interpolated - exact| / interpolation_allowance",
                                      worst_ratio(errs, alls, labels), field=fname, points=len(points),
                                      formulation=kind))
    # in-plane correspondence residual: the allowance must cover a deliberate 1 mm tangential error.
    u = nodal_vector(model, f_affine)
    element = model.mesh.elements[1]
    natural = (0.1, -0.2) if kind == "Q4" else (0.2, 0.2)
    w = S.st.element_interpolation_weights(element, natural)
    x_p = w @ np.array([xyz(model, nn) for nn in element.node_ids])
    d = 1.0e-3
    point = S.st.TransferPoint(77, 1, natural, tuple(x_p + np.array([d, 0.0, 0.0])), tuple(w), inverse_residual=d)
    value = log.interpolate(model, u, [point])[77]
    err = np.abs(value.values - f_affine(np.asarray(point.target_point)))
    allow = S.st.interpolation_allowance(value, model, u)
    out.checks.append(ratio_check(f"A0.interp.{kind}.in_plane_residual", "|error| / allowance for a 1 mm tangential residual",
                                  worst_ratio(err, allow, [f"affine|{c}" for c in DOF_NAMES]), formulation=kind))
    out.checks.append(bool_check(f"A0.interp.{kind}.in_plane_residual_exercised", "tangential error is nonzero",
                                 float(np.max(err)) > 0.0, "max error > 0", max_error=float(np.max(err))))
    # ambiguity / convention refusals
    u_rigid = nodal_vector(model, f_rigid)
    out.checks.append(expect_code(
        f"A0.refusal.{kind}.convention_weights", lambda: S.st.interpolate_nodal_field(
            model, u_rigid, [S.st.TransferPoint(99, 1, natural, tuple(x_p), (0.25,) * len(element.node_ids))]),
        "TRANSFER_CONVENTION_MISMATCH"))
    out.checks.append(expect_code(
        f"A0.refusal.{kind}.nonfinite_weights", lambda: S.st.interpolate_nodal_field(
            model, u_rigid, [S.st.TransferPoint(98, 1, natural, tuple(x_p), (math.nan,) * len(element.node_ids))]),
        "TRANSFER_CONVENTION_MISMATCH"))
    legacy = S.FEModel("legacy")
    legacy.add_material("steel", E_MOD, NU, density=7850.0)
    for nn, p in enumerate([(0, 0, 0), (1, 0, 0), (0, 1, 0)], start=1):
        legacy.add_node(nn, *p)
    legacy.add_element(1, S.ShellElement(1, [1, 2, 3], "steel", thickness=THK))
    out.checks.append(expect_code(
        f"A0.refusal.{kind}.unadmitted_element", lambda: S.st.interpolate_nodal_field(
            legacy, np.zeros(18), [S.st.TransferPoint(9, 1, (0.2, 0.2), (0.2, 0.2, 0.0))]),
        "TRANSFER_ELEMENT_NOT_ADMITTED"))
    return out


def run_a0_mpc(kind: str, log: CallLog) -> CaseOutcome:
    """Contract 8.1 / D5: slave s = (m1 + m2)/2, s and m1 on the cut, m2 interior."""

    S = _sdk()
    out = CaseOutcome()
    n = 4
    g = grid_model(n, kind, membrane=True)
    model = g.model
    case = line_load_case(S, n)
    s, m1, m2 = node_id(n, 0, 4), node_id(n, 0, 2), node_id(n, 1, 4)
    sx, m1x, m2x = (int(dofs(model, nn)[0]) for nn in (s, m1, m2))
    model.add_constraint_equation(terms=((sx, 1.0), (m1x, -0.5), (m2x, -0.5)), rhs=0.0, source_id="internal",
                                  dependent_dof=sx)
    cut = [node_id(n, 0, j) for j in range(n + 1)]
    values = {nn: f_gfield(xyz(model, nn)) for nn in cut}
    trace = log.trace(model, values, policy="drive_all")
    redundant = {(trace.rows[i].node, trace.rows[i].label) for i in trace.redundant}
    expected = {(nn, name) for nn in cut for name in ("uz", "rx", "ry")}
    out.checks.append(bool_check(f"A0.mpc.{kind}.redundant_rows", "redundant trace rows equal the membrane-implied set",
                                 redundant == expected, "{uz,rx,ry} of every cut node",
                                 extra=sorted(redundant - expected), missing=sorted(expected - redundant)))
    S.st.apply_driven_trace(model, trace)
    u, allow = log.solve(f"A0.mpc.{kind}", model, case)
    inc = bool(allow.inconclusive)
    rounding = lambda v: 16.0 * EPS * abs(v)  # noqa: E731 - comparison's own roundoff
    disp = [(sx, values[s][0], "s.ux"), (m1x, values[m1][0], "m1.ux"), (m2x, 2 * values[s][0] - values[m1][0], "m2.ux")]
    out.checks.append(ratio_check(
        f"A0.mpc.{kind}.displacement", "|u - driven value| / (solve allowance + 16 eps |value|)",
        worst_ratio([u[d] - v for d, v, _ in disp], [allow.displacement[d] + rounding(v) for d, v, _ in disp],
                    [lab for *_, lab in disp]), inconclusive=inc, formulation=kind))
    forces = log.forces(model, u, case, trace)
    k, f = stiffness_and_load(S, model, case)
    r = k @ u - f
    floor = S.st.force_allowance(model, u, case, allow.displacement)
    exp_forces = [(forces.node_forces[m1][0], r[m1x] - r[m2x], floor[m1x] + floor[m2x], "mu(m1.ux) = r_m1 - r_m2"),
                  (forces.node_forces[s][0], 2 * r[m2x] + r[sx], floor[sx] + 2 * floor[m2x], "mu(s.ux) = r_s + 2 r_m2")]
    out.checks.append(ratio_check(
        f"A0.mpc.{kind}.generalized_reaction", "|interface force - hand-eliminated reaction| / sum of force floors",
        worst_ratio([a - b for a, b, *_ in exp_forces], [c for *_, c, _ in exp_forces], [lab for *_, lab in exp_forces]),
        inconclusive=inc, formulation=kind))
    applied = np.array([2.0e5, 3.0e4])
    total = sum(forces.node_forces[nn][:2] for nn in cut) + applied
    tol = [sum(floor[int(dofs(model, nn)[c])] for nn in cut) + 8 * EPS * abs(applied[c]) for c in (0, 1)]
    out.checks.append(ratio_check(
        f"A0.mpc.{kind}.cut_equilibrium", "|sum cut forces + applied| / (sum force floors + 8 eps P)",
        worst_ratio(total, tol, ["Fx", "Fy"]), inconclusive=inc, formulation=kind,
        equilibrium_residual=float(forces.equilibrium_residual)))
    out.data["trace"] = {"kept": len(trace.kept), "redundant": len(trace.redundant)}
    return out


# ----------------------------------------------------------------------------------------
# A1
# ----------------------------------------------------------------------------------------
A1_N = 8
A1_BLOCK = range(2, 6)            # cells 2..5 -> nodes 2..6
A1_PRESSURE = 1.0e3


def _pressure_case(S: SimpleNamespace, g: Grid, cells: Sequence[Tuple[int, int]], name: str) -> Any:
    share: Dict[int, float] = {}
    area = g.h * g.h
    for i, j in cells:
        ids = [node_id(g.n, i, j), node_id(g.n, i + 1, j), node_id(g.n, i + 1, j + 1), node_id(g.n, i, j + 1)]
        if g.kind == "Q4":
            for nn in ids:
                share[nn] = share.get(nn, 0.0) + area / 4.0
        else:
            for tri in ((ids[0], ids[1], ids[2]), (ids[0], ids[2], ids[3])):
                for nn in tri:
                    share[nn] = share.get(nn, 0.0) + area / 6.0
    case = S.LoadCase(name)
    for nn, a in sorted(share.items()):
        case.add_nodal_load(nn, np.array([0.0, 0.0, A1_PRESSURE * a]))
    return case


def _incident(g: Grid, cells: Sequence[Tuple[int, int]], node: int) -> Tuple[int, int]:
    """(element id, local node index) of ``node`` in the first allowed cell that touches it (lineage restriction)."""

    i0, j0 = g.nodes[node]
    for (i, j) in sorted(cells, key=lambda c: (c[1], c[0])):
        if i <= i0 <= i + 1 and j <= j0 <= j + 1:
            ids = [node_id(g.n, i, j), node_id(g.n, i + 1, j), node_id(g.n, i + 1, j + 1), node_id(g.n, i, j + 1)]
            if g.kind == "Q4":
                return g.cell_elements[(i, j)][0], ids.index(node)
            for eid, tri in zip(g.cell_elements[(i, j)], ((ids[0], ids[1], ids[2]), (ids[0], ids[2], ids[3]))):
                if node in tri:
                    return eid, tri.index(node)
    raise ValueError(f"node {node} touches none of the allowed cells")


def _drive(S: SimpleNamespace, log: CallLog, target: Any, gl: Grid, gu: np.ndarray, gallow: Any,
           cut: Sequence[int], cells: Sequence[Tuple[int, int]], policy: str) -> Any:
    points = []
    for nn in cut:
        eid, local = _incident(gl, cells, nn)
        points.append(S.st.TransferPoint(nn, eid, corner_natural(gl.kind, local), tuple(xyz(gl.model, nn))))
    values = log.interpolate(gl.model, gu, points)
    allowance = {nn: S.st.interpolation_allowance(values[nn], gl.model, gu, gallow.displacement) for nn in cut}
    normals = {nn: [(0.0, 0.0, 1.0)] for nn in cut}
    trace = log.trace(target, {nn: values[nn].values for nn in cut}, policy=policy, normals=normals,
                      allowance=allowance)
    S.st.apply_driven_trace(target, trace)
    return trace


def _a1_groups() -> Dict[str, List[int]]:
    lo, hi = A1_BLOCK[0], A1_BLOCK[-1] + 1
    n = A1_N
    mid = range(lo + 1, hi)
    return {
        "face_south": [node_id(n, i, lo) for i in mid], "face_east": [node_id(n, hi, j) for j in mid],
        "face_north": [node_id(n, i, hi) for i in mid], "face_west": [node_id(n, lo, j) for j in mid],
        "corner_sw": [node_id(n, lo, lo)], "corner_se": [node_id(n, hi, lo)],
        "corner_ne": [node_id(n, hi, hi)], "corner_nw": [node_id(n, lo, hi)],
    }


def _node_dict(model: Any, vec: np.ndarray, nodes: Sequence[int]) -> Dict[int, np.ndarray]:
    return {nn: np.asarray(vec[dofs(model, nn)], dtype=float) for nn in nodes}


def _group_checks(S: SimpleNamespace, prefix: str, model: Any, groups: Dict[str, List[int]], diff: Dict[int, np.ndarray],
                  allow: Dict[int, np.ndarray], about: np.ndarray, *, inconclusive: bool, graded: bool,
                  metric: str, **detail: Any) -> List[Check]:
    checks = []
    for name, nodes in groups.items():
        resultant = np.asarray(S.st.interface_resultant(model, {nn: diff[nn] for nn in nodes}, tuple(about)), float)
        total = np.zeros(6)
        for nn in nodes:
            a = allow[nn]
            arm = float(np.linalg.norm(xyz(model, nn) - about))
            total[:3] += a[:3]
            total[3:] += a[3:] + arm * float(np.linalg.norm(a[:3]))
        checks.append(ratio_check(f"{prefix}.{name}", metric, worst_ratio(resultant, total, [f"{name}|{c}" for c in DOF_NAMES]),
                                  inconclusive=inconclusive, graded=graded, nodes=len(nodes),
                                  about=[float(v) for v in about], **detail))
    return checks


def run_a1(kind: str, log: CallLog) -> CaseOutcome:
    S = _sdk()
    out = CaseOutcome()
    n = A1_N
    inner = [(i, j) for j in A1_BLOCK for i in A1_BLOCK]
    outer = [(i, j) for j in range(n) for i in range(n) if (i, j) not in set(inner)]
    zero6 = {c: 0.0 for c in DOF_NAMES}

    def clamp(g: Grid) -> None:
        ids = sorted(nid for nid, (i, j) in g.nodes.items() if i in (0, n) or j in (0, n))
        if ids:
            g.model.add_boundary_condition(S.BoundaryCondition(name="clamp", node_ids=ids, dof_constraints=dict(zero6)))

    gl = grid_model(n, kind, name="global")
    clamp(gl)
    gcase = _pressure_case(S, gl, [(i, j) for j in range(n) for i in range(n)], "p")
    gu, gallow = log.solve(f"A1.{kind}.global", gl.model, gcase)
    gk, gf = stiffness_and_load(S, gl.model, gcase)
    gres = gk @ gu - gf

    groups = _a1_groups()
    cut = sorted({nn for nodes in groups.values() for nn in nodes})
    lo, hi = A1_BLOCK[0], A1_BLOCK[-1] + 1
    about = np.array([0.5 * (lo + hi) * gl.h, 0.5 * (lo + hi) * gl.h, 0.0])

    def build(cells, name):
        g = grid_model(n, kind, cells=cells, name=name)
        return g

    results: Dict[str, Any] = {}

    def inside(policy: str, graded: bool) -> None:
        tag = "P2" if policy == "drive_all" else "P1"
        g = build(inner, f"inside-{tag}")
        case = _pressure_case(S, g, inner, f"p-in-{tag}")
        trace = _drive(S, log, g.model, gl, gu, gallow, cut, inner, policy)
        u, al = log.solve(f"A1.{kind}.inside.{tag}", g.model, case)
        forces = log.forces(g.model, u, case, trace)
        results[tag] = (g, case, u, al, forces)
        inc = bool(al.inconclusive or gallow.inconclusive)
        interior = [nn for nn, (i, j) in sorted(g.nodes.items()) if nn not in set(cut)]
        for comps, label in (((0, 1, 2), "translations"), ((3, 4, 5), "rotations")):
            errs, alls, labs = [], [], []
            for nn in interior:
                for c in comps:
                    di, dg = dofs(g.model, nn)[c], dofs(gl.model, nn)[c]
                    errs.append(u[di] - gu[dg])
                    alls.append(al.displacement[di] + gallow.displacement[dg])
                    labs.append(f"interior {g.nodes[nn]}|{DOF_NAMES[c]}")
            out.checks.append(ratio_check(
                f"A1.{kind}.{tag}.interior_{label}", "|u_inside - u_global| / (inside + global solve allowance)",
                worst_ratio(errs, alls, labs), inconclusive=inc, graded=graded, policy=policy,
                interior_nodes=len(interior), formulation=kind))
        # oracle: the global field through the inside operator -> interface force of the global solution
        k, f = stiffness_and_load(S, g.model, case)
        ug_in = np.zeros_like(u)
        du_in = np.zeros_like(u)
        for nn in g.nodes:
            ug_in[dofs(g.model, nn)] = gu[dofs(gl.model, nn)]
            du_in[dofs(g.model, nn)] = gallow.displacement[dofs(gl.model, nn)]
        oracle = k @ ug_in - f
        floor = S.st.force_allowance(g.model, u, case, al.displacement)
        per_dof_allow = floor + abs(k) @ du_in
        fn = _node_dict(g.model, np.zeros_like(u), cut)
        for nn in cut:
            fn[nn] = np.asarray(forces.node_forces[nn], float) - oracle[dofs(g.model, nn)]
        alw = _node_dict(g.model, per_dof_allow, cut)
        errs = np.concatenate([fn[nn] for nn in cut])
        alls = np.concatenate([alw[nn] for nn in cut])
        out.checks.append(ratio_check(
            f"A1.{kind}.{tag}.oracle.per_node", "|F_inside - (K_in u_global - f_in)| / (force floor + |K_in| du_global)",
            worst_ratio(errs, alls, comp_labels("cut", g.model, cut, range(6))), inconclusive=inc, graded=graded,
            policy=policy, formulation=kind))
        out.checks.extend(_group_checks(
            S, f"A1.{kind}.{tag}.oracle", g.model, groups, fn, alw, about, inconclusive=inc, graded=graded,
            metric="|resultant(F_inside - oracle)| / summed allowance (about the block centre)", policy=policy))
        applied = sum(f[dofs(g.model, nn)[2]] for nn in g.nodes if nn not in set(cut)) + \
            sum(f[dofs(g.model, nn)[2]] for nn in cut)
        total_z = sum(forces.node_forces[nn][2] for nn in cut)
        out.checks.append(ratio_check(
            f"A1.{kind}.{tag}.closed_cut_total", "|sum Fz + retained-area load| / sum cut floors (SUPPLEMENTARY: totals can vanish)",
            worst_ratio([total_z + applied], [sum(floor[dofs(g.model, nn)[2]] for nn in cut) + 8 * EPS * abs(applied)], ["Fz"]),
            inconclusive=inc, graded=False, policy=policy))

    inside("drive_all", True)
    inside("release_normal_rotation", False)           # P1: reported, not graded

    # complementary outside model, driven the same way (P2), plus its own oracle
    g_out = build(outer, "outside")
    clamp(g_out)
    case_out = _pressure_case(S, g_out, outer, "p-out")
    trace_out = _drive(S, log, g_out.model, gl, gu, gallow, cut, outer, "drive_all")
    u_out, al_out = log.solve(f"A1.{kind}.outside.P2", g_out.model, case_out)
    f_out = log.forces(g_out.model, u_out, case_out, trace_out)
    g_in, case_in, u_in, al_in, f_in = results["P2"]
    inc = bool(al_in.inconclusive or al_out.inconclusive or gallow.inconclusive)
    floor_in = S.st.force_allowance(g_in.model, u_in, case_in, al_in.displacement)
    floor_out = S.st.force_allowance(g_out.model, u_out, case_out, al_out.displacement)
    newton, alw = {}, {}
    for nn in cut:
        gdof = dofs(gl.model, nn)
        newton[nn] = np.asarray(f_in.node_forces[nn], float) + np.asarray(f_out.node_forces[nn], float)
        alw[nn] = np.abs(gres[gdof]) + floor_in[dofs(g_in.model, nn)] + floor_out[dofs(g_out.model, nn)]
    out.checks.append(ratio_check(
        f"A1.{kind}.newton.per_node", "|F_inside + F_outside| / (|global residual| + inside floor + outside floor)",
        worst_ratio(np.concatenate([newton[nn] for nn in cut]), np.concatenate([alw[nn] for nn in cut]),
                    comp_labels("cut", gl.model, cut, range(6))), inconclusive=inc, formulation=kind))
    out.checks.extend(_group_checks(
        S, f"A1.{kind}.newton", gl.model, groups, newton, alw, about, inconclusive=inc, graded=True,
        metric="|resultant(F_inside + F_outside)| / summed allowance (about the block centre)"))
    # outside vs the global field through the outside operator
    k, f = stiffness_and_load(S, g_out.model, case_out)
    ug_o = np.zeros_like(u_out)
    du_o = np.zeros_like(u_out)
    for nn in g_out.nodes:
        ug_o[dofs(g_out.model, nn)] = gu[dofs(gl.model, nn)]
        du_o[dofs(g_out.model, nn)] = gallow.displacement[dofs(gl.model, nn)]
    oracle = k @ ug_o - f
    fo = {nn: np.asarray(f_out.node_forces[nn], float) - oracle[dofs(g_out.model, nn)] for nn in cut}
    ao = _node_dict(g_out.model, floor_out + abs(k) @ du_o, cut)
    out.checks.extend(_group_checks(
        S, f"A1.{kind}.outside_oracle", g_out.model, groups, fo, ao, about, inconclusive=inc, graded=True,
        metric="|resultant(F_outside - oracle)| / summed allowance (about the block centre)"))
    out.data["counts"] = {"cut_nodes": len(cut), "groups": {k2: len(v) for k2, v in groups.items()},
                          "interior_nodes": len(g_in.nodes) - len(cut)}
    out.notes.append("A1 is solver-level: matched node ids, tributary-pressure load partition, hard clamp on the outer "
                     "boundary. The ANYfem cut/job route is covered by the pending external rows of the inventory.")
    return out


# ----------------------------------------------------------------------------------------
# A2-exact
# ----------------------------------------------------------------------------------------
def _tension_source(S: SimpleNamespace, kind: str, log: CallLog, n: int) -> Tuple[Grid, np.ndarray, Any, Any]:
    g = grid_model(n, kind, membrane=True, name="tension-parent")
    zero = lambda *names: {c: 0.0 for c in names}  # noqa: E731
    left = sorted(nid for nid, (i, j) in g.nodes.items() if i == 0)
    g.model.add_boundary_condition(S.BoundaryCondition(name="x0", node_ids=left, dof_constraints=zero("ux")))
    g.model.add_boundary_condition(S.BoundaryCondition(name="origin", node_ids=[node_id(n, 0, 0)], dof_constraints=zero("uy")))
    case = S.LoadCase("tension")
    for j in range(n + 1):
        share = (0.5 if j in (0, n) else 1.0) * g.h
        case.add_nodal_load(node_id(n, n, j), np.array([SIGMA * THK * share, 0.0, 0.0]))
    u, allow = log.solve(f"A2.exact.tension_parent.{kind}", g.model, case)
    return g, u, allow, case


def run_a2_exact(src_kind: str, tgt_kind: str, log: CallLog) -> CaseOutcome:
    S = _sdk()
    out = CaseOutcome()
    ns, nt = 2, 3
    tension_parent = None
    per_cell: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for fname, fn in FIELDS.items():
        if fname == "tension":
            if tension_parent is None:
                tension_parent = _tension_source(S, src_kind, log, ns)
            src, usrc, src_allow, _ = tension_parent
            source_mode, src_du = "solved parent (edge traction, minimal supports)", src_allow.displacement
            src_inc = bool(src_allow.inconclusive)
        else:
            src = grid_model(ns, src_kind, name="sampled-parent")
            usrc, src_du, source_mode, src_inc = nodal_vector(src.model, fn), None, "exact nodal sampling (S0 style)", False
        for policy in ("drive_all", "release_normal_rotation"):
            tag = "P2" if policy == "drive_all" else "P1"
            tgt = grid_model(nt, tgt_kind, name=f"target-{fname}-{tag}")
            model = tgt.model
            driven = boundary_nodes(tgt)
            points = transfer_points(S, src, {nn: xyz(model, nn) for nn in driven})
            values = log.interpolate(src.model, usrc, points)
            ia = {nn: S.st.interpolation_allowance(values[nn], src.model, usrc, src_du) for nn in driven}
            exact = {nn: fn(xyz(model, nn)) for nn in driven}
            cid = f"A2.exact.{src_kind}to{tgt_kind}.{fname}.{tag}"
            out.checks.append(ratio_check(
                f"{cid}.boundary", "|interpolated - exact| / interpolation_allowance (driven nodes)",
                worst_ratio(np.concatenate([values[nn].values - exact[nn] for nn in driven]),
                            np.concatenate([ia[nn] for nn in driven]),
                            [f"{fname}|node {nn}|{c}" for nn in driven for c in DOF_NAMES]),
                inconclusive=src_inc, source_mode=source_mode, policy=policy,
                max_locator_residual=max(p.inverse_residual for p in points)))
            trace = log.trace(model, {nn: values[nn].values for nn in driven}, policy=policy,
                              normals={nn: [(0.0, 0.0, 1.0)] for nn in driven}, allowance=ia)
            S.st.apply_driven_trace(model, trace)
            case = S.LoadCase("none")
            u, allow = log.solve(f"{cid}", model, case)
            exact_u = nodal_vector(model, fn)
            # interior propagation of the driven-value allowance: translations get a lever of the plate size
            rot = max(float(np.max(ia[nn][3:])) for nn in driven)
            trn = max(float(np.max(ia[nn][:3])) for nn in driven) + tgt.size * rot
            extra = np.tile(np.array([trn, trn, trn, rot, rot, rot]), len(model.mesh.nodes))
            order = [d for nn in sorted(model.mesh.nodes) for d in dofs(model, nn)]
            err = np.abs(u - exact_u)[order]
            alw = (allow.displacement[order] + extra)
            out.checks.append(ratio_check(
                f"{cid}.field", "|u_solved - exact| / (solve allowance + propagated driven allowance)",
                worst_ratio(err, alw, [f"{fname}|node {nn}|{c}" for nn in sorted(model.mesh.nodes) for c in DOF_NAMES]),
                inconclusive=bool(allow.inconclusive or src_inc), source_mode=source_mode, policy=policy,
                note="propagated allowance = max driven-row interpolation allowance (+ plate-size lever on rotations)"))
            if fname == "rigid":
                forces = log.forces(model, u, case, trace)
                floor = S.st.force_allowance(model, u, case, allow.displacement)
                nodes = sorted(forces.node_forces)
                out.checks.append(ratio_check(
                    f"{cid}.zero_interface_force", "|interface force| / force floor (rigid motion, no load)",
                    worst_ratio(np.concatenate([forces.node_forces[nn] for nn in nodes]),
                                np.concatenate([floor[dofs(model, nn)] for nn in nodes]),
                                comp_labels("cut", model, nodes, range(6))),
                    inconclusive=bool(allow.inconclusive), policy=policy))
            per_cell.setdefault((fname, tag), {})["ok"] = all(
                c.status == PASS for c in out.checks if c.id.startswith(cid + "."))
    # D3 disposition on the exact set (algebraic score only; the default decision stays with the owner)
    dispositions = {}
    for fname in FIELDS:
        a, b = per_cell[(fname, "P2")]["ok"], per_cell[(fname, "P1")]["ok"]
        dispositions[fname] = "tie_both_within_allowance" if a and b else (
            "P1_fails" if a and not b else ("P2_fails" if b and not a else "both_fail"))
    out.checks.append(Check(f"A2.exact.{src_kind}to{tgt_kind}.d3_disposition", "per-field P1/P2 outcome on exactly representable fields",
                            dispositions, "reported", "none", REPORTED, "", {"note": "D3 decision is not taken from this score"}))
    return out


# ----------------------------------------------------------------------------------------
# A2-bend
# ----------------------------------------------------------------------------------------
class MomentLoadUnavailable(Exception):
    pass


def _bend_parent(S: SimpleNamespace, kind: str, n: int) -> Tuple[Grid, Any]:
    """Cylindrical bending by consistent edge moments (virtual-work derived signs, see below)."""

    g = grid_model(n, kind, name=f"bend-parent-{n}")
    model = g.model
    z = lambda *names: {c: 0.0 for c in names}  # noqa: E731
    model.add_boundary_condition(S.BoundaryCondition(name="pin0", node_ids=[node_id(n, 0, 0)],
                                                     dof_constraints=z("ux", "uy", "uz", "ry")))
    model.add_boundary_condition(S.BoundaryCondition(name="pin1", node_ids=[node_id(n, 0, n)],
                                                     dof_constraints=z("uy", "uz")))
    D = E_MOD * THK ** 3 / (12.0 * (1.0 - NU ** 2))
    W = L = g.size
    # about +y at x=0: +D k W; at x=L: -D k W (virtual work with theta_y = -k(x - L/2)).
    # y-edges carry nu D k L about x so that the anticlastic curvature vanishes: +x-axis at y=W, -x-axis at y=0.
    load: Dict[int, np.ndarray] = {}
    for j in range(n + 1):
        share = (0.5 if j in (0, n) else 1.0) / n
        load.setdefault(node_id(n, 0, j), np.zeros(6))[4] += D * KAPPA * W * share
        load.setdefault(node_id(n, n, j), np.zeros(6))[4] -= D * KAPPA * W * share
    for i in range(n + 1):
        share = (0.5 if i in (0, n) else 1.0) / n
        load.setdefault(node_id(n, i, n), np.zeros(6))[3] += NU * D * KAPPA * L * share
        load.setdefault(node_id(n, i, 0), np.zeros(6))[3] -= NU * D * KAPPA * L * share
    case = S.LoadCase("pure-moment")
    try:
        for nn, vec in sorted(load.items()):
            case.add_nodal_load(nn, vec)
    except Exception as error:  # noqa: BLE001
        raise MomentLoadUnavailable(f"{type(error).__name__}: {error}") from error
    return g, case


def _own_interp(src: Grid, u: np.ndarray, eid: int, natural: Sequence[float], target: np.ndarray) -> np.ndarray:
    """Independent (non-library) interpolation + D4 transport used to cross-check the driven values."""

    element = src.model.mesh.elements[eid]
    if src.kind == "Q4":
        xi, eta = natural
        w = 0.25 * np.array([(1 - xi) * (1 - eta), (1 + xi) * (1 - eta), (1 + xi) * (1 + eta), (1 - xi) * (1 + eta)])
    else:
        r, s = natural
        w = np.array([1.0 - r - s, r, s])
    ids = list(element.node_ids)
    val = w @ np.array([u[dofs(src.model, nn)] for nn in ids])
    x_p = w @ np.array([xyz(src.model, nn) for nn in ids])
    val = val.copy()
    val[:3] += np.cross(val[3:], target - x_p)
    return val


def _drive_child(S: SimpleNamespace, log: CallLog, src: Grid, usrc: np.ndarray, src_du: Optional[np.ndarray],
                 tgt_kind: str, child_n: int, policy: str, label: str) -> Dict[str, Any]:
    tgt = grid_model(child_n, tgt_kind, name=f"child-{label}")
    model = tgt.model
    driven = boundary_nodes(tgt)
    points = transfer_points(S, src, {nn: xyz(model, nn) for nn in driven})
    values = log.interpolate(src.model, usrc, points)
    ia = {nn: S.st.interpolation_allowance(values[nn], src.model, usrc, src_du) for nn in driven}
    trace = log.trace(model, {nn: values[nn].values for nn in driven}, policy=policy,
                      normals={nn: [(0.0, 0.0, 1.0)] for nn in driven}, allowance=ia)
    S.st.apply_driven_trace(model, trace)
    case = S.LoadCase("none")
    u, allow = log.solve(label, model, case)
    own = {p.target_node: _own_interp(src, usrc, p.element_id, p.natural, np.asarray(p.target_point)) for p in points}
    driven_comps = range(6) if policy == "drive_all" else range(5)   # P1 releases theta_z (normal (0,0,1))
    errs, alls, labs = [], [], []
    for nn in driven:
        for c in driven_comps:
            d = dofs(model, nn)[c]
            errs.append(u[d] - own[nn][c])
            alls.append(ia[nn][c] + allow.displacement[d] + 16 * EPS * abs(own[nn][c]))
            labs.append(f"{label}|node {nn}|{DOF_NAMES[c]}")
    exact = nodal_vector(model, f_bend)
    err = np.abs(u - exact)
    core = [nn for nn in model.mesh.nodes if all(0.25 - 1e-12 <= v <= 0.75 + 1e-12 for v in xyz(model, nn)[:2])]
    bnd = driven

    def mx(nodes, c):
        return float(max(err[dofs(model, nn)[c]] for nn in nodes))

    return {
        "boundary_check": worst_ratio(errs, alls, labs), "inconclusive": bool(allow.inconclusive),
        "core_err": {DOF_NAMES[c]: mx(core, c) for c in range(6)},
        "boundary_err": {DOF_NAMES[c]: mx(bnd, c) for c in range(6)},
        "core_allow": float(max(allow.displacement[dofs(model, nn)].max() for nn in core)),
        "core_nodes": len(core), "driven_nodes": len(driven),
    }


def run_a2_bend(src_kind: str, tgt_kind: str, log: CallLog, *, parent_sizes: Sequence[int] = (4, 8, 16, 32),
                child_n: int = 40) -> CaseOutcome:
    S = _sdk()
    out = CaseOutcome()
    registered = tuple(parent_sizes) == (4, 8, 16, 32) and child_n == 40
    if not registered:
        out.notes.append("NON-REGISTERED CONFIGURATION: parent_sizes/child_n differ from the registered 4,8,16,32 / 40")
    out.data["registered_configuration"] = registered
    rows: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
    parent_rows: List[Dict[str, Any]] = []
    physical_unavailable = None
    for n in parent_sizes:
        sampled = grid_model(n, src_kind, name=f"sampled-{n}")
        u_sampled = nodal_vector(sampled.model, f_bend)
        modes = [("sampled_analytic", sampled, u_sampled, None)]
        if physical_unavailable is None:
            try:
                parent, case = _bend_parent(S, src_kind, n)
            except MomentLoadUnavailable as error:
                physical_unavailable = str(error)
            else:
                up, pal = log.solve(f"A2.bend.parent.{src_kind}.n{n}", parent.model, case)
                exact = nodal_vector(parent.model, f_bend)
                perr = np.abs(up - exact)
                parent_rows.append({"n": n, "h": 1.0 / n, "inconclusive": bool(pal.inconclusive),
                                    "max_err": {DOF_NAMES[c]: float(max(perr[dofs(parent.model, nn)[c]]
                                                                        for nn in parent.model.mesh.nodes)) for c in range(6)}})
                modes.insert(0, ("physical_parent", parent, up, pal.displacement))
        for mode, src, usrc, du in modes:
            for policy in ("drive_all", "release_normal_rotation"):
                tag = "P2" if policy == "drive_all" else "P1"
                rec = _drive_child(S, log, src, usrc, du, tgt_kind, child_n, policy, f"A2.bend.{mode}.{src_kind}to{tgt_kind}.{tag}.n{n}")
                rec["n"] = n
                rec["h"] = 1.0 / n
                rows.setdefault((mode, tag), []).append(rec)
    for (mode, tag), recs in sorted(rows.items()):
        worst = max(recs, key=lambda r: r["boundary_check"]["ratio"])
        w = dict(worst["boundary_check"])
        w["sensor"] = f"n{worst['n']}|{w['sensor']}"
        out.checks.append(ratio_check(
            f"A2.bend.{src_kind}to{tgt_kind}.{mode}.{tag}.boundary_equals_interpolation",
            "|child driven value - independently interpolated coarse value| / (interpolation + solve allowance)",
            w, inconclusive=any(r["inconclusive"] for r in recs), policy=tag, levels=[r["n"] for r in recs]))
        hs = [r["h"] for r in recs]
        for comp in ("uz", "ry"):
            for where in ("core_err", "boundary_err"):
                errs = [r[where][comp] for r in recs]
                out.checks.append(Check(
                    f"A2.bend.{src_kind}to{tgt_kind}.{mode}.{tag}.convergence.{where}.{comp}",
                    "observed order vs parent size (child fixed)", observed_orders(hs, errs), "reported (no registered pass threshold)",
                    "none", REPORTED, comp, {"parent_sizes_m": hs, "errors": errs,
                                              "field_scale": 0.5 * KAPPA if comp == "uz" else KAPPA}))
    if parent_rows:
        hs = [r["h"] for r in parent_rows]
        for comp in ("uz", "ry"):
            errs = [r["max_err"][comp] for r in parent_rows]
            out.checks.append(Check(f"A2.bend.{src_kind}.physical_parent.error.{comp}", "parent solve error vs analytic field",
                                    observed_orders(hs, errs), "reported", "none", REPORTED, comp,
                                    {"parent_sizes_m": hs, "errors": errs}))
    # D3 dispositions at the finest parent level, per mode
    for mode in sorted({m for m, _ in rows}):
        per_metric = {}
        p1, p2 = rows[(mode, "P1")][-1], rows[(mode, "P2")][-1]
        for comp in ("uz", "ry"):
            for where in ("core_err", "boundary_err"):
                per_metric[f"{where}.{comp}"] = metric_disposition(p1[where][comp], p2[where][comp],
                                                                   2.0 * max(p1["core_allow"], p2["core_allow"]))
        out.checks.append(Check(f"A2.bend.{src_kind}to{tgt_kind}.{mode}.d3_disposition", "P1 vs P2 at the finest parent",
                                {"overall": overall_disposition(per_metric), "per_metric": per_metric}, "reported", "none",
                                REPORTED, "", {"note": "disposition is recorded, never auto-selected; the owner decides"}))
    if physical_unavailable:
        out.informational_unavailable.append({
            "id": "A2.bend.physical_parent", "reason": "LoadCase.add_nodal_load rejected a six-component moment load: "
            + physical_unavailable, "effect": "only the sampled-analytic coarse field was run"})
    out.informational_unavailable.append({
        "id": "A2.bend.translations_only_control", "reason": "public build_driven_trace always drives the rotations (D2); "
        "the historical translations-only control has no public-API equivalent"})
    out.informational_unavailable.append({
        "id": "A2.bend.d3_energy_split", "reason": "physical vs PL/hourglass/drilling energy is not exposed by the public API; "
        "InterfaceForces.partitioned is False. The D3 drilling decision stays separate from this algebraic score."})
    if not rows or not any(m == "physical_parent" for m, _ in rows):
        out.checks.append(Check("A2.bend.physical_parent_present", "a physically loaded parent was solved", False, True,
                                "exact_expectation", INCONCLUSIVE, "", {"reason": physical_unavailable or "no parent run"}))
    return out


# ----------------------------------------------------------------------------------------
# A7
# ----------------------------------------------------------------------------------------
A7_EXTERNAL = [
    # (id, plan item, layer, existing test selection from the independent assessor's inventory)
    ("A7-X01", "empty volume; coincident cut plane; unsupported surface; straddling relation; stale plan", "ANYgeometry",
     "python -m pytest -q tests/test_volume_cut.py -k \"empty_volume or box_plane_on_a_plate or relations_and_members or non_planar_coons or stale_plan\""),
    ("A7-X02", "ambiguous correspondence / disconnected coincident sheets / lineage exclusion", "ANYmesh",
     "python -m pytest -q tests/test_submodel_correspondence.py -k \"disconnected_coincident or lineage_excludes\""),
    ("A7-X03", "ANYfem clipped distributed load (unselected case), free-standing beam, job source refusals, recipe, "
               "publication refuses changed inputs, cancellation leaves nothing, lineage refusals, activity/unsupported inputs",
     "ANYfem",
     "python -m pytest -q tests/test_submodel_cut.py tests/test_submodel_jobs.py tests/test_submodel_workflow.py -k "
     "\"clipped_distributed_load or free_standing_beam_across or job_source_refusals or recipe_refuses or "
     "publication_refuses_changed_inputs or cancellation_leaves_nothing or lineage_refusals or activity_and_unsupported_inputs_refuse\""),
    ("A7-X04", "stale source / nonlinear source binding and atomic publication (typed codes enumerated by the owner's tests)",
     "ANYfem", "same selection as A7-X03 (source binding: SOURCE_STALE, SOURCE_INCONSISTENT, SOURCE_RECIPE_UNSUPPORTED)"),
]
A7_UNAVAILABLE = [
    ("A7-U01", "a CHOSEN case with a clipped total-distributed load must be rejected at the public ANYfem entry",
     "the existing clipped_distributed_load test shows an unselected case can be skipped; no existing row exercises a chosen "
     "case, and this solver-level adapter cannot build the ANYfem cut"),
    ("A7-U02", "unchanged persistent model, job and artifact ownership after each refusal path",
     "no existing test enumerates ownership per refusal; needs ANYfem session/job fixtures outside this adapter"),
    ("A7-U03", "concrete typed codes for the geometry and correspondence refusals (A7-X01, A7-X02)",
     "their codes are owned by ANYgeometry/ANYmesh; this adapter does not import them, so it neither lists nor asserts them"),
]


def _small_plate(S: SimpleNamespace, n: int = 4, kind: str = "Q4", membrane: bool = True) -> Grid:
    return grid_model(n, kind, membrane=membrane)


def run_a7(log: CallLog) -> CaseOutcome:
    S = _sdk()
    out = CaseOutcome()
    st = S.st
    n = 4

    # D5 equality of two cut DOFs: consistent -> redundant; inconsistent -> TRACE_INCONSISTENT
    def equal_model(consistent: bool):
        g = _small_plate(S, n)
        model = g.model
        s, m = node_id(n, 0, 4), node_id(n, 0, 3)
        sd, md = int(dofs(model, s)[0]), int(dofs(model, m)[0])
        model.add_constraint_equation(terms=((sd, 1.0), (md, -1.0)), rhs=0.0, source_id="equal", dependent_dof=sd)
        cut = [node_id(n, 0, j) for j in range(n + 1)]

        def field(x):
            six = f_gfield(x)
            if consistent:
                six[0] = 1.0e-4 * x[0] + 1.0e-4
            return six
        return model, {nn: field(xyz(model, nn)) for nn in cut}, s

    model, values, s = equal_model(True)
    trace = log.trace(model, values, policy="drive_all")
    out.checks.append(bool_check("A7.D5.consistent_equality_is_redundant", "slave ux row reported redundant",
                                 any(trace.rows[i].node == s and trace.rows[i].label == "ux" for i in trace.redundant),
                                 "(s, ux) in redundant rows"))
    model, values, _ = equal_model(False)
    out.checks.append(expect_code("A7.D5.inconsistent_equality", lambda: st.build_driven_trace(model, values, policy="drive_all"),
                                  "TRACE_INCONSISTENT"))
    # nearly dependent rows
    g = _small_plate(S, n)
    dm = g.model.mesh.dof_manager
    s2, m1, m2 = node_id(n, 0, 4), node_id(n, 0, 3), node_id(n, 1, 3)
    d = [int(dofs(g.model, nn)[0]) for nn in (s2, m1, m2)]
    g.model.add_constraint_equation(terms=((d[0], 1.0), (d[1], -1.0), (d[2], -1.0e-10)), rhs=0.0, source_id="near",
                                    dependent_dof=d[0])
    vals = {node_id(n, 0, j): f_gfield(xyz(g.model, node_id(n, 0, j))) for j in range(n + 1)}
    out.checks.append(expect_code("A7.D5.nearly_dependent_rows", lambda: st.build_driven_trace(g.model, vals, policy="drive_all"),
                                  "TRACE_RANK_AMBIGUOUS"))
    # D5 underdetermined input shapes
    out.checks.append(expect_code("A7.D5.empty_driven_set", lambda: st.build_driven_trace(g.model, {}, policy="drive_all"),
                                  "TRACE_EMPTY"))
    out.checks.append(expect_code("A7.D5.unknown_policy", lambda: st.build_driven_trace(g.model, vals, policy="bogus"),
                                  "TRACE_POLICY"))
    out.checks.append(expect_code("A7.D5.p1_without_normals", lambda: st.build_driven_trace(
        g.model, vals, policy="release_normal_rotation"), "TRACE_NORMAL_MISSING"))
    # D6 junction rule: exact expectations
    dofs3 = (3, 4, 5)
    copl = st.rotation_rows(1, dofs3, [(0, 0, 1), (0, 0, -1)])
    out.checks.append(bool_check("A7.D6.coplanar_releases_normal_rotation", "rank-2 junction: two in-plane rows, normal released",
                                 len(copl) == 2 and all(r.direction is not None and abs(r.direction[2]) < 1e-12 for r in copl),
                                 "2 rows with zero normal component"))
    corner = st.rotation_rows(1, dofs3, [(0, 0, 1), (1, 0, 0)])
    out.checks.append(bool_check("A7.D6.noncoplanar_drives_all", "rank-3 junction: rx, ry, rz driven",
                                 [r.label for r in corner] == ["rx", "ry", "rz"], "[rx, ry, rz]"))
    tilt = 1.0e-5
    out.checks.append(expect_code("A7.D6.inconsistent_normals", lambda: st.rotation_rows(
        1, dofs3, [(0, 0, 1), (math.sin(tilt), 0, math.cos(tilt))]), "TRACE_JUNCTION_ILL_CONDITIONED"))
    out.checks.append(expect_code("A7.D6.zero_normal", lambda: st.rotation_rows(1, dofs3, [(0, 0, 0)]), "TRACE_NORMAL_INVALID"))
    # transfer refusals
    g2 = grid_model(1, "Q4")
    u_r = nodal_vector(g2.model, f_rigid)
    out.checks.append(expect_code("A7.transfer.convention_mismatch", lambda: st.interpolate_nodal_field(
        g2.model, u_r, [st.TransferPoint(99, 1, (0.2, -0.3), (0.5, 0.5, 0.0), (0.25,) * 4)]), "TRANSFER_CONVENTION_MISMATCH"))
    out.checks.append(expect_code("A7.transfer.invalid_control", lambda: st.interpolate_nodal_field(
        g2.model, u_r, [], weight_tolerance=math.nan), "CONTROL_INVALID"))
    legacy = S.FEModel("legacy-target")
    legacy.add_material("steel", E_MOD, NU, density=7850.0)
    for nn, p in enumerate([(0, 0, 0), (1, 0, 0), (0, 1, 0)], start=1):
        legacy.add_node(nn, *p)
    legacy.add_element(1, S.ShellElement(1, [1, 2, 3], "steel", thickness=THK))
    out.checks.append(expect_code("A7.transfer.unadmitted_target_shell", lambda: st.build_driven_trace(
        legacy, {1: np.zeros(6)}, policy="drive_all"), "TRANSFER_ELEMENT_NOT_ADMITTED"))
    # binding and atomicity
    g3 = grid_model(2, "Q4")
    other = grid_model(2, "Q4")
    bvals = {nn: f_affine(xyz(g3.model, nn)) for nn in boundary_nodes(g3)}
    tr = log.trace(g3.model, bvals, policy="drive_all")
    out.checks.append(expect_code("A7.binding.other_model", lambda: st.apply_driven_trace(other.model, tr), "TRACE_MODEL_MISMATCH"))
    before = (list(other.model.boundary_conditions), list(other.model.constraint_equations))
    out.checks.append(bool_check("A7.binding.other_model_unchanged", "refused apply left the other model's constraints unchanged",
                                 before == (list(other.model.boundary_conditions), list(other.model.constraint_equations)),
                                 "identical constraint lists"))
    g3.model.add_boundary_condition(S.BoundaryCondition(name="late", node_ids=[node_id(2, 1, 1)], dof_constraints={"uz": 0.0}))
    snap = (list(g3.model.boundary_conditions), list(g3.model.constraint_equations))
    out.checks.append(expect_code("A7.binding.model_changed", lambda: st.apply_driven_trace(g3.model, tr), "TRACE_MODEL_CHANGED"))
    out.checks.append(bool_check("A7.binding.model_changed_unchanged", "refused apply left the changed model's constraints unchanged",
                                 snap == (list(g3.model.boundary_conditions), list(g3.model.constraint_equations)),
                                 "identical constraint lists"))
    g4 = grid_model(2, "Q4")
    drv = boundary_nodes(g4)
    v4 = {nn: f_affine(xyz(g4.model, nn)) for nn in drv}
    tr4 = st.apply_driven_trace(g4.model, log.trace(g4.model, v4, policy="drive_all"))
    bc0 = [dict(bc.dof_constraints) for bc in g4.model.boundary_conditions]
    eq0 = list(g4.model.constraint_equations)
    val0, unc0 = tr4.values.copy(), tr4.uncertainty.copy()
    bad = dict(v4)
    bad[drv[0]] = np.array([math.nan, 0, 0, 0, 0, 0])
    out.checks.append(expect_code("A7.atomic.refused_update", lambda: st.update_driven_trace_values(g4.model, tr4, bad), "TRACE_VALUES"))
    out.checks.append(bool_check(
        "A7.atomic.refused_update_unchanged", "supports, equations, trace values and uncertainty unchanged by the refused update",
        [dict(bc.dof_constraints) for bc in g4.model.boundary_conditions] == bc0 and list(g4.model.constraint_equations) == eq0
        and np.array_equal(tr4.values, val0) and np.array_equal(tr4.uncertainty, unc0), "bitwise identical"))
    # numerical-honesty refusals on a real solve
    g5 = grid_model(4, "Q4", membrane=True)
    case5 = line_load_case(S, 4)
    cut5 = [node_id(4, 0, j) for j in range(5)]
    tr5 = st.apply_driven_trace(g5.model, log.trace(g5.model, {nn: f_gfield(xyz(g5.model, nn)) for nn in cut5}, policy="drive_all"))
    u5, al5 = log.solve("A7.force_refusals", g5.model, case5)
    wrong = u5.copy()
    wrong[int(dofs(g5.model, node_id(4, 2, 2))[0])] += 1.0e-6
    out.checks.append(expect_code("A7.force.uncertified_displacements", lambda: st.interface_forces(g5.model, wrong, case5, tr5),
                                  "FORCE_UNCERTIFIED"))
    out.checks.append(expect_code("A7.force.equilibrium_enforced", lambda: st.interface_forces(
        g5.model, wrong, case5, tr5, displacement_allowance=al5.displacement), "FORCE_EQUILIBRIUM"))
    g6 = grid_model(1, "Q4")
    t6 = st.apply_driven_trace(g6.model, log.trace(g6.model, {nn: f_rigid(xyz(g6.model, nn)) for nn in g6.nodes}, policy="drive_all"))
    c6 = S.LoadCase("none")
    u6, _ = log.solve("A7.reconstruction_refusal", g6.model, c6)
    p6 = u6.copy()
    p6[0] += 1.0e-10
    out.checks.append(expect_code("A7.allowance.perturbed_prescribed_input", lambda: st.estimate_static_error_allowance(g6.model, p6, c6),
                                  "ALLOWANCE_RECONSTRUCTION"))
    for xid, item, layer, command in A7_EXTERNAL:
        out.pending_external.append({"id": xid, "plan_item": item, "layer": layer, "kind": "existing_test_row",
                                     "command_selection": command, "executed_by_this_tool": False})
    for uid, item, reason in A7_UNAVAILABLE:
        out.unavailable.append({"id": uid, "plan_item": item, "reason": reason, "kind": "genuinely_unavailable"})
    return out


# ----------------------------------------------------------------------------------------
# case registry, inventory and CLI
# ----------------------------------------------------------------------------------------
@dataclass(frozen=True)
class CaseSpec:
    id: str
    stage: str
    description: str
    runner: Callable[[CallLog], CaseOutcome]
    solve_ceiling: int
    wall_ceiling_s: float
    checks: Tuple[Tuple[str, str, str], ...]      # (id pattern, metric, threshold)
    apis: Tuple[str, ...]
    limitations: Tuple[str, ...]


_RATIO = "ratio <= 1 (error / actual API allowance)"
_COMMON_API = ("anysolver.submodel_transfer.interpolate_nodal_field", "interpolation_allowance", "build_driven_trace",
               "apply_driven_trace", "interface_forces", "force_allowance", "estimate_static_error_allowance",
               "anysolver.solve_linear")


def _build_cases() -> Dict[str, CaseSpec]:
    cases: Dict[str, CaseSpec] = {}

    def add(spec: CaseSpec) -> None:
        cases[spec.id] = spec

    for kind in ("Q4", "S3"):
        add(CaseSpec(f"A0-INTERP-{kind}", "A0", f"{kind}: exact affine/tension/rigid interpolation incl. transport, in-plane residual, refusals",
                     lambda log, k=kind: run_a0_interp(k, log), 0, 120.0,
                     ((f"A0.interp.{kind}.tension", "interpolation error / interpolation_allowance", _RATIO),
                      (f"A0.interp.{kind}.affine_membrane_rotation", "same", _RATIO), (f"A0.interp.{kind}.rigid", "same", _RATIO),
                      (f"A0.interp.{kind}.in_plane_residual", "same", _RATIO),
                      (f"A0.interp.{kind}.in_plane_residual_exercised", "bool", "error > 0"),
                      (f"A0.refusal.{kind}.*", "refusal code", "exact code TRANSFER_CONVENTION_MISMATCH / TRANSFER_ELEMENT_NOT_ADMITTED")),
                     _COMMON_API[:2] + ("element_interpolation_weights",), ("local sampling points only; no ANYmesh locate",)))
        add(CaseSpec(f"A0-MPC-{kind}", "A0", f"{kind}: D5 MPC example, displacement, generalized reaction, cut equilibrium",
                     lambda log, k=kind: run_a0_mpc(k, log), 2, 180.0,
                     ((f"A0.mpc.{kind}.redundant_rows", "bool", "redundant == {uz,rx,ry} of cut nodes"),
                      (f"A0.mpc.{kind}.displacement", "|u - driven| / (solve allowance + 16 eps|v|)", _RATIO),
                      (f"A0.mpc.{kind}.generalized_reaction", "|F - hand reaction| / force floors", _RATIO),
                      (f"A0.mpc.{kind}.cut_equilibrium", "|sum F + P| / force floors", _RATIO)),
                     _COMMON_API, ("the plan's example is Q4-only; S3 is an extension run here",)))
        add(CaseSpec(f"A1-{kind}", "A1", f"{kind}: global / inside / complementary outside, per-face and corner work, P2 graded, P1 reported",
                     lambda log, k=kind: run_a1(k, log), 5, 600.0,
                     ((f"A1.{kind}.P2.interior_*", "interior |du| / (inside+global allowance)", _RATIO),
                      (f"A1.{kind}.P2.oracle.*", "interface force vs global-u oracle, per node / face / corner", _RATIO),
                      (f"A1.{kind}.newton.*", "inside + outside interface force, per node / face / corner", _RATIO),
                      (f"A1.{kind}.outside_oracle.*", "outside interface force vs global-u oracle", _RATIO),
                      (f"A1.{kind}.P2.closed_cut_total", "supplementary, reported", "reported"),
                      (f"A1.{kind}.P1.*", "P1 conditions, reported only", "reported")),
                     _COMMON_API + ("interface_resultant",),
                     ("solver-level matched-node restriction, not the ANYfem cut route", "pressure as tributary nodal loads",
                      "historical integration_a1.py not imported (sys.path hacks, writes results)")))
    for s in ("Q4", "S3"):
        for t in ("Q4", "S3"):
            add(CaseSpec(f"A2-EXACT-{s}-{t}", "A2", f"source {s} -> target {t}: 3 exact fields x {{P1,P2}}",
                         lambda log, a=s, b=t: run_a2_exact(a, b, log), 8, 300.0,
                         ((f"A2.exact.{s}to{t}.*.boundary", "driven value error / interpolation_allowance", _RATIO),
                          (f"A2.exact.{s}to{t}.*.field", "solved field error / (solve + propagated allowance)", _RATIO),
                          (f"A2.exact.{s}to{t}.rigid.*.zero_interface_force", "|F| / force floor", _RATIO),
                          (f"A2.exact.{s}to{t}.d3_disposition", "reported", "reported")),
                         _COMMON_API, ("tension parent is solved; affine and rigid parents are exact nodal samples",
                                       "local regular-grid locator")))
            add(CaseSpec(f"A2-BEND-{s}-{t}", "A2", f"source {s} -> target {t}: w = k x^2/2 convergence over parent 0.25..0.03125, child 0.025",
                         lambda log, a=s, b=t: run_a2_bend(a, b, log), 24, 1200.0,
                         ((f"A2.bend.{s}to{t}.*.boundary_equals_interpolation", "driven vs independent interpolation / allowance", _RATIO),
                          (f"A2.bend.{s}to{t}.*.convergence.*", "observed order", "reported"),
                          (f"A2.bend.{s}.physical_parent.error.*", "parent error order", "reported"),
                          (f"A2.bend.{s}to{t}.*.d3_disposition", "P1/P2 disposition", "reported"),
                          ("A2.bend.physical_parent_present", "bool", "a loaded parent was solved")),
                         _COMMON_API, ("moment sign derived by virtual work, not confirmed against S0",
                                       "no translations-only control, no energy split (public API)",
                                       "convergence has no registered pass threshold in PLAN section 7")))
    add(CaseSpec("A7-SOLVER", "A7", "frozen typed refusal inventory (ANYsolver layer invoked; other layers listed, not masked)",
                 run_a7, 6, 180.0,
                 (("A7.D5.*", "typed code / redundancy", "exact code"), ("A7.D6.*", "junction rows", "exact expectation"),
                  ("A7.transfer.*", "typed code", "exact code"), ("A7.binding.*", "typed code and unchanged model", "exact"),
                  ("A7.atomic.*", "typed code and bitwise unchanged state", "exact"), ("A7.force.*", "typed code", "exact code"),
                  ("A7.allowance.*", "typed code", "exact code")),
                 _COMMON_API + ("rotation_rows", "update_driven_trace_values"),
                 ("ANYgeometry/ANYmesh/ANYfem rows are pending external test rows or unavailable; the grade cannot be 'pass' "
                  "while any remains",)))
    return cases


CASES: Dict[str, CaseSpec] = _build_cases()


def inventory() -> Dict[str, Any]:
    """Pre-freeze stage manifest: cases, ceilings, checks, thresholds. Needs no solver."""

    return clean({
        "schema": SCHEMA, "kind": "inventory", "status": "development candidate, not frozen",
        "registered": REGISTERED, "exit_codes": EXIT_CODES,
        "invocation": "python shell_assessment.py --case <ID> --output <new file>; one case per process, one thread",
        "cases": [{"id": c.id, "stage": c.stage, "description": c.description, "solve_ceiling": c.solve_ceiling,
                   "wall_ceiling_s": c.wall_ceiling_s,
                   "checks": [{"id": cid, "metric": m, "threshold": t} for cid, m, t in c.checks],
                   "public_apis": list(c.apis), "limitations": list(c.limitations)} for c in CASES.values()],
        "a7_pending_external": [{"id": i, "plan_item": p, "layer": l, "command_selection": c} for i, p, l, c in A7_EXTERNAL],
        "a7_unavailable": [{"id": i, "plan_item": p, "reason": r} for i, p, r in A7_UNAVAILABLE],
        "not_in_scope": ["A3", "A4", "A5", "A6", "A8"],
        "helper_limitations": [
            "local regular-grid locator replaces the ANYmesh correspondence API",
            "historical s0/s1s3 scripts are not imported or executed",
            "the module origin and hashes of the installed anysolver are recorded per run, not pinned here"],
    })


def check_declared(case_id: str, check_id: str) -> bool:
    return any(fnmatch.fnmatchcase(check_id, pattern) for pattern, _m, _t in CASES[case_id].checks)


def candidate_record() -> Dict[str, Any]:
    rec: Dict[str, Any] = {"python": sys.version, "executable": sys.executable, "platform": platform.platform(),
                           "numpy": np.__version__, "numpy_imported_before_thread_pin": _NUMPY_PRELOADED,
                           "script": str(Path(__file__).resolve()),
                           "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                           "thread_env": {k: os.environ.get(k) for k in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS")}}
    try:
        import scipy
        rec["scipy"] = scipy.__version__
    except Exception as error:  # noqa: BLE001
        rec["scipy"] = f"unavailable: {error}"
    try:
        import inspect
        S = _sdk()
        rec["anysolver_file"] = str(Path(S.anysolver.__file__).resolve())
        mod = Path(S.st.__file__).resolve()
        rec["submodel_transfer_file"] = str(mod)
        rec["submodel_transfer_sha256"] = hashlib.sha256(mod.read_bytes()).hexdigest()
        rec["solver_inconclusive_threshold_default"] = inspect.signature(
            S.st.estimate_static_error_allowance).parameters["inconclusive_threshold"].default
    except Exception as error:  # noqa: BLE001
        rec["anysolver_error"] = f"{type(error).__name__}: {error}"
    try:
        from threadpoolctl import threadpool_info
        info = threadpool_info()
        rec["blas_threads"] = [{"library": i.get("internal_api"), "num_threads": i.get("num_threads")} for i in info]
        rec["single_thread_confirmed"] = all(i.get("num_threads") == 1 for i in info)
    except Exception:  # noqa: BLE001 - optional
        rec["blas_threads"] = None
        rec["single_thread_confirmed"] = None
    return rec


def run_case(case_id: str, runner_override: Optional[Callable[[CallLog], CaseOutcome]] = None) -> Dict[str, Any]:
    spec = CASES[case_id]
    log = CallLog(spec.solve_ceiling, spec.wall_ceiling_s)
    t0 = time.perf_counter()
    error: Optional[Dict[str, Any]] = None
    outcome = CaseOutcome()
    grade = None
    try:
        outcome = (runner_override or spec.runner)(log)
    except ResourceCeiling as exc:
        error = {"type": "ResourceCeiling", "message": str(exc)}
        grade = "inconclusive"
    except Exception as exc:  # noqa: BLE001 - preserved as failed evidence
        error = {"type": type(exc).__name__, "message": str(exc), "traceback": traceback.format_exc()}
        grade = "harness_error"
    undeclared = [c.id for c in outcome.checks if not check_declared(case_id, c.id)] if runner_override is None else []
    if grade is None:
        grade = grade_checks(outcome.checks, pending=len(outcome.pending_external), unavailable=len(outcome.unavailable))
        if undeclared:
            grade = "failed" if grade == "pass" else grade
    elif grade == "inconclusive":
        grade = "inconclusive" if all(c.status != FAIL for c in outcome.checks) else "failed"
    result = {
        "schema": SCHEMA, "case": case_id, "stage": spec.stage, "description": spec.description, "grade": grade,
        "registered": REGISTERED, "ceilings": {"solves": spec.solve_ceiling, "wall_s": spec.wall_ceiling_s},
        "counts": dict(log.counts), "solves": log.solves,
        "checks": [c.__dict__ for c in outcome.checks],
        "pending_external": outcome.pending_external, "unavailable": outcome.unavailable,
        "informational_unavailable": outcome.informational_unavailable,
        "notes": outcome.notes, "data": outcome.data, "undeclared_check_ids": undeclared, "error": error,
        "limitations": list(spec.limitations),
    }
    result = clean(result)
    digest = hashlib.sha256(json.dumps(result, sort_keys=True).encode()).hexdigest()
    result["digest_sha256_of_deterministic_part"] = digest
    result["candidate"] = clean(candidate_record())
    result["timing"] = {"case_wall_s": time.perf_counter() - t0, "solve_wall_s": clean(log.solve_wall_s)}
    result["exit_code"] = EXIT_CODES.get(grade, EXIT_CODES["harness_error"])
    return result


def output_path_error(path: Path) -> Optional[str]:
    resolved = path.resolve()
    if resolved.exists():
        return f"output {resolved} already exists (exclusive output; nothing was run)"
    if not resolved.parent.is_dir():
        return f"output directory {resolved.parent} does not exist"
    for name in PROTECTED_DIRS:
        protected = (HANDOFF_ROOT / name).resolve()
        if resolved == protected or protected in resolved.parents:
            return f"output {resolved} lies inside historical evidence folder {protected}"
    return None


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="S6 shell assessment input builder (A0, A1, A2, A7)")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--inventory", action="store_true", help="print the pre-freeze stage manifest and exit")
    group.add_argument("--case", choices=sorted(CASES), help="run exactly one case")
    parser.add_argument("--output", help="new JSON result file (must not exist)")
    args = parser.parse_args(argv)
    if args.inventory:
        print(json.dumps(inventory(), indent=2, sort_keys=True))
        return 0
    if not args.output:
        print("--output is required with --case", file=sys.stderr)
        return EXIT_CODES["invocation_error"]
    problem = output_path_error(Path(args.output))
    if problem:
        print(problem, file=sys.stderr)
        return EXIT_CODES["invocation_error"]
    result = run_case(args.case)
    text = json.dumps(result, indent=2, sort_keys=True)
    try:
        with open(args.output, "x", encoding="utf-8") as handle:     # exclusive create
            handle.write(text + "\n")
    except FileExistsError:
        print(f"output {args.output} appeared during the run; result not written", file=sys.stderr)
        print(text)
        return EXIT_CODES["invocation_error"]
    summary = {k: result[k] for k in ("case", "grade", "counts", "exit_code")}
    print(json.dumps(summary, sort_keys=True))
    return int(result["exit_code"])


if __name__ == "__main__":
    sys.exit(main())
