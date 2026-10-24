"""Bounded serialization and failure isolation for current-state reports."""
from collections.abc import Mapping
from dataclasses import dataclass
from types import SimpleNamespace
import json
import numpy as np
from anyfem import Project
from anyfem.diagnostics import _safe,_features,build_diagnostic_report,build_current_state


def test_safe_is_lazy_and_does_not_call_to_dict_deepcopy_or_repr():
    class Virtual(Mapping):
        reads=0
        def __len__(self):return 1000000
        def __iter__(self):return iter(range(1000000))
        def __getitem__(self,key):self.reads+=1;return key
        def items(self):raise AssertionError('eager items forbidden')
    value=Virtual();snapshot=_safe(value)
    assert value.reads==30 and snapshot['__diagnostic_truncation__']['total']==1000000
    @dataclass
    class Data:
        array: object
        code: str
        def to_dict(self):raise AssertionError('serializer forbidden')
        def __deepcopy__(self,memo):raise AssertionError('deepcopy forbidden')
    data=_safe(Data(np.broadcast_to(np.array([7.]),(10000000,)),'secret script source'))
    assert data['array']['shape']==[10000000] and len(data['array']['preview'])==6
    assert 'secret' not in json.dumps(data)


def test_feature_metadata_never_deepcopies_even_small_history():
    class Expensive:
        def __len__(self):return 1
        @property
        def records(self):raise AssertionError('history deepcopy forbidden')
    result=_features(SimpleNamespace(features=Expensive()))
    assert result['total']==1 and result['truncated'] and result['included']==0


def test_actual_mesh_metadata_and_loaded_arrays_are_bounded_without_recovery():
    mesh=SimpleNamespace(num_nodes=12,num_elements=4,beams={1:(1,2)},hybrid_diagnostics={'route':'existing'},structural_preparation={'status':'recorded'})
    class Result:
        def __init__(self):self.info={'converged':False,'iterations':4};self.displacements=np.broadcast_to(np.array([1.]),(10000000,));self.steps=[SimpleNamespace(load_factor=.5,iterations=4)];self.peak_load_factor=.5
        def summary(self):raise AssertionError('summary computes')
        def stresses(self):raise AssertionError('stress recovery forbidden')
    p=Project('Metadata');state=build_current_state(p,loaded_mesh=mesh,solution=Result())
    assert state['meshing']['loaded_mesh']['counts']['num_beams']==1
    assert state['meshing']['loaded_mesh']['metadata']['hybrid_diagnostics']['route']=='existing'
    result=state['solver_output']['loaded_result']
    assert result['metadata']['info']['iterations']==4 and result['metadata']['peak_load_factor']==.5
    assert result['steps']['total']==1 and 'preview' not in result['arrays']['displacements']
    assert len(json.dumps(state))<15000
