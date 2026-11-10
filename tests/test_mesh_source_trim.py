from dataclasses import asdict, replace
import json
import os

import numpy as np
import pytest

from anygeometry.entities import OrientedEdge
from anygeometry.nurbs import CurveOnSurface, NurbsCurve
from anygeometry.operations import trim_face
from anyfem import solve_linear_static
from anyfem.io.project_file import load_project
from anyfem.solve import build_fe_model
from test_mesh_general_interior import published_panel
from test_mesh_general_update import panel


@pytest.fixture(scope="module")
def published_crossing_panel(tmp_path_factory):
    from anyfem import pinned

    project, defaults, face, edges = panel("nurbs")
    for e in edges:
        project.add_support(pinned(project.edge(e)))
    project.load_case().add_pressure(project.face(face), 1.e4)
    # The smallest four-cell footprint has a curved exterior side on every
    # new Q4. This target admits that footprint on a five-division parent.
    settings = replace(defaults, strategy="auto", target_size=.205,
                       controls=replace(defaults.controls, recombine=True))
    session = project.mesh_session()
    assert len(session.generate_mesh(settings).quads) == 25
    path = session.save(tmp_path_factory.mktemp("crossing")/"parent.anyfem")
    session.close()
    return path, settings, face


def _edit_hole(published_panel, *, aligned, crossing=False):
    path, settings, face = published_panel
    project = load_project(path)
    session = project.mesh_session()
    old = session.mesh
    baseline = project.mesh_records[session.mesh_record_id].summary['regional_baseline']
    data = baseline['reusable_faces'][str(face)]['data']
    identity = (baseline['point_patch_parent'] if 'point_patch_parent' in baseline
                else baseline['patch_parents'][str(face)])
    uv = np.asarray([row[0] for row in data['node_rows']])
    index = min(range(len(data['cells'])), key=lambda i:
                np.linalg.norm(uv[data['cells'][i]].mean(axis=0) - [.5,.5]))
    removed = identity['cells'][index]
    hole = [uv[row] for row in reversed(data['cells'][index])]
    if crossing:
        # Each corner is in a different parent Q4 around the interior grid
        # vertex (.4,.4). These exact pcurves cross both old grid directions.
        hole = [(78/256,82/256), (82/256,128/256), (129/256,124/256), (125/256,78/256)]
    elif not aligned:
        # Dyadic source-chart values strictly inside the centre parent's
        # [.4,.6] x [.4,.6] chart footprint. The hole has its own four nodes.
        hole = [(116/256,117/256), (117/256,141/256), (141/256,139/256), (139/256,115/256)]
    g = project.geometry
    support = g.faces[face].surface
    verts = g.add_points([tuple(float(x) for x in support.exact_point(*p)) for p in hole])
    ring = []
    for i, p in enumerate(hole):
        j = (i+1) % len(hole)
        ring.append(g.add_curve(verts[i], verts[j], CurveOnSurface(support, NurbsCurve(
            1, (0.,0.,1.,1.), (p, hole[j])))))
    trim_face(g, face, [tuple(OrientedEdge(e, True) for e in ring)])
    divisions = 2 if crossing else 1
    edited = replace(settings, overrides=tuple((e,divisions) for e in sorted(ring)))
    return session, old, edited, face, removed, ring, hole


def test_new_curved_hole_preserves_exterior_numbers_and_solves(published_panel, tmp_path):
    session, old, edited, face, removed, ring, _ = _edit_hole(published_panel, aligned=True)
    project = session.project
    plan = session.preview_update(edited)
    assert plan.removes_material and plan.replaced_elements == (removed,)
    before = project.mesh_numbering
    child = session.update_mesh(edited)
    assert old.shells.keys() - child.shells.keys() == {removed}
    assert child.quads == {e:c for e,c in old.quads.items() if e != removed}
    assert child.nodes.keys() == old.nodes.keys()
    for n in child.nodes:
        np.testing.assert_array_equal(child.nodes[n], old.nodes[n])
    assert {n for e in ring for n in child.nodes_of_edge[e]} == set(old.quads[removed])
    assert (project.mesh_numbering.node_high_water, project.mesh_numbering.element_high_water) == (
        before.node_high_water, before.element_high_water)
    solved = solve_linear_static(built=build_fe_model(project, child))
    assert any(abs(solved.node_displacement(n)[2]) > 0 for n in child.nodes)
    restored = load_project(session.save(tmp_path/'trim.anyfem')).mesh_session()
    assert restored.mesh.quads == child.quads and restored.mesh.nodes_of_edge == child.nodes_of_edge


