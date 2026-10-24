"""Canonical empty membership, guarded edits and stable feature anchoring."""
import pytest
from anyfem import commands
from anyfem.application.controller import default_project
from anyfem.model.regions import Region,ManualRegion,BooleanRegion
from anyfem.presentation.sets import SetMembershipRequest,set_geometry_references
from anyfem.io.project_file import project_to_dict,project_from_dict


def model():
    p=default_project();v=[commands.AddPoint(*xy).do(p) for xy in ((0,0),(1,0),(1,1),(0,1))];f=commands.AddPlate(v).do(p)
    refs=(p.geometry.entity_ref('face',f),p.geometry.entity_ref('edge',p.geometry.faces[f].loop[0].edge))
    region=Region('Working','geometry','mixed',ManualRegion(()));commands.AddRegion(region).do(p)
    return p,region,refs


def test_union_subtract_empty_noop_identity_roundtrip_and_undo():
    p,region,refs=model();stack=commands.CommandStack(p)
    request=SetMembershipRequest.capture(p,region.id,refs+refs);stack.run(request.command(p))
    assert set(set_geometry_references(p,region.id))==set(refs)
    assert SetMembershipRequest.capture(p,region.id,refs).command(p) is None
    command=SetMembershipRequest.capture(p,region.id,(refs[0],),remove=True).command(p);stack.run(command)
    assert set_geometry_references(p,region.id)==(refs[1],)
    assert SetMembershipRequest.capture(p,region.id,(refs[0],),remove=True).command(p) is None
    stack.run(SetMembershipRequest.capture(p,region.id,refs,remove=True).command(p));assert not region.definition.anchors
    loaded=project_from_dict(project_to_dict(p));assert not loaded.regions[region.id].definition.anchors
    stack.undo();assert set_geometry_references(p,region.id)==(refs[1],)
    stack.redo();assert not region.definition.anchors


def test_request_refusals_and_source_defined_set_unchanged():
    p,region,refs=model();before=project_to_dict(p);request=SetMembershipRequest.capture(p,region.id,refs)
    with pytest.raises(PermissionError):request.command(p,read_only=True)
    p.read_only_reason='Locked'
    with pytest.raises(PermissionError):request.command(p)
    p.read_only_reason=None
    with pytest.raises(ValueError):request.command(default_project())
    commands.AddPoint(2,2).do(p)
    with pytest.raises(ValueError):request.command(p)
    assert not region.definition.anchors
    other=Region('Other','geometry','mixed',ManualRegion(()));p.regions.add(other)
    combined=Region('Combined','geometry','mixed',BooleanRegion('union',(region.id,other.id)));p.regions.add(combined)
    with pytest.raises(ValueError):SetMembershipRequest.capture(p,combined.id,refs).command(p)
    assert isinstance(combined.definition,BooleanRegion)


def test_feature_output_anchor_survives_membership_edit_and_regeneration():
    from anygeometry import FeatureOutputRef
    p=default_project();feature=commands.AddStiffenedPanel(4,3,1).do(p)
    ref=next(ref for ref in feature.outputs.values() if ref.kind=='face')
    region=Region('Feature plate','geometry','mixed',ManualRegion(()));p.regions.add(region)
    commands.EditGeometrySet(region.id,references=(ref,)).do(p)
    assert isinstance(region.definition.anchors[0],FeatureOutputRef)
    anchor=region.definition.anchors[0]
    commands.EditFeature(feature.feature_id,parameters={**feature.parameters,'origin':(1,0,0)}).do(p)
    assert p.regions[region.id].definition.anchors==(anchor,)
    assert set_geometry_references(p,region.id)
