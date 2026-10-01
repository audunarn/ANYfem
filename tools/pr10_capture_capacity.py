"""Read-only constitutive capture for the unchanged failing capacity tests."""
import inspect
import json
from pathlib import Path
import platform

import numpy as np
import pytest
import anysolver.plasticity as plasticity
from anysolver.jit_compiler import JIT_BACKEND

output = Path('reports/pr10/capture')
output.mkdir(parents=True, exist_ok=False)
original = plasticity._require_local_convergence

def capture(converged, scaled_residual, *, max_iterations):
    failed = np.flatnonzero(~np.asarray(converged, dtype=bool))
    if failed.size and not (output/'inputs.npz').exists():
        caller = inspect.currentframe().f_back
        local = caller.f_locals
        curve = local['curve']
        np.savez(output/'inputs.npz', strain=local['strain'],
                 plastic_strain=local['plastic_strain'], alpha=local['alpha'],
                 returned_stress=local['sigma'], returned_alpha=local['new_alpha'],
                 failed=failed, scaled_residual=scaled_residual)
        metadata = {'E':local['E'], 'nu':local['nu'],
                    'max_iterations':max_iterations, 'tolerance':local['tolerance'],
                    'curve':{key:float(getattr(curve,key)) for key in
                             ('sigma_prop','sigma_yield','sigma_yield_2','eps_p_y1','eps_p_y2','K','n','_power_offset')},
                    'numpy':np.__version__, 'python':platform.python_version(),
                    'jit_backend':JIT_BACKEND,
                    'failed_points':failed.tolist(),
                    'exact_failed_residuals':np.asarray(scaled_residual)[failed].tolist()}
        (output/'metadata.json').write_text(json.dumps(metadata,indent=2)+'\n')
    return original(converged, scaled_residual, max_iterations=max_iterations)

plasticity._require_local_convergence = capture
raise SystemExit(pytest.main(['-q',
    'tests/test_workflow.py::test_the_capacity_workflow_runs_every_stage',
    'tests/test_workflow.py::test_the_capacity_ratio_compares_the_two_load_factors',
    'tests/test_workflow.py::test_the_capacity_summary_names_both_load_factors']))