@pytest.mark.parametrize("route", ["single_cell", "cross_cell_minimal", "cross_cell_grown"])
def test_annulus_preserves_exterior_and_certifies_new_material(request, tmp_path, route):
    from anysolver.assembly import compute_reactions
    from anysolver.matrix_assembly import assemble_load_vector
    from anygeometry import EntityRef
    from anyfem.mesh_preview import mesh_update_preview
    from anyfem.selection import MeshEntityRef

    crossing = route != "single_cell"
    source = request.getfixturevalue("published_crossing_panel" if route == "cross_cell_minimal" else "published_panel")
    session, old, edited, face, central, ring, hole = _edit_hole(source, aligned=False, crossing=crossing)
    project = session.project
    plan = session.preview_update(edited)
    assert plan.hole_edges == tuple(sorted(ring)) and central in plan.replaced_elements
    assert plan.removes_material and plan.patched_face == face and plan.regenerated == ()
    preview = mesh_update_preview(plan, session._baseline(), old, session.mesh_record_id)
    assert set(preview.owners) == ({EntityRef("edge", e) for e in ring}
                                   | {MeshEntityRef("element", e, session.mesh_record_id) for e in plan.replaced_elements})
    if crossing:
        inside, = [n for n, xyz in old.nodes.items() if all(
            (b[0]-a[0])*(xyz[1]-a[1]) - (b[1]-a[1])*(xyz[0]-a[0]) < 0
            for a,b in zip(hole, hole[1:] + hole[:1]))]
        star = {e for e,c in old.shells.items() if inside in c}
        assert len(star) == 4 and star <= set(plan.replaced_elements)
        if route == "cross_cell_minimal":
            assert star == set(plan.replaced_elements) and len(plan.interface_nodes) == 8
        else:
            assert star < set(plan.replaced_elements)
    numbering = project.mesh_numbering
    child = session.update_mesh(edited)
    kept = old.shells.keys() - set(plan.replaced_elements)
    added = child.shells.keys() - kept
    assert old.shells.keys() & child.shells.keys() == kept and len(added) > 0
    if crossing:
        assert inside not in child.nodes
        if route == "cross_cell_minimal":
            assert len(added) == 8
        else:
            assert len(added) > 8
    assert all(e > numbering.element_high_water for e in added)
    exterior = {n for e in kept for n in old.shells[e]} | set(plan.interface_nodes)
    assert old.nodes.keys() & child.nodes.keys() == exterior
    assert all(child.shells[e] == old.shells[e] for e in kept)
    for n in exterior:
        np.testing.assert_array_equal(child.nodes[n], old.nodes[n])
    assert not child.tris and len(child.quads) == len(kept) + len(added)
    assert all(n > numbering.node_high_water for n in child.nodes.keys() - exterior)
    assert all(child.nodes_of_edge[e] == stations for e, stations in old.nodes_of_edge.items())
    hole_nodes = {n for e in ring for n in child.nodes_of_edge[e]}
    divisions = dict(edited.overrides)
    assert len(hole_nodes) == sum(divisions[e] for e in ring) and hole_nodes.isdisjoint(old.nodes)
    for edge, stations in plan._patch["hole_rim"]:
        chain = child.nodes_of_edge[edge]
        assert len(stations) == len(chain) == divisions[edge] + 1
        params = [t for t, _ in stations]
        assert params[0] == 0. and params[-1] == 1. and np.all(np.diff(params) > 0.)
        curve = project.geometry.edges[edge].curve
        for t, node in zip(params, chain):
            np.testing.assert_allclose(child.nodes[node], [float(p) for p in curve.exact_point(t)],
                                       rtol=0., atol=project.geometry.tolerance.effective_length(edited.target_size))

    def boundary(mesh, cells):
        sides = [(a,b) for e in cells for c in [mesh.shells[e]] for a,b in zip(c,c[1:] + c[:1])]
        return set(sides) - {(b,a) for a,b in sides}

    parent_boundary = boundary(old, plan.replaced_elements)
    fixed = plan.interface_nodes
    perimeter = set(zip(fixed, fixed[1:] + fixed[:1]))
    assert perimeter == parent_boundary or {(b,a) for a,b in perimeter} == parent_boundary
    inner = {(a,b) for e in ring for a,b in zip(child.nodes_of_edge[e], child.nodes_of_edge[e][1:])}
    assert boundary(child, added) == parent_boundary | inner
    def projected_moments(points):
        xy = np.asarray(points)
        following = np.roll(xy, -1, axis=0)
        cross = xy[:,0] * following[:,1] - xy[:,1] * following[:,0]
        signed_area = .5 * np.sum(cross)
        return np.r_[signed_area, np.sum((xy + following) * cross[:,None], axis=0) / 6.] * np.sign(signed_area)

    # The original source projects to the unit square. Its area and first
    # moments after trimming follow directly from the four authored corners.
    expected_moments = np.array([1., .5, .5]) - projected_moments(hole)
    actual_moments = sum(projected_moments([child.nodes[n][:2] for n in c]) for c in child.shells.values())
    np.testing.assert_allclose(actual_moments, expected_moments, rtol=1.e-12, atol=1.e-15)
    evidence = session._baseline()["last_update"]
    assert evidence["impact"] == plan.impact and evidence["new_elements"] == sorted(added)
    assert evidence["generation_scope"] == "fixed trim annulus" and evidence["quality"]["min_scaled_jacobian"] > 0
    measurement = evidence["sizing"][str(face)]
    assert measurement["new_cells"] == len(added) and measurement["min_longest_new_edge"] <= edited.target_size
    built = build_fe_model(project, child)
    solved = solve_linear_static(built=built)
    forces, _ = assemble_load_vector(built.fe_model, built.load_case)
    resultant = sum(forces[built.node_dofs(n)[:3]] for n in child.nodes)
    # The vertical component of uniform normal pressure is pressure times
    # horizontal projected material area, independently of chart curvature.
    np.testing.assert_allclose(abs(resultant[2]), 1.e4 * expected_moments[0], rtol=1.e-10)
    reactions = compute_reactions(built.fe_model, solved.displacements, built.load_case)
    np.testing.assert_allclose(sum(r[:3] for r in reactions.values()),
                               -resultant, rtol=1.e-8, atol=1.e-6)
    restored = load_project(session.save(tmp_path/"annulus.anyfem")).mesh_session()
    assert restored.mesh.shells == child.shells and restored.mesh.nodes_of_edge == child.nodes_of_edge
    assert restored.preview_update(edited).is_noop


