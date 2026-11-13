"""Tiny real owner routes and the application's capacity dispatch boundary."""
import os
import subprocess
import sys
import numpy as np
import pytest

from test_analyses import strut_project
from anyfem import solve_linear_static, solve_capacity
from anyfem.io.project_file import project_to_dict, project_from_dict


@pytest.mark.skipif(os.environ.get('ANYFEM_REQUIRE_INSTALLED_WHEEL') != '1', reason='release installed-wheel lane')
def test_installed_wheel_in_isolated_interpreter(tmp_path):
    result = subprocess.run([sys.executable, '-I', '-c',
        "import anyfem,importlib.metadata,pathlib;"
        "assert anyfem.__version__==importlib.metadata.version('ANYfem');"
        "assert 'site-packages' in str(pathlib.Path(anyfem.__file__).resolve());"
        "from anyfem import Project,steel,fixed,solve_linear_static;"
        "from anyfem.model import BeamSection;"
        "from anyfem.io.project_file import project_to_dict,project_from_dict;"
        "p=Project();p.add_material(steel('S355',.02));"
        "p.add_beam_section(BeamSection(name='bar',profile='Flatbar',material='S355',flange_width=.1,flange_thickness=.02));"
        "a=p.geometry.add_point(0,0,0);b=p.geometry.add_point(1,0,0);e=p.geometry.add_line(a,b);"
        "p.assign_beam(e,'bar');p.add_support(fixed(p.point(a)));"
        "p.load_case().add_point_load(p.point(b),force=(0,0,-1));"
        "s=solve_linear_static(p,target_size=.5);assert s.max_translation()[1]>0;"
        "q=project_from_dict(project_to_dict(p));"
        "t=solve_linear_static(q,target_size=.5);"
        "assert abs(s.max_translation()[1]-t.max_translation()[1])<1e-12"], cwd=tmp_path, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr


def test_geometry_mesh_solve_and_saved_project_reopen():
    project, *_ = strut_project()
    mesh = project.generate_mesh(.5)
    solution = solve_linear_static(project, mesh=mesh)
    assert np.isfinite(solution.max_translation()[1])
    reopened = project_from_dict(project_to_dict(project))
    again = solve_linear_static(reopened, target_size=.5)
    assert again.max_translation()[1] == pytest.approx(solution.max_translation()[1])


def test_tiny_capacity_workflow_runs_real_owner_stages():
    project, *_ = strut_project(load=1.)
    result = solve_capacity(project, target_size=.34, num_buckling_modes=1,
                            imperfection_amplitude=1e-5, num_steps=2)
    assert result.status == 'completed'
    assert len(result.buckling) == 1 and result.critical_factor > 0
    assert result.capacity_factor > 0
    assert 'capacity' in result.summary() and 'elastic critical' in result.summary()


def test_capacity_forwards_config_progress_cancellation_and_result(monkeypatch):
    import anyfesolver
    from anyfem.solve.build import build_fe_model
    project, *_ = strut_project(load=1.)
    built = build_fe_model(project, project.generate_mesh(.5))
    real = anyfesolver.run_nonlinear_capacity_workflow
    calls = []
    def dispatch(model, load_case, **kwargs):
        calls.append((model, load_case, kwargs))
        return real(model, load_case, **kwargs)
    monkeypatch.setattr(anyfesolver, 'run_nonlinear_capacity_workflow', dispatch)
    token = anyfesolver.CancellationToken(); progress = []
    result = solve_capacity(built=built, num_buckling_modes=1, num_steps=2,
                            cancellation_token=token, progress=progress.append)
    model, load_case, kwargs = calls.pop()
    assert not calls and model is built.fe_model and load_case is built.load_case
    assert kwargs['cancellation_token'] is token
    assert kwargs['config'].num_buckling_modes == 1
    assert kwargs['config'].nonlinear_num_steps == 2
    assert result.raw_result is result.info['raw'].nonlinear_result
    assert progress and result.buckling.status == result.info['raw'].buckling_result.solver_status
