"""Display, probes and saved artifacts use the geometry the solve ran on.

A run-time (or capacity-workflow) imperfection moves the reference coordinates
of a copy of the model; the displacements are measured from that copy.  The
deformed shape must therefore be "imperfect + displacement", and a saved
artifact must carry that reference so reopening it draws the same shape.
Runs without an imperfection, and project imperfections applied at build time,
keep drawing on the mesh coordinates bit for bit.
"""

from __future__ import annotations

import numpy as np
import pytest

from anyfem import eigenmode_imperfection, solve_capacity, solve_nonlinear_static
from anyfem.io.artifacts import ArtifactStore
from anyfem.io.result_artifact import write_solution_artifact
from anyfem.ui.scene import build_persisted_result_scene, build_result_scene

from test_imperfect_solved_geometry import (  # noqa: F401
    AMPLITUDE,
    compressed_plate,
    plate_and_mode,
)


def _fe_coordinates(model, node_ids):
    return np.array([[n.x, n.y, n.z] for n in (model.mesh.get_node(i) for i in node_ids)])


def _scene_positions(scene):
    return np.concatenate(
        [np.asarray(p, dtype=float).reshape(-1, 3) for patch in scene.faces for p in patch.polygons]
    )


def _runtime(plate_and_mode):
    project, mesh, buckling = plate_and_mode
    imperfection = eigenmode_imperfection(buckling, 1, amplitude=AMPLITUDE)
    return solve_nonlinear_static(
        project, mesh=mesh, imperfection=imperfection, max_load_factor=1.0, num_steps=1,
    )


def _capacity(plate_and_mode):
    project, mesh, _ = plate_and_mode
    return solve_capacity(
        project, mesh=mesh, num_buckling_modes=1, imperfection_amplitude=AMPLITUDE,
        num_steps=1, max_load_factor=1.0,
    )


def _store(tmp_path, solution):
    project = solution.built.project
    store = ArtifactStore(tmp_path / "plate.anyfem")
    identity = dict(mesh_id="mesh", document_id=project.document_id,
                    model_hash="model", mesh_hash="hash")
    store.write_mesh(solution.built.mesh, **identity)
    artifact = write_solution_artifact(store, solution, job_id="job", analysis_hash="a", **identity)
    return store, artifact


@pytest.mark.parametrize("route", ["nonlinear", "capacity"])
def test_deformed_positions_are_imperfect_reference_plus_displacement(plate_and_mode, route):
    solution = (_runtime if route == "nonlinear" else _capacity)(plate_and_mode)
    ids = sorted(solution.built.mesh.nodes)
    reference = _fe_coordinates(solution.solved_model(), ids)
    assert np.max(np.abs(reference - solution.built.mesh.node_positions())) > 0.5 * AMPLITUDE

    np.testing.assert_array_equal(solution.reference_positions(), reference)
    expected = reference + 3.0 * solution.translations()
    np.testing.assert_array_equal(solution.deformed_positions(3.0), expected)

    scene = build_result_scene(solution, field="magnitude", scale=3.0)
    by_node = dict(zip(ids, expected))
    first = next(iter(solution.built.mesh.shells))
    corners = np.array([by_node[n] for n in solution.built.mesh.corners_of(first)])
    polygons = [np.asarray(poly) for patch in scene.faces for poly in patch.polygons]
    assert any(poly.shape == corners.shape and np.array_equal(poly, corners) for poly in polygons)


def test_csv_positions_use_the_reference_geometry(plate_and_mode):
    from anyfem.post.report import field_to_csv

    solution = _runtime(plate_and_mode)
    ids = sorted(solution.built.mesh.nodes)
    reference = dict(zip(ids, _fe_coordinates(solution.solved_model(), ids)))
    rows = [line.split(",") for line in field_to_csv(solution, "magnitude").splitlines()[1:]]
    assert rows
    for row in rows:
        np.testing.assert_allclose([float(v) for v in row[1:4]], reference[int(row[0])], atol=1e-8)