@pytest.mark.parametrize("crossing", [False, True], ids=["aligned_stations", "cross_cell"])
def test_corrupt_numbered_trim_rim_refuses_publication(published_panel, monkeypatch, crossing):
    from anyfem import mesh_numbering
    from anyfem.mesh_general_update import GeneralSurfaceUnsupported

    session, old, edited, _, _, ring, _ = _edit_hole(published_panel, aligned=False, crossing=crossing)
    ledger = session.project.mesh_numbering
    before = set(session.project.mesh_records)
    number = mesh_numbering.number_mesh_with_maps

    def corrupt_rim(*args, **kwargs):
        candidate = number(*args, **kwargs)
        if crossing:
            # Wrong middle stations with all four endpoints still correct.
            a, b = (candidate.nodes_of_edge[e] for e in ring[:2])
            a[1], b[1] = b[1], a[1]
        else:
            candidate.nodes_of_edge[ring[0]].reverse()
        return candidate

    monkeypatch.setattr(mesh_numbering, "number_mesh_with_maps", corrupt_rim)
    reason = "oriented new material boundary|certified edge" if crossing else "endpoint ownership"
    with pytest.raises(GeneralSurfaceUnsupported, match=reason):
        session.update_mesh(edited)
    assert session.project.mesh_numbering == ledger and session.mesh_record_id == ledger.active_mesh_id
    assert set(session.project.mesh_records) == before and session.mesh.shells == old.shells


@pytest.mark.skipif(os.environ.get("ANYFEM_RUN_QT_TESTS") != "1", reason="requires real Qt widgets")
def test_annulus_confirmation_draws_parent_cells_and_current_hole(published_panel, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QApplication
    from anygeometry import EntityRef
    from anyfem.io import recovery
    from anyfem.selection import MeshEntityRef, entity_tag
    from anyfem.ui.qt.app import QtFemWindow

    session, old, edited, _, _, ring, _ = _edit_hole(published_panel, aligned=False)
    parent = session.mesh_record_id
    plan = session.preview_update(edited)
    assert plan.hole_edges and plan.replaced_elements
    expected = {MeshEntityRef("element", e, parent) for e in plan.replaced_elements}
    expected.update(EntityRef("edge", edge) for edge in ring)
    monkeypatch.setattr(recovery, "default_recovery_root", lambda: tmp_path/"recovery")
    app = QApplication.instance() or QApplication([])
    window = QtFemWindow(session.project, viewer_backend="software")
    try:
        window.path = published_panel[0]
        window.show_geometry()
        previous_scene = window.viewport._visibility_scene
        window.seeding_overrides.update(edited.overrides)
        panel = window.panels["Mesh"]
        panel.set_strategy(edited.strategy)
        panel.size.setText(str(edited.target_size))
        panel.options.setPlainText(json.dumps({"mesh_controls": asdict(edited.controls), "quality_policy": None,
                                              "structured_controls": None, "automation": None}))
        reviewed = []
        def inspect(title, message):
            assert title == "Update mesh" and "Remesh the material" in message
            assert window.viewport._collection_refs == expected
            assert window.viewport.canvas.highlighted_tags() == {entity_tag(r) for r in expected}
            scene = window.viewport._visibility_scene
            actual_edges = {line.ref for line in scene.lines}
            assert all(EntityRef("edge", edge) in actual_edges for edge in ring)
            actual_cells = {r for patch in scene.faces for group in patch.polygon_owners for r in group}
            assert all(MeshEntityRef("element", e, parent) in actual_cells for e in plan.replaced_elements)
            reviewed.append(True)
            return False

        monkeypatch.setattr(window.dialogs, "confirm", inspect)
        panel.start_update()
        assert reviewed == [True] and window.viewport._visibility_scene is previous_scene
        assert not window.viewport._collection_refs and session.mesh_record_id == parent
    finally:
        window.session.mark_saved()
        window.close()
        app.processEvents()
