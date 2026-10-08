"""Mixed S3/Q4 stress retention: ragged sample rows survive the artifact.

Fake recovery records with known element IDs and distinct values: element 100
(Q4, 4 Gauss points) and element 200 (S3, 3 stations) carry components with
different per-element row shapes; element 300 (S3) has no record and must stay
uncoloured.  S3's maximum von Mises value is its LAST sample, which the
retained browser contour must show when the named component is selected.
"""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest

from anyfem.io.artifacts import ArtifactStore
from anyfem.io.output_views import add_output_request_views
from anyfem.io.result_artifact import ResultArtifactPayload, result_artifact_payload
from anyfem.model.records import OutputRequest, ResultQuantityDescriptor
from anyfem.post.results import LinearBatchSolution, LinearSolution
from anyfem.ui.scene import COLOR_MESH_FILL, build_persisted_result_scene


class _DofManager:
    DOF_NAMES = ("ux", "uy", "uz", "rx", "ry", "rz")

    def __init__(self, node_ids):
        self.node_ids = tuple(node_ids)
        self.total_dofs = 6 * len(self.node_ids)

    def get_node_dofs(self, node_id):
        index = self.node_ids.index(int(node_id))
        return list(range(6 * index, 6 * index + 6))

    def get_dof_info(self, dof):
        node_index, local = divmod(int(dof), 6)
        return self.node_ids[node_index], local, self.DOF_NAMES[local]


NODES = {
    1: (0.0, 0.0, 0.0), 2: (1.0, 0.0, 0.0), 3: (1.0, 1.0, 0.0),
    4: (0.0, 1.0, 0.0), 5: (2.0, 0.0, 0.0), 6: (2.0, 1.0, 0.0),
    7: (1.5, 1.5, 0.0),
}
SHELLS = {100: (1, 2, 3, 4), 200: (2, 5, 6), 300: (3, 6, 7)}


def _mesh():
    return SimpleNamespace(
        nodes={key: np.asarray(value) for key, value in NODES.items()},
        num_nodes=len(NODES),
        num_elements=len(SHELLS),
        shells=dict(SHELLS),
        beams={},
        corners_of=lambda element_id: tuple(SHELLS[int(element_id)]),
        elements_of_face={1: (100, 200, 300)},
        elements_of_edge={},
    )


def _solution(element_stresses):
    mesh = _mesh()
    manager = _DofManager(sorted(NODES))
    built = SimpleNamespace(
        mesh=mesh,
        fe_model=SimpleNamespace(
            mesh=SimpleNamespace(dof_manager=manager, nodes=mesh.nodes)
        ),
        project=SimpleNamespace(name="mixed fake"),
    )
    return LinearSolution(
        displacements=np.arange(6 * len(NODES), dtype=float),
        built=built,
        _stress=SimpleNamespace(element_stresses=element_stresses, provenance={}),
    )


def _mixed_stresses():
    return {
        100: {  # Q4: 4 Gauss points
            "von_mises": np.array([10.0, 20.0, 30.0, 40.0]),
            "membrane_xx": np.array(
                [[1.0, 2.0], [3.0, 4.0], [5.0, 6.0], [7.0, 8.0]]
            ),
        },
        200: {  # S3: 3 stations; the maximum is the LAST sample
            "von_mises": np.array([50.0, 60.0, 70.0]),
            "membrane_xx": np.array(
                [[9.0, 10.0], [11.0, 12.0], [13.0, 14.0]]
            ),
        },
    }


def _scene_colors(scene):
    colors = {}
    for face in scene.faces:
        for owners, color in zip(face.polygon_owners, face.colors):
            for owner in owners:
                if getattr(owner, "kind", None) == "element":
                    colors[int(owner.id)] = color
    return colors


