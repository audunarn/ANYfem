"""Transaction snapshots respect ANYgeometry's read-only owner stores."""

from __future__ import annotations

import pytest

from anyfem.document import DocumentSession
from anyfem.io import project_to_dict
from anyfem.model.project import Project


def test_failed_transaction_restores_through_public_codec_and_keeps_owner_stores_read_only() -> None:
    project = Project("codec rollback")
    project.geometry.add_point(0.0, 0.0, 0.0)
    session = DocumentSession(project)
    before = project_to_dict(project)

    with pytest.raises(RuntimeError, match="rollback"):
        with session.transaction("failing geometry edit"):
            project.geometry.add_point(1.0, 0.0, 0.0)
            raise RuntimeError("rollback")

    assert project_to_dict(project) == before
    assert session.commands.project is project
    with pytest.raises(TypeError):
        project.geometry.vertices[99] = object()  # type: ignore[index]
