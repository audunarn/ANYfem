"""Development comparison of two refinement routes on the same finite-width holed strip."""
import json
from statistics import mean
from time import perf_counter

import pytest
import numpy as np
from scipy.spatial import cKDTree

from anygeometry.volume_cut import BoxVolume
from anyfem.io.artifacts import ArtifactStore
from anyfem.mesh_connected_update import explain_connected_ineligibility
from anyfem.model.records import AnalysisDefinition
from anyfem.model.submodels import SourceReference
from anyfem.regional_mesh import RegionalUpdateUnsupported
from anyfem.submodel_cut import cut_submodel
from anyfem.submodel_jobs import (
    add_submodel, discard_staged_run, job_source, publish_submodel_run, stage_submodel_run,
    submodel_snapshot,
)
from submodel_a8_helpers import DIAMETER, HOWLAND_KT, WIDTH, build_ring, configure_cut_mesh, holed_strip, mesh_settings, stress_factor
from test_submodel_regional_integration import solve_parent


def archived_solution(workflow, store, job):
    snapshot = workflow.session.snapshot()
    source = job_source(snapshot.thaw(), SourceReference(job.id, "default"), store,
                        document=snapshot.document, model_hash=snapshot.revision.model_hash)
    return source.solution


def test_regional_and_sequential_holed_strip(tmp_path, record_property):
    compare_holed_strip(tmp_path, record_property, coarse_divisions=4)


def test_hole_recovery_is_invariant_under_cyclic_q4_numbering(tmp_path, record_property):
    """A prescribed square and an authored square have identical cells with different starting corners."""
    from anyfem.submodel import SubmodelLineage, SubmodelSettings, bind_source, solve_submodel
    from test_submodel_workflow import sides

    project, arcs, spokes = holed_strip()
    analysis = project.add_analysis(AnalysisDefinition(name="Membrane tension", target_id="default",
                                                       settings={"load_case": "default"}))
    path = tmp_path / "cyclic.anyfem"
    workflow = project.mesh_session(path)
    try:
        workflow.generate_mesh(mesh_settings(arcs, spokes, 4, size=.125))
        workflow.save()
        store = ArtifactStore(path)
        job = solve_parent(workflow, analysis, store)
        source = archived_solution(workflow, store, job)
        binding = bind_source(source)
        box = BoxVolume((-.2, -.2, -.05), (.2, .2, .05))
        definition = cut_submodel(workflow.session.snapshot(), box, source=SourceReference(job.id, "default"),
                                  policy="drive_all", target_size=.035).definition
        configure_cut_mesh(definition.child, divisions=8, size=.035)
        cut_mesh = definition.child.generate_mesh(.035)
        cut_lineage = SubmodelLineage(definition.source_faces, definition.cut_faces)
        authored, arcs, spokes, *_ = build_ring(.2)
        prescribed_mesh = authored.generate_mesh(.035, strategy="mapped",
                                                overrides={**dict.fromkeys(arcs, 8), **dict.fromkeys(spokes, 7)})
        prescribed_lines = sides(authored, authored.geometry.faces, ((-.2, .2), (-.2, .2)))
        lineage = SubmodelLineage({f: (f,) for f in authored.geometry.faces}, prescribed_lines)
        settings = SubmodelSettings("drive_all")
        prescribed = solve_submodel(source, binding, authored, prescribed_mesh, lineage, settings)
        cut = solve_submodel(source, binding, definition.child, cut_mesh, cut_lineage, settings)

        ids, cut_ids = sorted(prescribed_mesh.nodes), sorted(cut_mesh.nodes)
        distances, index = cKDTree([cut_mesh.nodes[n] for n in cut_ids]).query([prescribed_mesh.nodes[n] for n in ids])
        # Roundoff in clipping and trigonometric reconstruction on a 0.4 m square.
        assert max(distances) < 1.e-12 and len(set(index)) == len(ids) == len(cut_ids)
        paired = {n: cut_ids[i] for n, i in zip(ids, index)}
        cut_cells = {frozenset(nodes): tuple(nodes) for nodes in cut_mesh.quads.values()}
        rotated = 0
        for nodes in prescribed_mesh.quads.values():
            mapped = tuple(paired[n] for n in nodes)
            other = cut_cells[frozenset(mapped)]
            assert any(mapped == other[i:] + other[:i] for i in range(4))
            rotated += mapped != other
        assert rotated and len(cut_cells) == len(prescribed_mesh.quads)
        assert {paired[n] for n in prescribed.hits} == set(cut.hits)
        u0 = np.array([prescribed.solution.node_displacement(n) for n in ids])
        u1 = np.array([cut.solution.node_displacement(paired[n]) for n in ids])
        # A reindexing permutes the FE linear system. Allow accumulated double-precision solve roundoff.
        np.testing.assert_allclose(u0, u1, rtol=1.e-10, atol=1.e-10*np.abs(u0).max())
        q0, q1 = stress_factor(prescribed.solution), stress_factor(cut.solution)
        np.testing.assert_allclose(q0, q1, rtol=1.e-10, atol=0.)
        record_property("cyclic_recovery", json.dumps({"nodes":len(ids), "rotated":rotated, "kt":[q0,q1],
                                                       "max_coordinate_error_m":float(max(distances))}))
    finally:
        workflow.close()


