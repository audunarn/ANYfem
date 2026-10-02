"""Native Project gravity inputs and accepted-state checkpoint continuation.

Experimental consumer; model matches the solver's eccentric-mass example.
Project input persistence and accepted checkpoint persistence are distinct.
"""
from contextlib import ExitStack
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import numpy as np
from anysolver import b3_ge, stiffened_shell as ss
from anysolver.control import CancellationToken, SolveCancelled
from anyfem.io.project_file import save_project, load_project
from anyfem.model.project import Project
from anyfem.model.records import AnalysisDefinition
from anyfem.solve.stiffened_shell import ExperimentalGEPointPanel
from anyfem.solve.stiffened_shell_project import point_panel_from_analysis, solve_point_panel

# Solver fixture SHA256: f61f2c86104720f1046553397cad94fba52c0199e701ace9f72dee886c5d9ccc
def definition():
    e, nu, rho = 210e9, .3, 7850.
    g=e/(2*(1+nu));area=.01;iy=iz=8.333333333333333e-6
    section=b3_ge.EllipsoidalGeneralizedSection(
        np.diag((e*area,g*.008,g*.008,g*(iy+iz),e*iy,e*iz)),np.eye(6),1e20,1e9)
    mass=rho*area;offset=np.array([0.,.015,.02])
    skew=np.array([[0.,-offset[2],offset[1]],[offset[2],0.,-offset[0]],[-offset[1],offset[0],0.]])
    inertia=np.block([[mass*np.eye(3),-mass*skew],
                      [mass*skew,rho*np.diag((iy+iz,iy,iz))-mass*skew@skew]])
    coordinates=np.array([(x,-.5,.1) for x in (0.,.5,1.,1.5,2.)])
    beams=tuple(b3_ge.define_beam('b3-ge',i+1,tuple(101+2*i+j for j in range(3)),
        coordinates[2*i:2*i+3],np.tile(np.eye(3),(3,1,1)),section,inertia) for i in range(2))
    shell=ss.ShellPanel((1,2,3,4,5,6),
        ((0.,-.5,0.),(1.,-.5,0.),(2.,-.5,0.),(0.,.5,0.),(1.,.5,0.),(2.,.5,0.)),
        ((1,2,5,4),(2,3,6,5)),.07,e,nu,rho)
    return ss.LinearDefinition(ss.B3_GE_E4_PL_STIFFENED_SHELL_V1,shell,beams,
        tuple(ss.PointConnection(i+1,101+2*i) for i in range(3)),
        tuple(ss.FixedCoordinate('shell',node,axis) for node in (1,4) for axis in range(6)),
        q4_pullback_policy=ss.Q4_ALGORITHMIC_PULLBACK_V1)



def workflow(directory):
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=True)
    source=definition()
    panel=ExperimentalGEPointPanel(source.shell,source.beams,source.points,source.fixed,
                                  q4_pullback_policy=source.q4_pullback_policy)
    binding=panel.create_analysis()
    try:
        panel=replace(panel,body_load=ss.GEBodyLoad(binding.preflight()['identity'],(0.,0.,-9.81)))
    finally:binding.close()
    project=Project('experimental native gravity',mesh_only=True)
    record=AnalysisDefinition.experimental_point_panel('gravity',panel);project.add_analysis(record)
    path=save_project(project,directory/'gravity.anyfem')
    reopened=load_project(path);record2=reopened.analyses[record.id]
    restored=point_panel_from_analysis(record2)
    assert record2.to_dict()==record.to_dict() and restored.to_dict()==panel.to_dict()
    assert restored.load_input.serialize()==panel.load_input.serialize()
    assert all(a.raw==b.raw and a.sha256==b.sha256 for a,b in zip(restored.beams,panel.beams,strict=True))
    linear=solve_point_panel(record2)
    assert linear.status=='completed' and linear.body_load.identity==panel.body_load.identity
    observations=[]
    def observe(result,branch):
        row=dict(branch=branch,status=result.status,factor=result.load_factor,
                 generation=result.generation,equilibrium=result.free_equilibrium_ratio,
                 compatibility=result.constraint_max,force=list(result.body_load.applied_force),
                 moment=list(result.body_load.applied_moment_about_origin))
        observations.append(row)
        (directory/'observations.json').write_text(json.dumps(observations,indent=2),encoding='utf-8')
    with ExitStack() as stack:
        owner=record2.create_point_panel_analysis();resume=record2.create_point_panel_analysis()
        stack.callback(owner.close);stack.callback(resume.close)
        targets=(.25,.5,1.);control=ss.AdaptiveIncrementControl(2,16)
        token=CancellationToken()
        def cancel(result):
            observe(result,'prefix');token.cancel('after complete gravity prefix')
        try:owner.solve(restored.load_input,targets,increment_control=control,on_accepted=cancel,cancellation_token=token)
        except SolveCancelled:pass
        else:raise AssertionError('expected cooperative cancellation after accepted prefix')
        prefix=owner.recover()
        assert prefix.status=='accepted_prefix' and prefix.accepted_factors==(.25,)
        checkpoint=owner.checkpoint();(directory/'prefix.checkpoint').write_bytes(checkpoint)
        loaded=resume.restore((directory/'prefix.checkpoint').read_bytes())
        assert loaded.status=='accepted_prefix' and loaded.accepted_factors==(.25,)
        (directory/'restored-prefix.checkpoint').write_bytes(resume.checkpoint())
        assert resume.checkpoint()==checkpoint
        final=owner.solve(restored.load_input,targets,increment_control=control,on_accepted=lambda r:observe(r,'direct'))
        continued=resume.solve(restored.load_input,targets,increment_control=control,on_accepted=lambda r:observe(r,'resumed'))
        assert final.status==continued.status=='completed'
        assert final.accepted_factors==continued.accepted_factors==targets
        assert owner.checkpoint()==resume.checkpoint()!=checkpoint
        (directory/'direct-final.checkpoint').write_bytes(owner.checkpoint())
        (directory/'resumed-final.checkpoint').write_bytes(resume.checkpoint())
        assert all(r['equilibrium']<=1e-8 and r['compatibility']<=1e-9 for r in observations)
        result=dict(status='EXPERIMENTAL_NOT_QUALIFIED',production_qualified=False,
                    linear_status=linear.status,prefix_status=prefix.status,final_status=final.status,
                    resumed_status=continued.status,accepted_factors=final.accepted_factors,
                    acceleration_m_s2=restored.body_load.acceleration_m_s2,
                    body_identity=restored.body_load.identity,model_identity=restored.body_load.model_identity,
                    native_beam_sha256=[b.sha256 for b in restored.beams],mass_kg=final.body_load.total_mass,
                    applied_force_N=final.body_load.applied_force,restart_identical=True)
        (directory/'summary.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
        return result


if __name__=='__main__':
    with TemporaryDirectory(prefix='anyfem-gravity-') as directory:
        print(json.dumps(workflow(directory),indent=2))
