"""Save/reopen and solve an explicitly experimental static Q4/S3 V2D/B2 panel.

Requires a solver exposing B2MixedPanelDefinition; no historical solver fallback.
This persists inputs, not material state, inertia, or an accepted checkpoint.
"""
import argparse
import json
from pathlib import Path

import numpy as np
from scipy import sparse
from anysolver import stiffened_shell as api
from anyfem.io.project_file import load_project, save_project
from anyfem.model.project import Project
from anyfem.model.records import AnalysisDefinition
from anyfem.solve.stiffened_shell_b2 import ExperimentalB2MixedPointPanel
from anyfem.solve.stiffened_shell_project import point_panel_from_analysis, solve_point_panel


def build_panel(count=10):
    if count not in (1, 10, 100):
        raise ValueError("example supports 1, 10 or 100 cases")
    nodes = {1: (0., 0., 0.), 2: (1., 0., 0.), 3: (2., 0., 0.),
             4: (0., 1., 0.), 5: (1., 1., 0.), 6: (2., 1., 0.)}
    shells = (api.Q4PanelShell("left", (1, 2, 5, 4), 1000., .3, .15),
              api.S3V2DPanelShell("right", (2, 3, 6), 1000., .3, .15, (0., 0., 1.)),
              api.S3V2DPanelShell("right-upper", (2, 6, 5), 1000., .3, .15, (0., 0., 1.)))
    section = api.LinearSection.isotropic(E=2000., nu=.25, area=.04,
        Iy=.00015, Iz=.0003, J=.0004, ky=.8, kz=.7)
    members = tuple(api.PhysicalB2Member(name, name+"-source", (10+i, 11+i),
        ((.2+i, .5, .2), (1.2+i, .5, .2)), (0., 1., 0.), "section", section)
        for i, name in enumerate(("beam-left", "beam-right")))
    points = (api.Q4B2Point("joint-left", "left", "beam-left", .5, (0., .08, -.03)),
              api.Q4B2Point("joint-right", "right", "beam-right", .5, (0., .08, -.03)))
    fixed = set(range(6)) | set(range(18, 24)) | set(range(36, 42))
    fixed.update(6*i+5 for i in (1, 2, 4, 5))
    free = [i for i in range(54) if i not in fixed]
    definition = api.B2MixedPanelDefinition(api.B2_E4_PL_FAST_LINEAR_V1, nodes,
        shells, members, points, sparse.eye(54, format="csr")[:, free], np.zeros(54))
    load = np.zeros(54)
    load[-6:] = (.04, .08, -.03, .01, -.02, .015)
    return ExperimentalB2MixedPointPanel.from_definition(definition,
        tuple((f"case-{i}", (1.+i/count)*load) for i in range(count)),
        selected_dofs=(49, 50, 52), selected_points=("joint-right",),
        recover_members={"beam-right": (.25, .75)})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path, help="new or replaceable .anyfem Project path")
    parser.add_argument("--cases", type=int, choices=(1, 10, 100), default=10)
    args = parser.parse_args()
    panel = build_panel(args.cases)
    project = Project("experimental static mixed panel", mesh_only=True)
    analysis = AnalysisDefinition.experimental_point_panel("mixed", panel)
    project.add_analysis(analysis)
    reopened = load_project(save_project(project, args.output))
    restored = reopened.analyses[analysis.id]
    assert type(point_panel_from_analysis(restored)) is ExperimentalB2MixedPointPanel
    result = solve_point_panel(restored)
    if result.status != "completed":
        raise RuntimeError(f"experimental mixed panel {result.status}: {result.failure_reason}")
    print(json.dumps({"status": panel.status, "schema": panel.schema,
        "file": str(args.output.resolve()), "cases": len(result.cases),
        "factorizations": result.factorization_count, "solves": result.solve_count,
        "selected_first": result.cases[0].selected_displacements,
        "selected_last": result.cases[-1].selected_displacements}, sort_keys=True))


if __name__ == "__main__":
    main()
