"""A persisted curved surface with a point refinement and a fixed interior cut."""
from contextlib import contextmanager
from dataclasses import replace

import numpy as np
import pytest

from anyfem import pinned, solve_linear_static
from anyfem.io.project_file import load_project
from anyfem.mesh_general_update import GeneralSurfaceUnsupported
from anyfem.mesh_preview import mesh_update_preview
from anyfem.selection import MeshEntityRef
from anyfem.solve import build_fe_model
from anymesher.refinement import Refinement
from test_mesh_general_update import panel


@pytest.fixture(scope="module")
def published_panel(tmp_path_factory):
    project, defaults, face, edges = panel("nurbs")
    for edge in edges:
        project.add_support(pinned(project.edge(edge)))
    project.load_case().add_pressure(project.face(face), 1.e4)
    settings = replace(defaults, strategy="auto", target_size=.2,
                       controls=replace(defaults.controls, recombine=True))
    session = project.mesh_session()
    mesh = session.generate_mesh(settings)
    path = session.save(tmp_path_factory.mktemp("source") / "curved.anyfem")
    assert len(mesh.quads) >= 25 and not mesh.tris
    session.close()
    return path, settings, face


def _point_edit(published):
    path, settings, face = published
    project = load_project(path)
    session = project.mesh_session()
    old = session.mesh
    support = project.geometry.faces[face].surface
    # The original is an ordinary bilinear chart (x=u, y=v). Locate a child
    # centroid on the actual old grid, without assuming a generator row number.
    uvs = {e: np.asarray([old.nodes[n][:2] for n in conn]) for e, conn in old.quads.items()}
    middle = min(uvs.values(), key=lambda uv: np.linalg.norm(uv.mean(axis=0) - [.5, .5]))
    uv = (middle.mean(axis=0) + middle[0]) / 2
    xyz = support.evaluate(*uv)
    width = max(np.linalg.norm(middle - np.roll(middle, 1, axis=0), axis=1))
    zone = Refinement(center=xyz, size=.54 * width, radius=.03 * width, growth=8., name="contact point")
    project.add_refinement(zone)
    return session, settings, face, old, zone


def test_curved_point_patch_exact_mesh_preview_numbering_and_reopen(published_panel, tmp_path):
    session, settings, face, old, zone = _point_edit(published_panel)
    parent = session.mesh_record_id
    baseline = session.project.mesh_records[parent].summary["regional_baseline"]
    plan = session.preview_update(settings)
    assert plan.patched_face == face and plan.regenerated == () and not plan.is_noop
    assert len(plan.replaced_elements) == 7 and len(plan.interface_nodes) == 12
    preview = mesh_update_preview(plan, baseline, old, parent)
    assert preview.owners == tuple(MeshEntityRef("element", e, parent) for e in plan.replaced_elements)
    assert not preview.review_prepared_cells
    numbering = session.project.mesh_numbering
    child = session.update_mesh(settings)
    retired = set(plan.replaced_elements)
    kept = set(old.shells) - retired
    added = set(child.shells) - kept
    assert set(old.shells) & set(child.shells) == kept
    assert all(e > numbering.element_high_water for e in added)
    retained_nodes = {n for e in kept for n in old.shells[e]}
    assert old.nodes.keys() & child.nodes.keys() == retained_nodes
    assert all(n > numbering.node_high_water for n in child.nodes.keys() - old.nodes.keys())
    for e in kept:
        assert child.shells[e] == old.shells[e]
    for n in retained_nodes:
        np.testing.assert_array_equal(child.nodes[n], old.nodes[n])
    assert child.nodes_of_edge == old.nodes_of_edge and child.node_of_vertex == old.node_of_vertex
    assert len(added & child.quads.keys()) == 22 and not child.tris
    assert set(child.elements_of_face[face]) == kept | added
    achieved = []
    for e in added:
        xyz = np.asarray([child.nodes[n] for n in child.shells[e]])
        if np.linalg.norm(xyz.mean(axis=0) - zone.center) <= zone.radius:
            achieved.append(max(np.linalg.norm(xyz - np.roll(xyz, 1, axis=0), axis=1)))
    assert achieved and min(achieved) <= zone.size
    record = session.project.mesh_records[session.mesh_record_id]
    evidence = record.summary["regional_baseline"]["last_update"]
    assert evidence["impact"] == plan.impact and evidence["new_elements"] == sorted(added)
    assert evidence["generation_scope"] == "fixed interior cell patch"
    assert evidence["quality"]["num_shell_elements"] == len(child.shells)
    measured, = evidence["sizing"][str(face)]["point_zones"]
    assert measured["min_longest_edge"] == min(achieved) and measured["requested_size"] == zone.size
    from anysolver.assembly import compute_reactions
    from anysolver.matrix_assembly import assemble_load_vector

    built = build_fe_model(session.project, child)
    solved = solve_linear_static(built=built)
    displacement = {n: solved.node_displacement(n) for n in child.nodes}
    assert np.isfinite(list(displacement.values())).all() and max(abs(d[2]) for d in displacement.values()) > 0
    forces, _ = assemble_load_vector(built.fe_model, built.load_case)
    resultant = sum((forces[built.node_dofs(n)[:3]] for n in child.nodes), np.zeros(3))
    reactions = compute_reactions(built.fe_model, solved.displacements, built.load_case)
    np.testing.assert_allclose(np.sum([r[:3] for r in reactions.values()], axis=0), -resultant,
                               rtol=1.e-8, atol=1.e-6)
    assert session.preview_update(settings).is_noop
    path = session.save(tmp_path / "refined.anyfem")
    session.close()
    restored = load_project(path).mesh_session()
    assert restored.mesh.shells == child.shells
    assert restored.preview_update(settings).is_noop
    reopened_result = solve_linear_static(built=build_fe_model(restored.project, restored.mesh))
    for node, value in displacement.items():
        np.testing.assert_array_equal(reopened_result.node_displacement(node), value)


