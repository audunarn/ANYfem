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
