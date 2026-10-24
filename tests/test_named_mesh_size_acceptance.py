"""Independent production-consumer checks for reusable mesh-size properties."""
from dataclasses import replace
import numpy as np

from anyfem import commands
from anyfem.application.controller import default_project
from anyfem.mesh_sizes import MeshSizeProperty, NamedRefinement
from anyfem.io.project_file import project_to_dict, project_from_dict
from anyfem.document import _model_payload, canonical_hash


def test_named_size_reaches_mesher_and_reopens_with_live_property_links():
    project=default_project()
    commands.AddStiffenedPanel(2,2,1,beam_section="stiffener",plate_section="plate").do(project)
    stack=commands.CommandStack(project)
    size=MeshSizeProperty("Fine mesh",.25)
    stack.run(commands.SetMeshSizeProperty(size))
    refs=tuple(project.geometry.entity_ref("face",key) for key in project.face_sections)
    refs+=tuple(project.geometry.entity_ref("edge",key) for key in project.beam_edges)
    stack.run(commands.AssignMeshSize(size.id,refs))
    assert {item.ref for item in project.refinements if isinstance(item,NamedRefinement)}==set(refs)
    refined=project.generate_mesh(1.)
    coarse=project.generate_mesh(1.,refinements=[])
    lengths=lambda mesh:[np.linalg.norm(np.array(mesh.nodes[nodes[1]])-mesh.nodes[nodes[0]]) for nodes in mesh.beams.values()]
    assert len(refined.beams)>=3*len(coarse.beams)
    assert max(lengths(refined))<.5*max(lengths(coarse))
    loaded=project_from_dict(project_to_dict(project))
    assert loaded.mesh_size_properties[size.id]==size
    updated=replace(size,name="Finer mesh",size=.125)
    commands.SetMeshSizeProperty(updated).do(loaded)
    assert all(item.size==.125 and item.name=="Finer mesh" and item.property_id==size.id
               for item in loaded.refinements if isinstance(item,NamedRefinement))


def test_unused_definition_changes_document_without_changing_physical_hash():
    project=default_project()
    document=project_to_dict(project)
    before=canonical_hash(_model_payload(document))
    property=MeshSizeProperty("Available coarse mesh",2.)
    commands.SetMeshSizeProperty(property).do(project)
    after=project_to_dict(project)
    assert document!=after and not project.refinements
    assert canonical_hash(_model_payload(after))==before
