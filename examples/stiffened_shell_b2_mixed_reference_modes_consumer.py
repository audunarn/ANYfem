"""Persist and reopen supported or explicit free native S3/Q4/B2 reference modes.

Native S3 facets omit all rotary inertia; shared nodes may receive Q4 director
inertia. These are explicit virgin inputs, not a checkpoint or qualification.
Requires the public mixed reference-modal solver API; no legacy fallback.
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
from anyfem.solve.stiffened_shell_b2 import ExperimentalB2MixedReferenceModalPanel
from anyfem.solve.stiffened_shell_project import point_panel_from_analysis, solve_point_panel


def build_panel(*, all_s3=False, count=2, free_free=False):
    nodes = {1: (0., 0., 0.), 2: (1., 0., 0.), 3: (2., 0., 0.),
             4: (0., 1., 0.), 5: (1., 1., 0.), 6: (2., 1., 0.)}
    def s3(name, ids):
        return api.S3V2DPanelShell(name, ids, 1000., .3, .15,
                                   (0., 0., 1.), density_kg_m3=2.)
    left = ((s3("left", (1, 2, 5)), s3("left-upper", (1, 5, 4))) if all_s3 else
            (api.Q4PanelShell("left", (1, 2, 5, 4), 1000., .3, .15, density_kg_m3=2.),))
    shells = left + (s3("right", (2, 3, 6)), s3("right-upper", (2, 6, 5)))
    beam_density_kg_m3, area_m2, iy_m4, iz_m4 = 7.5, .04, .00015, .0003
    section = api.LinearSection.isotropic(E=2000., nu=.25, area=area_m2,
        Iy=iy_m4, Iz=iz_m4, J=.0004, ky=.8, kz=.7)
    # Uniform section density; the reference line passes through the centroid.
    # Inertia per metre follows (ux, uy, uz, rx, ry, rz) about that origin.
    diagonal = beam_density_kg_m3 * np.array((area_m2, area_m2, area_m2,
                                             iy_m4+iz_m4, iy_m4, iz_m4))
    mass = api.MassMetadata("uniform-rho7.5-centroid-section-inertia", (0., 0., 0.),
                           tuple(map(tuple, np.diag(diagonal))))
    members = tuple(api.PhysicalB2Member(name, name+"-source", (10+i, 11+i),
        ((.2+i, .5, .2), (1.2+i, .5, .2)), (0., 1., 0.), "section", section, mass=mass)
        for i, name in enumerate(("beam-left", "beam-right")))
    points = (api.Q4B2Point("joint-left", "left", "beam-left", .5, (0., .08, -.03)),
              api.Q4B2Point("joint-right", "right", "beam-right", .5, (0., .08, -.03)))
    fixed = set(range(6)) | set(range(18, 24)) | set(range(36, 42))
    free = list(range(54)) if free_free else [i for i in range(54) if i not in fixed]
    options = {"inertia_policy": api.S3_V2D_TRANSLATIONAL_LUMPED_SECTION_AREAL_MASS_V1}
    if free_free:
        options["boundary_policy"] = api.B2_FREE_FREE_MODAL_ONLY_V1
    definition = api.B2MixedReferenceModalDefinition(api.B2_E4_PL_FAST_LINEAR_V1,
        nodes, shells, members, points, sparse.eye(54, format="csr")[:, free], np.zeros(54),
        **options)
    return ExperimentalB2MixedReferenceModalPanel.from_definition(definition,
        api.B2ReferenceModesRequest(count=count, shift_hz=.5))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path, help="new or replaceable .anyfem Project file")
    parser.add_argument("--all-s3", action="store_true")
    parser.add_argument("--free-free", action="store_true", help="explicit modal-only policy with six checked rigid fields")
    parser.add_argument("--count", type=int, default=2)
    args = parser.parse_args()
    panel = build_panel(all_s3=args.all_s3, count=args.count, free_free=args.free_free)
    project = Project("experimental mixed reference modes", mesh_only=True)
    analysis = AnalysisDefinition.experimental_point_panel("modes", panel)
    project.add_analysis(analysis)
    reopened = load_project(save_project(project, args.output)).analyses[analysis.id]
    assert type(point_panel_from_analysis(reopened)) is ExperimentalB2MixedReferenceModalPanel
    result = solve_point_panel(reopened)
    if result.status != "ok":
        raise RuntimeError(f"experimental reference modes {result.status}: {result.failure_reason}")
    flexible = result.flexible if args.free_free else result
    summary = {"status": panel.status, "schema": panel.schema,
        "file": str(args.output.resolve()), "inertia_policy": panel.definition().inertia_policy,
        "s3_rotary_inertia": "omitted by S3 facets; Q4 facets, when present, supply director inertia",
        "frequencies_hz": flexible.frequencies_hz, "factorizations": flexible.factorization_count,
        "solves": flexible.solve_count, "production_qualified": result.production_qualified}
    if args.free_free:
        summary.update(boundary_policy=result.policy, rigid_fields=len(result.rigid_modes),
            rigid_equation_residual=max(result.rigid_equation_residuals),
            rigid_constraint_residual=max(result.rigid_constraint_residuals),
            rigid_flexible_mass_overlap=result.rigid_flexible_mass_overlap)
    print(json.dumps(summary, sort_keys=True))


if __name__ == "__main__":
    main()
