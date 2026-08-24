from __future__ import annotations

import anyfem.model.project as project_module
import pytest

from anygeometry.model import GeometryModel

from anyfem.io.project_file import (
    FORMAT_VERSION,
    ProjectFileError,
    load_project,
    project_from_dict,
    project_to_dict,
    save_project,
)
from anyfem.model.project import Project, ProjectError
from anyfem.native_meshing import ControlScope, NativeMeshControl, NativeMeshSettings


def _project_with_polygon() -> tuple[Project, int]:
    project = Project("native-polygon")
    vertices = project.geometry.add_points(
        ((0.0, 0.0, 0.0), (1.2, 0.0, 0.0), (1.5, 0.7, 0.0),
         (0.7, 1.3, 0.0), (-0.1, 0.7, 0.0))
    )
    face_id = project.geometry.add_plate(vertices)
    return project, face_id


def test_native_settings_and_model_bound_handles_round_trip() -> None:
    project, face_id = _project_with_polygon()
    handle = project.geometry.handle("face", face_id)
    control = NativeMeshControl(
        control_id="local-face-size",
        scope=ControlScope(handles=(handle,), include_descendants=True),
        target_size=0.12,
    )
    settings = NativeMeshSettings(
        target_size=0.3,
        element_order="quadratic",
        backend="native",
        controls=(control,),
    )
    project.set_native_mesh_settings(settings)

    payload = project_to_dict(project)
    restored = project_from_dict(payload)

    assert payload["anyfem"]["format"] == FORMAT_VERSION == 8
    assert payload["meshing"]["native_backend"] == "auto"
    assert restored.geometry.model_id == project.geometry.model_id
    assert restored.native_triangulation_backend == "auto"
    assert restored.native_mesh_settings is not None
    assert restored.native_mesh_settings.to_dict() == settings.to_dict()
    assert restored.native_mesh_settings.handles == (handle,)


def test_native_settings_reject_foreign_geometry_handles() -> None:
    project, _face_id = _project_with_polygon()
    foreign = GeometryModel()
    vertex = foreign.add_point(0.0, 0.0, 0.0)
    settings = NativeMeshSettings(
        target_size=0.25,
        controls=(
            NativeMeshControl(
                control_id="foreign",
                scope=ControlScope(handles=(foreign.handle("vertex", vertex),)),
                target_size=0.1,
            ),
        ),
    )

    with pytest.raises(ProjectError, match="another geometry model"):
        project.set_native_mesh_settings(settings)


def test_project_generation_uses_persisted_native_defaults() -> None:
    project, face_id = _project_with_polygon()
    project.set_native_mesh_settings(
        NativeMeshSettings(target_size=0.32, backend="native")
    )

    mesh = project.generate_mesh()

    assert mesh.geometry_model_id == project.geometry.model_id
    assert mesh.elements_of_face[face_id]
    assert mesh.hybrid_diagnostics["strategy_by_face"][face_id] == "native"


def _legacy_document(version: int) -> dict:
    if version <= 2:
        return {
            "anyfem": {"format": version},
            "name": f"legacy-{version}",
            "geometry": {"vertices": [], "edges": [], "faces": []},
            "meshing": {"element_order": "linear"},
        }
    document = project_to_dict(Project(f"legacy-{version}"))
    document["anyfem"]["format"] = version
    document["meshing"].pop("native_backend")
    # This fixture synthesizes an old document from the current writer.  Drop
    # the v7-only owner-intent section instead of weakening the legacy reader's
    # strict unknown-generation check.
    document.pop("ownership", None)
    return document


@pytest.mark.parametrize("version", range(1, 6))
def test_legacy_backend_omission_migrates_to_python(version: int, tmp_path) -> None:
    restored = project_from_dict(_legacy_document(version))

    assert restored.native_triangulation_backend == "python"
    canonical = project_to_dict(restored)
    assert canonical["anyfem"]["format"] == FORMAT_VERSION
    assert canonical["meshing"]["native_backend"] == "python"
    reopened = load_project(
        save_project(restored, tmp_path / f"omitted-v{version}.anyfem")
    )
    assert project_to_dict(reopened) == canonical


