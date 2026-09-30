"""Optional owner capabilities must not break headless project imports."""

import anygeometry
import pytest

from anyfem import Project
from anyfem.structural_preparation import (
    StructuralPreparationError,
    prepare_structural_connectivity,
)


def test_missing_batch_capability_reports_before_geometry_mutation(monkeypatch):
    for name in ("IntersectionBatchPolicy", "plan_intersections", "apply_intersections"):
        monkeypatch.delattr(anygeometry, name, raising=False)
    project = Project("Published owner without batch intersections")
    before = project.geometry.revision
    with pytest.raises(StructuralPreparationError, match="lacks the batch-intersection API"):
        prepare_structural_connectivity(project.geometry)
    assert project.geometry.revision == before
