from pathlib import Path
import hashlib,importlib,importlib.metadata as md,json,sys
root=Path(__file__).resolve().parent
sys.path[:0]=[str(root/'site'),str(root.parent/'r8-b2-free-reference-modes-v3/site')]
records={}
for package,distribution in (('anyfem','ANYfem'),('anysolver','ANYsolver'),('anymaterial','ANYmaterial'),('anygeometry','ANYgeometry'),('anymesh','ANYmesher'),('anyfileio','ANYfileio'),('numpy','numpy'),('scipy','scipy'),('h5py','h5py')):
    try:
        module=importlib.import_module(package)
        record=dict(import_path=str(Path(module.__file__).resolve()),metadata_version=md.version(distribution),runtime_version=getattr(module,'__version__',None))
    except ImportError as exc:record=dict(import_probe_unavailable=str(exc),metadata_version=md.version(distribution))
    records[distribution]=record
wheel=root.parent/'r8-bl5-projected-joint32-v2/dist/anysolver-0.4.7-py3-none-any.whl'
sha=hashlib.sha256(wheel.read_bytes()).hexdigest();assert sha=='1bf7dabd8771e76ed09825fb66a89e3e48d513dbb0321989c41701096a679632'
record=dict(packages=records,historical_solver_wheel_sha256=sha,current_solver_wheel_sha256='dea651b918e92871ffc017c6a435dd1f265d22e86f9a307d00d12ebd4abfa27b',scope='observed development graph; editable siblings are not isolated final qualification')
(root/'dependencies.json').write_text(json.dumps(record,indent=2)+'\n');print(json.dumps({name:value.get('metadata_version') for name,value in records.items()}))

