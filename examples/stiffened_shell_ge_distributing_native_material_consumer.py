"""Experimental native-fibre distributing input and zero-load restart.

This centered axial history checks public input ownership and restart. It does
not qualify the distributing connection or establish reverse plastic flow.
"""

from dataclasses import replace
from hashlib import sha256
from math import fsum
from pathlib import Path
import argparse
import json

import numpy as np
from anysolver import b3_ge, stiffened_shell as ss

from anyfem.io.project_file import load_project, save_project
from anyfem.model.project import Project
from anyfem.model.records import AnalysisDefinition
from anyfem.solve.stiffened_shell import (
    ExperimentalGEFiniteDistributingMaterialPanel, load_case, save_case,
)
from anyfem.solve.stiffened_shell_project import point_panel_from_analysis
from stiffened_shell_ge_distributing_material_consumer import definition_and_actions


HISTORY = (.25, .5, .75, 1., 0., -.25)


def native_definition_and_actions():
    model, actions = definition_and_actions(coupled=True)
    element, inertia = model.beams[0].instantiate()
    reference = element.operator.reference
    young = model.shell.young_pa
    shear = young / (2. * (1. + model.shell.poisson))
    arm = np.sqrt(8.333333333333333e-6 / .01)
    section = b3_ge.PhysicalFibreSection(
        tuple(b3_ge.Fibre(str(i), y*arm, z*arm, .0025, young,
                          b3_ge.FlowCurve.linear(200e6, 200e9))
              for i, (y, z) in enumerate(((-1., -1.), (-1., 1.),
                                           (1., -1.), (1., 1.)))),
        np.diag(np.sqrt((0., shear*.008, shear*.008,
                          shear*1.6666666666666667e-5, 0., 0.))))
    beam = b3_ge.define_beam(
        b3_ge.SELECTOR, element.element_id, element.node_ids,
        reference.coordinates, reference.nodal_triads, section, inertia,
        quadrature=element.operator.order,
        arithmetic_policy=element.operator.arithmetic_policy)
    return replace(model, beams=(beam,),
        finite_distributing_material_policy=
            ss.GE_FINITE_DISTRIBUTING_NATIVE_COUPLED_J2_CYCLIC_MATERIAL_V1,
        cyclic_reference_actions=actions), actions


def workflow(directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    model, actions = native_definition_and_actions()
    case = ExperimentalGEFiniteDistributingMaterialPanel.from_definition(model, actions)
    case_path = save_case(case, directory / "native-material.anyfem-ge.json")
    reopened = load_case(case_path)
    assert reopened.to_dict() == case.to_dict()
    assert tuple(beam.raw for beam in reopened.beams) == tuple(beam.raw for beam in model.beams)

    project = Project("experimental native distributing material", mesh_only=True)
    analysis = AnalysisDefinition.experimental_point_panel("native fibre pad", reopened)
    project.add_analysis(analysis)
    project_path = save_project(project, directory / "native-material.anyfem")
    analysis = load_project(project_path).analyses[analysis.id]
    panel = point_panel_from_analysis(analysis)
    assert panel.to_dict() == reopened.to_dict()

    owner = analysis.create_point_panel_analysis()
    resumed = None
    cumulative_flow = {}

    def observe(result):
        for station in result.beam_fibre_material:
            for fibre in station.fibres:
                key = (station.element_id, station.cell, station.station, fibre.fibre_id)
                cumulative_flow[key] = cumulative_flow.get(key, 0.) + abs(
                    fsum(fibre.plastic_increment))

    try:
        loading = owner.solve(panel.load_input, HISTORY[:4],
                              max_iterations=24, max_backtracks=16,
                              on_accepted=observe)
        initial_fibre_flow = max(cumulative_flow.values(), default=0.)
        initial_shell_alpha = float(np.max(loading.shell_layer_alpha))
        assert loading.status == "completed"
        assert initial_fibre_flow > 0. and initial_shell_alpha > 0.

        zero = owner.solve(panel.load_input, (HISTORY[4],),
                           max_iterations=24, max_backtracks=16,
                           on_accepted=observe)
        assert zero.status == "completed" and zero.load_factor == 0.
        raw = owner.checkpoint()
        checkpoint_path = directory / "native-zero-load.checkpoint"
        checkpoint_path.write_bytes(raw)
        resumed = analysis.create_point_panel_analysis()
        restored = resumed.restore(raw)
        assert restored.load_factor == 0. and resumed.checkpoint() == raw

        expected = owner.solve(panel.load_input, (HISTORY[5],),
                               max_iterations=24, max_backtracks=16)
        actual = resumed.solve(panel.load_input, (HISTORY[5],),
                               max_iterations=24, max_backtracks=16)
        assert expected.status == actual.status == "completed"
        assert actual.accepted_factors == HISTORY
        assert resumed.checkpoint() == owner.checkpoint()
        for name in ("shell_displacement", "beam_positions", "support_reactions",
                     "shell_layer_alpha"):
            np.testing.assert_array_equal(getattr(actual, name), getattr(expected, name))
        pad, = resumed.recover(selection=panel.recovery_selection)
        assert pad.configuration == "current" and not pad.production_qualified

        result = dict(
            status=panel.status,
            material_policy=panel.finite_distributing_material_policy,
            accepted_factors=actual.accepted_factors,
            equilibrium=actual.free_equilibrium_ratio,
            gap=actual.constraint_max,
            initial_shell_alpha=initial_shell_alpha,
            fibre_plastic_accumulated_max_after_loading=initial_fibre_flow,
            fibre_plastic_accumulated_max_at_zero=max(cumulative_flow.values(), default=0.),
            pad_law=pad.law,
            pad_model_identity=pad.model_identity,
            case_file=str(case_path), case_sha256=sha256(case_path.read_bytes()).hexdigest(),
            project_file=str(project_path), project_sha256=sha256(project_path.read_bytes()).hexdigest(),
            checkpoint_file=str(checkpoint_path), checkpoint_bytes=len(raw),
            production_qualified=False)
        (directory / "native-material-observations.json").write_text(
            json.dumps(result, indent=2) + "\n", encoding="utf-8")
        return result
    finally:
        if resumed is not None:
            resumed.close()
        owner.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    print(json.dumps(workflow(parser.parse_args().directory), indent=2))