def test_mixed_static_stress_components_survive_with_nan_padding():
    payload = result_artifact_payload(_solution(_mixed_stresses()))

    assert "stress_von_mises" in payload.fields
    assert "stress_membrane_xx" in payload.fields

    descriptor, values = payload.fields["stress_von_mises"]
    assert descriptor.location == "integration_point"
    assert descriptor.components == ("von_mises",)
    assert descriptor.provenance["scalar_sample_axes"] == [2]
    assert descriptor.provenance["element_sample_shapes"] == {
        "100": [4], "200": [3],
    }
    np.testing.assert_allclose(values[0, 0], [10.0, 20.0, 30.0, 40.0])
    np.testing.assert_allclose(values[0, 1, :3], [50.0, 60.0, 70.0])
    assert np.isnan(values[0, 1, 3])  # padded with NaN, never zero
    np.testing.assert_array_equal(
        np.asarray(payload.tables["stress_von_mises_element_ids"]), [100, 200]
    )  # element 300 has no record and stays absent

    descriptor_mx, values_mx = payload.fields["stress_membrane_xx"]
    assert values_mx.shape == (1, 2, 4, 2)
    assert descriptor_mx.provenance["element_sample_shapes"] == {
        "100": [4, 2], "200": [3, 2],
    }
    np.testing.assert_allclose(values_mx[0, 1, :3], [[9.0, 10.0], [11.0, 12.0], [13.0, 14.0]])
    assert np.isnan(values_mx[0, 1, 3]).all()


def test_uniform_static_stress_rows_stack_exactly_as_before():
    stresses = {
        100: {"von_mises": np.array([1.0, 2.0, 3.0])},
        200: {"von_mises": np.array([4.0, 5.0, 6.0])},
    }
    payload = result_artifact_payload(_solution(stresses))
    descriptor, values = payload.fields["stress_von_mises"]
    assert "element_sample_shapes" not in descriptor.provenance
    np.testing.assert_array_equal(
        values, np.stack([stresses[100]["von_mises"], stresses[200]["von_mises"]])[None]
    )


def test_retained_scene_named_component_reduces_samples_not_first_sample(tmp_path):
    payload = result_artifact_payload(_solution(_mixed_stresses()))
    store = ArtifactStore(tmp_path / "mixed.anyfem")
    artifact = store.write_result(
        job_id="mixed", document_id="doc", mesh_id="mesh",
        model_hash="model", mesh_hash="mesh", analysis_hash="analysis",
        **payload.write_result_inputs(),
    )
    dataset = store.open_result(artifact)
    stored = dataset.field("stress_von_mises")
    assert stored.descriptor.components == ("von_mises",)
    assert stored.descriptor.provenance["element_sample_shapes"] == {
        "100": [4], "200": [3],
    }

    scene = build_persisted_result_scene(
        SimpleNamespace(name="mixed fake"), _mesh(), dataset, "stress_von_mises",
        component="von_mises",
    )
    colors = _scene_colors(scene)
    assert colors[300] == COLOR_MESH_FILL  # no record: stays uncoloured
    assert colors[100] != COLOR_MESH_FILL
    assert colors[200] != COLOR_MESH_FILL
    assert colors[100] != colors[200]
    levels = scene.legend["levels"]
    assert levels[0] == pytest.approx(40.0)  # Q4 maximum-absolute sample
    assert levels[-1] == pytest.approx(70.0)  # S3 maximum is its LAST sample
    assert "von_mises" in scene.legend["title"]

    unnamed = build_persisted_result_scene(
        SimpleNamespace(name="mixed fake"), _mesh(), dataset, "stress_von_mises",
    )
    assert unnamed.legend["levels"] == scene.legend["levels"]


@pytest.mark.parametrize("reduction,expected", [
    ("mean", [25.0, 60.0]),
    ("min", [10.0, 50.0]),
    ("max", [40.0, 70.0]),
    ("max_abs", [40.0, 70.0]),
])
def test_output_request_reduction_ignores_nan_padding(reduction, expected):
    payload = result_artifact_payload(_solution(_mixed_stresses()))
    request = OutputRequest(
        ("stress.von_mises",), "region", "element",
        basis="element_local", reduction=reduction,
    )
    scope = {"request": request.to_dict(), "node_ids": [], "element_ids": [100, 200, 300]}
    viewed = add_output_request_views(payload, (scope,))
    outcome = viewed.provenance["output_request_outcomes"][0]
    assert outcome["status"] == "partial"  # element 300 honestly reported missing
    assert any("300" in item for item in outcome["diagnostics"])
    key = outcome["fields"][0]
    _view_descriptor, values = viewed.fields[key]
    np.testing.assert_allclose(values[0, :, 0], expected)


