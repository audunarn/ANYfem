"""Headless coverage for the commercial-style meshing method selector."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from anygeometry import GeometryModel
from anymesher import MeshAutomationOptions

from anyfem.document import DocumentSession
from anyfem.io.artifacts import ArtifactStore
from anyfem.io.project_file import project_from_dict, project_to_dict
from anyfem.mesh_jobs import MeshJobResult, MeshSettings, MeshTaskManager
from anyfem.mesh_controls import MeshControls, StructuredMeshControls
from anyfem.model.project import Project
from anyfem.ui.app import AnyFemApp
from anyfem.ui.panels import MeshPanel, mapped_mesh_eligibility


class _Value:
    def __init__(self, value: str) -> None:
        self.value = value

    def get(self) -> str:
        return self.value

    def set(self, value: str) -> None:
        self.value = value


def _plate_geometry(points) -> GeometryModel:
    geometry = GeometryModel()
    geometry.add_plate(geometry.add_points(points))
    return geometry


def test_mapped_eligibility_explains_supported_and_unsupported_plates() -> None:
    rectangle = _plate_geometry(
        ((0.0, 0.0, 0.0), (2.0, 0.0, 0.0),
         (2.0, 1.0, 0.0), (0.0, 1.0, 0.0))
    )
    eligible, message = mapped_mesh_eligibility(rectangle)
    assert eligible
    assert "four" in message
    assert "no holes" in message

    triangle = _plate_geometry(
        ((0.0, 0.0, 0.0), (2.0, 0.0, 0.0), (0.0, 1.0, 0.0))
    )
    eligible, message = mapped_mesh_eligibility(triangle)
    assert not eligible
    assert "Mapped is unavailable" in message
    assert "four-sided" in message
    assert "Automatic/Unstructured" in message


def test_mapped_preference_accepts_owner_supported_triangular_plate() -> None:
    project = Project("mapped preference")
    project.geometry = _plate_geometry(
        ((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0))
    )
    mesh = project.generate_mesh(
        0.25, order="linear", strategy="mapped",
        automation=MeshAutomationOptions(),
    )
    record = mesh.hybrid_diagnostics["automation"]
    assert record["selected_method"] == "mapped"
    assert record["status"] == "ready"
    assert mesh.num_elements > 0


def test_automatic_multiface_mapped_request_records_actual_route() -> None:
    project = Project("two mapped plates")
    geometry = project.geometry
    vertices = geometry.add_points((
        (0.0, 0.0, 0.0), (1.0, 0.0, 0.0),
        (1.0, 1.0, 0.0), (0.0, 1.0, 0.0),
        (2.0, 0.0, 0.0), (2.0, 1.0, 0.0),
    ))
    geometry.add_plate(vertices[:4])
    geometry.add_plate((vertices[1], vertices[4], vertices[5], vertices[2]))
    mesh = project.generate_mesh(
        0.25, order="linear", strategy="mapped",
        automation=MeshAutomationOptions(),
    )
    assert mesh.hybrid_diagnostics["automation"]["selected_method"] == "mapped"
    assert mesh.hybrid_diagnostics["automation"]["status"] == "ready"
    assert mesh.num_elements > 0


def test_missing_recovery_api_does_not_break_legacy_meshing(monkeypatch) -> None:
    import anymesher

    project = Project("older owner")
    project.geometry = _plate_geometry(
        ((0.0, 0.0, 0.0), (1.0, 0.0, 0.0),
         (1.0, 1.0, 0.0), (0.0, 1.0, 0.0))
    )
    monkeypatch.delattr(anymesher, "generate_automatic_mesh_result")
    assert project.generate_mesh(0.25, order="linear").num_elements > 0
    with pytest.raises(ValueError, match="automatic mesh recovery requires"):
        project.generate_mesh(
            0.25, order="linear", automation=MeshAutomationOptions(),
        )


def test_mesh_settings_strategy_is_canonical_and_hash_affecting() -> None:
    automatic = MeshSettings.create(0.25, element_order="linear")
    mapped = MeshSettings.create(
        0.25, element_order="linear", strategy="MAPPED"
    )
    native = MeshSettings.create(
        0.25, element_order="linear", strategy="native"
    )

    # Unspecified retains the pre-selector contract: inherit the snapshotted
    # project setting (Automatic when the project has no explicit setting).
    assert automatic.strategy is None
    assert mapped.strategy == "mapped"
    assert native.strategy == "native"
    assert len({automatic.input_hash, mapped.input_hash, native.input_hash}) == 3
    with pytest.raises(ValueError, match="expected one of auto, mapped, native"):
        MeshSettings.create(0.25, element_order="linear", strategy="magic")

    quad_first = MeshSettings.create(
        0.25,
        element_order="linear",
        strategy="auto",
        structure_preference="quad_first",
    )
    size_first = MeshSettings.create(
        0.25,
        element_order="linear",
        strategy="auto",
        structure_preference="size_first",
    )
    assert quad_first.input_hash != size_first.input_hash
    strict_quality = MeshSettings.create(
        0.25,
        element_order="linear",
        strategy="auto",
        quality_policy={"maximum_aspect_ratio": 3.0},
    )
    assert dict(strict_quality.quality_policy)["maximum_aspect_ratio"] == 3.0
    assert strict_quality.input_hash != quad_first.input_hash
    # The preference is not solver-affecting when the user explicitly chooses
    # a non-Automatic route.
    assert MeshSettings.create(
        0.25,
        element_order="linear",
        strategy="mapped",
        structure_preference="quad_first",
    ).input_hash == MeshSettings.create(
        0.25,
        element_order="linear",
        strategy="mapped",
        structure_preference="size_first",
    ).input_hash

    recoverable = MeshSettings.create(
        0.25, element_order="linear", strategy="quad_first",
        automation=MeshAutomationOptions(),
    )
    strict = MeshSettings.create(
        0.25, element_order="linear", strategy="quad_first",
        automation=MeshAutomationOptions(strict_method=True),
    )
    assert recoverable.input_hash != strict.input_hash
    assert recoverable.input_hash != MeshSettings.create(
        0.25, element_order="linear", strategy="quad_first",
    ).input_hash


def test_existing_native_settings_schema_persists_the_method_without_tk() -> None:
    project = Project("method")
    fake_app = SimpleNamespace(project=project)

    AnyFemApp._store_mesh_strategy(
        fake_app,
        "mapped",
        target_size=0.3,
        element_order="quadratic",
        structure_preference="quad_first",
        quality_policy={"maximum_aspect_ratio": 3.5},
    )

    settings = project.native_mesh_settings
    assert settings is not None
    assert settings.backend.value == "mapped"
    assert settings.target_size == pytest.approx(0.3)
    assert settings.element_order == "quadratic"
    assert dict(settings.parameters)["structure_preference"] == "quad_first"
    assert dict(settings.parameters)["mesh_quality_maximum_aspect_ratio"] == 3.5
    assert AnyFemApp._project_mesh_strategy(project, None) == "mapped"

    reopened = project_from_dict(project_to_dict(project))
    assert reopened.native_mesh_settings is not None
    assert reopened.native_mesh_settings.backend.value == "mapped"
    assert AnyFemApp._project_mesh_strategy(reopened, None) == "mapped"
    assert AnyFemApp._project_structure_preference(reopened, None) == "quad_first"


def test_explicit_quad_first_settings_persist_and_change_cache_identity() -> None:
    from anyfem.quad_first import sg1_quad_options

    project = Project("quad-first")
    fake_app = SimpleNamespace(project=project)
    AnyFemApp._store_mesh_strategy(
        fake_app, "quad_first", target_size=0.25, element_order="quadratic",
        quad_options=sg1_quad_options(),
    )
    reopened = project_from_dict(project_to_dict(project))
    assert AnyFemApp._project_mesh_strategy(reopened, None) == "quad_first"
    assert reopened.native_mesh_settings.to_dict()["parameters"]["quad_options"] == sg1_quad_options().to_dict()
    selected = MeshSettings.create(0.25, element_order="quadratic", strategy="quad_first")
    legacy = MeshSettings.create(0.25, element_order="quadratic", strategy="native")
    assert selected.input_hash != legacy.input_hash


def test_adaptive_quad_layout_persists_and_changes_job_identity() -> None:
    project = Project("adaptive quad layout")
    fake_app = SimpleNamespace(project=project)
    AnyFemApp._store_mesh_strategy(
        fake_app, "quad_first", target_size=0.5, element_order="linear",
        layout_policy="adaptive",
    )
    reopened = project_from_dict(project_to_dict(project))
    assert reopened.native_mesh_settings.to_dict()["parameters"]["layout_policy"] == "adaptive"
    existing = MeshSettings.create(0.5, element_order="linear", strategy="quad_first")
    adaptive = MeshSettings.create(0.5, element_order="linear", strategy="quad_first",
                                   layout_policy="adaptive")
    assert existing.input_hash != adaptive.input_hash
    with pytest.raises(ValueError, match="requires strategy"):
        MeshSettings.create(0.5, element_order="linear", strategy="native",
                            layout_policy="adaptive")


def test_explicit_quad_first_reaches_owner_without_changing_source() -> None:
    from anygeometry.serialization import to_dict as geometry_to_dict

    project = Project("quad-first owner")
    project.geometry = _plate_geometry(
        ((0.0, 0.0, 0.0), (1.0, 0.0, 0.0),
         (1.0, 1.0, 0.0), (0.0, 1.0, 0.0))
    )
    before = geometry_to_dict(project.geometry)
    mesh = project.generate_mesh(0.5, order="quadratic", strategy="quad_first")
    assert geometry_to_dict(project.geometry) == before
    assert mesh.shells
    assert all(len(shell) in (6, 8) for shell in mesh.shells.values())


def test_quad_first_background_snapshot_publishes_validated_mesh() -> None:
    from anyfem.mesh_jobs import _cancellation_token

    project = Project("quad-first job")
    project.geometry = _plate_geometry(
        ((0.0, 0.0, 0.0), (1.0, 0.0, 0.0),
         (1.0, 1.0, 0.0), (0.0, 1.0, 0.0))
    )
    snapshot = DocumentSession(project).snapshot()
    manager = MeshTaskManager()
    try:
        manager._run(
            "quad", snapshot,
            MeshSettings.create(0.5, element_order="quadratic", strategy="quad_first"),
            _cancellation_token(),
        )
        events = manager.poll()
    finally:
        manager.shutdown()
    completed = [event for event in events if event.kind == "completed"]
    assert len(completed) == 1
    result = completed[0].payload
    assert result.mesh.hybrid_diagnostics["high_order_geometry"]["status"] == "CERTIFIED_POSITIVE"


def test_cancelled_quad_first_job_does_not_publish_partial_mesh() -> None:
    from anyfem.mesh_jobs import _cancellation_token

    project = Project("cancel quad-first")
    project.geometry = _plate_geometry(
        ((0.0, 0.0, 0.0), (1.0, 0.0, 0.0),
         (1.0, 1.0, 0.0), (0.0, 1.0, 0.0))
    )
    token = _cancellation_token()
    token.cancel("RA1 cancellation control")
    manager = MeshTaskManager()
    try:
        manager._run(
            "cancelled-quad", DocumentSession(project).snapshot(),
            MeshSettings.create(0.5, element_order="quadratic", strategy="quad_first"),
            token,
        )
        events = manager.poll()
    finally:
        manager.shutdown()
    assert any(event.kind == "cancelled" for event in events)
    assert not any(event.kind == "completed" for event in events)


def test_quad_first_unsupported_beam_requests_remain_typed():
    from anymesher.hybrid import generate_hybrid_mesh_result
    from anymesher.quad.options import QuadMeshingOptions
    from anymesher.quad.public_integration import QuadPublicUnsupported
    from anymesher.errors import MeshError
    from anyfem import steel
    from anyfem.model import BeamSection

    geometry = _plate_geometry(
        ((0.0, 0.0, 0.0), (1.0, 0.0, 0.0),
         (1.0, 1.0, 0.0), (0.0, 1.0, 0.0))
    )
    face = next(iter(geometry.faces))
    boundary = geometry.faces[face].loop[0].edge
    project = Project("boundary beam")
    project.geometry = geometry
    project.add_material(steel())
    project.add_plate_section("plate", 0.01, "S355")
    project.add_beam_section(BeamSection(
        name="beam", profile="Flatbar", material="S355",
        flange_width=0.01, flange_thickness=0.1,
    ))
    project.assign_plate(face, "plate")
    project.assign_beam(boundary, "beam")
    with pytest.raises(QuadPublicUnsupported, match="source-boundary edge"):
        project.generate_mesh(0.5, order="quadratic", strategy="quad_first")
    start = geometry.add_point(0.0, 0.0, 1.0)
    middle = geometry.add_point(0.5, 0.5, 1.0)
    end = geometry.add_point(1.0, 0.0, 1.0)
    arc = geometry.add_arc(start, middle, end)
    with pytest.raises(MeshError, match="curved|straight|B3"):
        generate_hybrid_mesh_result(
            geometry, face_ids=(face,), beam_edges=(arc,),
            target_size=0.5, order="quadratic",
            quad_options=QuadMeshingOptions(quality_model="shape_jacobian"),
        )


def test_panel_routes_mapped_selection_and_hides_irrelevant_triangulator() -> None:
    project = Project("mapped route")
    project.geometry = _plate_geometry(
        ((0.0, 0.0, 0.0), (1.0, 0.0, 0.0),
         (1.0, 1.0, 0.0), (0.0, 1.0, 0.0))
    )
    submissions: list[dict] = []
    app = SimpleNamespace(
        project=project,
        run=lambda _command: None,
        generate_mesh_async=lambda size, **options: submissions.append(
            {"size": size, **options}
        ),
    )
    panel = SimpleNamespace(
        app=app,
        _size=_Value("0.2"),
        _order=_Value("linear"),
        _native_backend=_Value("Compiled native"),
        _strict_method=_Value(False),
        _method_dirty=True,
        _preference_dirty=True,
        number=lambda variable, _label: float(variable.get()),
        _method_value=lambda: "mapped",
        _structure_preference_value=lambda: "balanced",
        _mesh_controls_value=lambda: MeshControls(),
        _structured_controls_value=lambda: StructuredMeshControls(),
    )

    MeshPanel._generate(panel)

    assert submissions == [
        {
            "size": 0.2,
            "native_backend": None,
            "strategy": "mapped",
            "structure_preference": "balanced",
            "mesh_controls": MeshControls(),
            "structured_controls": StructuredMeshControls(),
            "automation": MeshAutomationOptions(),
        }
    ]
    assert panel._method_dirty is False
    assert panel._preference_dirty is False


def test_mapped_submission_reaches_anymesher_as_mapped() -> None:
    project = Project("mapped worker")
    project.geometry = _plate_geometry(
        ((0.0, 0.0, 0.0), (1.0, 0.0, 0.0),
         (1.0, 1.0, 0.0), (0.0, 1.0, 0.0))
    )
    snapshot = DocumentSession(project).snapshot()
    manager = MeshTaskManager()
    try:
        # Calling the worker body directly keeps this qualification bounded and
        # avoids a polling sleep while still exercising snapshot thawing and
        # the real Project -> ANYmesher strategy hand-off.
        from anyfem.mesh_jobs import _cancellation_token

        manager._run(
            "mapped",
            snapshot,
            MeshSettings.create(
                0.5, element_order="linear", strategy="mapped"
            ),
            _cancellation_token(),
        )
        events = manager.poll()
    finally:
        manager.shutdown()

    completed = [event for event in events if event.kind == "completed"]
    assert len(completed) == 1
    assert isinstance(completed[0].payload, MeshJobResult)
    assert set(
        completed[0].payload.mesh.hybrid_diagnostics["strategy_by_face"].values()
    ) == {"mapped"}


@pytest.mark.parametrize(
    ("strategy", "actual"),
    (("auto", "mapped"), ("mapped", "mapped"), ("native", "native")),
)
def test_each_ui_strategy_reaches_real_project_meshing(
    strategy: str, actual: str
) -> None:
    project = Project(f"{strategy} strategy")
    project.geometry = _plate_geometry(
        ((0.0, 0.0, 0.0), (1.0, 0.0, 0.0),
         (1.0, 1.0, 0.0), (0.0, 1.0, 0.0))
    )
    project.set_native_triangulation_backend("python")

    mesh = project.generate_mesh(0.5, strategy=strategy)

    assert set(mesh.hybrid_diagnostics["strategy_by_face"].values()) == {actual}


def test_strict_native_does_not_forward_structured_layout_options() -> None:
    project = Project("strict native handoff")
    project.geometry = _plate_geometry(
        ((0.0, 0.0, 0.0), (1.0, 0.0, 0.0),
         (1.0, 1.0, 0.0), (0.0, 1.0, 0.0))
    )
    project.set_native_triangulation_backend("python")

    mesh = project.generate_mesh(
        0.5,
        strategy="native",
        structure_preference="quad_first",
    )

    assert set(mesh.hybrid_diagnostics["strategy_by_face"].values()) == {"native"}


def test_anymesher_quality_optimization_provenance_is_retained() -> None:
    mesh = SimpleNamespace(
        hybrid_diagnostics={
            "triangulation_backend_by_face": {
                7: {
                    "requested_backend": "auto",
                    "actual_backend": "native",
                    "quality_optimization": {
                        "passes": 3,
                        "poor_before": 5,
                        "poor_after": 1,
                        "final_quality": {
                            "min_scaled_jacobian": 0.42,
                            "min_angle": 36.0,
                            "max_angle": 142.0,
                            "poor_element_ids": [11],
                        },
                    },
                }
            }
        }
    )

    assert AnyFemApp._mesh_quality_optimization_summary(mesh) == {
        "7": {
            "passes": 3,
            "poor_before": 5,
            "poor_after": 1,
            "final_quality": {
                "min_scaled_jacobian": 0.42,
                "min_angle": 36.0,
                "max_angle": 142.0,
                "poor_element_ids": [11],
            },
        }
    }


def test_anymesher_complex_geometry_provenance_is_retained() -> None:
    report = {
        "component_count": 1,
        "components": [
            {
                "component_id": "faces:7,8",
                "classification": "trimmed_regular",
                "attempted_strategies": ["native_aligned", "decomposed_residual"],
                "selected_strategy": "native_aligned",
                "fallback_reason": None,
            }
        ],
    }
    rejection = {
        "reason": "whole_mesh_quality_regression",
        "aligned_quality": {"accepted": False},
        "accepted_baseline_quality": {"accepted": True},
    }
    mesh = SimpleNamespace(
        hybrid_diagnostics={
            "complex_geometry": report,
            "alignment_candidate_rejected": rejection,
        }
    )

    assert AnyFemApp._complex_geometry_summary(mesh) == {
        **report,
        "alignment_candidate_rejected": rejection,
    }


def test_automatic_preference_reaches_anymesher_and_report_is_retained() -> None:
    project = Project("structured automatic")
    project.geometry = _plate_geometry(
        ((0.0, 0.0, 0.0), (2.0, 0.0, 0.0),
         (2.0, 1.0, 0.0), (0.0, 1.0, 0.0))
    )
    project.set_native_triangulation_backend("python")

    mesh = project.generate_mesh(
        0.25, strategy="auto", structure_preference="quad_first"
    )
    report = AnyFemApp._structured_layout_summary(mesh)

    assert report["status"] == "applied"
    assert report["plan_hash"].startswith("sha256:")
    assert report["quality"]["accepted"] is True
    assert report["quality"]["minimum_scaled_jacobian"] == pytest.approx(1.0)


def test_structured_report_and_source_map_persist_in_mesh_artifact(tmp_path) -> None:
    project = Project("structured artifact")
    project.geometry = _plate_geometry(
        ((0.0, 0.0, 0.0), (2.0, 0.0, 0.0),
         (2.0, 1.0, 0.0), (0.0, 1.0, 0.0))
    )
    mesh = project.generate_mesh(
        0.25, strategy="auto", structure_preference="balanced"
    )
    preparation = project._last_mesh_preparation

    assert preparation["source_to_working"]
    store = ArtifactStore(tmp_path / "structured.anyfem")
    artifact = store.write_mesh(mesh, structural_preparation=preparation)
    metadata = store.read_mesh_metadata(artifact)

    assert metadata["structural_preparation"]["source_to_working"] == preparation[
        "source_to_working"
    ]
