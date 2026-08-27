"""Immutable mesh snapshot contracts used when submitting solver jobs."""

from copy import deepcopy
from types import MappingProxyType
from types import SimpleNamespace

import pytest
from anymesher.serialize import mesh_to_dict

from anyfem.mesh_jobs import clone_mesh_for_job
from anyfem.document import DocumentSession
from anyfem.model.project import Project


def _plate_mesh():
    project = Project("job snapshot")
    points = project.geometry.add_points(
        ((0.0, 0.0, 0.0), (1.0, 0.0, 0.0),
         (1.0, 1.0, 0.0), (0.0, 1.0, 0.0))
    )
    project.geometry.add_plate(points)
    return project.generate_mesh(0.5)


def test_job_mesh_clone_accepts_read_only_geometry_provenance() -> None:
    mesh = _plate_mesh()
    mesh.structural_preparation = MappingProxyType(
        {
            "source_revision": 4,
            "source_to_working_faces": MappingProxyType({1: (1,)}),
        }
    )

    with pytest.raises(TypeError, match="mappingproxy"):
        deepcopy(mesh)

    cloned = clone_mesh_for_job(mesh)

    assert cloned is not mesh
    assert mesh_to_dict(cloned) == mesh_to_dict(mesh)
    assert cloned.structural_preparation == {
        "source_revision": 4,
        "source_to_working_faces": {"1": [1]},
    }


def test_failed_mesh_snapshot_does_not_leave_an_orphan_analysis(
    monkeypatch,
) -> None:
    from anyfem.ui import app as app_module

    project = Project("failed submission")
    application = SimpleNamespace(
        mesh=object(),
        imported=None,
        project=project,
        session=DocumentSession(project),
    )

    def reject_snapshot(_mesh):
        raise TypeError("unsupported mesh provenance")

    monkeypatch.setattr(app_module, "clone_mesh_for_job", reject_snapshot)

    with pytest.raises(TypeError, match="unsupported mesh provenance"):
        app_module.AnyFemApp.solve(application, "Linear static")

    assert project.analyses == {}
