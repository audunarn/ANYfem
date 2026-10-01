"""Experimental static Q4/B2 selected-node footprint input and Project roundtrip.

The complete right-cell node group locks normal spin at a physical B2 endpoint.
This is not a weld, distributing coupling, finite-state checkpoint or qualified
physical drilling/torque model. The JSON file stores inputs, not accepted state.
"""
import argparse
import json
from pathlib import Path

import numpy as np
from scipy import sparse
from anysolver import stiffened_shell as api
from anyfem.io.project_file import save_project, load_project
from anyfem.model.project import Project
from anyfem.model.records import AnalysisDefinition
from anyfem.solve.stiffened_shell_b2 import (
    ExperimentalB2NodalRigidFootprintPanel, save_case, load_case,
)
from anyfem.solve.stiffened_shell_project import solve_point_panel


def build_case(count=10):
    if count not in (1, 10, 100):
        raise ValueError("example supports 1, 10 or 100 cases")
    nodes = {1: (0., 0., 0.), 2: (1., 0., 0.), 3: (2., 0., 0.),
             4: (0., 1., 0.), 5: (1., 1., 0.), 6: (2., 1., 0.)}
    shells = (api.Q4PanelShell("left", (1, 2, 5, 4), 1000., .3, .15),
              api.Q4PanelShell("right", (2, 3, 6, 5), 1000., .3, .15))
    section = api.LinearSection.isotropic(E=2000., nu=.25, area=.04,
        Iy=.00015, Iz=.0003, J=.0004, ky=.8, kz=.7)
    members = tuple(api.PhysicalB2Member(f"beam-{i}", f"source-{i}",
        (10+i, 11+i), ((float(i), .5, .2), (float(i+1), .5, .2)),
        (0., 1., 0.), "section", section) for i in range(2))
    group = api.B2NodalRigidFootprint("right-patch", (2, 3, 6, 5), 11, ((2, 3, 6, 5),))
    # Sorted shell IDs1..6, then distinct beam-domain IDs10..12. No extra
    # normal-spin supports on footprint vertices: the six-pose law locks spin.
    fixed = set(range(6)) | set(range(18, 24)) | set(range(36, 42))
    free = [i for i in range(54) if i not in fixed]
    definition = api.B2Q4PanelDefinition(api.B2_E4_PL_FAST_LINEAR_V1,
        nodes, shells, members, (), sparse.eye(54, format="csr")[:, free], np.zeros(54), (group,))
    cases = []
    for i in range(count):
        load = np.zeros(54); scale = 1.+i/count
        load[49] = 2.*scale; load[50] = -.5*scale
        load[51] = .1*scale; load[53] = .15*scale
        cases.append((f"physical-{i:03d}", load))
    return ExperimentalB2NodalRigidFootprintPanel.from_definition(definition, cases,
        selected_dofs=(49, 50, 53), selected_footprints=api.B2NodalRigidFootprintSelection(("right-patch",)),
        recover_members={"beam-1": (.25, .75)})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path, help="input .anyfem-b2.json file")
    parser.add_argument("--cases", type=int, choices=(1, 10, 100), default=10)
    parser.add_argument("--project", type=Path, help="optional ordinary .anyfem Project file")
    args = parser.parse_args()
    reopened = load_case(save_case(build_case(args.cases), args.output))
    if args.project:
        project = Project("explicit experimental B2 footprint", mesh_only=True)
        record = AnalysisDefinition.experimental_point_panel("footprint", reopened)
        project.add_analysis(record)
        result = solve_point_panel(load_project(save_project(project, args.project)).analyses[record.id])
    else:
        result = reopened.solve_linear_many()
    if result.status != "completed":
        raise RuntimeError(f"experimental B2 footprint: {result.status}: {result.failure_reason}")
    first = result.cases[0].footprint_wrenches[0]
    print(json.dumps(dict(status=result.status, experimental_status=reopened.status,
        law=result.footprint_law_id, cases=len(result.cases), factorization_count=result.factorization_count,
        solve_count=result.solve_count, first_case=result.cases[0].case_id,
        selected_first=result.cases[0].selected_displacements,
        shell_force_N=first.shell_force_N, shell_moment_Nm=first.shell_moment_Nm,
        production_qualified=False), sort_keys=True))


if __name__ == "__main__":
    main()
