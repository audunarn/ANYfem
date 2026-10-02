from pathlib import Path
import sys,os,json,importlib.util
root=Path(__file__).parent
spec=importlib.util.spec_from_file_location('supervisor',root.parent/'r8-b2-panel-resolution-installed-v1/matched-run.py');s=importlib.util.module_from_spec(spec);spec.loader.exec_module(s)
repo=Path('C:/Github/ANYfem/.worktrees/experimental-ge-panel-consumer')
s.ENV['PYTHONPATH']='C:/Github/ANYsolver/.perf2-worktrees/stiffened-main-reconcile-20261001/src'+os.pathsep+str(repo/'src')
s.ENV['PYTEST_DISABLE_PLUGIN_AUTOLOAD']='1'
names=['test_stiffened_shell_ge_distributed_programme_consumer.py','test_stiffened_shell_ge_distributed_programme_workflow.py','test_stiffened_shell_ge_distributed_consumer.py','test_stiffened_shell_ge_pressure_consumer.py','test_stiffened_shell_ge_gravity_consumer.py','test_stiffened_shell_project.py','test_stiffened_shell_staged_load_consumer.py']
r=s.supervised([sys.executable,'-m','pytest','-q','-c',str(root.parent/'r8-anyfem-ge-pressure-installed-v1/fixtures/pytest.ini'),'-p','no:cacheprovider',*[str(repo/'tests'/n) for n in names]],root/'tests-v1')
print(json.dumps({k:r[k] for k in ('exit_code','elapsed_seconds','sampled_peak_tree_bytes')}))
