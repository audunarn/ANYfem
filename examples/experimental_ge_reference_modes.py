"""Experimental GE reference modes through an ordinary Project save/reopen.

Default: explicit free-free modal-only panel; --supported selects supported
virgin reference modes. This is a headless demonstration, not qualification.
"""
import argparse
from dataclasses import asdict
import json
from pathlib import Path
import tempfile

import numpy as np
from anysolver import b3_ge, stiffened_shell as api
from anyfem.io.project_file import load_project, save_project
from anyfem.model.project import Project
from anyfem.model.records import AnalysisDefinition
from anyfem.solve.stiffened_shell import ExperimentalGEReferenceModalPanel
from anyfem.solve.stiffened_shell_project import solve_point_panel


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--supported", action="store_true",
                        help="use supported virgin modes instead of free-free modal-only")
    args = parser.parse_args()
    e, nu, rho, width, depth = 210e9, .3, 7850., .1, .1
    area = width*depth
    iy, iz = width*depth**3/12., depth*width**3/12.
    g = e/(2*(1+nu))
    section = b3_ge.EllipsoidalGeneralizedSection(
        np.diag((e*area, .8*g*area, .8*g*area, g*(iy+iz), e*iy, e*iz)),
        np.eye(6), 1e20, 1e9)
    # Explicit rectangular mass per metre about the native section axis, with
    # centroid offset. Mass is prescribed independently of section stiffness.
    center = np.array((0., .03, -.02))
    m = rho*area
    skew = np.array(((0., -center[2], center[1]), (center[2], 0., -center[0]),
                     (-center[1], center[0], 0.)))
    inertia = np.block([[m*np.eye(3), -m*skew],
                        [m*skew, rho*np.diag((iy+iz, iy, iz))-m*skew@skew]])
    beam = b3_ge.define_beam("b3-ge", 17, (101, 102, 103),
        np.array(((0., 0., .1), (.5, 0., .1), (1., 0., .1))),
        np.tile(np.eye(3), (3, 1, 1)), section, inertia)
    shell = api.ShellPanel((1, 2, 3, 4),
        ((0., 0., 0.), (1., 0., 0.), (1., 1., 0.), (0., 1., 0.)),
        ((1, 2, 3, 4),), .02, e, nu, rho)
    policy = "supported" if args.supported else api.GE_FREE_FREE_MODAL_ONLY_V1
    fixed = (tuple(api.FixedCoordinate("shell", node, component)
                   for node in (1, 4) for component in range(6))
             if args.supported else ())
    definition = api.LinearDefinition(api.B3_GE_E4_PL_STIFFENED_SHELL_V1,
        shell, (beam,), (api.PointConnection(1, 101), api.PointConnection(2, 103)),
        fixed, modal_boundary_policy=policy)
    request = api.GEReferenceModesRequest(count=2, shift_hz=1.)
    panel = ExperimentalGEReferenceModalPanel.from_definition(definition, request)
    project = Project("experimental GE reference modes", mesh_only=True)
    record = AnalysisDefinition.experimental_point_panel("reference modes", panel)
    project.add_analysis(record)
    with tempfile.TemporaryDirectory(prefix="anyfem-ge-reference-modes-") as directory:
        path = save_project(project, Path(directory)/"reference-modes.anyfem")
        reopened = load_project(path)
        result = solve_point_panel(reopened.analyses[record.id])
    free = type(result) is api.GEFreeReferenceModesResult
    flexible = result.flexible if free else result
    print(json.dumps(dict(status=result.status,
        factorization_count=flexible.factorization_count,
        production_qualified=False,
        rigid_mode_count=len(result.rigid_coordinates) if free else 0,
        frequencies_hz=flexible.frequencies_hz,
        modal_boundary_policy=policy, numerical_policy=result.policy,
        request=asdict(request), model_identity=result.model_identity,
        selection=result.selection, failure_reason=result.failure_reason), indent=2))
    if result.status != "completed":
        raise RuntimeError(result.failure_reason)


if __name__ == "__main__":
    main()
