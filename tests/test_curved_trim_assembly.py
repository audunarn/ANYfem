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
