"""Experimental two-stiffener J2/fibre panel; no engineering qualification.

One member uses physical axial/biaxial fibres, the other an elastic generalized
section. Six objective eccentric point bonds retain their six-channel identity.
The deliberately low strengths exercise history; they are not a steel design.
"""
from contextlib import ExitStack
import json
import math
from pathlib import Path
from tempfile import TemporaryDirectory
from dataclasses import replace

from anyfem.io.project_file import load_project, save_project
from anyfem.model.project import Project
from anyfem.model.records import AnalysisDefinition
from anyfem.solve.stiffened_shell import ExperimentalGEPointPanel
from anyfem.solve.stiffened_shell_project import point_panel_from_analysis

import numpy as np
from anymaterial import DNVC208MaterialCurve
from anysolver import b3_ge, stiffened_shell as ss


OPTIONS = dict(max_iterations=20, max_backtracks=12)
CONTROL = ss.AdaptiveIncrementControl(4, 64)
ENDPOINT_FORCES_N = (1500., 375., -1500., 1650.)  # each stiffener


def definition():
    e, nu = 210e9, .3
    g = e/(2.*(1.+nu))
    section = b3_ge.EllipsoidalGeneralizedSection(
        np.diag((e*.01, g*.008, g*.008, g*1.6666666666666667e-5,
                 e*8.333333333333333e-6, e*8.333333333333333e-6)),
        np.eye(6), 1e20, 1e9)
    arm = math.sqrt(8.333333333333333e-6/.01)
    fibres = tuple(b3_ge.Fibre(str(i), y*arm, z*arm, .0025, e,
        b3_ge.FlowCurve((0.,), (1e5 if i == 0 else 1e20,), 1e9))
        for i, (y, z) in enumerate(((-1,-1),(-1,1),(1,-1),(1,1))))
    physical = b3_ge.PhysicalFibreSection(fibres,
        np.diag(np.sqrt((0.,g*.008,g*.008,g*1.6666666666666667e-5,0.,0.))))
    beams = []
    points = []
    for family, y, first_node, first_element, shell_nodes in (
        (physical,-.5,101,1,(1,2,3)), (section,.5,201,3,(4,5,6))):
        xyz = np.array([(x,y,.1) for x in (0.,.5,1.,1.5,2.)])
        for i in range(2):
            beams.append(b3_ge.define_beam('b3-ge',first_element+i,
                tuple(first_node+2*i+j for j in range(3)),xyz[2*i:2*i+3],
                np.tile(np.eye(3),(3,1,1)),family,np.eye(6)))
        points.extend(ss.PointConnection(node,first_node+2*i)
                      for i,node in enumerate(shell_nodes))
    strength = 1e6
    curve = DNVC208MaterialCurve(strength,1.05*strength,1.1*strength,
                                .005,.01,4.*strength,.2)
    shell = ss.ShellPanel((1,2,3,4,5,6),
        ((0.,-.5,0.),(1.,-.5,0.),(2.,-.5,0.),
         (0.,.5,0.),(1.,.5,0.),(2.,.5,0.)),
        ((1,2,5,4),(2,3,6,5)),.07,e,nu,7850.,j2_curve=curve)
    return ss.LinearDefinition(ss.B3_GE_E4_PL_STIFFENED_SHELL_V1,
        shell,tuple(beams),tuple(points),
        tuple(ss.FixedCoordinate('shell',node,axis)
              for node in (1,4) for axis in range(6)),
        q4_pullback_policy=ss.Q4_ALGORITHMIC_PULLBACK_V1)


def programme(owner):
    return ss.StagedDeadLoadProgramme(owner.preflight()['identity'],tuple(
        tuple(ss.NodalAction('beam',node,(0.,0.,force,0.,0.,0.))
              for node in (105,205)) for force in ENDPOINT_FORCES_N))


