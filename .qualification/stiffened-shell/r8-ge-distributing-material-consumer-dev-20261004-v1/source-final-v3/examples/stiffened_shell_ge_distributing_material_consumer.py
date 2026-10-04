"""Explicit experimental material input/Project persistence and yielded restart.

Centered axial state-ownership example, not a physical-connection or capacity
qualification. Uses a0.01% post-yield increment; the1% continuation is unresolved.
"""
from dataclasses import replace
from pathlib import Path
import argparse,json
import numpy as np
from anymaterial import DNVC208MaterialCurve
from anysolver import b3_ge,stiffened_shell as ss
from anyfem.io.project_file import save_project,load_project
from anyfem.model.project import Project
from anyfem.model.records import AnalysisDefinition
from anyfem.solve.stiffened_shell import ExperimentalGEFiniteDistributingMaterialPanel,save_case,load_case
from anyfem.solve.stiffened_shell_project import point_panel_from_analysis
from stiffened_shell_ge_finite_distributing_consumer import definition_and_actions as elastic_inputs


def definition_and_actions(coupled=True):
    base,_=elastic_inputs();element,inertia=base.beams[0].instantiate()
    coordinates=element.operator.reference.coordinates.copy();coordinates[:,2]=0.
    section=b3_ge.EllipsoidalGeneralizedSection(element.section.elastic,element.section.metric,1e6,1e11)
    beam=b3_ge.define_beam('b3-ge',element.element_id,element.node_ids,coordinates,
        element.operator.reference.nodal_triads,section,inertia,
        quadrature=element.operator.order,arithmetic_policy=element.operator.arithmetic_policy)
    curve=DNVC208MaterialCurve(200e6,220e6,230e6,.005,.01,1e9,.2) if coupled else None
    model=replace(base,beams=(beam,),shell=replace(base.shell,j2_curve=curve),
        finite_distributing_material_policy=(ss.GE_FINITE_DISTRIBUTING_COUPLED_J2_MATERIAL_V1
            if coupled else ss.GE_FINITE_DISTRIBUTING_GENERALIZED_MATERIAL_V1))
    return model,(ss.NodalAction('beam',103,(16e6,0.,0.,0.,0.,0.)),)


def workflow(directory):
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=True)
    model,loads=definition_and_actions()
    panel=ExperimentalGEFiniteDistributingMaterialPanel.from_definition(model,loads)
    panel=load_case(save_case(panel,directory/'material.anyfem-ge.json'))
    project=Project('experimental finite material',mesh_only=True)
    analysis=AnalysisDefinition.experimental_point_panel('yielded pad',panel);project.add_analysis(analysis)
    analysis=load_project(save_project(project,directory/'material.anyfem')).analyses[analysis.id]
    panel=point_panel_from_analysis(analysis)
    owner=analysis.create_point_panel_analysis();resumed=None
    try:
        prefix=owner.solve(panel.load_input,(.25,.5,.75,1.),max_iterations=24,max_backtracks=16)
        assert np.max(prefix.shell_layer_alpha)>0. and np.max(prefix.beam_station_accumulated)>0.
        raw=owner.checkpoint();(directory/'material.checkpoint').write_bytes(raw)
        resumed=analysis.create_point_panel_analysis();resumed.restore(raw)
        expected=owner.solve(panel.load_input,(1.0001,),max_iterations=24,max_backtracks=16)
        actual=resumed.solve(panel.load_input,(1.0001,),max_iterations=24,max_backtracks=16)
        assert owner.checkpoint()==resumed.checkpoint()
        np.testing.assert_array_equal(actual.shell_displacement,expected.shell_displacement)
        record,=resumed.recover(selection=panel.recovery_selection)
        result=dict(status=panel.status,material_policy=panel.finite_distributing_material_policy,
            accepted_factors=actual.accepted_factors,equilibrium=actual.free_equilibrium_ratio,
            gap=actual.constraint_max,shell_alpha=float(np.max(actual.shell_layer_alpha)),
            beam_accumulated=float(np.max(actual.beam_station_accumulated)),
            checkpoint_bytes=len(raw),law=record.law)
        (directory/'observations.json').write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
        return result
    finally:
        if resumed is not None:resumed.close()
        owner.close()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('directory',type=Path)
    print(json.dumps(workflow(parser.parse_args().directory),indent=2))
