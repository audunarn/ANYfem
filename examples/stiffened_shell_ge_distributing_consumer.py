"""Reference-linear distributing input/Project/checkpoint consumer example.

Physical actions come from the public solver recovery. This development example
does not qualify physical accuracy, finite poses, materials or dynamics.
"""
from dataclasses import replace
from pathlib import Path
import argparse
import json

import numpy as np
from anysolver import b3_ge, stiffened_shell as ss
from anyfem.io.project_file import load_project, save_project
from anyfem.model.project import Project
from anyfem.model.records import AnalysisDefinition
from anyfem.solve.stiffened_shell import ExperimentalGEReferenceDistributingPanel, save_case, load_case
from anyfem.solve.stiffened_shell_project import point_panel_from_analysis


def definition_and_actions():
    e, nu = 210e9, .3
    g = e/(2*(1+nu))
    section = b3_ge.EllipsoidalGeneralizedSection(np.diag((e*.01, g*.008, g*.008,
        g*1.6666666666666667e-5, e*8.333333333333333e-6, e*8.333333333333333e-6)),
        np.eye(6), 1e20, 1e9)
    beam = b3_ge.define_beam("b3-ge", 1, (101, 102, 103),
        np.array(((1., 0., .1), (2., 0., .1), (3., 0., .1))),
        np.tile(np.eye(3), (3, 1, 1)), section, np.eye(6))
    shell = ss.ShellPanel(tuple(range(1, 9)),
        tuple((x, y, 0.) for y in (-.5, .5) for x in (0., 1., 2., 3.)),
        ((1, 2, 6, 5), (2, 3, 7, 6), (3, 4, 8, 7)), .07, e, nu, 7850.)
    pad = ss.GEReferenceDistributingFootprint("two-host-area", (2, 3), 102, (1., 3., -.5, .5))
    definition = ss.LinearDefinition(ss.B3_GE_E4_PL_STIFFENED_SHELL_V1, shell, (beam,), (),
        tuple(ss.FixedCoordinate("shell", node, axis) for node in (1, 5) for axis in range(6)),
        q4_pullback_policy=ss.Q4_ALGORITHMIC_PULLBACK_V1, distributing_footprints=(pad,))
    actions = (ss.NodalAction("shell", 2, (0., 0., -100., 0., 0., 0.)),
               ss.NodalAction("shell", 4, (0., 0., 100., 0., 0., 0.)),
               ss.NodalAction("beam", 103, (0., 20., 0., 0., 0., 3.)))
    return definition, actions


def workflow(directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    definition, actions = definition_and_actions()
    panel = ExperimentalGEReferenceDistributingPanel.from_definition(definition, actions)
    standalone = save_case(panel, directory/"distributing.anyfem-ge.json")
    panel = load_case(standalone)
    project = Project("experimental reference distributing", mesh_only=True)
    analysis = AnalysisDefinition.experimental_point_panel("area-pad", panel)
    project.add_analysis(analysis)
    filename = save_project(project, directory/"distributing.anyfem")
    reopened = load_project(filename)
    analysis = reopened.analyses[analysis.id]
    panel = point_panel_from_analysis(analysis)
    owner = analysis.create_point_panel_analysis()
    resume = None
    try:
        first = owner.solve_linear(panel.actions)
        half = tuple(replace(action, wrench=tuple(.5*v for v in action.wrench)) for action in panel.actions)
        second = owner.solve_linear(half)
        record, = owner.recover(selection=panel.recovery_selection)
        checkpoint = directory/"reference.checkpoint"
        checkpoint.write_bytes(owner.checkpoint())
        resume = analysis.create_point_panel_analysis()
        restored = resume.restore(checkpoint.read_bytes())
        assert resume.checkpoint() == checkpoint.read_bytes()
        resumed = resume.solve_linear(panel.actions)
        observations = dict(status=panel.status, production_qualified=False,
            configuration="reference_linear", law=record.law, footprint_id=record.footprint_id,
            model_identity=record.model_identity, retained_dofs=owner.preflight()["retained_dofs"],
            constraints=owner.preflight()["constraints"],
            cached_factorizations=owner.preflight()["reference_linear_factorizations"],
            generations=[first.generation, second.generation, restored.generation, resumed.generation],
            shell_centroid_m=record.centroid_m.tolist(), beam_origin_m=record.beam_position_m.tolist(),
            shell_exerted_force_N=record.shell_action[:3].tolist(),
            shell_exerted_moment_about_centroid_Nm=record.shell_action[3:].tolist(),
            beam_exerted_force_N=record.beam_action[:3].tolist(),
            beam_exerted_moment_about_beam_origin_Nm=record.beam_action[3:].tolist(),
            gap=record.gap.tolist(), equilibrium=second.free_equilibrium_ratio,
            constraint=second.constraint_max,
            resumed_coordinate_difference=float(np.linalg.norm(np.r_[first.shell_displacement,
                first.beam_coordinates]-np.r_[resumed.shell_displacement, resumed.beam_coordinates])))
        (directory/"observations.json").write_text(json.dumps(observations, indent=2), encoding="utf-8")
        return observations
    finally:
        if resume is not None: resume.close()
        owner.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    print(json.dumps(workflow(parser.parse_args().directory), indent=2))
