"""Routing-only fakes: owner raises; no geometric proof, model or mesh is made."""
from types import SimpleNamespace
import pytest
from anygeometry import GeometryError
from anygeometry.nurbs import NurbsCurve
from anygeometry.surfaces import CoonsSurface
from anyfem.structural_preparation import prepare_structural_connectivity, StructuralPreparationError
from anymesher.errors import MeshError

class Routed(BaseException): pass
class OldRoute(BaseException): pass

def source():
    curve=NurbsCurve(1,(0.,0.,1.,1.),((0.,0.,0.),(0.,1.,0.)))
    geometry=SimpleNamespace(model_id='routing-only',revision=7,members={},sheets={},
        faces={i:SimpleNamespace(surface=CoonsSurface(),parameterization=None,
            loop=(SimpleNamespace(edge=i),)) for i in (1,2)},
        edges={i:SimpleNamespace(curve=curve) for i in (1,2)},transport_marker=object())
    geometry.handle=lambda kind,key:(kind,key)
    geometry.restore_topology=lambda value:pytest.fail('a refused owner cannot publish topology')
    return geometry

def test_raw_multiface_handoff_precedes_charts_and_preserves_owner_arguments(monkeypatch):
    import anygeometry,anymesher.preparation
    geometry=source();marker=geometry.transport_marker;before=dict(vars(geometry));calls=[];phases=[]
    failure=Routed()
    def callback(phase):phases.append(phase)
    def charts(*args,**kwargs):pytest.fail('raw profile was queried before owner handoff')
    def owner(actual,**kwargs):
        calls.append((actual,kwargs));raise failure
    monkeypatch.setattr(anygeometry,'query_trimmed_surface_charts',charts)
    monkeypatch.setattr(anymesher.preparation,'prepare_structural_closure',owner)
    with pytest.raises(Routed) as caught:
        prepare_structural_connectivity(geometry,source_model_id='authored-source',source_revision=3,
            cancellation_check=callback)
    assert caught.value is failure and len(calls)==1
    assert calls[0][0] is geometry
    assert calls[0][1]==dict(face_ids=(1,2),options={'declare_missing_owners':True},
        cancellation_check=callback,reuse_working_copy=True)
    assert phases==['structural intersection batch planning']
    assert vars(geometry)==before and geometry.transport_marker is marker

@pytest.mark.parametrize('variant',['single','installed','members','sheets','no_nurbs'])
def test_nonhint_scope_preserves_existing_chart_path(monkeypatch,variant):
    import anygeometry,anymesher.preparation
    geometry=source()
    if variant=='single':geometry.faces.pop(2)
    elif variant=='installed':geometry.faces[1].parameterization=object()
    elif variant=='members':geometry.members[9]=object()
    elif variant=='sheets':geometry.sheets[9]=object()
    else:
        for edge in geometry.edges.values():edge.curve=object()
    sentinel=OldRoute();seen=[]
    def charts(actual,handles):seen.append((actual,handles));raise sentinel
    def owner(*args,**kwargs):pytest.fail('nonhint entered raw-profile adapter')
    monkeypatch.setattr(anygeometry,'query_trimmed_surface_charts',charts)
    monkeypatch.setattr(anymesher.preparation,'prepare_structural_closure',owner)
    with pytest.raises(OldRoute) as caught:prepare_structural_connectivity(geometry,member_ids=())
    assert caught.value is sentinel and seen==[(geometry,(('face',1),))]

@pytest.mark.parametrize('error',[MeshError('owner proof refused'),GeometryError('unknown owner geometry'),KeyboardInterrupt('owner cancellation')])
def test_owner_refusal_or_cancellation_cannot_finalize_source(monkeypatch,error):
    import anygeometry,anymesher.preparation
    geometry=source();before=dict(vars(geometry));marker=geometry.transport_marker
    def charts(*args,**kwargs):pytest.fail('owner route must be selected before charts')
    def owner(*args,**kwargs):raise error
    monkeypatch.setattr(anygeometry,'query_trimmed_surface_charts',charts)
    monkeypatch.setattr(anymesher.preparation,'prepare_structural_closure',owner)
    if isinstance(error,(MeshError,GeometryError)):
        with pytest.raises(StructuralPreparationError) as caught:prepare_structural_connectivity(geometry)
        assert caught.value.__cause__ is error
    else:
        with pytest.raises(KeyboardInterrupt) as caught:prepare_structural_connectivity(geometry)
        assert caught.value is error
    assert vars(geometry)==before and geometry.transport_marker is marker
