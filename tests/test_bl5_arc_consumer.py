"""Installed consumer construction and persistence for the solver-owned arc path."""
import numpy as np
import pytest
from anysolver import stiffened_shell as ss
from anysolver.control import CancellationToken,SolveCancelled
from anyfem.io.project_file import load_project,save_project
from anyfem.model.project import Project
from anyfem.model.records import AnalysisDefinition
from anyfem.solve.stiffened_shell import load_case,save_case
from test_bl5_coupled_material_consumer import _coupled_panel


def test_project_arc_prefix_restore_and_selected_recovery(tmp_path,request):
    panel=_coupled_panel()
    panel=load_case(save_case(panel,tmp_path/'arc-input.anyfem-ge.json'))
    project=Project('experimental arc consumer',mesh_only=True)
    record=AnalysisDefinition.experimental_bl5_panel('arc panel',panel)
    project.add_analysis(record)
    reopened=load_project(save_project(project,tmp_path/'arc.anyfem'))
    record=reopened.analyses[record.id]
    owner=record.create_bl5_panel_analysis();request.addfinalizer(owner.close)
    action=(ss.NodalAction('beam',105,(0.,0.,-100.,0.,0.,0.)),)
    owner.solve(action,(.001,))
    preflight=owner.preflight()
    assert 'arc_length' in owner.capabilities and not preflight['production_qualified']
    count=len(preflight['arc_metric_node_order'])
    control=ss.ArcLengthControl('beam',105,2,
        ss.PhysicalChordMetric(np.full(count,1/count),preflight['reference_length_m']),1e-8,-1)
    token=CancellationToken();prefix=[]
    def cancel(result):
        prefix.append(result);token.cancel('persist complete consumer arc prefix')
    with pytest.raises(SolveCancelled):
        owner.continue_arc_length(action,control,steps=2,on_accepted=cancel,cancellation_token=token)
    assert len(prefix)==1 and prefix[0].status=='accepted_prefix'
    checkpoint=tmp_path/'arc-prefix.bin';checkpoint.write_bytes(owner.checkpoint())
    # Native input persistence and the solver's accepted-state file remain distinct.
    reopened=load_project(tmp_path/'arc.anyfem')
    restored=reopened.analyses[record.id].create_bl5_panel_analysis()
    request.addfinalizer(restored.close)
    result=restored.restore(checkpoint.read_bytes())
    assert restored.checkpoint()==checkpoint.read_bytes()
    a=owner.continue_arc_length(action,control)
    b=restored.continue_arc_length(action,control)
    assert a.status==b.status=='completed'
    assert owner.checkpoint()==restored.checkpoint()
    assert a.free_equilibrium_ratio<=1e-8 and a.constraint_max<=1e-9
    np.testing.assert_array_equal(a.cell_resultants,b.cell_resultants)
    selected=restored.recover(selection=ss.GERecoverySelection(beam_nodes=(105,),
        beam_material_elements=(b.beam_generalized_material[0].element_id,)))
    assert selected.amplitude==b.amplitude and selected.beam_generalized_material
    assert len(b.attempt_history)==2 and not b.production_qualified
