"""Real native Project persistence and mixed-material history integration."""
import json
from pathlib import Path
import runpy

from anyfem.io.project_file import load_project
from anyfem.solve.stiffened_shell_project import point_panel_from_analysis


def test_native_project_simultaneous_history_and_fresh_owner_restart(tmp_path):
    example = Path(__file__).resolve().parents[1]/"examples"/"stiffened_shell_coupled_plastic_history_consumer.py"
    workflow = runpy.run_path(str(example))["workflow"]
    summary = workflow(tmp_path)
    retained = json.loads((tmp_path/"summary.json").read_text(encoding="utf-8"))
    assert retained["status"] == summary["status"] == "EXPERIMENTAL_NOT_QUALIFIED"
    assert retained["qualified"] is False
    assert retained["solver_status"] == retained["resumed_status"] == "completed"
    assert retained["prefix_status"] == "completed" and retained["prefix_position"] == 1.
    assert retained["prefix_programme_complete"] is False
    assert retained["final_position"] == retained["resumed_position"] == 4.
    assert retained["final_programme_complete"] is retained["resumed_programme_complete"] is True
    assert retained["restart_identical"] is True
    assert retained["accepted_factors"] == retained["resumed_accepted_factors"]
    assert retained["reverse_flow_fibres"]
    history = retained["history"]
    count = retained["prefix_history_count"]
    assert any(row["simultaneous"] for row in history[:count])
    assert history[count:] == retained["resumed_history"]
    assert all(row["equilibrium"] <= 1e-8 and row["gap"] <= 1e-9
               for row in history+retained["resumed_history"])
    project = load_project(tmp_path/"mixed-history.anyfem")
    record, = project.analyses.values()
    panel = point_panel_from_analysis(record)
    assert panel.actions == ()
    assert panel.load_input.model_identity == retained["model_identity"]
    assert [beam.sha256 for beam in panel.beams] == retained["native_beam_sha256"]
    assert [[action.wrench[2] for action in endpoint]
            for endpoint in panel.load_programme.endpoints] == [
                [force, force] for force in retained["endpoint_forces_N"]]
    assert panel.shell.j2_curve is not None
    assert len(panel.beams) == 4 and len(panel.points) == 6
    assert (tmp_path/"prefix.checkpoint").read_bytes() == (
        tmp_path/"restored-prefix.checkpoint").read_bytes()
    assert (tmp_path/"direct-final.checkpoint").read_bytes() == (
        tmp_path/"resumed-final.checkpoint").read_bytes()
    assert (tmp_path/"prefix.checkpoint").read_bytes() != (
        tmp_path/"direct-final.checkpoint").read_bytes()
    observations = [json.loads(line) for line in
                    (tmp_path/"accepted-observations.jsonl").read_text().splitlines()]
    assert len(observations) == len(history)+len(retained["resumed_history"])
    assert {row["branch"] for row in observations} == {"direct", "resumed"}
    assert all(row["shell_layer_alpha"] and row["fibres"] for row in observations)