def test_point_patch_rejects_changed_interface_atomically(published_panel, monkeypatch):
    from anymesher import regional_source_faces as owner

    session, settings, face, old, _ = _point_edit(published_panel)
    parent, allocation = session.mesh_record_id, session.project.mesh_numbering
    originals = set(session.project.mesh_records)
    assert session.preview_update(settings).patched_face == face
    reuse = owner.reuse_source_faces

    @contextmanager
    def corrupt(*args, **kwargs):
        with reuse(*args, **kwargs) as stats:
            yield stats
            receipt = stats["patched_faces"][face]
            receipt["interface_rows"] = receipt["interface_rows"][1:]

    monkeypatch.setattr(owner, "reuse_source_faces", corrupt)
    with pytest.raises(GeneralSurfaceUnsupported, match="previewed cells or interface"):
        session.update_mesh(settings)
    assert session.mesh_record_id == parent and session.project.mesh_numbering == allocation
    assert session.mesh.shells == old.shells and set(session.project.mesh_records) == originals


def test_rim_point_previews_whole_face(published_panel):
    path, settings, face = published_panel
    project = load_project(path)
    project.add_refinement(Refinement(center=(0., 0., 0.), size=.04, radius=.01))
    session = project.mesh_session()
    plan = session.preview_update(settings)
    assert plan.patched_face is None and not plan.replaced_elements and plan.regenerated == (face,)
    assert "whole faces" in plan.description and "no fixed interior cut" in plan.description


def test_rehashed_foreign_parent_permutation_stops_before_meshing(published_panel, monkeypatch):
    from anyfem import regional_mesh
    from anyfem.mesh_general_update import generate_general_update
    from anymesher import regional_source_faces
    from copy import deepcopy

    session, settings, _, mesh, _ = _point_edit(published_panel)
    parent = deepcopy(session.project.mesh_records[session.mesh_record_id].summary["regional_baseline"])
    rows = parent["point_patch_parent"]["cells"]
    rows[0], rows[-1] = rows[-1], rows[0]
    parent["digest"] = regional_mesh._digest({k: v for k, v in parent.items() if k != "digest"})
    monkeypatch.setattr(regional_source_faces, "reuse_source_faces", lambda *a, **kw: pytest.fail("mesher entered"))
    state = session.project.mesh_numbering
    with pytest.raises(GeneralSurfaceUnsupported, match="oriented source cells"):
        generate_general_update(session.project, settings, parent, mesh, node_high_water=state.node_high_water,
                                element_high_water=state.element_high_water)


def test_cancel_while_proving_exterior_preserves_parent(published_panel):
    session, settings, _, old, _ = _point_edit(published_panel)
    parent, allocation = session.mesh_record_id, session.project.mesh_numbering

    class CancelExterior:
        def raise_if_cancelled(self, stage):
            if stage == "source patch exterior sizing bounds":
                raise InterruptedError("point patch cancelled")

    with pytest.raises(InterruptedError, match="point patch cancelled"):
        session.update_mesh(settings, cancellation=CancelExterior())
    assert session.mesh_record_id == parent and session.project.mesh_numbering == allocation
    assert session.mesh.shells == old.shells
