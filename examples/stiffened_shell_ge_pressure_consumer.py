"""Experimental native Project current-surface pressure and restart workflow.

The two-Q4/two-GE panel shares geometry with the gravity example. Pressure
acts on the shell interpolation surface and has no endpoint potential.
This example does not qualify mixed-mesh pressure or finite S3 reference-dead work.
"""
from contextlib import ExitStack
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import json
from anysolver import stiffened_shell as ss
from anysolver.control import CancellationToken, SolveCancelled
from anyfem.io.project_file import save_project, load_project
from anyfem.model.project import Project
from anyfem.model.records import AnalysisDefinition
from anyfem.solve.stiffened_shell import ExperimentalGEPointPanel
from anyfem.solve.stiffened_shell_project import point_panel_from_analysis, solve_point_panel
from stiffened_shell_ge_gravity_consumer import definition


def workflow(directory):
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=True)
    source=definition()
    panel=ExperimentalGEPointPanel(source.shell,source.beams,source.points,source.fixed,
                                  q4_pullback_policy=source.q4_pullback_policy)
    binding=panel.create_analysis()
    try:
        request=ss.GEShellPressureLoad(binding.preflight()['identity'],tuple(
            ss.GEShellPressureAction(i,100.,"current_surface") for i in (1,2)))
    finally:binding.close()
    panel=replace(panel,pressure_load=request)
    project=Project('experimental native pressure loads',mesh_only=True)
    record=AnalysisDefinition.experimental_point_panel('shell pressure',panel)
    project.add_analysis(record)
    path=save_project(project,directory/'pressure.anyfem')
    reopened=load_project(path);record2=reopened.analyses[record.id]
    restored=point_panel_from_analysis(record2)
    assert record.to_dict()==record2.to_dict() and panel.to_dict()==restored.to_dict()
    assert all(a.raw==b.raw for a,b in zip(panel.beams,restored.beams,strict=True))
    linear=solve_point_panel(record2)
    assert linear.status=='completed' and linear.pressure_load.identity==request.identity
    observations=[]
    def observe(result,branch):
        assert result.pressure_load.potential is None
        observations.append(dict(branch=branch,factor=result.load_factor,
            equilibrium=result.free_equilibrium_ratio,compatibility=result.constraint_max,
            force=result.pressure_load.applied_force,moment=result.pressure_load.applied_moment_about_origin))
    with ExitStack() as stack:
        original=record2.create_point_panel_analysis();stack.callback(original.close)
        resume=record2.create_point_panel_analysis();stack.callback(resume.close)
        targets=(.25,.5,1.);control=ss.AdaptiveIncrementControl(2,16);token=CancellationToken()
        def cancel(result):observe(result,'prefix');token.cancel('complete pressure prefix')
        try:original.solve(restored.load_input,targets,increment_control=control,on_accepted=cancel,cancellation_token=token)
        except SolveCancelled:pass
        else:raise AssertionError('expected accepted-prefix cancellation')
        prefix=original.recover();assert prefix.accepted_factors==(.25,)
        checkpoint=directory/'prefix.checkpoint';checkpoint.write_bytes(original.checkpoint())
        resume.restore(checkpoint.read_bytes())
        (directory/'restored-prefix.checkpoint').write_bytes(resume.checkpoint())
        assert resume.checkpoint()==checkpoint.read_bytes()
        final=original.solve(restored.load_input,targets,increment_control=control,on_accepted=lambda r:observe(r,'direct'))
        continued=resume.solve(restored.load_input,targets,increment_control=control,on_accepted=lambda r:observe(r,'resumed'))
        assert final.status==continued.status=='completed'
        assert original.checkpoint()==resume.checkpoint()!=checkpoint.read_bytes()
        (directory/'direct-final.checkpoint').write_bytes(original.checkpoint())
        (directory/'resumed-final.checkpoint').write_bytes(resume.checkpoint())
        summary=dict(status=panel.status,production_qualified=False,linear_status=linear.status,
            prefix_status=prefix.status,final_status=final.status,resumed_status=continued.status,
            accepted_factors=final.accepted_factors,load_identity=request.identity,
            model_identity=request.model_identity,native_beam_sha256=[b.sha256 for b in panel.beams],
            applied_force_N=final.pressure_load.applied_force,
            applied_moment_Nm=final.pressure_load.applied_moment_about_origin,
            total_potential=final.pressure_load.potential,restart_identical=True)
    (directory/'observations.json').write_text(json.dumps(observations,indent=2),encoding='utf-8')
    (directory/'summary.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
    return summary


if __name__=='__main__':
    with TemporaryDirectory(prefix='anyfem-pressure-') as directory:
        print(json.dumps(workflow(directory),indent=2))
