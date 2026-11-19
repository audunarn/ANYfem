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

    assert dataset.metadata("provenance")["reference_geometry"]["kind"] == "solved_imperfect"
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
    assert dataset.metadata("provenance")["reference_geometry"]["kind"] == "mesh_record"
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
        table_keys = tuple(k for k in dataset.table_keys if not k.startswith("reference_"))
        table = staticmethod(dataset.table)

        @staticmethod
        def metadata(name):
            old = dict(dataset.metadata(name))
            old.pop("reference_geometry", None)
            return old

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


def test_artifact_of_an_unbound_imperfect_result_refuses_its_reference(plate_and_mode, tmp_path):
    from anyfem.post.results import NonlinearSolution

    solution = _runtime(plate_and_mode)
    unbound = NonlinearSolution(
        displacements=solution.displacements, built=solution.built,
        raw_result=solution.raw_result, info=dict(solution.info),
    )
    store, artifact = _store(tmp_path, unbound)
    dataset = store.open_result(artifact)
    assert dataset.metadata("provenance")["reference_geometry"]["kind"] == "unbound_imperfect"
    assert "reference_node_positions" not in dataset.table_keys
    with pytest.raises(ValueError, match="does not store"):
        build_persisted_result_scene(
            unbound.built.project, unbound.built.mesh, dataset, "displacement", scale=1.0
        )


def test_element_csv_centroids_use_the_reference_geometry(plate_and_mode):
    from anyfem.post.report import field_to_csv

    solution = _runtime(plate_and_mode)
    mesh = solution.built.mesh
    ids = sorted(mesh.nodes)
    reference = dict(zip(ids, _fe_coordinates(solution.solved_model(), ids)))
    rows = [l.split(",") for l in field_to_csv(solution, "von_mises").splitlines()[1:]]
    assert rows
    for row in rows:
        expected = np.mean([reference[n] for n in mesh.corners_of(int(row[0]))], axis=0)
        np.testing.assert_allclose([float(v) for v in row[1:4]], expected, atol=1e-8)


class _Patched:
    """A real dataset with edited provenance or tables."""

    def __init__(self, dataset, reference=None, tables=None):
        self._d, self._reference, self._tables = dataset, reference, tables or {}

    def __getattr__(self, name):
        return getattr(self._d, name)

    def metadata(self, name):
        data = dict(self._d.metadata(name))
        if name == "provenance" and self._reference is not None:
            data["reference_geometry"] = self._reference
        return data

    def table(self, key, *args):
        return self._tables[key] if key in self._tables else self._d.table(key, *args)


@pytest.mark.parametrize("reference", [{"kind": "future"}, {}, "text"])
def test_unrecognised_reference_record_is_refused(plate_and_mode, tmp_path, reference):
    solution = _runtime(plate_and_mode)
    store, artifact = _store(tmp_path, solution)
    dataset = _Patched(store.open_result(artifact), reference=reference)
    with pytest.raises(ValueError, match="unrecognised"):
        build_persisted_result_scene(
            solution.built.project, solution.built.mesh, dataset, "displacement", scale=1.0
        )


@pytest.mark.parametrize("damage", ["duplicate", "shape", "fractional"])
def test_malformed_stored_reference_is_refused(plate_and_mode, tmp_path, damage):
    solution = _runtime(plate_and_mode)
    store, artifact = _store(tmp_path, solution)
    real = store.open_result(artifact)
    ids = np.asarray(real.table("reference_node_ids"))
    xyz = np.asarray(real.table("reference_node_positions"))
    tables = {
        "duplicate": {"reference_node_ids": np.concatenate([ids[:-1], ids[-2:-1]])},
        "shape": {"reference_node_positions": xyz[:-1]},
        "fractional": {"reference_node_ids": ids + 0.5},
    }[damage]
    with pytest.raises(ValueError, match="malformed"):
        build_persisted_result_scene(
            solution.built.project, solution.built.mesh, _Patched(real, tables=tables),
            "displacement", scale=1.0,
        )


def test_capacity_stage_fields_stay_on_the_perfect_geometry(plate_and_mode, tmp_path):
    from anyfem.ui.scene import persisted_reference_positions

    solution = _capacity(plate_and_mode)
    store, artifact = _store(tmp_path, solution)
    dataset = store.open_result(artifact)
    assert "static_displacement" in dataset.field_keys
    mesh = solution.built.mesh
    ordered = sorted(mesh.nodes)
    perfect = np.asarray([mesh.nodes[n] for n in ordered], dtype=float)
    # Reference static and elastic buckling ran on the unmoved model.
    np.testing.assert_array_equal(
        persisted_reference_positions(dataset, mesh, ordered, perfect_stage=True), perfect
    )
    assert not np.array_equal(persisted_reference_positions(dataset, mesh, ordered), perfect)


def test_perfect_stage_is_deformed_by_its_own_vector(plate_and_mode):
    from types import SimpleNamespace

    project, mesh, _ = plate_and_mode
    ids = np.asarray(sorted(mesh.nodes))
    own = np.zeros((len(ids), 6)); own[:, 2] = 0.01
    collapse = np.zeros((len(ids), 6)); collapse[:, 0] = 5.0
    comps = ("ux", "uy", "uz", "rx", "ry", "rz")

    def stored(array, provenance):
        return SimpleNamespace(
            descriptor=SimpleNamespace(
                components=comps, provenance=provenance, location="node", unit="m",
                label="d", reduction="none"),
            shape=(1,) + array.shape, read=lambda frame, a=array: a,
            read_component=lambda c, frame, a=array: a[:, comps.index(c)],
            root_frame_index=lambda frame: 0,
        )

    fields = {"static_displacement": stored(own, {"capacity_stage": "reference_static"}),
              "displacement": stored(collapse, {})}
    shifted = np.asarray([mesh.nodes[n] for n in ids], dtype=float) + [0.0, 0.0, 0.5]
    dataset = SimpleNamespace(
        field_keys=tuple(fields), field=fields.__getitem__,
        table_keys=("static_displacement_node_ids", "displacement_node_ids",
                    "reference_node_ids", "reference_node_positions"),
        table=lambda key: {"reference_node_ids": ids, "reference_node_positions": shifted}.get(key, ids),
        metadata=lambda name: {"reference_geometry": {
            "kind": "solved_imperfect", "ids_table": "reference_node_ids",
            "positions_table": "reference_node_positions"}},
    )
    scene = build_persisted_result_scene(project, mesh, dataset, "static_displacement", scale=3.0)
    perfect = np.asarray([mesh.nodes[n] for n in ids], dtype=float)
    expected = dict(zip(ids.tolist(), perfect + [0.0, 0.0, 0.03]))
    first = next(iter(mesh.shells))
    corners = np.array([expected[n] for n in mesh.corners_of(first)])
    polygons = [np.asarray(p) for patch in scene.faces for p in patch.polygons]
    assert any(p.shape == corners.shape and np.allclose(p, corners) for p in polygons)


def test_malformed_top_level_provenance_is_refused(plate_and_mode, tmp_path):
    solution = _runtime(plate_and_mode)
    store, artifact = _store(tmp_path, solution)
    real = store.open_result(artifact)

    class ListProvenance(_Patched):
        def metadata(self, name):
            return ["not", "a", "mapping"] if name == "provenance" else self._d.metadata(name)

    with pytest.raises(ValueError, match="unrecognised"):
        build_persisted_result_scene(
            solution.built.project, solution.built.mesh, ListProvenance(real),
            "displacement", scale=1.0,
        )
