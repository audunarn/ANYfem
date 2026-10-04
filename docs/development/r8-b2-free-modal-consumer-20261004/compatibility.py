from pathlib import Path
import hashlib,importlib.util,json,subprocess,sys
root=Path(__file__).resolve().parent;repo=Path('C:/Github/ANYfem/.worktrees/experimental-ge-panel-consumer')
git=['git','-c','safe.directory='+repo.as_posix()]
old=subprocess.check_output(git+['show','2ede338ba28932bb9dd3e8eb0f0a4908d37fc765:src/anyfem/solve/stiffened_shell_b2.py'],cwd=repo)
oldpath=root/'historical-b2-consumer.py';oldpath.write_bytes(old)
spec=importlib.util.spec_from_file_location('historical_b2_consumer',oldpath);baseline=importlib.util.module_from_spec(spec);sys.modules[spec.name]=baseline;spec.loader.exec_module(baseline)
sys.path.insert(0,str(repo/'tests'))
from test_experimental_b2_mixed_reference_modal_consumer import _definition
from anysolver import stiffened_shell as solver
from anyfem.solve.stiffened_shell_b2 import ExperimentalB2MixedReferenceModalPanel
from dataclasses import replace
from scipy import sparse
records={}
for kind in ('q4','mixed','s3'):
    definition=_definition(kind);request=solver.B2ReferenceModesRequest(count=2,shift_hz=.5)
    prior=baseline.ExperimentalB2MixedReferenceModalPanel.from_definition(definition,request)
    current=ExperimentalB2MixedReferenceModalPanel.from_definition(definition,request)
    assert prior.payload==current.payload
    assert baseline.ExperimentalB2MixedReferenceModalPanel(current.payload).payload==prior.payload
    free=replace(definition,transformation=sparse.eye(definition.transformation.shape[0],format='csr'),boundary_policy=solver.B2_FREE_FREE_MODAL_ONLY_V1)
    new=ExperimentalB2MixedReferenceModalPanel.from_definition(free,request)
    try:baseline.ExperimentalB2MixedReferenceModalPanel(new.payload)
    except ValueError:rejected=True
    else:raise AssertionError('historical consumer admitted new free schema')
    records[kind]=dict(supported_payload_bytes=len(current.payload),supported_sha256=hashlib.sha256(current.payload).hexdigest(),exact_historical_match=True,historical_free_rejected=rejected,new_schema=new.schema)
(root/'compatibility.json').write_text(json.dumps(dict(baseline_commit='2ede338ba28932bb9dd3e8eb0f0a4908d37fc765',baseline_module_sha256=hashlib.sha256(old).hexdigest(),records=records),indent=2)+'\n')
print(json.dumps(records))
