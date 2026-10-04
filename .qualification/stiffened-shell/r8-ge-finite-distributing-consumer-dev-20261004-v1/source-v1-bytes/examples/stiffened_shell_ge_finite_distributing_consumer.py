"""Experimental finite area pose: input/Project save, solve and binary resume.

Flat elastic Q4/generalized native B3, monotone translation forces only.
This reduced distributing pose is not a weld or qualified physical prediction.
"""
from dataclasses import replace
from pathlib import Path
import argparse,json
import numpy as np
from anysolver import stiffened_shell as ss
from anyfem.io.project_file import save_project,load_project
from anyfem.model.project import Project
from anyfem.model.records import AnalysisDefinition
from anyfem.solve.stiffened_shell import ExperimentalGEFiniteDistributingPanel,save_case,load_case
from anyfem.solve.stiffened_shell_project import point_panel_from_analysis
from stiffened_shell_ge_distributing_consumer import definition_and_actions as reference_inputs


def definition_and_actions():
    base,_=reference_inputs()
    return replace(base,distributing_footprints=(),finite_distributing_footprints=(
        ss.GEFiniteDistributingFootprint(base.distributing_footprints[0]),)),(
        ss.NodalAction("beam",103,(0.,20.,100.,0.,0.,0.)),)


def workflow(directory):
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=True)
    model,actions=definition_and_actions()
    panel=ExperimentalGEFiniteDistributingPanel.from_definition(model,actions)
    standalone=save_case(panel,directory/'finite-distributing.anyfem-ge.json')
    panel=load_case(standalone)
    project=Project('experimental finite distributing',mesh_only=True)
    analysis=AnalysisDefinition.experimental_point_panel('finite area pose',panel)
    project.add_analysis(analysis)
    filename=save_project(project,directory/'finite-distributing.anyfem')
    analysis=load_project(filename).analyses[analysis.id]
    panel=point_panel_from_analysis(analysis)
    owner=analysis.create_point_panel_analysis();resumed=None
    try:
        prefix=owner.solve(panel.load_input,(.25,.5))
        checkpoint=directory/'finite-distributing.checkpoint'
        checkpoint.write_bytes(owner.checkpoint())
        resumed=analysis.create_point_panel_analysis()
        restored=resumed.restore(checkpoint.read_bytes())
        assert resumed.checkpoint()==checkpoint.read_bytes()
        expected=owner.solve(panel.load_input,(1.,))
        final=resumed.solve(panel.load_input,(1.,))
        np.testing.assert_allclose(final.shell_displacement,expected.shell_displacement,rtol=1e-10,atol=1e-12)
        record,=resumed.recover(selection=panel.recovery_selection)
        observations=dict(status=panel.status,production_qualified=False,law=record.law,
            model_identity=record.model_identity,configuration=record.configuration,
            accepted_factors=final.accepted_factors,generations=(prefix.generation,restored.generation,final.generation),
            equilibrium=final.free_equilibrium_ratio,constraint=final.constraint_max,
            checkpoint_bytes=checkpoint.stat().st_size,shell_action=record.shell_action.tolist(),
            beam_action=record.beam_action.tolist(),
            resumed_displacement_difference=float(np.linalg.norm(final.shell_displacement-expected.shell_displacement)))
        (directory/'observations.json').write_text(json.dumps(observations,indent=2),encoding='utf-8')
        return observations
    finally:
        if resumed is not None:resumed.close()
        owner.close()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('directory',type=Path)
    print(json.dumps(workflow(parser.parse_args().directory),indent=2))