def compare_holed_strip(tmp_path, record_property, *, coarse_divisions):
    start = perf_counter()
    project, arcs, spokes = holed_strip()
    analysis = project.add_analysis(AnalysisDefinition(name="Membrane tension", target_id="default",
                                                       settings={"load_case": "default"}))
    path = tmp_path / "holed_strip.anyfem"
    store = ArtifactStore(path)
    workflow = project.mesh_session(path)
    try:
        levels = (coarse_divisions, coarse_divisions * 2)
        child_size = .035 * (4 / coarse_divisions)
        base = mesh_settings(arcs, spokes, levels[0], size=WIDTH / levels[0])
        mesh = workflow.generate_mesh(base)
        allocation = project.mesh_numbering
        parent_id = workflow.mesh_record_id
        workflow.save()
        coarse_job = solve_parent(workflow, analysis, store, timeout=120)
        coarse_solution = archived_solution(workflow, store, coarse_job)
        coarse = stress_factor(coarse_solution)
        coarse_dofs = len(coarse_solution.displacements)
        parent_wall = perf_counter() - start
        # Equal half-spans cut the source O-grid along four four-sided faces.
        half_x = half_y = 2 * DIAMETER
        box = BoxVolume((-half_x, -half_y, -.05), (half_x, half_y, .05))
        cut = cut_submodel(workflow.session.snapshot(), box, source=SourceReference(coarse_job.id, "default"),
                           policy="drive_all", target_size=child_size)
        child = cut.definition.child
        configure_cut_mesh(child, divisions=levels[1], size=child_size)
        with workflow.session.transaction("Register holed strip cut", solver_affecting=False):
            add_submodel(project, cut.definition)
        staged = stage_submodel_run(submodel_snapshot(workflow.session), cut.definition.id, store)
        try:
            sequential = stress_factor(staged.result.solution)
            child_dofs = len(staged.result.solution.displacements)
            child_cells = len(staged.result.solution.built.mesh.shells)
            publish_submodel_run(workflow.session, staged)
        finally:
            if not staged.published:
                discard_staged_run(staged)
        child_wall = perf_counter() - start - parent_wall

        refined = mesh_settings(arcs, spokes, levels[1], size=base.target_size)
        plan = workflow.preview_update(refined)
        assert plan.applicable and plan.affected_faces
        replacement = workflow.update_mesh(refined)
        assert workflow.mesh_record_id != parent_id
        assert str(replacement.geometry_model_id) == str(mesh.geometry_model_id)
        assert replacement.geometry_revision == mesh.geometry_revision
        for face in plan.retained_faces:
            for element in mesh.elements_of_face[face]:
                assert replacement.quads[element] == mesh.quads[element]
        retired = {e for f in plan.affected_faces for e in mesh.elements_of_face[f]}
        assert retired.isdisjoint(replacement.shells)
        assert all(e > allocation.element_high_water for e in replacement.shells.keys() - mesh.shells.keys())
        workflow.save()
        fine_job = solve_parent(workflow, analysis, store, timeout=120)
        regional_solution = archived_solution(workflow, store, fine_job)
        regional = stress_factor(regional_solution)
        result = {"levels": levels, "reference_gross_kt": HOWLAND_KT, "source": coarse, "box_cut": sequential,
                  "regional": regional, "parent_cells": len(mesh.shells), "child_cells": child_cells,
                  "dofs": (coarse_dofs, child_dofs, len(regional_solution.displacements)),
                  "wall_seconds_by_arm": (parent_wall, child_wall, perf_counter() - start - parent_wall - child_wall),
                  "node_sensor_coordinates_m": ((0., DIAMETER / 2, 0.), (0., -DIAMETER / 2, 0.)),
                  "quality": {role: {k: project.mesh_records[identity].summary["quality"][k] for k in
                              ("max_aspect_ratio", "min_scaled_jacobian", "max_warp")} for role, identity in
                              (("source", parent_id), ("regional", workflow.mesh_record_id))},
                  "regional_cells": len(replacement.shells), "affected_faces": list(plan.affected_faces),
                  "retained_faces": list(plan.retained_faces),
                  "relative_errors": {k: mean(pair) / HOWLAND_KT - 1 for k, pair in
                                      (("source", coarse), ("box_cut", sequential), ("regional", regional))}}
        record_property("hole_comparison", json.dumps(result, sort_keys=True))
        print(json.dumps(result, sort_keys=True))
        for pair in (coarse, sequential, regional):
            assert min(pair) > 0
            assert pair[0] == pytest.approx(pair[1], rel=1e-6)  # mirrored sensors, same axial traction
    finally:
        workflow.close()


def test_changed_arc_cannot_reuse_a_published_baseline():
    project, arcs, spokes = holed_strip()
    settings = mesh_settings(arcs, spokes, 4, size=WIDTH / 4)
    workflow = project.mesh_session()
    try:
        old = workflow.generate_mesh(settings)
        allocation = project.mesh_numbering
        vertex = project.geometry.edges[arcs[0]].curve.via_vertex
        x, y, z = project.geometry.vertex_position(vertex)
        project.geometry.move_point(vertex, x, y * 1.005, z)
        with pytest.raises(RegionalUpdateUnsupported, match="geometry changed|requires live source-cell publication"):
            workflow.update_mesh(mesh_settings(arcs, spokes, 8, size=settings.target_size))
        assert project.mesh_numbering == allocation
        assert workflow.mesh is old
    finally:
        workflow.close()


def test_connected_scope_preserves_planarity_for_straight_coons_plates():
    from test_mesh_connected_update import build, full

    project, _, left, _ = build()
    base, mesh, _ = full(project)
    vertex = project.geometry.edges[left[0]].start
    x, y, _ = project.geometry.vertex_position(vertex)
    project.geometry.move_point(vertex, x, y, .02)
    reason = explain_connected_ineligibility(project, base, mesh)
    assert reason and "planar plate" in reason
