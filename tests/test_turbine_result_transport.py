import numpy as np
import pytest
from test_solve import cantilever_project
from test_wind_turbine_load import steady
from anyfem.application.floating_mass import dry_mass_properties
from anyfem.application.floating_transient import mass_identity

def test_public_static_build_and_result_transport():
    from test_solve import cantilever_project
    from anyfem import solve_linear_static
    from anyfem.solve.build import build_fe_model
    from anyfem.result_transport import encode_result,decode_result
    project,_,_=cantilever_project()
    baseline=solve_linear_static(project,target_size=1.)
    m=baseline.built.fe_model
    tip=max(m.mesh.nodes,key=lambda n:m.mesh.nodes[n].x)
    element=next(k for k,e in m.mesh.elements.items() if tip in e.node_ids)
    t=steady(hub_node_id=tip,mass_mode='additional',mass=100.,mass_cg_offset=(0.,0.,1.),
             inertia_cg=np.diag([3.,4.,5.]),attachment_element_id=element)
    project.add_wind_turbine_load(t)
    solution=solve_linear_static(project,mesh=baseline.built.mesh)
    assert dry_mass_properties(solution.built.fe_model).mass==pytest.approx(dry_mass_properties(m).mass+100.)
    from anyfem.job_process import dumps, loads
    restored=decode_result(loads(dumps(encode_result(solution))))
    assert mass_identity(dry_mass_properties(restored.built.fe_model))==mass_identity(dry_mass_properties(solution.built.fe_model))



def test_geometry_process_boundary_preserves_definition_not_live_authority():
    import weakref
    from anygeometry import GeometryModel,to_dict
    from anyfem.job_process import dumps,loads
    g=GeometryModel(); g.add_point(1.,2.,3.)
    called=[]; callback=lambda: called.append(True)
    g._intersection_preparation_receipt=(callback,)
    g._edge_subcurve_preimages_receipt=(weakref.ref(g),)
    copied=loads(dumps([g,g]))
    assert copied[0] is copied[1]
    assert to_dict(copied[0])==to_dict(g)
    assert not hasattr(copied[0],'_intersection_preparation_receipt')
    assert not hasattr(copied[0],'_edge_subcurve_preimages_receipt')
    g._intersection_preparation_receipt[0](); assert called==[True]


def test_transported_preparation_cannot_fall_back_to_lineage():
    from types import SimpleNamespace
    from anygeometry import GeometryModel
    from anyfem.job_process import dumps, loads
    from anyfem.structural_preparation import source_edge_paths, StructuralPreparationError
    g = GeometryModel()
    g._intersection_preparation_receipt = (lambda: None,)
    restored = loads(dumps(g))
    with pytest.raises(StructuralPreparationError, match="fresh owner preparation"):
        source_edge_paths(SimpleNamespace(working_model=restored))


def test_real_owner_preparation_is_invalidated_by_transport():
    from types import SimpleNamespace
    from anyfem.job_process import dumps, loads
    from anyfem.structural_preparation import prepare_structural_connectivity, source_edge_paths, StructuralPreparationError
    project, _, _ = cantilever_project()
    g = project.geometry
    prepare_structural_connectivity(g)
    assert hasattr(g, '_intersection_preparation_receipt')
    restored = loads(dumps(g))
    closure = SimpleNamespace(working_model=restored, source_to_work={})
    with pytest.raises(StructuralPreparationError, match="fresh owner preparation"):
        source_edge_paths(closure)
    prepare_structural_connectivity(restored)
    assert source_edge_paths(closure) == {}
