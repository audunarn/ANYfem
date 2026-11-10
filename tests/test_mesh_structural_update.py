"""Selected structural meshing must produce a solver-consumable beam/shell component."""
import json
from dataclasses import replace

import numpy as np
import pytest
from anymesher.serialize import mesh_to_dict

from anyfem.mesh_structural_update import (
    StructuralUpdateUnsupported, capture_structural_baseline, generate_structural_update, plan_structural_update,
)
from anyfem.mesh_jobs import MeshSettings
from anyfem.mesh_session import ProjectMeshSession
from anyfem.io.project_file import load_project
from anyfem.model import BeamSection
from anyfem.solve.build import build_fe_model
from test_regional_mesh import two_plates


def model(crossing=False):
    p, faces, edges = two_plates()
    p.add_beam_section(BeamSection(name="beam", profile="Flatbar", material="S355",
                                  flange_width=.01, flange_thickness=.1))
    edge = (p.geometry.add_line(*p.geometry.add_points([(.3, .4, -1.), (.3, .4, 1.)]))
            if crossing else edges[0][0])
    p.assign_beam(edge, "beam")
    return p, faces, edges, edge


@pytest.mark.parametrize("transport", [False, True])
def test_beam_on_plate_edge_regenerates_only_its_component_and_assembles(monkeypatch, transport):
    import anymesher.hybrid as hybrid
    project, faces, edges, beam_edge = model()
    settings = MeshSettings.create(.5, strategy="mapped", element_order="linear", quality_policy={})
    old = project.generate_mesh(.5, strategy="mapped", quality_policy={})
    baseline = capture_structural_baseline(project, settings, old)
    assert baseline is not None
    edited = replace(settings, overrides=((edges[0][1], 4),))
    calls = []
    generate = hybrid.generate_hybrid_mesh_result

    def selected(geometry, **options):
        calls.append((tuple(options["face_ids"]), tuple(options["member_ids"]), len(geometry.faces)))
        return generate(geometry, **options)

    monkeypatch.setattr(hybrid, "generate_hybrid_mesh_result", selected)
    result = generate_structural_update(project, edited, json.loads(json.dumps(baseline)),
                                        mesh_to_dict(old) if transport else old,
                                        node_high_water=100, element_high_water=200)
    mesh = result.mesh
    assert calls == [((1,), (1,), 2)]
    assert result.plan.changed == (0,)
    assert set(result.retained_elements) == set(old.elements_of_face[faces[1]])
    assert mesh.elements_of_face[faces[1]] == old.elements_of_face[faces[1]]
    for e in result.retained_elements:
        assert mesh.quads[e] == old.quads[e]
        for n in old.quads[e]:
            np.testing.assert_array_equal(mesh.nodes[n], old.nodes[n])
    assert len(mesh.elements_of_face[faces[0]]) == 8
    assert min(set(mesh.nodes) - set(old.nodes)) > 100
    assert min((mesh.quads.keys() | mesh.beams.keys()) - result.retained_elements) > 200
    assert set(mesh.elements_of_edge[beam_edge]) == set(mesh.beams)
    assert set(mesh.nodes_of_edge[beam_edge]) <= {n for e in mesh.elements_of_face[faces[0]] for n in mesh.quads[e]}
    built = build_fe_model(project, mesh, load_case=None, require_loads=False, require_supports=False)
    assert len(built.fe_model.mesh.elements) == len(mesh.quads) + len(mesh.beams)
    from anysolver.matrix_assembly import assemble_stiffness_matrix
    initial = build_fe_model(project, old, load_case=None, require_loads=False, require_supports=False).fe_model
    old_k, _ = assemble_stiffness_matrix(initial)
    new_k, _ = assemble_stiffness_matrix(built.fe_model)
    dofs = lambda fe: [i for n in sorted(result.retained_nodes) for i in fe.mesh.nodes[n].dofs]
    a, b = dofs(initial), dofs(built.fe_model)
    np.testing.assert_array_equal(old_k[a][:, a].toarray(), new_k[b][:, b].toarray())
    assert np.isfinite(new_k.data).all() and new_k.nnz > 0
    assert mesh.structural_preparation["source_to_working_faces"] == {"1": [1], "2": [2]}
    assert mesh.automatic_beam_connections == len(mesh.nodes_of_edge[beam_edge])
    assert plan_structural_update(project, edited, result.baseline).is_noop


