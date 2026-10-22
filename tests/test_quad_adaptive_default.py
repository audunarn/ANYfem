"""Adaptive defaults reach the owner while explicit saved layouts survive."""
import pytest

from anyfem.application.examples import floating_barge_section
from anyfem.mesh_execution import MeshExecutionPolicy
from anyfem.mesh_jobs import MeshSettings
from anyfem.model import project as project_module
from anyfem.native_meshing import NativeMeshSettings


@pytest.mark.parametrize('strategy', ['quad_first', 'smart_mapped'])
def test_quad_job_defaults_are_adaptive(strategy):
    settings = MeshSettings.create(.5, element_order='linear', strategy=strategy)
    assert settings.layout_policy == 'adaptive'
    assert settings.input_hash == MeshSettings.create(
        .5, element_order='linear', strategy=strategy,
        layout_policy='adaptive').input_hash
    assert MeshSettings.create(.5, element_order='linear', strategy=strategy,
                               layout_policy='existing').layout_policy == 'existing'


@pytest.mark.parametrize('strategy', [None, 'auto', 'native', 'mapped'])
def test_nonquad_jobs_keep_their_layout_contract(strategy):
    settings = MeshSettings.create(.5, element_order='linear', strategy=strategy)
    assert settings.layout_policy == 'existing'
    with pytest.raises(ValueError, match='adaptive layout requires'):
        MeshSettings.create(.5, element_order='linear', strategy=strategy,
                            layout_policy='adaptive')


@pytest.mark.parametrize('saved,requested,expected', [
    (None, None, 'adaptive'),
    ('existing', None, 'existing'),
    ('adaptive', None, 'adaptive'),
    ('existing', 'adaptive', 'adaptive'),
    ('adaptive', 'existing', 'existing'),
])
def test_project_resolves_default_without_overriding_saved_layout(
        monkeypatch, saved, requested, expected):
    project = floating_barge_section(length=2., bulkheads=(1.,), girders=(0.,),
                                     breadth=2., stiffener_spacing=1.)
    project.set_native_mesh_settings(NativeMeshSettings.create(
        .5, backend='quad_first',
        parameters={} if saved is None else {'layout_policy': saved}))
    class ReachedOwner(Exception):
        pass
    observed = {}
    def owner(_geometry, **kwargs):
        observed.update(kwargs)
        raise ReachedOwner
    monkeypatch.setattr(project_module, 'generate_hybrid_mesh', owner)
    with pytest.raises(ReachedOwner):
        project.generate_mesh(layout_policy=requested,
                              execution_policy=MeshExecutionPolicy.off())
    assert observed['layout_policy'] == expected
