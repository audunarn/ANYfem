from pathlib import Path
import hashlib,json,sys,zipfile
root=Path(__file__).resolve().parent;previous=root.parent/'r8-bl5-projected-joint32-v2'
sys.path.insert(0,str(previous/'site'))
from anysolver import stiffened_shell as api
assert not hasattr(api,'B2_FREE_FREE_MODAL_ONLY_V1')
assert 'boundary_policy' not in api.B2MixedReferenceModalDefinition.__dataclass_fields__
assert 'boundary_policy' not in api.ExperimentalB2MixedReferenceModalCapabilities.__dataclass_fields__
modules={}
with zipfile.ZipFile(previous/'dist/anysolver-0.4.7-py3-none-any.whl') as archive:
    for name in ('anysolver/stiffened_shell.py','anysolver/_stiffened_shell_b2_q4_panel_experimental.py'):
        data=(previous/'site'/name).read_bytes();assert data==archive.read(name)
        modules[name]=hashlib.sha256(data).hexdigest()
record=dict(site=str(previous/'site'),public_free_exports_absent=True,definition_boundary_field_absent=True,capability_boundary_field_absent=True,site_wheel_module_identity=modules)
(root/'historical-api.json').write_text(json.dumps(record,indent=2)+'\n');print(json.dumps({k:v for k,v in record.items() if k.endswith('absent')}))
