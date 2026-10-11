"""A5 stiffened panel: discretisation error budget (comparative evidence, Level A).

The A5 regression in ``tests/test_submodel_cut.py`` checks discrete carry-over on one
mesh.  This file reports what PLAN.md A5 asks beyond it ("error budget as A3; full
fine model as comparative evidence only"), for the same parent: the 3 x 3 plate grid
under pressure with flat-bar stiffeners (automatic offset) on y = 0.3 and x = 0.7, a
coarse parent at h = 0.1 and the box (0.2, 0.2)-(0.8, 0.8) cutting both stiffeners.

* Reference: the full stiffened model at h = 0.1, 0.05, 0.025 and 0.0125; observed
  order from the three finest levels and a Richardson estimate of the converged value.
* Submodels: the child cut from the h = 0.1 parent, meshed at hc = 0.05, 0.025, 0.0125.
* Budget per quantity and child size: reference discretisation (Richardson estimate
  minus the finest full value), transfer error (child minus the full model at the same
  h: what driving from the coarse parent costs), submodel discretisation (full model at
  hc minus the reference) and the total (child minus the reference).

Quantities: plate deflection w at the panel centre (0.5, 0.5), stiffener deflection w
and section rotation rx at (0.5, 0.3).  No analytical bound is available for this
panel; the full model shares ANYsolver's element mathematics and is comparative
evidence only.  The engineering limit (3 %, proposed in PLAN.md for A3) is reported,
not asserted: the acceptance decision belongs to S6.

Run from the worktree root (not collected by default: pytest's testpaths is ``tests``):
``python docs/handoff/submodelling-20261009/s4a/run_tests.py docs/handoff/submodelling-20261009/s4b/test_a5_budget.py -s --basetemp=<writable dir>``.
Writes ``a5_budget.json`` next to this file.
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[3] / "tests"))

from anygeometry.volume_cut import BoxVolume  # noqa: E402
from anyfem.submodel_jobs import add_submodel, discard_staged_run, stage_submodel_run, submodel_snapshot  # noqa: E402
from test_submodel_cut import Parent, stiffened_project  # noqa: E402
from test_submodel_workflow import solved  # noqa: E402

FULL = (0.1, 0.05, 0.025, 0.0125)
CHILD = (0.05, 0.025, 0.0125)  # children of the h = 0.1 parent
PARENTS = (0.1, 0.05, 0.025)  # parents of an hc = 0.0125 child: the transfer error against parent refinement
BOX = BoxVolume((0.2, 0.2, -0.1), (0.8, 0.8, 0.1))
POINTS = {"w_centre": ((0.5, 0.5), 2), "w_stiffener": ((0.5, 0.3), 2), "rx_stiffener": ((0.5, 0.3), 3)}
LIMIT = 0.03


def value(mesh, solution, quantity):
    (x, y), component = POINTS[quantity]
    node = min(mesh.nodes, key=lambda n: np.linalg.norm(np.asarray(mesh.nodes[n]) - (x, y, 0.0)))
    assert np.linalg.norm(np.asarray(mesh.nodes[node]) - (x, y, 0.0)) < 1e-12, (quantity, mesh.nodes[node])
    return float(solution.node_displacement(node)[component])


def test_a5_error_budget(tmp_path):
    full = {}
    for h in FULL:
        project, _faces, _stiffeners = stiffened_project()
        mesh, solution, _binding = solved(project, h)
        full[h] = {q: value(mesh, solution, q) for q in POINTS}

    def children(hp, sizes):
        project, gfaces, _stiffeners = stiffened_project()
        parent = Parent(tmp_path / f"parent-{hp}", project, gfaces, mesh=project.generate_mesh(hp))
        found = {}
        for hc in sizes:
            definition = parent.cut(BOX, target_size=hc, name=f"a5 {hp} {hc}").definition
            parent.register(definition)
            staged = stage_submodel_run(submodel_snapshot(parent.session), definition.id, parent.store)
            try:
                result = staged.result
                body = result.diagnostics["free_body"]
                assert body["force_ratio"] < 1e-10 and body["moment_ratio"] < 1e-10
                found[hc] = {q: value(result.solution.built.mesh, result.solution, q) for q in POINTS}
            finally:
                discard_staged_run(staged)
        return found

    child = children(0.1, CHILD)
    by_parent = {hp: children(hp, (0.0125,))[0.0125] for hp in PARENTS[1:]}
    by_parent[0.1] = child[0.0125]

    report = {"full": {str(h): v for h, v in full.items()}, "child": {str(h): v for h, v in child.items()},
              "child_0.0125_by_parent": {str(h): v for h, v in sorted(by_parent.items())},
              "limit_proposed": LIMIT, "quantities": {}}
    for q in POINTS:
        f1, f2, f3 = (full[h][q] for h in FULL[1:])
        order = math.log(abs((f1 - f2) / (f2 - f3))) / math.log(2.0)
        reference = f3 + (f3 - f2) / (2.0 ** order - 1.0)
        scale = abs(reference)
        rows = {}
        for hc in CHILD:
            rows[str(hc)] = {
                "transfer": (child[hc][q] - full[hc][q]) / scale,
                "submodel_discretisation": (full[hc][q] - reference) / scale,
                "total": (child[hc][q] - reference) / scale,
            }
            rows[str(hc)]["within_limit"] = abs(rows[str(hc)]["total"]) <= LIMIT
        report["quantities"][q] = {"observed_order": order, "reference": reference,
                                   "reference_discretisation": (reference - f3) / scale,
                                   "coarse_parent": (full[0.1][q] - reference) / scale, "children": rows,
                                   "child_0.0125_by_parent": {
                                       str(hp): {"parent": (full[hp][q] - reference) / scale,
                                                 "transfer": (by_parent[hp][q] - full[0.0125][q]) / scale,
                                                 "total": (by_parent[hp][q] - reference) / scale}
                                       for hp in PARENTS}}
        assert math.isfinite(order) and math.isfinite(reference), (q, order)
    (HERE / "a5_budget.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report["quantities"], indent=2))
