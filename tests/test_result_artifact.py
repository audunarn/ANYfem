"""Solution-wrapper to immutable result-artifact adaptation."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import numpy as np

from anyfem.io.artifacts import ArtifactStore
from anyfem.io.result_artifact import (
    build_result_artifact_inputs,
    result_artifact_payload,
    write_solution_artifact,
)
from anyfem.io.results import ImportedResults
from anyfem.post.fields import Field
from anyfem.post.results import (
    BucklingSolution,
    CapacitySolution,
    ImpactSolution,
    ImportedSolution,
    LinearBatchSolution,
    LinearSolution,
    ModalSolution,
    NonlinearSolution,
    ShapeView,
    TransientSolution,
)


class _DofManager:
    DOF_NAMES = ("ux", "uy", "uz", "rx", "ry", "rz")

    def __init__(self, node_ids=(10, 20)):
        self.node_ids = tuple(node_ids)
        self.total_dofs = 6 * len(self.node_ids)

    def get_node_dofs(self, node_id):
        index = self.node_ids.index(int(node_id))
        return list(range(6 * index, 6 * index + 6))

    def get_dof_info(self, dof):
        node_index, local = divmod(int(dof), 6)
        return self.node_ids[node_index], local, self.DOF_NAMES[local]


def _built():
    manager = _DofManager()
    mesh = SimpleNamespace(
        nodes={10: np.zeros(3), 20: np.ones(3)},
        num_nodes=2,
        num_elements=0,
    )
    fe_mesh = SimpleNamespace(dof_manager=manager, nodes=mesh.nodes)
    return SimpleNamespace(
        mesh=mesh,
        fe_model=SimpleNamespace(mesh=fe_mesh),
        project=SimpleNamespace(name="artifact model"),
    )


def _vector(offset=0.0):
    return np.arange(12, dtype=float) + float(offset)


def test_linear_displacement_is_one_complete_static_frame():
    solution = LinearSolution(displacements=_vector(), built=_built())
    payload = result_artifact_payload(solution)

    descriptor, values = payload.fields["displacement"]
    assert payload.frames == (0.0,)
    assert payload.frame_kind == "static"
    assert values.shape == (1, 2, 6)
    np.testing.assert_array_equal(values[0, 1], np.arange(6, 12))
    assert descriptor.location == "node"
    assert descriptor.components == ("ux", "uy", "uz", "rx", "ry", "rz")
    assert descriptor.deformation_required
    assert payload.tables["displacement_node_ids"].tolist() == [10, 20]
    assert "velocity" not in payload.fields


def test_modal_and_buckling_modes_use_their_physical_frame_values():
    built = _built()
    modal = ModalSolution(
        built=built,
        shapes=[
            ShapeView(_vector(), built, value=3.0),
            ShapeView(_vector(100.0), built, value=7.0),
        ],
    )
    modal_payload = result_artifact_payload(modal)
    assert modal_payload.frames == (3.0, 7.0)
    assert modal_payload.frame_kind == "frequency"
    assert modal_payload.fields["displacement"][1].shape == (2, 2, 6)
    assert modal_payload.fields["frequency"][0].unit == "Hz"

    buckling = BucklingSolution(
        built=built,
        shapes=[
            ShapeView(_vector(), built, value=1.5),
            ShapeView(_vector(10.0), built, value=2.5),
        ],
    )
    buckling_payload = result_artifact_payload(buckling)
    assert buckling_payload.frames == (1.5, 2.5)
    assert buckling_payload.frame_kind == "buckling_factor"
    assert buckling_payload.fields["buckling_factor"][0].unit == "1"


def test_linear_batch_persists_each_named_case_as_one_frame():
    built = _built()
    batch = LinearBatchSolution(
        built=built,
        shapes=[
            LinearSolution(_vector(), built, label="static: dead"),
            LinearSolution(_vector(20.0), built, label="static: live"),
        ],
        case_names=("dead", "live"),
    )

    payload = result_artifact_payload(batch)

    assert payload.frames == (0.0, 1.0)
    assert payload.frame_kind == "load_case"
    assert payload.fields["displacement"][1].shape == (2, 2, 6)
    assert payload.tables["load_case_names"] == ["dead", "live"]
    assert payload.fields["displacement"][0].provenance["shared_factorization"]


def test_nonlinear_uses_real_committed_snapshots_and_states_only(tmp_path):
    built = _built()
    snapshots = (
        SimpleNamespace(
            step_index=1,
            load_factor=0.4,
            control_value=None,
            displacements=_vector(10.0),
            element_states={7: {"yielded": False, "alpha": np.array([0.0, 0.001])}},
        ),
        SimpleNamespace(
            step_index=2,
            load_factor=0.8,
            control_value=None,
            displacements=_vector(20.0),
            element_states={7: {"yielded": True, "alpha": np.array([0.002, 0.003])}},
        ),
    )
    raw = SimpleNamespace(
        snapshots=snapshots,
        element_states={7: {"yielded": True, "alpha": np.array([0.002, 0.003])}},
        diagnostics={"converged": True},
    )
    steps = [
        SimpleNamespace(
            step_index=1, load_factor=0.4, displacement_norm=1.0, iterations=3,
            support_reactions={"fixed": (100.0, 0, 0, 0, 0, 0)},
        ),
        SimpleNamespace(
            step_index=2, load_factor=0.8, displacement_norm=2.0, iterations=4,
            support_reactions={"fixed": (200.0, 0, 0, 0, 0, 0)},
        ),
    ]
    solution = NonlinearSolution(
        displacements=_vector(999.0),
        built=built,
        value=0.8,
        steps=steps,
        raw_result=raw,
        info={"raw": raw},
    )
    payload = result_artifact_payload(solution)

    descriptor, values = payload.fields["displacement"]
    assert payload.frames == (0.4, 0.8)
    assert values.shape == (2, 2, 6)
    assert values[0, 0, 0] == 10.0
    assert values[-1, -1, -1] == 31.0
    assert descriptor.recovery == "committed_state"
    states = payload.tables["increment_element_states"]
    assert states[1]["element_states"]["7"]["yielded"] is True
    assert "load_factor" in payload.histories
    reaction_key = "support_reaction_fixed_Fx"
    assert reaction_key in payload.histories
    assert payload.fields[reaction_key][0].unit == "N"
    np.testing.assert_allclose(payload.histories[reaction_key][1], [100.0, 200.0])
    plastic_descriptor, plastic_values = payload.fields[
        "equivalent_plastic_strain"
    ]
    assert plastic_descriptor.unit == "1"
    assert plastic_descriptor.recovery == "committed_state"
    np.testing.assert_allclose(plastic_values[:, 0], [0.001, 0.003])
    np.testing.assert_array_equal(
        payload.tables["equivalent_plastic_strain_element_ids"], [7]
    )
    artifact = write_solution_artifact(
        ArtifactStore(tmp_path / "nonlinear.anyfem"),
        solution,
        job_id="nonlinear-job",
        document_id="document",
        mesh_id="mesh",
        model_hash="model",
        mesh_hash="mesh-hash",
        analysis_hash="analysis",
    )
    dataset = ArtifactStore(tmp_path / "nonlinear.anyfem").open_result(artifact)
    np.testing.assert_allclose(
        dataset.field("equivalent_plastic_strain").read(1), [0.003]
    )


def test_capacity_retains_reference_static_and_buckling_stages():
    built = _built()
    nonlinear_raw = SimpleNamespace(snapshots=(), element_states={})
    buckling = BucklingSolution(
        built=built,
        shapes=[ShapeView(_vector(20.0), built, value=2.0)],
    )
    workflow = SimpleNamespace(
        static_displacements=_vector(30.0),
        nonlinear_result=nonlinear_raw,
        diagnostics={"workflow": "complete"},
    )
    solution = CapacitySolution(
        displacements=_vector(40.0),
        built=built,
        value=1.2,
        buckling=buckling,
        raw_result=nonlinear_raw,
        info={"raw": workflow},
    )
    payload = result_artifact_payload(solution)
    assert payload.fields["displacement"][1].shape == (1, 2, 6)
    assert payload.fields["static_displacement"][1].shape == (1, 2, 6)
    assert payload.fields["buckling_mode_shape"][1].shape == (1, 2, 6)


def _transient_solution(raw):
    return TransientSolution(
        built=_built(),
        shapes=[],
        status="completed",
        info={"raw": raw, "diagnostics": raw.diagnostics},
        times=np.asarray(raw.times, dtype=float),
    )


def test_full_transient_retains_vectors_impulses_energies_stress_and_reactions():
    times = np.array([0.0, 0.1])
    raw = SimpleNamespace(
        times=times,
        displacements=np.vstack((_vector(), _vector(10.0))),
        velocities=np.vstack((_vector(20.0), _vector(30.0))),
        accelerations=np.vstack((_vector(40.0), _vector(50.0))),
        history_storage_mode="full",
        history_dof_indices=None,
        node_histories={},
        displacement_envelope=None,
        velocity_envelope=None,
        acceleration_envelope=None,
        load_impulse=_vector(60.0),
        force_impulse=np.array([1.0, 2.0, 3.0]),
        moment_impulse=np.array([4.0, 5.0, 6.0]),
        stress_history=(
            {5: {"von_mises": np.array([10.0, 11.0])}},
            {5: {"von_mises": np.array([12.0, 13.0])}},
        ),
        reactions={10: np.arange(6, dtype=float)},
        diagnostics={
            "kinetic_energy": [1.0, 2.0],
            "strain_energy": [3.0, 4.0],
        },
        result_case={"analysis_case": {"type": "linear_transient"}},
    )
    payload = result_artifact_payload(_transient_solution(raw))

    assert payload.fields["displacement"][1].shape == (2, 2, 6)
    assert payload.fields["velocity"][1].shape == (2, 2, 6)
    assert payload.fields["acceleration"][1].shape == (2, 2, 6)
    assert payload.fields["load_impulse"][1].shape == (1, 2, 6)
    assert payload.fields["force_impulse"][1].shape == (1, 3)
    assert payload.fields["kinetic_energy"][1].shape == (2, 1)
    assert "kinetic_energy" in payload.histories
    assert payload.fields["stress_history_von_mises"][1].shape == (2, 1, 2)
    assert payload.fields["reaction"][1].shape == (1, 1, 6)
    assert payload.provenance["result_case"]["analysis_case"]["type"] == "linear_transient"


def test_selected_transient_maps_columns_to_selected_nodes_not_all_dofs():
    times = np.array([0.0, 0.1])
    raw = SimpleNamespace(
        times=times,
        displacements=np.arange(12, dtype=float).reshape(2, 6),
        velocities=(100.0 + np.arange(12, dtype=float)).reshape(2, 6),
        accelerations=(200.0 + np.arange(12, dtype=float)).reshape(2, 6),
        history_storage_mode="selected",
        history_dof_indices=np.arange(6, 12),
        node_histories={20: np.arange(12, dtype=float).reshape(2, 6)},
        displacement_envelope=None,
        velocity_envelope=None,
        acceleration_envelope=None,
        load_impulse=None,
        force_impulse=None,
        moment_impulse=None,
        stress_history=None,
        reactions={},
        diagnostics={},
        result_case=None,
    )
    payload = result_artifact_payload(_transient_solution(raw))
    descriptor, values = payload.fields["displacement"]
    assert values.shape == (2, 1, 6)
    assert payload.tables["displacement_node_ids"].tolist() == [20]
    assert descriptor.provenance["history_storage_mode"] == "selected"
    assert descriptor.provenance["global_dof_indices"] == list(range(6, 12))


def test_envelope_only_transient_does_not_fabricate_time_histories():
    raw = SimpleNamespace(
        times=np.array([0.0, 0.1, 0.2]),
        displacements=np.zeros((0, 0)),
        velocities=np.zeros((0, 0)),
        accelerations=np.zeros((0, 0)),
        history_storage_mode="envelope",
        history_dof_indices=None,
        node_histories={},
        displacement_envelope=_vector(),
        velocity_envelope=_vector(20.0),
        acceleration_envelope=None,
        load_impulse=None,
        force_impulse=None,
        moment_impulse=None,
        stress_history=None,
        reactions={},
        diagnostics={},
        result_case=None,
    )
    payload = result_artifact_payload(_transient_solution(raw))
    assert "displacement" not in payload.fields
    assert "velocity" not in payload.fields
    descriptor, values = payload.fields["displacement_envelope"]
    assert values.shape == (1, 2, 6)
    assert descriptor.recovery == "envelope"
    assert descriptor.reduction == "max_abs"
    assert not descriptor.deformation_required
    assert "acceleration_envelope" not in payload.fields


def test_impact_retains_contact_damage_and_committed_stress_histories(tmp_path):
    times = np.array([0.0, 0.1])
    raw = SimpleNamespace(
        times=times,
        displacements=np.vstack((_vector(), _vector(1.0))),
        velocities=np.vstack((_vector(2.0), _vector(3.0))),
        accelerations=np.vstack((_vector(4.0), _vector(5.0))),
        history_storage_mode="full",
        node_histories={},
        load_impulse=_vector(),
        force_impulse=np.ones(3),
        moment_impulse=np.ones(3) * 2,
        sphere_impulse=np.ones(3) * 3,
        sphere_positions=np.array([[0, 0, 1], [0, 0, 0.9]], dtype=float),
        sphere_velocities=np.array([[0, 0, -1], [0, 0, -0.5]], dtype=float),
        sphere_accelerations=np.zeros((2, 3)),
        contact_force_history=np.array([[0, 0, 0], [0, 0, 100]], dtype=float),
        active_contact_history=((), ({"element_id": 7, "penetration": 0.01},)),
        diagnostics={
            "kinetic_energy": [4.0, 2.0],
            "strain_energy": [0.0, 1.0],
            "impact_damage_summary": {"damaged_element_ids": [7]},
            "damage_state_update_count": 1,
            "erosion_summary": {"all_eroded_element_ids": []},
            "state_von_mises_history": ({7: 10.0}, {7: 12.0}),
        },
        reactions={},
        result_case=None,
    )
    solution = ImpactSolution(
        built=_built(),
        shapes=[],
        status="completed",
        info={"raw": raw},
        times=times,
    )
    payload = result_artifact_payload(solution)
    assert payload.fields["contact_force"][1].shape == (2, 3)
    assert payload.fields["impactor_position"][1].shape == (2, 3)
    assert payload.fields["impactor_impulse"][1].shape == (1, 3)
    assert "contact_force_magnitude" in payload.histories
    assert "active_contact_history" in payload.tables
    assert payload.tables["impact_damage_summary"]["damaged_element_ids"] == [7]
    assert "committed_von_mises_history_von_mises" in payload.fields

    artifact = write_solution_artifact(
        ArtifactStore(tmp_path / "impact.anyfem"),
        solution,
        job_id="impact-job",
        document_id="document",
        mesh_id="mesh",
        model_hash="model",
        mesh_hash="mesh-hash",
        analysis_hash="analysis",
    )
    assert ArtifactStore(tmp_path / "impact.anyfem").open_result(artifact).field(
        "contact_force"
    ).shape == (2, 3)


def test_imported_stress_only_result_has_no_displacement_field():
    built = _built()
    imported = ImportedResults(
        source=Path("stress-only.sif"),
        format="SESAM",
        node_stresses={10: {"sxx": 100.0}, 20: {"sxx": 200.0}},
        reactions={10: (1.0, 2.0, 3.0)},
    )
    solution = ImportedSolution(
        displacements=np.full(12, np.nan),
        built=built,
        results=imported,
        components=frozenset(),
        fields={
            "sxx": Field(
                name="sxx",
                unit="Pa",
                node_values={10: 100.0, 20: 200.0},
            )
        },
        covered=2,
    )
    payload = result_artifact_payload(solution)
    assert "displacement" not in payload.fields
    assert payload.fields["stress_sxx"][1].shape == (1, 2, 1)
    assert payload.fields["stress_sxx"][0].recovery == "imported"
    assert payload.fields["reaction"][1].shape == (1, 1, 3)


def test_imported_translations_retain_only_components_the_file_contains():
    built = _built()
    vector = np.full(12, np.nan)
    vector[:3] = [1.0, 2.0, 3.0]
    vector[6:9] = [4.0, 5.0, 6.0]
    imported = ImportedResults(
        source=Path("result.frd"),
        format="CalculiX",
        displacements={10: (1.0, 2.0, 3.0), 20: (4.0, 5.0, 6.0)},
    )
    solution = ImportedSolution(
        displacements=vector,
        built=built,
        results=imported,
        components=frozenset(("ux", "uy", "uz")),
        covered=2,
    )
    descriptor, values = result_artifact_payload(solution).fields["displacement"]
    assert descriptor.components == ("ux", "uy", "uz")
    assert descriptor.unit == "m"
    assert values.shape == (1, 2, 3)
    assert np.all(np.isfinite(values))


def test_writer_helper_persists_hashes_provenance_and_frame_major_fields(tmp_path):
    solution = LinearSolution(displacements=_vector(), built=_built())
    store = ArtifactStore(tmp_path / "model.anyfem")
    artifact = write_solution_artifact(
        store,
        solution,
        job_id="job-result-adapter",
        document_id="document-1",
        mesh_id="mesh-1",
        model_hash="model-hash",
        mesh_hash="mesh-hash",
        analysis_hash="analysis-hash",
        provenance={"submission": {"document_hash": "document-hash"}},
        diagnostics=({"path": Path("run.log"), "nonfinite": float("nan")},),
    )
    dataset = store.open_result(artifact)
    assert dataset.field("displacement").shape == (1, 2, 6)
    assert dataset.field("displacement").read(0).shape == (2, 6)
    provenance = dataset.metadata("provenance")
    assert provenance["hashes"] == {
        "analysis_hash": "analysis-hash",
        "mesh_hash": "mesh-hash",
        "model_hash": "model-hash",
    }
    assert provenance["submission"]["document_hash"] == "document-hash"
    assert provenance["submission"]["project_hash"] == "document-hash"
    assert provenance["submission"]["job_hash"].startswith("sha256:")
    assert provenance["producer_versions"]["python"]
    assert dataset.metadata("diagnostics")[0]["nonfinite"] == "NaN"
    assert store.verify(artifact)


def test_inputs_helper_matches_the_writer_keyword_contract():
    inputs = build_result_artifact_inputs(
        LinearSolution(displacements=_vector(), built=_built())
    )
    assert set(inputs) == {
        "fields",
        "frames",
        "frame_kind",
        "histories",
        "tables",
        "provenance",
        "summary",
        "diagnostics",
        "partial",
    }
