from __future__ import annotations

import pytest

from anygeometry.model import GeometryModel

from anyfem.io.project_file import FORMAT_VERSION, project_from_dict, project_to_dict
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

    assert payload["anyfem"]["format"] == FORMAT_VERSION == 5
    assert restored.geometry.model_id == project.geometry.model_id
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
