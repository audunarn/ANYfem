from pathlib import Path
import json,runpy,sys
root=Path(__file__).resolve().parent;repo=Path('C:/Github/ANYfem/.worktrees/experimental-ge-panel-consumer')
import anyfem,anysolver
assert Path(anyfem.__file__).is_relative_to(root/'site')
assert Path(anysolver.__file__).is_relative_to(root.parent/'r8-b2-free-reference-modes-v3/site')
for name,options in (('supported',[]),('mixed-free',['--free-free']),('s3-free',['--free-free','--all-s3'])):
    destination=root/(name+'.anyfem');assert not destination.exists()
    sys.argv=[str(repo/'examples/stiffened_shell_b2_mixed_reference_modes_consumer.py'),str(destination),'--count','3',*options]
    runpy.run_path(sys.argv[0],run_name='__main__')
