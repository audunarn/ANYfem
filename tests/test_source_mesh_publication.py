"""The worker must number live curved cells before its process exits."""
from time import monotonic, sleep

import numpy as np
import pytest

from anyfem import pinned, solve_linear_static
from anyfem.io.project_file import load_project
from anyfem.mesh_jobs import MeshTaskManager
from anyfem.solve.build import build_fe_model
from test_mesh_general_update import panel
from test_mesh_publication_lifecycle import FileWorkbench


def _published(app, job):
    deadline = monotonic() + 90
    while app.mesh_task_manager.busy and monotonic() < deadline:
        app._poll_mesh_jobs()
        sleep(.025)
    assert not app.mesh_task_manager.busy
    record = app.project.mesh_records[job.id]
    assert record.status == 'completed', record.diagnostics
    assert app.mesh_record_id == job.id and 'regional_baseline' in record.summary
    return app.mesh


def test_numbered_curved_worker_artifact_can_solve_after_reopen(tmp_path):
    project, settings, face, edges = panel('extruded')
    project.element_order = 'linear'
    for edge in edges:
        project.add_support(pinned(project.edge(edge)))
    project.load_case().add_pressure(project.face(face), 1.e4)
    app = FileWorkbench(project, mesh_task_manager_factory=lambda: MeshTaskManager(executor='process'))
    parent = app.generate_mesh(settings.target_size, strategy=settings.strategy, mesh_controls=settings.controls)
    try:
        options = dict(strategy=settings.strategy, mesh_controls=settings.controls)
        new = _published(app, app.update_mesh_async(.21, **options))
        assert min(new.quads) > max(parent.quads) and min(new.nodes) > max(parent.nodes)
        assert len(new.quads) > len(parent.quads)
        before = solve_linear_static(built=build_fe_model(project, new))
        path = tmp_path / 'curved.anyfem'
        app.save_project(path=str(path))
        restored = load_project(path).mesh_session()
        after = solve_linear_static(built=build_fe_model(restored.project, restored.mesh))
        assert restored.mesh.quads == new.quads
        assert np.any([np.linalg.norm(before.node_displacement(n)) > 0 for n in new.nodes])
        for n in new.nodes:
            np.testing.assert_array_equal(before.node_displacement(n), after.node_displacement(n))
        rebuilt = _published(app, app.generate_mesh_async(.21, **options))
        assert min(rebuilt.quads) > max(new.quads) and min(rebuilt.nodes) > max(new.nodes)
        full = solve_linear_static(built=build_fe_model(project, rebuilt))
        by_point = {tuple(point): n for n, point in rebuilt.nodes.items()}
        assert len(by_point) == len(new.nodes) == len(rebuilt.nodes)
        for n, point in new.nodes.items():
            np.testing.assert_allclose(before.node_displacement(n), full.node_displacement(by_point[tuple(point)]),
                                       rtol=1.e-9, atol=1.e-12)
    finally:
        app.mesh_task_manager.shutdown()
        if app._project_lock:
            app._project_lock.release()


def test_source_triangle_solver_receives_issued_normals_and_rejects_foreign_transfer():
    from anyfem.model.project import ProjectError
    from anyfem.solve.build import _qualified_s3_owner_normals
    from anysolver.e4_pl_s3_v2d_element import NativeParityCapabilityError

    project, settings, face, edges = panel('nurbs')
    for edge in edges:
        project.add_support(pinned(project.edge(edge)))
    project.load_case().add_pressure(project.face(face), 1.e4)
    mesh = project.mesh_session().generate_mesh(settings)
    built = build_fe_model(project, mesh)
    with pytest.raises(NativeParityCapabilityError, match='not facet-normal'):
        solve_linear_static(built=built)
    owners = mesh.structural_preparation['qualified_s3']['element_owner_sources']
    owner = owners[str(next(iter(mesh.tris)))]
    owner['sheet_ids'][0] += 1
    with pytest.raises(ProjectError, match='content differs'):
        build_fe_model(project, mesh)
    with pytest.raises(ProjectError, match='source face uses differ'):
        _qualified_s3_owner_normals(project, mesh)
    owner['sheet_ids'][0] -= 1
    mesh.structural_preparation['closure_source_transfer']['source_model_id'] = 'foreign'
    with pytest.raises(ProjectError, match='matching closure publication'):
        _qualified_s3_owner_normals(project, mesh)


def test_automatic_planar_nurbs_uses_owner_approximation_and_rebuilds():
    from dataclasses import replace
    from anyfem.document import canonical_hash
    from anyfem.mesh_controls import MeshControls
    from anyfem.mesh_jobs import MeshSettings

    project, _, face, _ = panel('nurbs', rise=0.)
    project.native_mesh_settings = None
    session = project.mesh_session()
    settings = MeshSettings.create(.5, strategy='auto', element_order='linear')
    first = session.generate_mesh(settings)
    assert first.hybrid_diagnostics['geometry_approximation']['mode'] == 'automatic'
    record = project.mesh_records[session.mesh_record_id]
    assert record.summary.get('regional_baseline'), record.summary.get('regional_refusal')
    def input_hash(candidate):
        return canonical_hash({'mesh_settings': candidate.input_hash,
                               'native_backend': project.native_triangulation_backend})
    assert record.mesh_input_hash == input_hash(settings)
    assert record.mesh_input_hash != input_hash(replace(settings, controls=MeshControls()))
    second = session.generate_mesh(settings)
    assert second.elements_of_face[face]
    assert min(second.shells) > max(first.shells) and min(second.nodes) > max(first.nodes)
