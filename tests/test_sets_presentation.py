"""Presentation sets never rewrite canonical scopes."""
from anyfem.application.controller import default_project
from anyfem import commands
from anyfem.model.regions import Region,ManualRegion,BooleanRegion,MeshEntityRef
from anyfem.presentation.sets import is_user_set,is_geometry_set,set_geometry_references
from anyfem.tables import CategoryAdapter
from anyfem.io.project_file import project_to_dict


def test_visible_mesh_boolean_and_internal_sets_have_separate_capabilities():
    p=default_project();v=commands.AddPoint(0,0).do(p);ref=p.geometry.entity_ref('vertex',v)
    manual=Region('Named','geometry','vertex',ManualRegion((ref,)))
    internal=Region('Hidden','geometry','vertex',ManualRegion((ref,)),hidden=True)
    mesh=Region('Mesh nodes','mesh','node',ManualRegion((MeshEntityRef('mesh1','node',1),)))
    for r in (manual,internal,mesh):p.regions.add(r)
    boolean=Region('Combined','geometry','vertex',BooleanRegion('union',(manual.id,internal.id)));p.regions.add(boolean)
    before=project_to_dict(p)
    assert is_user_set(mesh) and not is_geometry_set(mesh)
    assert is_user_set(boolean) and not is_geometry_set(boolean)
    adapter=CategoryAdapter(p,'Sets');assert set(adapter.by_key)=={manual.id,mesh.id,boolean.id}
    assert set_geometry_references(p,mesh.id)==()
    assert set_geometry_references(p,boolean.id)==(ref,)
    assert project_to_dict(p)==before
