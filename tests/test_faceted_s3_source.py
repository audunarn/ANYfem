from dataclasses import replace

import numpy as np
import pytest

from anyfem import pinned, solve_linear_static
from anyfem.io.project_file import load_project
from anyfem.model.formulations import ShellFormulationPolicy
from anyfem.solve.build import build_fe_model
from anysolver.assembly import compute_reactions
from anysolver.matrix_assembly import assemble_load_vector
from test_mesh_general_update import panel


def test_faceted_v2d_source_update_and_pressure_equilibrium(tmp_path):
    project, settings, face, edges = panel('nurbs')
    project.shell_formulation_policy = replace(project.shell_formulation_policy, s3_reference_surface='faceted')
    for edge in edges:
        project.add_support(pinned(project.edge(edge)))
    project.load_case().add_pressure(project.face(face), 1.e4)
    session = project.mesh_session()
    old = session.generate_mesh(settings)
    mesh = session.update_mesh(replace(settings, target_size=.30))
    assert min(mesh.tris) > max(old.tris)
    built = build_fe_model(project, mesh)
    sources = mesh.structural_preparation['qualified_s3']['element_owner_normals']
    distinct = False
    for eid, nodes in mesh.tris.items():
        v0, v1, v2 = (mesh.nodes[n] for n in nodes)
        facet = np.cross(v1-v0, v2-v0)
        facet /= np.linalg.norm(facet)
        source = sources[str(eid)]
        distinct |= np.linalg.norm(facet-source) > 1.e-6
        assert np.dot(facet, source) > 0
        np.testing.assert_allclose(built.fe_model.mesh.elements[eid].physical_reference_director, facet, atol=1.e-13)
    assert distinct
    solved = solve_linear_static(built=built)
    assert any(abs(solved.node_displacement(n)[2]) > 0 for n in mesh.nodes)
    forces, _ = assemble_load_vector(built.fe_model, built.load_case)
    reactions = compute_reactions(built.fe_model, solved.displacements, built.load_case)
    resultant = sum((forces[built.node_dofs(n)[:3]] for n in mesh.nodes), np.zeros(3))
    # The boundary of z=x*y/8 consists of straight segments. Integrating the
    # positively oriented area vector gives (-1/16,-1/16,1) on both surfaces.
    np.testing.assert_allclose(resultant, 1.e4*np.array([-1/16, -1/16, 1.]), rtol=1.e-12, atol=1.e-8)
    np.testing.assert_allclose(sum((r[:3] for r in reactions.values()), np.zeros(3)), -resultant,
                               rtol=1.e-8, atol=1.e-6)
    path = session.save(tmp_path/'facets.anyfem')
    reopened = load_project(path).mesh_session()
    assert reopened.project.shell_formulation_policy == project.shell_formulation_policy
    repeated = solve_linear_static(built=build_fe_model(reopened.project, reopened.mesh))
    for n in mesh.nodes:
        np.testing.assert_array_equal(repeated.node_displacement(n), solved.node_displacement(n))


def test_faceted_v2d_refuses_grazing_source_normal_before_constructing_shells(monkeypatch):
    from anymesher import Mesh
    from anyfem.model import ProjectError
    from anyfem.solve import build

    project, _, _, _ = panel('nurbs')
    project.shell_formulation_policy = replace(project.shell_formulation_policy, s3_reference_surface='faceted')
    mesh = Mesh()
    mesh.nodes = {1: np.array([0., 0., 0.]), 2: np.array([1., 0., 0.]), 3: np.array([0., 1., 0.])}
    mesh.tris = {17: (1, 2, 3)}
    grazing = np.array([1., 0., 1.e-8])
    monkeypatch.setattr(build, '_qualified_s3_owner_normals', lambda *_: ([17], {17: grazing / np.linalg.norm(grazing)}))
    with pytest.raises(ProjectError, match='alignment|signed area'):
        build._add_shells(project, mesh, None)


def test_reference_surface_cannot_enter_a_legacy_formulation_or_older_record():
    with pytest.raises(ValueError, match='V2D'):
        replace(ShellFormulationPolicy.legacy_compatible(), s3_reference_surface='faceted')
    old = ShellFormulationPolicy.current_default().to_dict()
    old['s3_reference_surface'] = 'faceted'
    with pytest.raises(ValueError, match='exactly'):
        ShellFormulationPolicy.from_dict(old)


def test_mesh_panel_applies_and_undoes_explicit_triangle_reference(monkeypatch):
    import os
    if os.environ.get("ANYFEM_RUN_QT_TESTS") != "1":
        pytest.skip("requires the Qt test profile")
    from PySide6.QtWidgets import QApplication
    from anyfem.ui.qt.tasks import MeshTask
    from test_mesh_publication_lifecycle import FileWorkbench
    from anyfem.commands import SetS3ReferenceSurface

    gui = QApplication.instance() or QApplication([])
    project, _, _, _ = panel('nurbs')
    project.element_order = 'linear'
    app = FileWorkbench(project)
    task = MeshTask(app)
    task.reset_mesh_drafts()
    calls = []
    def execute(change):
        calls.append(change)
        change.do(project)
    monkeypatch.setattr(app, "run", execute)
    monkeypatch.setattr(app, "generate_mesh_async", lambda **kwargs: calls.append(project.shell_formulation_policy))
    try:
        task.s3_reference.setCurrentIndex(task.s3_reference.findData("faceted"))
        with pytest.raises(ValueError, match="reference surface changed"):
            task.start_update()
        assert project.shell_formulation_policy.s3_reference_surface == 'source'
        task.start()
        assert isinstance(calls[0], SetS3ReferenceSurface) and calls[1].s3_reference_surface == 'faceted'
        calls[0].undo(project)
        assert project.shell_formulation_policy.s3_reference_surface == 'source'
    finally:
        app.mesh_task_manager.shutdown()
        task.close()
