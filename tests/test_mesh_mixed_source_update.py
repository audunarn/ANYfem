"""Two independent, physically different source supports in one publication."""
from dataclasses import replace

import numpy as np
import pytest
from anygeometry import Plane
from anyfem import pinned
from anyfem.io.project_file import load_project
from anyfem.solve import build_fe_model
from anysolver.matrix_assembly import assemble_load_vector
from test_mesh_general_update import panel


@pytest.mark.parametrize('family,retained,height', [('nurbs','plane',2.), ('extruded','curved',0.),
                                                    ('nurbs','plane',0.)])
def test_mixed_source_update_after_reopen(tmp_path, family, retained, height):
    project, defaults, curved, _ = panel(family)
    geometry = project.geometry
    plane = geometry.add_plate(geometry.add_points(((4.,0.,height),(5.,0.,height),(5.,1.,height),(4.,1.,height))))
    geometry.add_sheet((plane,))
    project.assign_plate(plane, 'plate')
    assert type(geometry.faces[plane].surface) is Plane
    kept, edited = (plane,curved) if retained == 'plane' else (curved,plane)
    for f in (plane, curved):
        for use in geometry.faces[f].loop:
            project.add_support(pinned(project.edge(use.edge)))
    project.load_case().add_pressure(project.face(kept), 30.)
    settings = replace(defaults, strategy='auto',
                       controls=replace(defaults.controls, recombine=True, point_placement='frontal_delaunay'))
    session = project.mesh_session()
    original = session.generate_mesh(settings)
    baseline = project.mesh_records[session.mesh_record_id].summary['regional_baseline']
    assert set(baseline['reusable_faces']) == {str(plane), str(curved)}
    before_url = session.save(tmp_path/'before.anyfem')
    session.close()
    document = load_project(before_url)
    session = document.mesh_session(before_url)
    before = session.mesh
    assert before.shells == original.shells
    assert document.mesh_records[session.mesh_record_id].summary['regional_baseline']['digest'] == baseline['digest']
    def loaded_forces(model, mesh):
        built = build_fe_model(model, mesh)
        force, _ = assemble_load_vector(built.fe_model, built.load_case)
        return {n: force[built.node_dofs(n)] for e in mesh.elements_of_face[kept] for n in mesh.shells[e]}
    forces = loaded_forces(document, before)
    assert max(np.linalg.norm(f) for f in forces.values()) > 0
    settings = replace(settings, overrides=tuple((u.edge, 4) for u in geometry.faces[edited].loop))
    preview = session.preview_update(settings)
    assert preview.reuse_candidates == (kept,) and preview.regenerated == (edited,)
    child = session.update_mesh(settings)
    proof = document.mesh_records[session.mesh_record_id].summary['regional_baseline']['last_update']
    assert proof['reused_source_faces'] == [kept] and proof['generated_faces'] == [edited]
    assert child.elements_of_face[kept] == before.elements_of_face[kept]
    assert len(child.elements_of_face[edited]) != len(before.elements_of_face[edited])
    assert set(child.elements_of_face[edited]).isdisjoint(before.shells)
    for elem in child.elements_of_face[kept]:
        assert child.shells[elem] == before.shells[elem]
        for n in child.shells[elem]:
            np.testing.assert_array_equal(child.nodes[n], before.nodes[n])
    after_url = session.save(tmp_path/'after.anyfem')
    session.close()
    restored = load_project(after_url).mesh_session(after_url)
    assert restored.mesh.shells == child.shells
    assert restored.preview_update(settings).is_noop
    updated = loaded_forces(restored.project, restored.mesh)
    for n, expected in forces.items():
        np.testing.assert_array_equal(updated[n], expected)
