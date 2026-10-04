"""Experimental coupled load/unload/reversal with project and zero-load restart.

Run with both experimental package wheels installed. This centered axial
history exercises state ownership; it does not qualify the connection law.
"""
from dataclasses import replace
from pathlib import Path
import argparse
import json
import numpy as np
from anysolver import stiffened_shell as ss
from anyfem.io.project_file import save_project, load_project
from anyfem.model.project import Project
from anyfem.model.records import AnalysisDefinition
from anyfem.solve.stiffened_shell import (
    ExperimentalGEFiniteDistributingMaterialPanel, save_case, load_case)
from anyfem.solve.stiffened_shell_project import point_panel_from_analysis
from stiffened_shell_ge_distributing_material_consumer import definition_and_actions

HISTORY = (.25, .5, .75, 1., .75, .5, .25, 0., -.25, -.5, -.75, -1., -.5, 0., .5, 1.)


def workflow(directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    model, actions = definition_and_actions(coupled=True)
    model = replace(model,
        finite_distributing_material_policy=ss.GE_FINITE_DISTRIBUTING_COUPLED_J2_CYCLIC_MATERIAL_V1,
        cyclic_reference_actions=actions)
    panel = ExperimentalGEFiniteDistributingMaterialPanel.from_definition(model, actions)
    panel = load_case(save_case(panel, directory / 'cyclic.anyfem-ge.json'))
    project = Project('experimental cyclic material', mesh_only=True)
    analysis = AnalysisDefinition.experimental_point_panel('cyclic pad', panel)
    project.add_analysis(analysis)
    analysis = load_project(save_project(project, directory / 'cyclic.anyfem')).analyses[analysis.id]
    panel = point_panel_from_analysis(analysis)
    owner = analysis.create_point_panel_analysis()
    resumed = None
    try:
        prefix = owner.solve(panel.load_input, HISTORY[:8], max_iterations=24, max_backtracks=16)
        assert prefix.status == 'completed' and prefix.accepted_factors[-1] == 0.
        assert np.max(prefix.shell_layer_alpha) > 0. and np.max(prefix.beam_station_accumulated) > 0.
        raw = owner.checkpoint()
        (directory / 'zero-load.checkpoint').write_bytes(raw)
        resumed = analysis.create_point_panel_analysis()
        resumed.restore(raw)
        assert resumed.checkpoint() == raw
        expected = owner.solve(panel.load_input, HISTORY[8:], max_iterations=24, max_backtracks=16)
        actual = resumed.solve(panel.load_input, HISTORY[8:], max_iterations=24, max_backtracks=16)
        assert actual.status == expected.status == 'completed'
        assert actual.accepted_factors == HISTORY
        assert resumed.checkpoint() == owner.checkpoint()
        np.testing.assert_array_equal(actual.shell_displacement, expected.shell_displacement)
        record, = resumed.recover(selection=panel.recovery_selection)
        result = dict(status=panel.status, material_policy=panel.finite_distributing_material_policy,
            accepted_factors=actual.accepted_factors, equilibrium=actual.free_equilibrium_ratio,
            gap=actual.constraint_max, shell_alpha=float(np.max(actual.shell_layer_alpha)),
            beam_accumulated=float(np.max(actual.beam_station_accumulated)),
            zero_checkpoint_bytes=len(raw), law=record.law)
        (directory / 'observations.json').write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
        return result
    finally:
        if resumed is not None:
            resumed.close()
        owner.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    print(json.dumps(workflow(parser.parse_args().directory), indent=2))
