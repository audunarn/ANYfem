"""Persist and run experimental reference-linear B2 body acceleration in Project.

Q4, mixed/all-S3 points and admitted Q4 nodal footprints are demonstrated with
explicit shell density and physical member inertia. This is input persistence,
not a state checkpoint, loaded interior pose, finite gravity or qualification.
"""
import argparse
from dataclasses import replace
import json
from pathlib import Path

import numpy as np
from scipy import sparse
from anysolver import stiffened_shell as api
from anyfem.io.project_file import load_project, save_project
from anyfem.model.project import Project
from anyfem.model.records import AnalysisDefinition
from anyfem.solve.stiffened_shell_b2 import ExperimentalB2BodyAccelerationPanel
from anyfem.solve.stiffened_shell_project import point_panel_from_analysis, solve_point_panel

if __package__:
    from .stiffened_shell_b2_mixed_panel_consumer import build_panel as _mixed_panel
else:
    from stiffened_shell_b2_mixed_panel_consumer import build_panel as _mixed_panel


def build_case(count=10, mesh="q4"):
    if count not in (1, 10, 100) or mesh not in ("q4", "mixed", "s3", "footprint"):
        raise ValueError("example supports q4/mixed/s3/footprint and 1/10/100 cases")
    definition = _mixed_panel(1).to_definition()
    if mesh in ("q4", "footprint"):
        shells = (definition.shells[0], api.Q4PanelShell("right", (2, 3, 6, 5),
                    1000., .3, .15, (0., 0., 1.)))
        definition = api.B2Q4PanelDefinition(definition.profile, definition.shell_nodes_m,
            shells, definition.members, definition.points, definition.transformation,
            definition.prescribed_offset)
    elif mesh == "s3":
        left = definition.shells[0]
        shells = (api.S3V2DPanelShell("left", (1, 2, 5), left.E, left.nu,
                    left.thickness_m, left.reference_normal),
                  api.S3V2DPanelShell("left-upper", (1, 5, 4), left.E, left.nu,
                    left.thickness_m, left.reference_normal)) + definition.shells[1:]
        definition = replace(definition, shells=shells)
    if mesh == "footprint":
        # Reuse the explicit complete right-cell endpoint footprint geometry.
        members = tuple(replace(m,
            reference_points_m=((float(i), .5, .2), (float(i+1), .5, .2)))
            for i, m in enumerate(definition.members))
        fixed = set(range(6)) | set(range(18, 24)) | set(range(36, 42))
        free = [i for i in range(54) if i not in fixed]
        definition = replace(definition, members=members, points=(),
            transformation=sparse.eye(54, format="csr")[:, free],
            footprints=(api.B2NodalRigidFootprint("right-patch", (2, 3, 6, 5),
                        11, ((2, 3, 6, 5),)),))
    mass = api.MassMetadata("explicit-example-physical-inertia", (0., .02, -.015),
        tuple(map(tuple, np.diag((.3, .3, .3, .003, .001, .002)))))
    definition = replace(definition,
        shells=tuple(replace(s, density_kg_m3=2.) for s in definition.shells),
        members=tuple(replace(m, mass=mass) for m in definition.members))
    return ExperimentalB2BodyAccelerationPanel.from_definition(definition,
        tuple((f"body-{i:03d}", (.2*(1.+i/count), -.1, -.3)) for i in range(count)),
        selected_dofs=(49, 50, 53),
        selected_points=() if mesh == "footprint" else ("joint-right",),
        recover_members={"beam-right": (.25, .5, .75)},
        selected_footprints=(api.B2NodalRigidFootprintSelection(("right-patch",))
                             if mesh == "footprint" else None))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path, help="new or replaceable .anyfem Project file")
    parser.add_argument("--cases", type=int, choices=(1, 10, 100), default=10)
    parser.add_argument("--mesh", choices=("q4", "mixed", "s3", "footprint"), default="q4")
    args = parser.parse_args()
    panel = build_case(args.cases, args.mesh)
    project = Project("experimental B2 reference body acceleration", mesh_only=True)
    analysis = AnalysisDefinition.experimental_point_panel("body", panel)
    project.add_analysis(analysis)
    restored = load_project(save_project(project, args.output)).analyses[analysis.id]
    reopened = point_panel_from_analysis(restored)
    assert type(reopened) is ExperimentalB2BodyAccelerationPanel
    result = solve_point_panel(restored)
    if result.status != "completed":
        raise RuntimeError(f"experimental body acceleration {result.status}: {result.failure_reason}")
    first = result.cases[0]
    print(json.dumps({"status": result.status, "experimental_status": reopened.status,
        "schema": reopened.schema, "mesh": args.mesh, "file": str(args.output.resolve()),
        "cases": len(result.cases), "factorization_count": result.factorization_count,
        "solve_count": result.solve_count, "maximum_rhs_chunk": result.maximum_rhs_chunk,
        "typed_case": type(first).__name__, "first_case": first.case_id,
        "acceleration_m_s2": first.body_acceleration_m_s2,
        "selected_displacement_count": len(first.selected_displacements),
        "selected_reaction_count": len(first.selected_affine_reactions),
        "point_multiplier_count": len(first.point_multipliers),
        "footprint_wrench_count": len(getattr(first, "footprint_wrenches", ())),
        "equilibrated_member_count": len(first.equilibrated_sections),
        "equilibrated_stations": first.equilibrated_sections[0].stations,
        "production_qualified": False}, sort_keys=True))


if __name__ == "__main__":
    main()
