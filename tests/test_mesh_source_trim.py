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
