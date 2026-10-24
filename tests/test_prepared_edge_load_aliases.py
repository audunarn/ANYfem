"""Exact prepared shared-boundary aliases retain the design load path."""
import numpy as np
import pytest
from anygeometry import EntityRef
from anyfem.application.controller import default_project
from anyfem.commands import AddStiffenedPanel
from anyfem.solve.build import _line_load_to_nodes, build_fe_model
from anymesher.serialize import mesh_from_dict, mesh_to_dict


def connected_panels():
    project=default_project()
    for options in ({}, {'u_direction':(0,0,1)}, {'origin':(0,0,5)}):
        AddStiffenedPanel(5,10,.5,beam_section='stiffener',plate_section='plate',**options).do(project)
    case=project.load_case('default')
    for edge in range(126,184,3):
        case.add_line_load(EntityRef('edge',edge),(0,0,-100000))
    return project


def assert_load_paths(project, mesh):
    total=np.zeros(3)
    for edge in range(126,184,3):
        sequence=mesh.nodes_of_edge[edge]
        geometry=project.geometry
        np.testing.assert_allclose(mesh.nodes[sequence[0]],geometry.vertex_position(geometry.edges[edge].start),atol=1e-12)
        np.testing.assert_allclose(mesh.nodes[sequence[-1]],geometry.vertex_position(geometry.edges[edge].end),atol=1e-12)
        force=sum((value for node,value in _line_load_to_nodes(mesh,EntityRef('edge',edge),np.array((0,0,-100000)))),np.zeros(3))
        np.testing.assert_allclose(force,(0,0,-50000),atol=1e-8)
        assert not set(sequence)&set(mesh.offset_nodes_of_edge.get(edge,()))
        total+=force
    np.testing.assert_allclose(total,(0,0,-1000000),atol=1e-7)


@pytest.mark.parametrize('order',['linear','quadratic'])
def test_connected_panel_design_line_alias_build_roundtrip_and_cache(order):
    project=connected_panels()
    mesh=project.generate_mesh(target_size=.5,order=order,strategy='quad_first',certification_mode='interactive')
    assert_load_paths(project,mesh)
    build_fe_model(project,mesh,require_supports=False)
    restored=mesh_from_dict(mesh_to_dict(mesh))
    assert_load_paths(project,restored)
    if order=='linear':
        # The prepared clone intentionally has no owner receipt. Only the
        # private exact source-identity/revision cache reuses derived paths.
        again=project.generate_mesh(target_size=.5,order=order,strategy='quad_first',certification_mode='interactive')
        assert_load_paths(project,again)
        assert again.nodes_of_edge==mesh.nodes_of_edge
        build_fe_model(project,again,require_supports=False)


def test_alias_uses_authenticated_higher_id_and_refuses_ambiguous_occurrences(monkeypatch):
    from types import SimpleNamespace
    from anygeometry import GeometryModel, extract_model_closure
    from anyfem.structural_preparation import source_edge_paths, StructuralPreparationError
    geometry=GeometryModel()
    orphan=geometry.add_line(geometry.add_point(0,0,0),geometry.add_point(1,0,0))
    face=geometry.add_plate(geometry.add_points(((0,0,0),(1,0,0),(1,1,0),(0,1,0))))
    canonical=geometry.faces[face].loop[0].edge
    assert canonical>orphan
    closure=extract_model_closure(geometry,(geometry.handle('edge',orphan),geometry.handle('face',face)),include_features=False)
    work=closure.working_model
    initial=closure.source_to_work[geometry.handle('edge',orphan)].id
    canonical=closure.source_to_work[geometry.handle('edge',canonical)].id
    ancestor=SimpleNamespace(model_id=work.model_id,definition=SimpleNamespace(edge_id=initial))
    def row(edge):return SimpleNamespace(edge_id=edge,ancestor=ancestor,interval=((0,1),(1,1)))
    binding=SimpleNamespace(records=(row(initial),),alias_records=(row(canonical),))
    monkeypatch.setattr('anygeometry.query_prepared_edge_subcurve_preimages',lambda model:binding)
    assert source_edge_paths(closure)[orphan]==((canonical,True),)
    # A second active, complete occurrence cannot be selected by numeric ID.
    other=work.faces[next(iter(work.faces))].loop[1].edge
    binding.alias_records+=(row(other),)
    with pytest.raises(StructuralPreparationError,match='ambiguous'):
        source_edge_paths(closure)


