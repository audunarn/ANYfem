"""Automatic falls back to quad_first only after the exact structured element-limit refusal on a curved shell.

Owner decision 2026-11-24 ("A: fallback"): Automatic keeps today's route, so everything that meshes today is
unchanged. Only when that route refuses with the exact global-seed element-limit refusal on a model with a
non-planar face does Automatic rerun as quad_first with the model's stored seam policy (no implicit ring_split).
Planar models, quadratic order and explicit strategies keep the refusal unchanged.
"""
from __future__ import annotations

import numpy as np
import pytest

from anyfem import commands
from anyfem.application.controller import default_project
from anyfem.jobs import _cancellation_token
from anyfem.mesh_controls import StructuredMeshControls
from anyfem.mesh_execution import MeshExecutionPolicy
from anyfem.mesh_jobs import MeshSettings, run_mesh_job
from anyfem.native_meshing import NativeMeshSettings
from anymesher.errors import StructuredElementLimitExceeded
from anymesher.quad.public_integration import QuadPublicUnsupported

LIMIT = StructuredMeshControls(maximum_estimated_elements=400)  # the generated cylinder needs 520


def cylinder():
    project = default_project()
    commands.AddCylinder(1., 5., circumferential_segments=12, longitudinal_spacing=.5,
                         beam_section='stiffener', plate_section='plate').do(project)
    return project


def plate():
    project = default_project()
    geometry = project.geometry
    face = geometry.add_plate(geometry.add_points(((0, 0, 0), (6, 0, 0), (6, 5, 0), (0, 5, 0))))
    project.assign_plate(face, 'plate')
    return project


def signature(mesh):
    return (sorted((int(k), tuple(np.round(v, 12))) for k, v in mesh.nodes.items()),
            sorted((int(k), tuple(v)) for k, v in mesh.shells.items()))


def counts(mesh):
    return tuple(sum(1 for nodes in mesh.shells.values() if len(nodes) == k) for k in (4, 3))


def test_within_the_limit_automatic_is_unchanged():
    mesh = cylinder().generate_mesh(.25, order='linear', strategy='auto')
    assert counts(mesh) == (520, 0)  # today's legacy Automatic result
    assert 'automatic_fallback' not in mesh.hybrid_diagnostics


@pytest.mark.parametrize('strategy', ['auto', None])
def test_curved_element_limit_refusal_reruns_as_quad_first(strategy):
    expected = cylinder().generate_mesh(.25, order='linear', strategy='quad_first', structured_controls=LIMIT)
    project = cylinder()
    mesh = project.generate_mesh(.25, order='linear', strategy=strategy, structured_controls=LIMIT)
    assert signature(mesh) == signature(expected)
    record = mesh.hybrid_diagnostics['automatic_fallback']
    assert (record['requested'], record['selected'], record['estimated'], record['maximum']) == (
        'auto', 'quad_first', 520, 400)
    assert record['seam_policy'] is None
    assert record['reason'].startswith('global seed solution estimates 520 structured elements')
    assert record['refusal_seconds'] >= 0.
    assert project._last_mesh_execution['automatic_fallback'] == record
    assert project.mesh_records == {}


def test_fallback_uses_the_stored_seam_policy_and_stored_limit():
    def stored(backend):
        project = cylinder()
        project.set_native_mesh_settings(NativeMeshSettings.create(
            .25, element_order='linear', backend=backend,
            parameters={'seam_policy': 'ring_split', 'structured_maximum_estimated_elements': 400}))
        return project

    expected = stored('quad_first').generate_mesh()
    mesh = stored('automatic').generate_mesh()
    assert signature(mesh) == signature(expected)
    assert mesh.hybrid_diagnostics['automatic_fallback']['seam_policy'] == 'ring_split'


def test_mesh_job_falls_back_to_the_explicit_quad_first_job():
    def job(backend):
        project = cylinder()
        project.set_native_mesh_settings(NativeMeshSettings.create(
            .25, element_order='linear', backend=backend,
            parameters={'structured_maximum_estimated_elements': 400}))
        return run_mesh_job(project, 'm', MeshSettings.create(.25, element_order='linear',
                                                              execution_policy=MeshExecutionPolicy.off()),
                            lambda *a: None, _cancellation_token()).mesh

    mesh = job('automatic')
    assert mesh.hybrid_diagnostics['automatic_fallback']['estimated'] == 520
    assert signature(mesh) == signature(job('quad_first'))


@pytest.mark.parametrize('case', ['planar', 'quadratic', 'explicit_mapped'])
def test_refusal_is_unchanged_outside_the_contract(case):
    project = plate() if case == 'planar' else cylinder()
    options = dict(order='quadratic' if case == 'quadratic' else 'linear',
                   strategy='mapped' if case == 'explicit_mapped' else 'auto',
                   structured_controls=StructuredMeshControls(maximum_estimated_elements=
                                                              40 if case == 'planar' else 400))
    # 6 x 5 m at 0.9 m: the area pre-estimate (38) passes, the exact seed solution (7 x 6 = 42) refuses.
    size = .9 if case == 'planar' else .25
    with pytest.raises(StructuredElementLimitExceeded) as caught:
        project.generate_mesh(size, **options)
    assert not getattr(caught.value, '__notes__', None)


def test_a_failing_fallback_keeps_the_original_refusal_as_context():
    project = default_project()
    commands.AddCone(1., .5, 5., longitudinal_spacing=.5,
                     beam_section='stiffener', plate_section='plate').do(project)
    with pytest.raises(QuadPublicUnsupported, match='source-boundary') as caught:
        project.generate_mesh(.25, order='linear', strategy='auto',
                              structured_controls=StructuredMeshControls(maximum_estimated_elements=20))
    assert isinstance(caught.value.__context__, StructuredElementLimitExceeded)
    assert any('fell back to quad_first' in note for note in caught.value.__notes__)


def test_cancellation_is_checked_before_the_fallback_runs():
    stages = []

    class Cancelled(Exception):
        pass

    def check(stage):
        stages.append(stage)
        if stage == 'automatic quad-first fallback':
            raise Cancelled

    with pytest.raises(Cancelled):
        cylinder().generate_mesh(.25, order='linear', strategy='auto', structured_controls=LIMIT,
                                 cancellation_check=check)
    assert stages[-1] == 'automatic quad-first fallback'