def _batch_solution(case_stresses):
    mesh = _mesh()
    manager = _DofManager(sorted(NODES))
    built = SimpleNamespace(
        mesh=mesh,
        fe_model=SimpleNamespace(
            mesh=SimpleNamespace(dof_manager=manager, nodes=mesh.nodes)
        ),
        project=SimpleNamespace(name="mixed fake"),
    )
    shapes = [
        LinearSolution(
            displacements=np.arange(6 * len(NODES), dtype=float) + 100.0 * index,
            built=built,
            _stress=SimpleNamespace(element_stresses=stresses, provenance={}),
        )
        for index, stresses in enumerate(case_stresses)
    ]
    return LinearBatchSolution(built=built, shapes=shapes, case_names=("dead", "live"))


def _batch_cases():
    return (
        {  # dead: Q4 4 Gauss points / S3 3 stations
            100: {"von_mises": np.array([10.0, 20.0, 30.0, 40.0])},
            200: {"von_mises": np.array([50.0, 60.0, 70.0])},
        },
        {  # live: distinct values per named case
            100: {"von_mises": np.array([80.0, 90.0, 100.0, 110.0])},
            200: {"von_mises": np.array([120.0, 130.0, 140.0])},
        },
    )


def test_linear_batch_mixed_cached_stresses_survive_as_padded_fields():
    payload = result_artifact_payload(_batch_solution(_batch_cases()))

    assert payload.frames == (0.0, 1.0)
    assert payload.frame_kind == "load_case"
    assert list(payload.tables["load_case_names"]) == ["dead", "live"]
    assert payload.tables["batch_stress_recovery"]["status"] == "complete"

    descriptor, values = payload.fields["stress_von_mises"]
    assert descriptor.location == "integration_point"
    assert descriptor.frames == (0.0, 1.0)
    assert descriptor.provenance["load_cases"] == ["dead", "live"]
    assert len(descriptor.provenance["case_recovery"]) == 2
    assert descriptor.provenance["scalar_sample_axes"] == [2]
    assert descriptor.provenance["frame_element_sample_shapes"] == {
        "0": {"100": [4], "200": [3]},
        "1": {"100": [4], "200": [3]},
    }
    assert values.shape == (2, 2, 4)
    np.testing.assert_allclose(values[0, 0], [10.0, 20.0, 30.0, 40.0])
    np.testing.assert_allclose(values[0, 1, :3], [50.0, 60.0, 70.0])
    assert np.isnan(values[0, 1, 3])  # padded with NaN, never zero
    np.testing.assert_allclose(values[1, 0], [80.0, 90.0, 100.0, 110.0])
    np.testing.assert_allclose(values[1, 1, :3], [120.0, 130.0, 140.0])
    assert np.isnan(values[1, 1, 3])
    np.testing.assert_array_equal(
        np.asarray(payload.tables["stress_von_mises_element_ids"]), [100, 200]
    )


def test_linear_batch_write_open_read_and_contour_both_frames(tmp_path):
    payload = result_artifact_payload(_batch_solution(_batch_cases()))
    store = ArtifactStore(tmp_path / "batch.anyfem")
    artifact = store.write_result(
        job_id="batch", document_id="doc", mesh_id="mesh",
        model_hash="model", mesh_hash="mesh", analysis_hash="analysis",
        **payload.write_result_inputs(),
    )
    dataset = store.open_result(artifact)
    stored = dataset.field("stress_von_mises")
    assert stored.descriptor.provenance["load_cases"] == ["dead", "live"]
    np.testing.assert_allclose(stored.read(0)[1, :3], [50.0, 60.0, 70.0])
    np.testing.assert_allclose(stored.read(1)[0], [80.0, 90.0, 100.0, 110.0])

    scene_dead = build_persisted_result_scene(
        SimpleNamespace(name="mixed fake"), _mesh(), dataset, "stress_von_mises",
        frame=0, component="von_mises",
    )
    scene_live = build_persisted_result_scene(
        SimpleNamespace(name="mixed fake"), _mesh(), dataset, "stress_von_mises",
        frame=1, component="von_mises",
    )
    colors_dead = _scene_colors(scene_dead)
    colors_live = _scene_colors(scene_live)
    assert colors_dead[300] == COLOR_MESH_FILL  # no record: uncoloured in both frames
    assert colors_live[300] == COLOR_MESH_FILL
    for colors in (colors_dead, colors_live):
        assert colors[100] != COLOR_MESH_FILL
        assert colors[200] != COLOR_MESH_FILL
        assert colors[100] != colors[200]
    assert scene_dead.legend["levels"] != scene_live.legend["levels"]
    assert scene_dead.legend["levels"][0] == pytest.approx(40.0)
    assert scene_dead.legend["levels"][-1] == pytest.approx(70.0)
    assert scene_live.legend["levels"][0] == pytest.approx(110.0)
    assert scene_live.legend["levels"][-1] == pytest.approx(140.0)

    unnamed = build_persisted_result_scene(
        SimpleNamespace(name="mixed fake"), _mesh(), dataset, "stress_von_mises",
        frame=1,
    )
    assert unnamed.legend["levels"] == scene_live.legend["levels"]


