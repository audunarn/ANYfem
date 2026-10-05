"""Boundary aliases must not create duplicate or unassigned physical beams."""
from types import SimpleNamespace
import pytest
from anyfem.model.project import Project, ProjectError
from anyfem.model.materials import steel
from anyfem.model.sections import BeamSection
from anyfem.solve.build import _add_beams


def setup():
    project = Project()
    project.add_material(steel("S355"))
    project.add_beam_section(BeamSection("T", "T-bar", "S355", web_height=.2,
        web_thickness=.008, flange_width=.1, flange_thickness=.01, web_direction=(0,0,1)))
    points = project.geometry.add_points(((0,0,0),(1,0,0),(2,0,0)))
    beam, alias = project.geometry.add_lines(((points[0],points[1]),(points[1],points[2])))
    project.assign_beam(beam,"T")
    mesh = SimpleNamespace(is_quadratic=False, beams={1:(1,2)}, elements_of_edge={alias:[1],beam:[1]})
    added = []
    return project, mesh, SimpleNamespace(add_element=lambda *args: added.append(args)), added, alias


def test_source_boundary_alias_assembles_beam_once():
    project,mesh,model,added,_ = setup()
    _add_beams(project,mesh,model)
    assert [entry[0] for entry in added] == [1]


def test_repeated_same_owner_entry_assembles_beam_once():
    project,mesh,model,added,alias = setup()
    beam = next(edge for edge in mesh.elements_of_edge if edge != alias)
    mesh.elements_of_edge[beam] = [1,1]
    _add_beams(project,mesh,model)
    assert [entry[0] for entry in added] == [1]


def test_orphan_beam_is_not_silently_dropped():
    project,mesh,model,_,_ = setup()
    mesh.beams[2] = (2,3)
    with pytest.raises(ProjectError,match="no section-bearing"):
        _add_beams(project,mesh,model)


def test_ambiguous_assigned_alias_is_rejected():
    project,mesh,model,_,alias = setup()
    project.assign_beam(alias,"T")
    with pytest.raises(ProjectError,match="multiple section-bearing"):
        _add_beams(project,mesh,model)