def test_partial_published_child_path_refuses_before_mesh_mutation():
    from types import SimpleNamespace
    from anygeometry import GeometryModel, extract_model_closure
    from anyfem.structural_preparation import remap_mesh_to_source, StructuralPreparationError
    geometry=GeometryModel()
    first=geometry.add_line(geometry.add_point(0,0,0),geometry.add_point(1,0,0))
    second=geometry.add_line(geometry.edges[first].end,geometry.add_point(2,0,0))
    closure=extract_model_closure(geometry,(geometry.handle('edge',first),geometry.handle('edge',second)),include_features=False)
    mesh=SimpleNamespace(structural_preparation={},elements_of_face={},elements_of_edge={},
        nodes_of_edge={first:[10,11]},offset_nodes_of_edge={})
    before=dict(mesh.nodes_of_edge)
    with pytest.raises(StructuralPreparationError,match='incomplete prepared nodes_of_edge'):
        remap_mesh_to_source(mesh,closure,edge_paths={first:((first,True),(second,True))})
    assert mesh.nodes_of_edge==before




def test_alias_seeding_does_not_count_orphan_and_canonical_twice():
    from types import SimpleNamespace
    from anygeometry import GeometryModel, extract_model_closure
    from anyfem.structural_preparation import remap_mesh_to_source
    geometry=GeometryModel()
    first=geometry.add_line(geometry.add_point(0,0,0),geometry.add_point(1,0,0))
    second=geometry.add_line(geometry.edges[first].start,geometry.edges[first].end)
    closure=extract_model_closure(geometry,(geometry.handle('edge',first),geometry.handle('edge',second)),include_features=False)
    mesh=SimpleNamespace(structural_preparation={},elements_of_face={},elements_of_edge={},nodes_of_edge={second:[10,11]},
        offset_nodes_of_edge={},node_of_vertex={},grid_of_face={},thickness_of_face={},
        seeding=SimpleNamespace(divisions={first:4,second:4},classes={first:1,second:2}))
    remap_mesh_to_source(mesh,closure,edge_paths={first:((second,True),)})
    assert mesh.seeding.divisions[first]==4
    assert mesh.nodes_of_edge[first]==[10,11]


def test_partial_offset_coverage_retains_full_design_axis():
    from types import SimpleNamespace
    from anygeometry import GeometryModel, extract_model_closure
    from anyfem.structural_preparation import remap_mesh_to_source
    geometry=GeometryModel()
    first=geometry.add_line(geometry.add_point(0,0,0),geometry.add_point(1,0,0))
    second=geometry.add_line(geometry.edges[first].end,geometry.add_point(2,0,0))
    closure=extract_model_closure(geometry,(geometry.handle('edge',first),geometry.handle('edge',second)),include_features=False)
    mesh=SimpleNamespace(structural_preparation={},elements_of_face={},elements_of_edge={first:[100]},
        nodes_of_edge={first:[10,11],second:[11,12]},offset_nodes_of_edge={first:[20,21]},
        node_of_vertex={},grid_of_face={},thickness_of_face={},seeding=None)
    remap_mesh_to_source(mesh,closure,edge_paths={first:((first,True),(second,True))})
    assert mesh.nodes_of_edge[first]==[10,11,12]
    assert mesh.offset_nodes_of_edge[first]==[20,21]
    assert mesh.elements_of_edge[first]==[100]


def test_older_owner_without_alias_query_retains_lineage_mapping(monkeypatch):
    import anygeometry
    from anygeometry import GeometryModel, extract_model_closure
    from anyfem.structural_preparation import source_edge_paths, source_work_mapping
    geometry=GeometryModel()
    edge=geometry.add_line(geometry.add_point(0,0,0),geometry.add_point(1,0,0))
    closure=extract_model_closure(geometry,(geometry.handle('edge',edge),),include_features=False)
    monkeypatch.delattr(anygeometry,'query_prepared_edge_subcurve_preimages')
    assert source_edge_paths(closure)=={}
    assert source_work_mapping(closure)['edge:'+str(edge)]==('edge:'+str(edge),)
