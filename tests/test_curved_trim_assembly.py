from dataclasses import replace

import numpy as np
import pytest

from anyfem import pinned
from anyfem.io.project_file import load_project
from anyfem.solve import build_fe_model
from anyfesolver.matrix_assembly import assemble_load_vector
from test_mesh_general_update import panel
from test_mesh_source_trim import _edit_hole


@pytest.fixture(scope="module")
def curved_assembly(tmp_path_factory):
    project, settings, face, _ = panel("nurbs", second=True)
    remote, = set(project.geometry.faces) - {face}
    for definition in project.geometry.faces.values():
        for use in definition.loop:
            project.add_support(pinned(project.edge(use.edge)))
    project.load_case().add_pressure(project.face(remote), 30.)
    settings = replace(settings, strategy="auto", target_size=.2, controls=replace(settings.controls, recombine=True))
    session = project.mesh_session()
    session.generate_mesh(settings)
    path = session.save(tmp_path_factory.mktemp("assembly") / "parent.anyfem")
    session.close()
    return path, settings, face


def _solver_face(project, mesh, face):
    built = build_fe_model(project, mesh)
    loads, _ = assemble_load_vector(built.fe_model, built.load_case)
    cells = {e: tuple(built.fe_model.mesh.elements[e].node_ids) for e in mesh.elements_of_face[face]}
    nodal_loads = {n: loads[built.node_dofs(n)] for cell in cells.values() for n in cell}
    return cells, nodal_loads


@pytest.mark.parametrize("crossing", [False, True], ids=["cell-aligned", "cross-cell"])
def test_local_trim_preserves_second_panel_through_solver_and_reopen(curved_assembly, tmp_path, crossing):
    path, _, face = curved_assembly
    parent = load_project(path).mesh_session()
    remote, = set(parent.project.geometry.faces) - {face}
    old_cells, old_loads = _solver_face(parent.project, parent.mesh, remote)
    assert any(np.any(v) for v in old_loads.values())
    session, old, edited, _, _, _, _ = _edit_hole(curved_assembly, aligned=not crossing, crossing=crossing)
    plan = session.preview_update(edited)
    assert plan.patched_face == face and plan.retained == plan.reuse_candidates == (remote,)
    assert set(plan.replaced_elements) < set(old.elements_of_face[face])
    new = session.update_mesh(edited)
    assert new.elements_of_face[remote] == old.elements_of_face[remote]
    for e in old.elements_of_face[remote]:
        assert new.shells[e] == old.shells[e]
        for n in new.shells[e]:
            np.testing.assert_array_equal(new.nodes[n], old.nodes[n])
    evidence = session.project.mesh_records[session.mesh_record_id].summary["regional_baseline"]["last_update"]
    assert evidence["reused_source_faces"] == [remote] and evidence["patched_face"] == face
    saved = session.save(tmp_path / "child.anyfem")
    reopened = load_project(saved).mesh_session()
    cells, loads = _solver_face(reopened.project, reopened.mesh, remote)
    assert cells == old_cells
    for n in old_loads:
        np.testing.assert_array_equal(loads[n], old_loads[n])
    # In the next update the perforated panel becomes the unchanged one.
    remote_edges = [u.edge for u in reopened.project.geometry.faces[remote].loop]
    next_settings = replace(edited, overrides=tuple(sorted((*edited.overrides, *((e, 7) for e in remote_edges)))))
    next_plan = reopened.preview_update(next_settings)
    assert next_plan.regenerated == (remote,) and next_plan.reuse_candidates == (face,)
    previous_cells = {e: new.shells[e] for e in new.elements_of_face[face]}
    next_mesh = reopened.update_mesh(next_settings)
    assert next_mesh.elements_of_face[face] == new.elements_of_face[face]
    for e, conn in previous_cells.items():
        assert next_mesh.shells[e] == conn
        for n in conn:
            np.testing.assert_array_equal(next_mesh.nodes[n], new.nodes[n])
    for use in reopened.project.geometry.faces[face].holes[0]:
        assert next_mesh.nodes_of_edge[use.edge] == new.nodes_of_edge[use.edge]


def test_trim_and_independent_reseed_cannot_publish_as_one_local_cut(curved_assembly):
    session, _, edited, face, _, _, _ = _edit_hole(curved_assembly, aligned=True)
    project = session.project
    remote, = set(project.geometry.faces) - {face}
    edge = project.geometry.faces[remote].loop[0].edge
    edited = replace(edited, overrides=tuple(sorted((*edited.overrides, (edge, 9)))))
    old_state = project.mesh_numbering
    impact = session.preview_update(edited)
    assert impact.patched_face is None and set(impact.regenerated) == {face, remote}
    assert project.mesh_numbering == old_state
