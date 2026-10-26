import numpy as np
import pytest
from anyfem import Project,steel,pinned
from anyfem.model import WetSurface,WetPressure
from anyfem.document import canonical_hash
from anyfem.io.project_file import project_to_dict,project_from_dict
from anyfem.solve import solve_linear_static_many
from anyfem.solve.build import wet_element_pressures
from anymesher.serialize import mesh_to_dict
from anyloads.pressure_history import MappedPressureHistory


def micro():
    p=Project('history microfixture');p.add_material(steel());p.add_plate_section('plate',.01,'S355')
    g=p.geometry;v=g.add_points([(0,0,0),(2,0,0),(2,1,0),(0,1,0)])
    edges=g.add_polyline(v,close=True);f=g.add_face(edges);p.assign_plate(f,'plate')
    for e in edges:p.add_support(pinned(p.edge(e)))
    p.add_wet_surface(WetSurface(ref=p.face(f),fluid_side='positive_normal'))
    mesh=p.generate_mesh(.5,order='quadratic');ids=tuple(sorted(mesh.elements_of_face[f]))
    h=MappedPressureHistory((0.,.25,.5),ids,(1000.,)*len(ids),(0.,)*len(ids),2*np.pi,
        canonical_hash(mesh_to_dict(mesh)),'manufactured')
    parent=p.add_load_series('wave',h.times);parent.wet_pressures.append(WetPressure(history=h,mapping_key='element'))
    return p,mesh,h


def test_one_owner_one_stored_history_derived_views_and_format(monkeypatch):
    p,mesh,h=micro();data=project_to_dict(p)
    assert data['anyfem']['format']==15 and len(data['load_cases'])==1
    assert len(data['load_cases'][0]['wet_pressures'])==1
    import anyfem.io.project_file as codec
    with monkeypatch.context() as old:
        old.setattr(codec,'MAX_READ_FORMAT',14)
        with pytest.raises(ValueError,match='upgrade'):project_from_dict(data)
    reopened=project_from_dict(data)
    children=reopened.series_children('wave')
    assert len(children)==3 and all(not c.wet_pressures for c in children)
    parent=reopened.load_cases['wave']
    assert reopened.resolve_case(children[0]).wet_pressures is parent.wet_pressures
    for case in children:
        values=wet_element_pressures(reopened,mesh,reopened.resolve_case(case))
        assert [v.pressure for v in values]==pytest.approx((-h.evaluate(case.time)).tolist())


def test_real_batch_solve_uses_actual_time_and_reuses_factorization():
    p,mesh,h=micro()
    result=solve_linear_static_many(p,mesh=mesh,load_cases=[c.name for c in p.series_children('wave')],load_tolerance=0.)
    assert len(result.shapes)==3
    u=[shape.displacements for shape in result.shapes]
    assert np.linalg.norm(u[0])>0
    assert np.allclose(u[1],0.,atol=1e-15)
    assert np.allclose(u[2],-u[0],rtol=1e-9,atol=1e-15)


def test_explicit_replace_owner_preserves_other_loads_and_undo_redo_identity():
    from anyfem.application.hydro_wave_input import ApplyWavePressure
    p,mesh,h=micro();p.load_case('Other load').set_time(1.)
    before={name:case.id for name,case in p.load_cases.items()}
    result={'name':'wave','history':h.to_dict(),'surfaces':[s.id for s in p.wet_surfaces],
        'input_document_hash':canonical_hash(project_to_dict(p))}
    command=ApplyWavePressure(result,replace_legacy=True)
    parent=command.do(p)
    assert p.load_cases['Other load'].id==before['Other load']
    assert len(parent.wet_pressures)==1 and len(p.analyses)==1
    after={name:case.id for name,case in p.load_cases.items()}
    command.undo(p);assert {name:c.id for name,c in p.load_cases.items()}==before
    command.redo(p);assert {name:c.id for name,c in p.load_cases.items()}==after


@pytest.mark.parametrize('legacy',[False,True])
def test_mixed_owner_or_legacy_snapshot_replacement_refuses_without_mutation(legacy):
    from anyfem.application.hydro_wave_input import ApplyWavePressure
    p,mesh,h=micro()
    if legacy:
        p.remove_load_series('wave')
        def builder(case,time,index):
            case.add_wet_pressure(mapping={e:float(v) for e,v in zip(h.element_ids,h.evaluate(time))},mapping_key='element')
            from dataclasses import replace
            case.wet_pressures[0]=replace(case.wet_pressures[0],mesh_content_sha256=h.mesh_content_sha256)
        p.add_load_case_series('wave',h.times,builder=builder)
        mixed_case=next(iter(p.load_cases.values()))
    else:mixed_case=p.load_cases['wave']
    mixed_case.set_parameter('gain',2.)
    before=project_to_dict(p)
    command=ApplyWavePressure({'name':'wave','history':h.to_dict(),'surfaces':[s.id for s in p.wet_surfaces],
        'input_document_hash':canonical_hash(before)},replace_legacy=True)
    with pytest.raises(ValueError,match='additional loads'):
        command.do(p)
    assert canonical_hash(project_to_dict(p))==canonical_hash(before)
