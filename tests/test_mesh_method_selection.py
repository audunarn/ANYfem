"""Headless coverage for the commercial-style meshing method selector."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from anygeometry import GeometryModel

from anyfem.document import DocumentSession
from anyfem.io.artifacts import ArtifactStore
from anyfem.io.project_file import project_from_dict, project_to_dict
from anyfem.mesh_jobs import MeshJobResult, MeshSettings, MeshTaskManager
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
        _method_dirty=True,
        _preference_dirty=True,
        number=lambda variable, _label: float(variable.get()),
        _method_value=lambda: "mapped",
        _structure_preference_value=lambda: "balanced",
    )

    MeshPanel._generate(panel)

    assert submissions == [
        {
            "size": 0.2,
            "native_backend": None,
            "strategy": "mapped",
            "structure_preference": "balanced",
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

    assert report["plan"]["options"]["preference"] == "quad_first"
    assert report["metrics"]["quad_fraction"] == pytest.approx(1.0)
    assert len(report["blocks"]) >= 1


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

    assert preparation["structured_layout"]["source_to_working_faces"]
    store = ArtifactStore(tmp_path / "structured.anyfem")
    artifact = store.write_mesh(mesh, structural_preparation=preparation)
    metadata = store.read_mesh_metadata(artifact)

    assert metadata["structural_preparation"]["structured_layout"] == preparation[
        "structured_layout"
    ]