def activity(result, previous=None):
    """Actual last accepted update, rather than cumulative yielding alone."""
    alpha = np.asarray(result.shell_layer_alpha)
    before = np.zeros_like(alpha) if previous is None else np.asarray(previous.shell_layer_alpha)
    delta = alpha-before
    budget = 64.*np.finfo(float).eps*np.maximum(np.abs(alpha),np.abs(before))
    assert np.all(delta >= -budget)
    fibre_increments = []
    active = []
    for station in result.beam_fibre_material:
        for fibre in station.fibres:
            pair = fibre.plastic_increment
            assert all(math.isfinite(v) for v in pair)
            increment = math.fsum(pair)
            fibre_increments.append(increment)
            if abs(increment) > 64.*np.finfo(float).eps*max(map(abs,pair)):
                active.append((station.element_id,station.cell,station.station,fibre.fibre_id))
    dissipation = [math.fsum(e.incremental_dissipation_j)
                   for e in result.beam_fibre_energy]
    assert all(math.isfinite(d) and d >= 0. for d in dissipation)
    shell_active = bool(np.any(delta > budget))
    fibre_active = bool(active)
    return dict(position=result.load_position,generation=result.generation,
        shell_alpha_max=float(np.max(alpha)),shell_active=shell_active,
        fibre_active=fibre_active,simultaneous=shell_active and fibre_active,
        active_fibres=active,
        fibre_plastic_increment_min=min(fibre_increments),
        fibre_plastic_increment_max=max(fibre_increments),
        last_increment_fibre_dissipation_J=math.fsum(dissipation),
        equilibrium=result.free_equilibrium_ratio,gap=result.constraint_max)


