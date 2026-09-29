"""Run an unqualified elastic Q4/B2 point-panel through ANYfem.

Two flat Q4 shells and two physical B2 members share two eccentric reference
points. This demonstrates input save/reopen, 1/10/100 compatible linear loads,
selected results and one reused factorization. It is not a production model or
a qualified weld/footprint, nonlinear, modal or buckling analysis.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
from scipy import sparse

from anyfem.solve.stiffened_shell_b2 import (
    ExperimentalB2PointPanel, load_case, save_case,
)
from anysolver import stiffened_shell


def build_case(count: int) -> ExperimentalB2PointPanel:
    if count not in (1, 10, 100):
        raise ValueError("example supports 1, 10 or 100 load cases")
    nodes = {
        1: (0., 0., 0.), 2: (1., 0., 0.), 3: (2., 0., 0.),
        4: (0., 1., 0.), 5: (1., 1., 0.), 6: (2., 1., 0.),
    }
    shells = (
        stiffened_shell.Q4PanelShell("left", (1, 2, 5, 4), 1000., .3, .15),
        stiffened_shell.Q4PanelShell("right", (2, 3, 6, 5), 1000., .3, .15),
    )
    section = stiffened_shell.LinearSection.isotropic(
        E=2000., nu=.25, area=.04, Iy=.00015, Iz=.0003,
        J=.0004, ky=.8, kz=.7)
    members = (
        stiffened_shell.PhysicalB2Member(
            "beam-left", "source-left", (10, 11),
            ((.2, .5, .2), (1.2, .5, .2)),
            (0., 1., 0.), "section", section),
        stiffened_shell.PhysicalB2Member(
            "beam-right", "source-right", (11, 12),
            ((1.2, .5, .2), (2.2, .5, .2)),
            (0., 1., 0.), "section", section),
    )
    points = (
        stiffened_shell.Q4B2Point(
            "joint-left", "left", "beam-left", .5, (0., .08, -.03)),
        stiffened_shell.Q4B2Point(
            "joint-right", "right", "beam-right", .5, (0., .08, -.03)),
    )
    fixed = set()
    for node_id in (1, 4):
        fixed.update(6*(node_id-1)+axis for axis in range(6))
    fixed.update(6*6+axis for axis in range(6))
    for node_id in (2, 3, 5, 6):
        fixed.add(6*(node_id-1)+5)
    free = [index for index in range(54) if index not in fixed]
    definition = stiffened_shell.B2Q4PanelDefinition(
        stiffened_shell.B2_E4_PL_FAST_LINEAR_V1,
        nodes, shells, members, points,
        sparse.eye(54, format="csr")[:, free], np.zeros(54))
    cases = []
    for index in range(count):
        load = np.zeros(54)
        scale = 1.+index/count
        load[6*8+1] = .08*scale
        load[6*5+2] = -.16*scale
        cases.append((f"load-{index:03d}", load))
    return ExperimentalB2PointPanel.from_definition(
        definition, cases, selected_dofs=(6*8+1, 6*5+2),
        selected_points=("joint-right",),
        recover_members={"beam-right": (.25, .75)})


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path,
                        help="new or replaceable .anyfem-b2.json input path")
    parser.add_argument("--cases", type=int, choices=(1, 10, 100), default=10)
    args = parser.parse_args()
    reopened = load_case(save_case(build_case(args.cases), args.output))
    result = reopened.solve_linear_many()
    if result.status != "completed":
        raise RuntimeError(f"experimental B2 panel: {result.status}: "
                           f"{result.failure_reason}")
    print(json.dumps({
        "status": reopened.status,
        "file": str(args.output.resolve()),
        "cases": len(result.cases),
        "factorizations": result.factorization_count,
        "solves": result.solve_count,
        "selected_first": result.cases[0].selected_displacements,
        "selected_last": result.cases[-1].selected_displacements,
    }, sort_keys=True))


if __name__ == "__main__":
    main()
