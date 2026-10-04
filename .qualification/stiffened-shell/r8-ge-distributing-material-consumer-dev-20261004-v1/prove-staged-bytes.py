from pathlib import Path
import hashlib,json,subprocess
ROOT=Path(__file__).resolve().parent;REPO=ROOT.parents[2]
git=['git','-c','safe.directory='+str(REPO)]
paths=subprocess.check_output(git+['diff','--cached','--name-only'],cwd=REPO,text=True).splitlines()
expected=set((ROOT/'stage-paths.txt').read_text(encoding='utf-8-sig').splitlines())
assert set(paths)==expected,(set(paths)-expected,expected-set(paths))
proof={}
for path in paths:
    if path=='.gitattributes':continue
    blob=subprocess.check_output(git+['show',':'+path],cwd=REPO)
    assert blob==(REPO/path).read_bytes(),path
    proof[path]=hashlib.sha256(blob).hexdigest()
(ROOT/'staged-byte-proof.json').open('x',encoding='utf-8').write(json.dumps(proof,indent=2)+'\n')
print(json.dumps(dict(exact_files=len(proof),intentional_text_normalization=['.gitattributes'])))
