"""Desktop box selection must preserve the solver's plate and history contracts."""
from dataclasses import replace
from types import SimpleNamespace

import numpy as np
import pytest
from anygeometry.serialization import to_dict

from anyfem.submodel import SubmodelError
from anyfem.submodel_design import (
    RemoveSubmodelDefinition, ReplaceSubmodelDefinition, solid_box_footprint,
)
from test_shell_driven_solid_jobs import make_definition
from test_submodel_workflow import grid_project


def test_box_selects_real_interior_footprint_and_both_skins():
    project, faces = grid_project("plate", [0, 1], [0, 1])
    face = faces[0, 0]
    footprint = np.array(solid_box_footprint(project, face, (.2, .3, -.005), (.7, .8, .005)))
    assert set(tuple(p) for p in np.round(footprint, 10)) == {
        (.2, .3, 0), (.2, .8, 0), (.7, .3, 0), (.7, .8, 0),
    }
    normal = project.geometry.face_normal(face, .5, .5)
    assert np.dot(np.cross(footprint[1] - footprint[0], footprint[2] - footprint[1]), normal) > 0


@pytest.mark.parametrize(("lower", "upper", "code"), [
    ((.1, .2, -.004), (.6, .7, .004), "CUT_BOX_THICKNESS"),
    ((.1, .2, .04), (.6, .7, .08), "CUT_BOX_MISSES_PLATE"),
    ((-1., .2, -.1), (.6, .7, .1), "DETAIL_"),
    ((.1, .2, -.1), (.1, .7, .1), "CUT_BOX_BOUNDS"),
])
def test_box_refusals_preserve_source(lower, upper, code):
    project, faces = grid_project("plate", [0, 1], [0, 1])
    original_geometry = to_dict(project.geometry)
    with pytest.raises(SubmodelError) as caught:
        solid_box_footprint(project, faces[0, 0], lower, upper)
    assert caught.value.code.startswith(code)
    assert to_dict(project.geometry) == original_geometry


def test_replace_and_delete_keep_history_and_undo_exactly():
    initial = make_definition()
    history = {"run": object()}
    project = SimpleNamespace(submodels={initial.id: initial}, submodel_runs=history)
    replacement = replace(initial, id="preview-id", target_size=.02)
    edit = ReplaceSubmodelDefinition(initial, replacement)
    edit.do(project)
    assert set(project.submodels) == {initial.id}
    assert project.submodels[initial.id].target_size == .02
    with pytest.raises(SubmodelError, match="changed"):
        RemoveSubmodelDefinition(initial).do(project)
    delete = RemoveSubmodelDefinition(project.submodels[initial.id])
    delete.do(project)
    assert not project.submodels and project.submodel_runs is history
    delete.undo(project)
    edit.undo(project)
    assert project.submodels[initial.id].to_dict() == initial.to_dict()
    edit.redo(project)
    assert project.submodels[initial.id].target_size == .02