def test_created_or_removed_intersection_refuses_before_meshing():
    project, _, _, _ = model()
    settings = MeshSettings.create(.5, strategy="mapped", element_order="linear")
    old = project.generate_mesh(.5, strategy="mapped")
    baseline = capture_structural_baseline(project, settings, old)
    edge = project.geometry.add_line(*project.geometry.add_points([(.3, .4, -1.), (.3, .4, 1.)]))
    with pytest.raises(StructuralUpdateUnsupported, match="Loose construction edges"):
        plan_structural_update(project, settings, baseline)
    project.assign_beam(edge, "beam")
    with pytest.raises(StructuralUpdateUnsupported, match="intersection|source edge"):
        plan_structural_update(project, settings, baseline)


def test_saved_session_structural_update_and_explicit_user_quality(tmp_path):
    from anyfem.regional_mesh import RegionalUpdateError

    project, _, edges, _ = model()
    s = MeshSettings.create(.5, element_order="linear", strategy="mapped",
                            quality_policy={"maximum_aspect_ratio": 1.1})
    session = ProjectMeshSession(project)
    prior = session.generate_mesh(s)
    path = session.save(tmp_path / "beam-plate.anyfem")
    restored = ProjectMeshSession(load_project(path), path)
    with pytest.raises(RegionalUpdateError, match="quality"):
        restored.update_mesh(replace(s, overrides=((edges[0][1], 4),)))
    assert restored.mesh_record_id == session.mesh_record_id
    assert restored.mesh.quads == prior.quads
    # Re-seed both plate directions uniformly: the explicit quality rule
    # now admits the square quads while the beam station count changes too.
    settings = replace(s, overrides=((edges[0][0], 4), (edges[0][1], 4)))
    child = restored.update_mesh(settings)
    assert len(child.quads) == 20 and len(child.beams) == 4
    assert child.automatic_beam_connections == 5
    restored.save()
    reopened = ProjectMeshSession(load_project(path), path)
    assert reopened.mesh.quads == child.quads and reopened.mesh.beams == child.beams
    assert reopened.preview_update(settings).is_noop


def test_saved_session_creates_then_removes_beam_plate_intersection(tmp_path):
    project, faces, _ = two_plates()
    g = project.geometry
    project.add_beam_section(BeamSection(name="beam", profile="Flatbar", material="S355",
                                        flange_width=.01, flange_thickness=.1))
    endpoints = g.add_points([(3., .5, -1.), (3., .5, 1.)])
    beam = g.add_line(*endpoints)
    project.assign_beam(beam, "beam")
    settings = MeshSettings.create(.5, strategy="mapped", element_order="linear",
                                   quality_policy={"maximum_aspect_ratio": 1.1})
    session = ProjectMeshSession(project)
    old = session.generate_mesh(settings)
    path = session.save(tmp_path / "moving-member.anyfem")
    for x, count in ((.5, 1), (3., 0)):
        session = ProjectMeshSession(load_project(path), path)
        g = session.project.geometry
        for vertex, z in zip(endpoints, (-1., 1.)):
            g.move_point(vertex, x, .5, z)
        plan = session.preview_update(settings)
        assert f"Regenerate plate {faces[0]}" in plan.description
        new = session.update_mesh(settings)
        assert new.geometry_revision == g.revision
        assert new.automatic_beam_connections == count
        assert new.elements_of_face[faces[1]] == old.elements_of_face[faces[1]]
        for e in new.elements_of_face[faces[1]]:
            assert old.quads[e] == new.quads[e]
            for n in old.quads[e]:
                np.testing.assert_array_equal(old.nodes[n], new.nodes[n])
        assert set(new.elements_of_face[faces[0]]).isdisjoint(old.elements_of_face[faces[0]])
        assert set(new.beams).isdisjoint(old.beams)
        overlap = set(new.nodes_of_edge[beam]) & {n for e in new.elements_of_face[faces[0]] for n in new.quads[e]}
        assert len(overlap) == count
        if count:
            np.testing.assert_array_equal(new.nodes[overlap.pop()], [.5, .5, 0.])
        assert len(new.structural_preparation["source_to_working_faces"][str(faces[0])]) == (4 if count else 1)
        assert len(new.structural_preparation["source_to_working_edges"][str(beam)]) == (2 if count else 1)
        built = build_fe_model(session.project, new, load_case=None, require_loads=False, require_supports=False)
        from anysolver.matrix_assembly import assemble_stiffness_matrix
        stiffness, _ = assemble_stiffness_matrix(built.fe_model)
        assert np.isfinite(stiffness.data).all() and stiffness.nnz > 0
        session.save()
        old = new
    # Proximity alone cannot authorise a weld. Stop just above the plate and
    # verify the saved parent remains active after the rejected update.
    session = ProjectMeshSession(load_project(path), path)
    g = session.project.geometry
    for v, z in zip(endpoints, (.1, 1.)):
        g.move_point(v, .5, .5, z)
    parent = session.mesh_record_id
    with pytest.raises(StructuralUpdateUnsupported, match="cross the plate plane"):
        session.update_mesh(settings)
    assert session.mesh_record_id == parent and session.mesh.beams == old.beams