def workflow(directory):
    """Persist virgin inputs, then retain separate accepted-state evidence.

    This is an experimental consumer integration check, not qualification of
    a physical panel. Native Project persistence never replaces a checkpoint.
    """
    output = Path(directory)
    output.mkdir(parents=True, exist_ok=True)
    source = definition()
    panel = ExperimentalGEPointPanel(source.shell, source.beams, source.points,
        source.fixed, q4_pullback_policy=source.q4_pullback_policy)
    # The programme identity comes from the actual compiled public owner.
    binding_owner = panel.create_analysis()
    try:
        panel = replace(panel, load_programme=programme(binding_owner))
    finally:
        binding_owner.close()
    project = Project("experimental simultaneous plastic history", mesh_only=True)
    record = AnalysisDefinition.experimental_point_panel("mixed-history", panel)
    project.add_analysis(record)
    project_path = save_project(project, output/"mixed-history.anyfem")
    reopened = load_project(project_path)
    restored_record = reopened.analyses[record.id]
    restored = point_panel_from_analysis(restored_record)
    assert restored_record.to_dict() == record.to_dict()
    assert restored.to_dict() == panel.to_dict()
    assert all(a.raw == b.raw and a.sha256 == b.sha256
               for a, b in zip(restored.beams, panel.beams, strict=True))
    assert restored.load_input.serialize() == panel.load_input.serialize()
    assert restored.actions == ()
    assert restored.status == "EXPERIMENTAL_NOT_QUALIFIED"
    assert tuple(tuple(action.wrench[2] for action in endpoint)
                 for endpoint in restored.load_programme.endpoints) == tuple(
                     (force, force) for force in ENDPOINT_FORCES_N)

    with ExitStack() as resources:
        owner = restored_record.create_point_panel_analysis()
        resources.callback(owner.close)
        history, results, resumed_history = [], [], []
        signed_flow = {}
        observations = resources.enter_context(
            (output/"accepted-observations.jsonl").open("w", encoding="utf-8"))

        def observe(result, previous, branch):
            # Preserve actual returned quantities before activity assertions.
            raw = dict(branch=branch, position=result.load_position,
                generation=result.generation,
                shell_layer_alpha=np.asarray(result.shell_layer_alpha).tolist(),
                fibres=[dict(element_id=station.element_id, cell=station.cell,
                    station=station.station, fibre_id=fibre.fibre_id,
                    plastic_increment=list(fibre.plastic_increment))
                    for station in result.beam_fibre_material for fibre in station.fibres],
                fibre_dissipation=[list(e.incremental_dissipation_j)
                                   for e in result.beam_fibre_energy],
                equilibrium=result.free_equilibrium_ratio, gap=result.constraint_max)
            observations.write(json.dumps(raw, allow_nan=False)+"\n")
            observations.flush()
            if branch == "direct":
                for fibre in raw["fibres"]:
                    key = tuple(fibre[name] for name in
                                ("element_id", "cell", "station", "fibre_id"))
                    pair = fibre["plastic_increment"]
                    increment = math.fsum(pair)
                    if abs(increment) > 64.*np.finfo(float).eps*max(map(abs, pair)):
                        signed_flow.setdefault(key, set()).add(1 if increment > 0. else -1)
            return activity(result, previous)

        def accepted(result):
            previous = results[-1] if results else None
            results.append(result)
            history.append(observe(result, previous, "direct"))

        loads = restored.load_input
        prefix = owner.solve(loads, (.5, 1.), increment_control=CONTROL,
                             on_accepted=accepted, **OPTIONS)
        assert prefix.status == 'completed' and prefix.load_position == 1.
        assert not prefix.load_programme_complete
        saved = owner.checkpoint()
        (output/"prefix.checkpoint").write_bytes(saved)
        prefix_history_count = len(history)
        resumed = restored_record.create_point_panel_analysis()
        resources.callback(resumed.close)
        resumed.restore(saved)
        restored_checkpoint = resumed.checkpoint()
        (output/"restored-prefix.checkpoint").write_bytes(restored_checkpoint)
        assert restored_checkpoint == saved
        resumed_results = [results[-1]]

        def resumed_accepted(result):
            previous = resumed_results[-1]
            resumed_results.append(result)
            resumed_history.append(observe(result, previous, "resumed"))

        result = owner.solve(loads, (2., 3., 4.), increment_control=CONTROL,
                             on_accepted=accepted, **OPTIONS)
        final = owner.checkpoint()
        (output/"direct-final.checkpoint").write_bytes(final)
        repeated = resumed.solve(loads, (2., 3., 4.), increment_control=CONTROL,
                                on_accepted=resumed_accepted, **OPTIONS)
        repeated_final = resumed.checkpoint()
        (output/"resumed-final.checkpoint").write_bytes(repeated_final)
        summary = dict(status=restored.status, solver_status=result.status,
            resumed_status=repeated.status, qualified=False,
            input_persistence="native Project; virgin inputs only",
            state_persistence="separate solver checkpoints",
            member_families=owner.capabilities['beam_section_families'],
            stiffeners=2, beam_elements=4, shell_elements=2,
            native_beam_sha256=[beam.sha256 for beam in restored.beams],
            endpoint_forces_N=ENDPOINT_FORCES_N,
            model_identity=loads.model_identity,
            prefix_status=prefix.status,
            prefix_position=prefix.load_position,
            prefix_programme_complete=prefix.load_programme_complete,
            prefix_accepted_factors=prefix.accepted_factors,
            final_position=result.load_position,
            final_programme_complete=result.load_programme_complete,
            resumed_position=repeated.load_position,
            resumed_programme_complete=repeated.load_programme_complete,
            accepted_factors=result.accepted_factors,
            resumed_accepted_factors=repeated.accepted_factors,
            prefix_history_count=prefix_history_count,
            history=history, resumed_history=resumed_history,
            reverse_flow_fibres=[key for key, signs in signed_flow.items()
                                 if signs == {-1, 1}],
            restart_identical=final == repeated_final)
        (output/"summary.json").write_text(
            json.dumps(summary, indent=2, allow_nan=False)+"\n", encoding="utf-8")
        assert result.status == repeated.status == 'completed'
        assert result.load_position == repeated.load_position == 4.
        assert result.load_programme_complete and repeated.load_programme_complete
        assert result.accepted_factors == repeated.accepted_factors
        assert final == repeated_final
        assert history[prefix_history_count:] == resumed_history
        assert any(row['simultaneous'] for row in history[:prefix_history_count])
        assert summary['reverse_flow_fibres']
        assert any(row['fibre_plastic_increment_min'] < 0. for row in history)
        assert any(row['fibre_plastic_increment_max'] > 0. for row in history)
        assert max(row['equilibrium'] for row in history+resumed_history) <= 1e-8
        assert max(row['gap'] for row in history+resumed_history) <= 1e-9
        return summary


def main():
    with TemporaryDirectory(prefix="anyfem-mixed-history-") as directory:
        print(json.dumps(workflow(directory), allow_nan=False))


if __name__ == '__main__':
    main()
