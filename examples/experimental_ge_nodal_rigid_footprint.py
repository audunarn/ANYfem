"""Headless experimental footprint input/Project/finite ordinary restart.

Selected-node six-channel pose constraints are not a distributing or qualified
area weld. This example uses elastic response only; chart rebase is unsupported.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
from hashlib import sha256
import json
from pathlib import Path

import numpy as np
from anysolver import b3_ge, stiffened_shell as ss
from anyfem.io.project_file import load_project, save_project
from anyfem.model.project import Project
from anyfem.model.records import AnalysisDefinition
from anyfem.solve import stiffened_shell as consumer
from anyfem.solve.stiffened_shell_project import point_panel_from_analysis


def build_case():
    E = 210e9; nu = .3; G = E/(2.*(1.+nu))
    section = b3_ge.EllipsoidalGeneralizedSection(
        np.diag((E*.01, G*.008, G*.008, G*1.6666666666666667e-5,
                 E*8.333333333333333e-6, E*8.333333333333333e-6)),
        np.eye(6), 1e20, 1e9)
    positions = [(x, -.5, .1) for x in (0., .5, 1., 1.5, 2.)]
    beams = tuple(b3_ge.define_beam(b3_ge.SELECTOR, i+1,
        (101+2*i, 102+2*i, 103+2*i), np.array(positions[2*i:2*i+3]),
        np.tile(np.eye(3), (3, 1, 1)), section, np.eye(6)) for i in range(2))
    shell = ss.ShellPanel((1, 2, 3, 4, 5, 6),
        ((0., -.5, 0.), (1., -.5, 0.), (2., -.5, 0.),
         (0., .5, 0.), (1., .5, 0.), (2., .5, 0.)),
        ((1, 2, 5, 4), (2, 3, 6, 5)), .07, E, nu, 7850.)
    panel = consumer.ExperimentalGEPointPanel(shell, beams, (ss.PointConnection(1, 101),),
        tuple(ss.FixedCoordinate("shell", node, component) for node in (1, 4) for component in range(6)),
        (ss.NodalAction("beam", 105, (0., 0., 10., 0., 0., 0.)),),
        q4_pullback_policy=ss.Q4_ALGORITHMIC_PULLBACK_V1)
    return consumer.ExperimentalGENodalRigidFootprintPanel(panel,
        (ss.NodalRigidFootprint("tip-pad", (2, 3, 6, 5), 105, ((2, 3, 6, 5),)),))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output_dir", type=Path, help="exclusive new directory for input/Project/checkpoint/result")
    args = parser.parse_args(); output = args.output_dir.resolve(); output.mkdir(parents=True, exist_ok=False)
    source = build_case()
    reopened = consumer.load_case(consumer.save_case(source, output/"footprint.anyfem-ge.json"))
    project = Project("explicit experimental nodal footprint", mesh_only=True)
    analysis = AnalysisDefinition.experimental_point_panel("footprint", reopened); project.add_analysis(analysis)
    project = load_project(save_project(project, output/"footprint.anyfem"))
    analysis = project.analyses[analysis.id]; panel = point_panel_from_analysis(analysis)
    linear = analysis.create_point_panel_analysis()
    owner = None; resumed = None
    try:
        linear_result = linear.solve_linear(panel.actions)
        linear_group, = linear.recover(selection=panel.recovery_selection)
        owner = analysis.create_point_panel_analysis()
        prefix = owner.solve(panel.actions, (.25, .5), max_iterations=20, max_backtracks=12)
        raw = owner.checkpoint(); (output/"accepted.checkpoint").write_bytes(raw)
        resumed = analysis.create_point_panel_analysis(); resumed.restore(raw)
        assert resumed.checkpoint() == raw
        direct = owner.solve(panel.actions, (1.,), max_iterations=20, max_backtracks=12)
        continued = resumed.solve(panel.actions, (1.,), max_iterations=20, max_backtracks=12)
        for name, scale in (("beam_positions", 2.), ("beam_frames", 1.), ("cell_frames", 1.),
                            ("cell_resultants", np.array((10.,)*6+(20.,)*12)),
                            ("multipliers", np.tile((10.,)*3+(20.,)*3, 5))):
            np.testing.assert_allclose(getattr(continued, name)/scale, getattr(direct, name)/scale, rtol=0., atol=1e-9)
        assert continued.free_equilibrium_ratio <= 1e-8 and continued.constraint_max <= 1e-9
        selected, = resumed.recover(selection=panel.recovery_selection)
        summary = dict(status=panel.status, production_qualified=False,
            scope="experimental selected-node elastic unrebased point owner; chart continuation remains open",
            model_preflight=resumed.preflight(), input_schema=panel.to_dict()["schema"],
            linear_equilibrium=linear_result.free_equilibrium_ratio,
            linear_selected=asdict(linear_group), finite_selected=asdict(selected),
            accepted_factors=continued.accepted_factors, prefix_generation=prefix.generation,
            checkpoint_sha256=sha256(raw).hexdigest(),
            solver_facade=dict(path=str(Path(ss.__file__).resolve()), sha256=sha256(Path(ss.__file__).read_bytes()).hexdigest()),
            consumer_source=dict(path=str(Path(consumer.__file__).resolve()), sha256=sha256(Path(consumer.__file__).read_bytes()).hexdigest()))
        (output/"result.json").write_text(json.dumps(summary, sort_keys=True, indent=2)+"\n", encoding="utf-8")
        print(json.dumps(dict(status=panel.status, accepted_factors=continued.accepted_factors,
            equilibrium=continued.free_equilibrium_ratio, gap=continued.constraint_max,
            footprint_id=selected.footprint_id, result=str(output/"result.json")), sort_keys=True))
    finally:
        for analysis_owner in (linear, owner, resumed):
            if analysis_owner is not None:analysis_owner.close()


if __name__ == "__main__":main()