def test_artifact_records_the_reference_geometry_and_reopens_on_it(plate_and_mode, tmp_path):
    solution = _runtime(plate_and_mode)
    store, artifact = _store(tmp_path, solution)
    dataset = store.open_result(artifact)
    ids = sorted(solution.built.mesh.nodes)
    reference = _fe_coordinates(solution.solved_model(), ids)

    assert dataset.provenance["reference_geometry"]["kind"] == "solved_imperfect"
    np.testing.assert_array_equal(np.asarray(dataset.table("reference_node_ids")), ids)
    np.testing.assert_array_equal(np.asarray(dataset.table("reference_node_positions")), reference)

    scene = build_persisted_result_scene(
        solution.built.project, solution.built.mesh, dataset, "displacement", scale=3.0
    )
    live = build_result_scene(solution, field="magnitude", scale=3.0)
    np.testing.assert_array_equal(_scene_positions(scene), _scene_positions(live))


def test_ordinary_artifact_names_the_mesh_and_gains_no_table(plate_and_mode, tmp_path):
    project, mesh, _ = plate_and_mode
    solution = solve_nonlinear_static(project, mesh=mesh, max_load_factor=0.5, num_steps=1)
    store, artifact = _store(tmp_path, solution)
    dataset = store.open_result(artifact)
    assert dataset.provenance["reference_geometry"]["kind"] == "mesh_record"
    assert "reference_node_positions" not in dataset.table_keys
    scene = build_persisted_result_scene(
        project, solution.built.mesh, dataset, "displacement", scale=3.0
    )
    live = build_result_scene(solution, field="magnitude", scale=3.0)
    np.testing.assert_array_equal(_scene_positions(scene), _scene_positions(live))


def test_artifact_without_a_reference_record_reads_as_the_mesh(plate_and_mode, tmp_path):
    from anyfem.ui.scene import persisted_reference_positions

    project, mesh, _ = plate_and_mode
    solution = solve_nonlinear_static(project, mesh=mesh, max_load_factor=0.5, num_steps=1)
    store, artifact = _store(tmp_path, solution)
    dataset = store.open_result(artifact)

    class Old:  # an artifact written before the reference record existed
        provenance = {}
        table_keys = tuple(k for k in dataset.table_keys if not k.startswith("reference_"))
        table = staticmethod(dataset.table)

    ordered = sorted(mesh.nodes)
    np.testing.assert_array_equal(
        persisted_reference_positions(Old, mesh, ordered), mesh.node_positions()
    )


def test_unbound_runtime_imperfection_refuses_the_display(plate_and_mode):
    from anyfem.post.results import NonlinearSolution, UnboundSolvedGeometryError

    solution = _runtime(plate_and_mode)
    unbound = NonlinearSolution(
        displacements=solution.displacements, built=solution.built,
        raw_result=solution.raw_result, info=dict(solution.info),
    )
    with pytest.raises(UnboundSolvedGeometryError):
        unbound.deformed_positions(1.0)


def test_ordinary_and_build_time_displays_are_unchanged(plate_and_mode):
    from anyfem.model.imperfections import Imperfection

    project, mesh, _ = plate_and_mode
    plain = solve_nonlinear_static(project, mesh=mesh, max_load_factor=0.5, num_steps=1)
    np.testing.assert_array_equal(
        plain.deformed_positions(2.0), mesh.node_positions() + 2.0 * plain.translations()
    )
    p2, face = compressed_plate()
    p2.imperfections.append(Imperfection(name="dent", ref=p2.face(face), amplitude=AMPLITUDE,
                                         kind="plate_mode", direction=(0.0, 0.0, 1.0)))
    m2 = p2.generate_mesh(0.25)
    built_time = solve_nonlinear_static(p2, mesh=m2, max_load_factor=0.5, num_steps=1)
    # Pinned scope: build-time imperfections keep drawing on the mesh record.
    np.testing.assert_array_equal(
        built_time.deformed_positions(2.0), m2.node_positions() + 2.0 * built_time.translations()
    )