@pytest.mark.parametrize("version", range(1, 6))
@pytest.mark.parametrize("location", ("settings", "control", "matching-both"))
def test_legacy_nested_backend_migrates_to_canonical_top_level(
    version: int, location: str, tmp_path
) -> None:
    document = _legacy_document(version)
    control = NativeMeshControl.create("legacy-control")
    native = NativeMeshSettings.create(
        target_size=0.3, controls=(control,)
    ).to_dict()
    if location in {"settings", "matching-both"}:
        native.setdefault("parameters", {})["native_backend"] = "native"
    if location in {"control", "matching-both"}:
        native["controls"][0]["parameters"]["native_backend"] = "native"
    document["meshing"]["native"] = native

    restored = project_from_dict(document)

    assert restored.native_triangulation_backend == "native"
    assert restored.native_mesh_settings is not None
    assert "native_backend" not in dict(restored.native_mesh_settings.parameters)
    canonical = project_to_dict(restored)
    assert canonical["meshing"]["native_backend"] == "native"
    assert "native_backend" not in canonical["meshing"]["native"]["parameters"]
    assert "native_backend" not in canonical["meshing"]["native"]["controls"][0]["parameters"]
    reopened = load_project(
        save_project(
            restored,
            tmp_path / f"explicit-v{version}-{location}.anyfem",
        )
    )
    assert project_to_dict(reopened) == canonical


def test_conflicting_legacy_nested_backends_fail_closed() -> None:
    document = _legacy_document(5)
    control = NativeMeshControl.create("legacy-control")
    native = NativeMeshSettings.create(
        target_size=0.3, controls=(control,)
    ).to_dict()
    native["parameters"]["native_backend"] = "python"
    native["controls"][0]["parameters"]["native_backend"] = "native"
    document["meshing"]["native"] = native

    with pytest.raises(ProjectFileError, match="conflicting legacy native_backend"):
        project_from_dict(document)


def test_format_six_requires_one_canonical_backend_source() -> None:
    document = project_to_dict(Project("missing-backend"))
    document["meshing"].pop("native_backend")

    with pytest.raises(ProjectFileError, match="native_backend is required"):
        project_from_dict(document)


@pytest.mark.parametrize("location", ("settings", "control"))
def test_format_six_rejects_every_nested_backend_duplicate(location: str) -> None:
    project, face_id = _project_with_polygon()
    control = NativeMeshControl(
        control_id="local",
        scope=ControlScope(handles=(project.geometry.handle("face", face_id),)),
    )
    project.set_native_mesh_settings(
        NativeMeshSettings(target_size=0.3, controls=(control,))
    )
    document = project_to_dict(project)
    nested = document["meshing"]["native"]
    if location == "settings":
        nested["parameters"]["native_backend"] = "auto"
    else:
        nested["controls"][0]["parameters"]["native_backend"] = "auto"

    with pytest.raises(ProjectFileError, match="non-canonical in format 6"):
        project_from_dict(document)


@pytest.mark.parametrize("value", (None, True, "automatic", "AUTO"))
def test_format_six_rejects_invalid_backend_values(value) -> None:
    document = project_to_dict(Project("invalid-backend"))
    document["meshing"]["native_backend"] = value

    with pytest.raises(ProjectFileError, match="must be one of auto, python, native"):
        project_from_dict(document)


def test_project_runtime_forwards_one_explicit_backend(monkeypatch) -> None:
    project, _face_id = _project_with_polygon()
    project.target_size = 0.3
    project.set_native_triangulation_backend("python")
    captured = {}
    expected = object()

    def fake_generate(_geometry, **kwargs):
        captured.update(kwargs)
        return expected

    monkeypatch.setattr(project_module, "generate_hybrid_mesh", fake_generate)

    assert project.generate_mesh() is expected
    assert captured["native_backend"] == "python"
    assert list(key for key in captured if key == "native_backend") == [
        "native_backend"
    ]


def test_nested_runtime_backend_setting_is_rejected() -> None:
    project = Project("nested-runtime-backend")
    settings = NativeMeshSettings.create(
        target_size=0.3, parameters={"native_backend": "python"}
    )

    with pytest.raises(ProjectError, match="project-level setting"):
        project.set_native_mesh_settings(settings)
