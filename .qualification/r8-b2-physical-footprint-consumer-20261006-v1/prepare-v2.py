from pathlib import Path
import hashlib, json
ROOT=Path(__file__).resolve().parent; REPO=ROOT.parents[1]
sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
entry=(ROOT/'entry.py').read_text().replace('installed-input-snapshot-v1','installed-input-snapshot-v2').replace('installed-input-snapshot-manifest-v1','installed-input-snapshot-manifest-v2')
(ROOT/'entry-v2.py').write_text(entry)
runner=(ROOT/'runner.py').read_text().replace("ROOT/'entry.py'", "ROOT/'entry-v2.py'")
(ROOT/'runner-v2.py').write_text(runner)
snapshot=ROOT/'installed-input-snapshot-v2'; snapshot.mkdir(exist_ok=False)
files=('tests/test_stiffened_shell_b2_physical_footprint_consumer.py','examples/stiffened_shell_b2_physical_footprint_consumer.py')
identities={}
for name in files:
    target=snapshot/name; target.parent.mkdir(exist_ok=True); target.write_bytes((REPO/name).read_bytes()); identities[name]=sha(target)
(ROOT/'installed-input-snapshot-manifest-v2.json').write_text(json.dumps(identities,indent=2)+'\n')
owned=('src/anyfem/solve/stiffened_shell_b2.py','src/anyfem/solve/stiffened_shell_project.py',*files,'docs/experimental_b2_physical_rigid_footprint.md')
(ROOT/'candidate-source-shas-v2.json').write_text(json.dumps({name:sha(REPO/name) for name in owned},indent=2)+'\n')
