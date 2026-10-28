from dataclasses import replace
from types import SimpleNamespace
import numpy as np
import pytest
from anyfem.model import Project
from anyfem.model.wind_turbine import WindTurbineLoadError
from anyfem.io.project_file import project_to_dict,project_from_dict
from anyfem.solve.build import _add_turbine_masses
from anyfem.application.floating_mass import dry_mass_properties
from anyfem.application.floating_transient import mass_identity
from test_wind_turbine_load import steady
from anysolver import FEModel, BeamElement


def model():
    m=FEModel('tower'); m.add_material('steel',1e7,.3,density=5.)
    m.add_node(1,0.,0.,0.); m.add_node(2,0.,0.,10.)
    m.add_element(10,BeamElement(10,[1,2],'steel',{'area':.2,'Iy':.001,'Iz':.002,'J':.001}))
    return m


def turbine(**kw):
    return steady(hub_node_id=2,hub_body=(1.,0.,2.),hub_reference='attachment',attachment_element_id=10,
                  mass_mode='additional',mass=100.,mass_cg_offset=(.5,0.,1.),inertia_cg=np.diag([3.,4.,5.]),**kw)


def test_attachment_mass_roundtrip_and_no_duplication():
    t=turbine(); p=Project(); p.add_wind_turbine_load(t)
    data=project_to_dict(p); assert data['anyfem']['format']==19
    restored=project_from_dict(data); assert restored.wind_turbine_loads[t.id]==t
    m=model(); old=dry_mass_properties(m); _add_turbine_masses(restored,m)
    new=dry_mass_properties(m)
    assert new.mass==pytest.approx(old.mass+100.)
    np.testing.assert_allclose(new.cg,(old.mass*old.cg+100*np.array([.5,0.,11.]))/new.mass)
    np.testing.assert_allclose(t.resolved_hub_body(m),[1.,0.,12.])
    assert mass_identity(old)!=mass_identity(new)
    # Each fresh build adds once; modelled mode retains data but adds nothing.
    fresh=model(); _add_turbine_masses(restored,fresh)
    assert mass_identity(dry_mass_properties(fresh))==mass_identity(new)
    restored.wind_turbine_loads[t.id]=replace(t,mass_mode='already_modelled')
    fresh=model(); _add_turbine_masses(restored,fresh)
    assert mass_identity(dry_mass_properties(fresh))==mass_identity(old)


def test_attachment_refuses_wrong_member_missing_or_disconnected_node():
    t=turbine(); m=model()
    with pytest.raises(WindTurbineLoadError,match='selected structural element'):
        replace(t,attachment_element_id=11).resolved_hub_body(m)
    with pytest.raises(WindTurbineLoadError,match='existing'):
        replace(t,hub_node_id=9).resolved_hub_body(m)
    m.add_node(9,1.,2.,3.)
    with pytest.raises(WindTurbineLoadError,match='not connected'):
        replace(t,hub_node_id=9).resolved_hub_body(m)


def test_legacy_record_defaults_no_mass_and_absolute_hub():
    t=steady(); raw=t.to_dict()
    for key in ('mass','mass_mode','mass_cg_offset','inertia_cg','attachment_element_id','hub_reference'): raw.pop(key)
    from anyfem.model.wind_turbine import WindTurbineLoad
    old=WindTurbineLoad.from_dict(raw)
    assert old.mass_mode=='already_modelled' and old.hub_reference=='absolute'


def test_public_static_build_includes_turbine_mass():
    from test_solve import cantilever_project
    from anyfem import solve_linear_static
    from anyfem.solve.build import build_fe_model
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
    # Generated-mesh transport remains independently blocked; full failed probe is retained in delivery evidence.


def test_exact_thrust_lever_arm_transfer():
    from anyfem.application.floating_transient import _Operators
    m=model(); t=turbine(); hub=t.resolved_hub_body(m)
    points=np.array([m.mesh.nodes[k].coords() for k in sorted(m.mesh.nodes)])
    cg=np.array([.2,.4,.6]); force=np.array([12.,3.,-4.])
    owner=SimpleNamespace(index={1:0,2:1},n=2,points=points,cg=cg)
    nodal=_Operators.point_load(owner,2,hub-cg,force)
    np.testing.assert_allclose(nodal[:,:3].sum(axis=0),force)
    moment=np.cross(points-cg,nodal[:,:3]).sum(axis=0)+nodal[:,3:].sum(axis=0)
    np.testing.assert_allclose(moment,np.cross(hub-cg,force),atol=1e-13)
