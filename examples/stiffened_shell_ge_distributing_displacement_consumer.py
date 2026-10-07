"""Experimental public Project displacement input, checkpoint and physical recovery.

Absolute physical targets are metres. The amplitude is an affine increment from
the accepted force seed, not a force factor. This example establishes no yield
reversal, scientific qualification or release claim.
"""
import argparse
import json
from pathlib import Path

import numpy as np
from anysolver import stiffened_shell as ss
from anyfem.io.project_file import load_project, save_project
from anyfem.model.project import Project
from anyfem.model.records import AnalysisDefinition
from anyfem.solve.stiffened_shell import (
    ExperimentalGEFiniteDistributingPanel, load_case, save_case,
)
from anyfem.solve.stiffened_shell_project import point_panel_from_analysis, solve_point_panel
from stiffened_shell_ge_finite_distributing_consumer import definition_and_actions


def physical_translation(model, result, control):
    """Read a translation from public physical output and owned reference input."""
    if control.domain == "shell":
        displacement = np.asarray(result.shell_displacement)
        if displacement.shape != (6 * len(model.shell.node_ids),):
            raise ValueError("public flat six-coordinate shell displacement required")
        index = sorted(model.shell.node_ids).index(control.node)
        return float(displacement[6 * index + control.component])
    reference = {}
    for beam in model.beams:
        record = json.loads(beam.raw)
        reference.update(zip(record["node_ids"], record["reference"]["coordinates"]))
    index = sorted(reference).index(control.node)
    return float(result.beam_positions[index, control.component] -
                 reference[control.node][control.component])


def workflow(directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    model, actions = definition_and_actions()
    control = ss.DisplacementControl("beam", 103, 2)
    panel = ExperimentalGEFiniteDistributingPanel.from_definition(
        model, actions, displacement_control=control,
        displacement_seed_factors=(.25,))
    case = save_case(panel, directory / "displacement.anyfem-ge.json")
    reopened = load_case(case)
    assert reopened.to_dict() == panel.to_dict()
    project = Project("experimental distributing displacement", mesh_only=True)
    analysis = AnalysisDefinition.experimental_point_panel("physical pad translation", reopened)
    project.add_analysis(analysis)
    project_path = save_project(project, directory / "displacement.anyfem")
    analysis = load_project(project_path).analyses[analysis.id]
    panel = point_panel_from_analysis(analysis)
    assert panel.to_dict() == reopened.to_dict()

    # Factories return fresh owners; restoration never implicitly seeds them.
    owner = analysis.create_point_panel_analysis()
    resumed = None
    options = dict(displacement_control=panel.displacement_control,
                   max_iterations=20, max_backtracks=12)
    try:
        seed = owner.solve(panel.actions, panel.displacement_seed_factors)
        initial = physical_translation(panel.definition(), seed, control)
        first = owner.solve(panel.actions, (initial * 1.01,), **options)
        combined = solve_point_panel(analysis, targets=(initial * 1.01,),
                                    max_iterations=20, max_backtracks=12)
        np.testing.assert_allclose(combined.cell_resultants, first.cell_resultants,
                                   rtol=1e-10, atol=1e-12)
        assert combined.target == first.target
        checkpoint = owner.checkpoint()
        checkpoint_path = directory / "accepted-displacement.bin"
        checkpoint_path.write_bytes(checkpoint)
        resumed = analysis.create_point_panel_analysis()
        restored = resumed.restore(checkpoint_path.read_bytes())
        assert resumed.checkpoint() == checkpoint
        assert restored.target == first.target
        expected = owner.solve(panel.actions, (initial * 1.02,), **options)
        actual = resumed.solve(panel.actions, (initial * 1.02,), **options)
        for name in ("cell_resultants", "shell_displacement", "beam_positions",
                     "beam_frames", "support_reactions", "applied_dead_load"):
            np.testing.assert_allclose(getattr(actual, name), getattr(expected, name),
                                       rtol=1e-10, atol=1e-12)
        selected, = resumed.recover(selection=panel.recovery_selection)
        evaluated, = actual.distributing_footprint_wrenches
        assert selected.generation == evaluated.generation == actual.generation
        np.testing.assert_array_equal(selected.shell_action, evaluated.shell_action)
        np.testing.assert_array_equal(selected.beam_action, evaluated.beam_action)
        assert actual.production_qualified is False
        assert actual.free_equilibrium_ratio <= 1e-8
        assert actual.constraint_max <= 1e-9
        result = dict(status=panel.status, schema=panel.to_dict()["schema"],
                      target_m=actual.target, affine_increment_amplitude=actual.amplitude,
                      generation=actual.generation, checkpoint_bytes=len(checkpoint),
                      equilibrium=actual.free_equilibrium_ratio, gap=actual.constraint_max,
                      law=selected.law, shell_action=selected.shell_action.tolist(),
                      beam_action=selected.beam_action.tolist())
        (directory / "observations.json").write_text(
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
