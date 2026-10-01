"""Experimental supported virgin B2 footprint modes and input roundtrip.

Normal spin is locked by the reference-linear footprint law. Mechanical
qualification is incomplete. The files store physical inputs, not solver state.
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
from anyfem.solve.stiffened_shell_b2 import ExperimentalB2ReferenceModalPanel, save_case, load_case
from anyfem.solve.stiffened_shell_project import solve_point_panel


def build_case(count=2):
    nodes = {1: (0., 0., 0.), 2: (1., 0., 0.), 3: (2., 0., 0.),
             4: (0., 1., 0.), 5: (1., 1., 0.), 6: (2., 1., 0.)}
    shells = (api.Q4PanelShell("left", (1, 2, 5, 4), 1000., .3, .15, density_kg_m3=2.),
              api.Q4PanelShell("right", (2, 3, 6, 5), 1000., .3, .15, density_kg_m3=2.))
    section = api.LinearSection.isotropic(E=2000., nu=.25, area=.04,
        Iy=.00015, Iz=.0003, J=.0004, ky=.8, kz=.7)
    mass = api.MassMetadata("explicit-centered-physical-inertia", (0., 0., 0.),
        tuple(map(tuple, np.diag((.3, .3, .3, .003, .001, .002)))))
    members = tuple(api.PhysicalB2Member(f"beam-{i}", f"source-{i}",
        (10+i, 11+i), ((float(i), .5, .2), (float(i+1), .5, .2)),
        (0., 1., 0.), "section", section, mass=mass) for i in range(2))
    group = api.B2NodalRigidFootprint("right-patch", (2, 3, 6, 5), 11, ((2, 3, 6, 5),))
    # Sorted shell nodes1..6, then distinct beam-domain nodes10..12.
    # Physical coordinate supports detect all six rigid fields; offsets are zero.
    fixed = set(range(6)) | set(range(18, 24)) | set(range(36, 42))
    free = [i for i in range(54) if i not in fixed]
    definition = api.B2Q4PanelDefinition(api.B2_E4_PL_FAST_LINEAR_V1,
        nodes, shells, members, (), sparse.eye(54, format="csr")[:, free],
        np.zeros(54), (group,))
    return ExperimentalB2ReferenceModalPanel.from_definition(
        definition, api.B2ReferenceModesRequest(count=count, shift_hz=.5))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path, help="input .anyfem-b2.json file")
    parser.add_argument("--count", type=int, default=2)
    parser.add_argument("--project", type=Path, help="optional ordinary .anyfem Project file")
    args = parser.parse_args()
    reopened = load_case(save_case(build_case(args.count), args.output))
    if args.project:
        project = Project("experimental B2 footprint reference modes", mesh_only=True)
        record = AnalysisDefinition.experimental_point_panel("modes", reopened)
        project.add_analysis(record)
        restored = load_project(save_project(project, args.project)).analyses[record.id]
        result = solve_point_panel(restored)
    else:
        result = reopened.modes()
    if result.status != "ok":
        raise RuntimeError(f"experimental B2 footprint modes: {result.status}: {result.failure_reason}")
    print(json.dumps(dict(status=result.status, experimental_status=reopened.status,
        frequencies_hz=result.frequencies_hz, equation_residuals=result.equation_residuals,
        constraint_residuals=result.constraint_residuals, constraint_rows=len(result.constraint_rows),
        footprint_ids=result.footprint_ids, footprint_law_id=result.footprint_law_id,
        normal_spin=result.normal_spin, mechanical_qualification=result.mechanical_qualification,
        production_qualified=result.production_qualified, factorization_count=result.factorization_count,
        solve_count=result.solve_count), sort_keys=True))


if __name__ == "__main__":
    main()