def test_selected_tee_reseeding_preserves_distant_plate_and_rebuilds_junction(tmp_path):
    from anyfem import Project, steel
    from anysolver.matrix_assembly import assemble_stiffness_matrix

    project = Project("isolated web and flange")
    project.add_material(steel("S355", .01))
    project.add_plate_section("plate", thickness=.01, material="S355")
    faces, edges = [], []
    for points in ([(0, 0, 0), (2, 0, 0), (2, 1, 0), (0, 1, 0)],
                   [(1, 0, 0), (1, 1, 0), (1, 1, 1), (1, 0, 1)],
                   [(5, 0, 0), (6, 0, 0), (6, 1, 0), (5, 1, 0)]):
        boundary = project.geometry.add_polyline(project.geometry.add_points(points), close=True)
        face = project.geometry.add_face(boundary)
        project.assign_plate(face, "plate")
        faces.append(face)
        edges.append(boundary)
    flange, web, distant = faces
    settings = MeshSettings.create(.5, strategy="mapped", element_order="linear",
                                   quality_policy={"maximum_aspect_ratio": 2.1})
    session = ProjectMeshSession(project)
    previous = session.generate_mesh(settings)
    path = session.save(tmp_path / "tee.anyfem")
    session = ProjectMeshSession(load_project(path), path)
    updated = replace(settings, overrides=((edges[1][1], 4),))
    assert "Regenerate T-junction plates" in session.preview_update(updated).description
    child = session.update_mesh(updated)
    assert len(child.quads) == 20 and len(child.elements_of_face[web]) == 8
    assert child.elements_of_face[distant] == previous.elements_of_face[distant]
    for e in previous.elements_of_face[distant]:
        assert child.quads[e] == previous.quads[e]
        for n in child.quads[e]:
            np.testing.assert_array_equal(child.nodes[n], previous.nodes[n])
    assert child.automatic_intersections == 1 and len(child.declared_plate_junction_edges) == 2
    assert child.structural_preparation["source_to_working_faces"][str(flange)] == [4, 5]
    sides = {tuple(sorted(p)) for p in child.declared_plate_junction_edges}
    assert sides.isdisjoint(tuple(sorted(p)) for p in previous.declared_plate_junction_edges)
    chain = child.nodes_of_edge[edges[1][0]]
    assert sides == {tuple(sorted(p)) for p in zip(chain, chain[1:])}
    for pair in sides:
        incident = [face for face, cells in child.elements_of_face.items()
                    for e in cells if set(pair) <= set(child.quads[e])]
        assert sorted(incident) == sorted([flange, flange, web])
    built = build_fe_model(session.project, child, load_case=None, require_loads=False, require_supports=False)
    stiffness, _ = assemble_stiffness_matrix(built.fe_model)
    assert stiffness.nnz and np.isfinite(stiffness.data).all()
    session.save()
    session = ProjectMeshSession(load_project(path), path)
    assert session.preview_update(updated).is_noop
    assert sides == {tuple(sorted(p)) for p in session.mesh.declared_plate_junction_edges}
    # One flange edge became two prepared occurrences. A scalar division
    # count on it has no unique distribution over those children.
    record_id = session.mesh_record_id
    with pytest.raises(StructuralUpdateUnsupported, match="unique unchanged source occurrence"):
        session.update_mesh(replace(updated, overrides=((edges[0][0], 5),)))
    assert session.mesh_record_id == record_id
    assert sides == {tuple(sorted(p)) for p in session.mesh.declared_plate_junction_edges}
