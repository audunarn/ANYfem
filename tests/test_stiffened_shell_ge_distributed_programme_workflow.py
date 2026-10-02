"""Real public owner, persisted Project and signed distributed checkpoint continuation."""
from contextlib import ExitStack
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest
from anysolver import stiffened_shell as ss
from anysolver.control import CancellationToken, SolveCancelled
from anyfem.io.project_file import load_project, save_project
from anyfem.model.project import Project
from anyfem.model.records import AnalysisDefinition
from anyfem.solve.stiffened_shell import ExperimentalGEPointPanel
from anyfem.solve.stiffened_shell_project import point_panel_from_analysis


def test_project_signed_programme_cancel_disk_restart_continue(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1]/'examples'))
    from stiffened_shell_ge_gravity_consumer import definition
    source = definition()
    panel = ExperimentalGEPointPanel(source.shell, source.beams, source.points, source.fixed,
                                    q4_pullback_policy=source.q4_pullback_policy)
    with ExitStack() as stack:
        binding = panel.create_analysis(); stack.callback(binding.close)
        programme = ss.GEBeamDistributedProgramme(binding.preflight()['identity'], tuple(
            tuple(ss.GEBeamLineAction(i, (0., 0., density)) for i in (1, 2))
            for density in (100., -100.)))
        panel = replace(panel, distributed_programme=programme)
        project = Project('signed distributed inputs', mesh_only=True)
        record = AnalysisDefinition.experimental_point_panel('stages', panel)
        project.add_analysis(record)
        reopened = load_project(save_project(project, tmp_path/'programme.anyfem'))
        restored_record = reopened.analyses[record.id]
        restored_panel = point_panel_from_analysis(restored_record)
        assert restored_panel.to_dict() == panel.to_dict()
        assert restored_panel.load_input.serialize() == programme.serialize()
        original = restored_record.create_point_panel_analysis(); stack.callback(original.close)
        resumed = restored_record.create_point_panel_analysis(); stack.callback(resumed.close)
        token = CancellationToken(); control = ss.AdaptiveIncrementControl(2, 16)
        targets = (1., 2.)  # No equilibrium claim at the zero crossing, position 1.5.
        with pytest.raises(SolveCancelled):
            original.solve(restored_panel.load_input, targets, increment_control=control,
                max_iterations=15, cancellation_token=token,
                on_accepted=lambda r: token.cancel('complete positive prefix'))
        prefix = original.recover()
        assert prefix.load_position == 1. and prefix.accepted_factors == (1.,)
        saved = tmp_path/'prefix.checkpoint'; saved.write_bytes(original.checkpoint())
        restored = resumed.restore(saved.read_bytes())
        assert restored.load_position == 1. and resumed.checkpoint() == saved.read_bytes()
        final = original.solve(restored_panel.load_input, targets, increment_control=control, max_iterations=15)
        other = resumed.solve(restored_panel.load_input, targets, increment_control=control, max_iterations=15)
        assert final.status == other.status == 'completed'
        assert final.load_programme_complete and final.accepted_factors == (1., 2.)
        assert final.load_programme_identity == programme.identity
        np.testing.assert_allclose(final.distributed_load.applied_force, (0., 0., -200.), atol=1e-10)
        np.testing.assert_array_equal(final.beam_positions, other.beam_positions)
        assert original.checkpoint() == resumed.checkpoint() != saved.read_bytes()
        assert max(final.free_equilibrium_ratio, other.free_equilibrium_ratio) <= 1e-8
        assert max(final.constraint_max, other.constraint_max) <= 1e-9
        assert restored_panel.status == 'EXPERIMENTAL_NOT_QUALIFIED'