def test_linear_batch_missing_frame_element_keeps_conservative_table_fallback():
    dead, live = _batch_cases()
    live = {100: live[100]}  # element 200 absent from the live case
    payload = result_artifact_payload(_batch_solution((dead, live)))
    assert "stress_von_mises" not in payload.fields
    assert "stress_von_mises" in payload.tables  # raw history, no silent zero fill


def test_output_view_keeps_missing_row_nan_and_signed_max_abs_tie():
    descriptor = ResultQuantityDescriptor(
        "stress_von_mises", "Von Mises", "integration_point", unit="Pa",
        components=("von_mises",), basis="element_local", frames=(0.0,),
        recovery="recovered", provenance={"scalar_sample_axes": [2]},
    )
    values = np.array([[[np.nan, np.nan, np.nan], [-40.0, 40.0, 5.0]]])
    native = ResultArtifactPayload(
        fields={"stress_von_mises": (descriptor, values)},
        frames=(0.0,),
        tables={"stress_von_mises_element_ids": np.array([11, 12])},
    )
    request = OutputRequest(
        ("stress.von_mises",), "region", "element",
        basis="element_local", reduction="max_abs",
    )
    scope = {"request": request.to_dict(), "node_ids": [], "element_ids": [11, 12]}
    viewed = add_output_request_views(native, (scope,))
    outcome = viewed.provenance["output_request_outcomes"][0]
    _descriptor, data = viewed.fields[outcome["fields"][0]]
    assert np.isnan(data[0, 0, 0])  # unavailable, never a fabricated zero
    assert data[0, 1, 0] == -40.0  # keep valid element despite missing neighbour

    values[0, 0] = [-30.0, 30.0, 1.0]  # tie: first maximum-absolute sample keeps its sign
    reduced = add_output_request_views(native, (scope,))
    outcome = reduced.provenance["output_request_outcomes"][0]
    assert outcome["status"] == "available"
    _view_descriptor, viewed = reduced.fields[outcome["fields"][0]]
    np.testing.assert_allclose(viewed[0, :, 0], [-30.0, -40.0])


def test_ragged_numeric_storage_preserves_complex_samples():
    stresses = {
        100: {"complex_fixture": np.array([1 + 2j, 3 + 4j])},
        200: {"complex_fixture": np.array([5 + 6j])},
    }
    payload = result_artifact_payload(_solution(stresses))
    _descriptor, values = payload.fields["stress_complex_fixture"]
    np.testing.assert_array_equal(values[0, 0], [1 + 2j, 3 + 4j])
    assert values[0, 1, 0] == 5 + 6j
    assert np.isnan(values[0, 1, 1])


def test_selected_valid_frame_remains_available_after_missing_frame():
    descriptor = ResultQuantityDescriptor(
        "stress_von_mises", "Von Mises", "integration_point", unit="Pa",
        components=("von_mises",), basis="element_local", frames=(0.0, 1.0),
        recovery="recovered", provenance={"scalar_sample_axes": [2]},
    )
    native = ResultArtifactPayload(
        fields={"stress_von_mises": (descriptor, np.array([[[np.nan, np.nan]], [[20., 30.]]]))},
        frames=(0.0, 1.0), tables={"stress_von_mises_element_ids": np.array([11])},
    )
    request = OutputRequest(
        ("stress.von_mises",), "region", "element", basis="element_local",
        reduction="max_abs", frame_policy="selected", frame_indices=(1,),
    )
    viewed = add_output_request_views(native, ({
        "request": request.to_dict(), "node_ids": [], "element_ids": [11],
    },))
    outcome = viewed.provenance["output_request_outcomes"][0]
    assert outcome["status"] == "available"
    selected, values = viewed.fields[outcome["fields"][0]]
    assert selected.frames == (1.0,)
    np.testing.assert_array_equal(values, [[[30.]]])
