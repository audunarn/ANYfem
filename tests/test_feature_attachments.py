"""Feature replay preserves engineering records or refuses before publication."""
from copy import deepcopy
from dataclasses import fields, replace
import numpy as np
import pytest
from anygeometry import GeometryError, SketchDefinition, FeatureOutputRef, from_dict
from anyfem import Project, steel, commands as cmd
from anyfem.io.project_file import project_to_dict
from anyfem.model.attributes import Support
from anyfem.model.attributes import Mass
from anyfem.model.coordinates import CoordinateSystem
from anyfem.model.imperfections import Imperfection
from anyfem.mesh.refinement import Refinement
from anyfem.model.regions import Region, RegionRef, ManualRegion


def attached_sketch():
    project=Project()
    project.add_material(steel("S355",.01))
    project.add_plate_section("plate",thickness=.01,material="S355")
    stack=cmd.CommandStack(project)
    parent=stack.run(cmd.AddFeature("generator.plate",parameters={"length":3,"width":3}))
    support=next(ref for ref in parent.outputs.values() if ref.kind=="face")
    child=stack.run(cmd.AddSketch(support,SketchDefinition(
        points={"p1":(.5,.5),"p2":(1.5,.5),"p3":(1.5,1.5),"p4":(.5,1.5)},
        path=("p1","p2","p3","p4"),closed=True,extrusion=.2)))
    vertex=child.outputs["point/p1"]
    face=next(ref for key,ref in child.outputs.items() if key.startswith("extrusion/face/"))
    fixed=stack.run(cmd.AddSupport(Support("anchor",vertex,{"ux":.001,"uy":0,"uz":0})))
    pressure=stack.run(cmd.AddPressure(face,1234))
    stack.run(cmd.AssignPlate(face.id,"plate"))
    return project,stack,parent,child,fixed,pressure


def test_feature_edit_preserves_exact_record_identity_and_scope():
    project,stack,parent,child,fixed,pressure=attached_sketch()
    before=deepcopy(project_to_dict(project))
    parameters=dict(parent.parameters);parameters["origin"]=(1,0,0)
    stack.run(cmd.EditFeature(parent.feature_id,parameters=parameters))
    after=deepcopy(project_to_dict(project))
    current=project.geometry.features.get(child.feature_id)
    assert project.supports[0].id==fixed.id
    assert project.supports[0].region==fixed.region
    assert project.supports[0].constraints==fixed.constraints
    assert project.supports[0].coordinate_system_id==fixed.coordinate_system_id
    assert project.supports[0].ref==current.outputs["point/p1"]
    assert project.geometry.vertex_position(project.supports[0].ref.id)==pytest.approx((1.5,.5,0))
    actual=project.load_case().pressures[0]
    assert (actual.id,actual.region,actual.value)==(pressure.id,pressure.region,1234)
    assert project.face_sections[actual.ref.id]=="plate"
    def persisted_design(value):
        result=deepcopy(value)
        from_dict(result["geometry"])  # Validate the actual persisted checksum.
        result["geometry"].pop("revision")  # Owner revisions advance on undo/redo.
        allocators=result["geometry"].pop("id_state")
        assert all(allocators[key]>=before["geometry"]["id_state"][key] for key in allocators)
        result["geometry"].pop("checksum")  # Includes monotonic allocator state.
        return result
    stack.undo();assert persisted_design(project_to_dict(project))==persisted_design(before)
    stack.redo();assert persisted_design(project_to_dict(project))==persisted_design(after)


def test_public_feature_regeneration_rebinds_existing_records():
    project,stack,parent,child,fixed,pressure=attached_sketch()
    parameters=dict(parent.parameters);parameters["origin"]=(1,0,0)
    project.geometry.features.update(parent.feature_id,parameters=parameters)
    report=project.regenerate_geometry_features()
    assert report.success,report.diagnostic
    current=project.geometry.features.get(child.feature_id)
    assert project.supports[0].id==fixed.id
    assert project.supports[0].ref==current.outputs["point/p1"]
    assert project.geometry.vertex_position(project.supports[0].ref.id)==pytest.approx((1.5,.5,0))
    assert project.load_case().pressures[0].id==pressure.id
    assert project.face_sections[project.load_case().pressures[0].ref.id]=="plate"


@pytest.mark.parametrize("direct",[False,True])
def test_all_feature_attachment_records_keep_exact_values(direct):
    project,stack,parent,child,fixed,pressure=attached_sketch()
    system=CoordinateSystem("attachment frame",axis=(0,1,0),reference=(1,0,0))
    project.coordinate_systems[system.id]=system
    project.supports[0]=replace(project.supports[0],coordinate_system_id=system.id)
    vertex=child.outputs["point/p1"]
    edge=child.outputs["profile/edge/0"]
    face=pressure.ref
    case=project.load_case()
    case.add_point_load(vertex,(1,2,3),(4,5,6),coordinate_system_id=system.id)
    case.add_line_load(edge,(7,8,9),coordinate_system_id=system.id)
    case.add_surface_traction(face,(10,11,12),coordinate_system_id=system.id)
    project.masses.append(Mass(vertex,13,region=project.singleton_region(vertex)))
    project.imperfections.append(Imperfection(face,amplitude=.002,waves=(2,1)))
    project.refinements.append(Refinement(size=.1,ref=face,radius=.2,growth=1.7))
    def records():
        return tuple(item for container in (project.supports,project.masses,project.imperfections,project.refinements,
            case.point_loads,case.pressures,case.line_loads,case.surface_tractions) for item in container)
    before=records();roles={ref:key for key,ref in child.outputs.items()}
    parameters=dict(parent.parameters);parameters["origin"]=(1,0,0)
    if direct:
        project.geometry.features.update(parent.feature_id,parameters=parameters)
        report=project.regenerate_geometry_features();assert report.success,report.diagnostic
    else:stack.run(cmd.EditFeature(parent.feature_id,parameters=parameters))
    current=project.geometry.features.get(child.feature_id)
    assert len(records())==len(before)
    for old,new in zip(before,records()):
        assert new.ref==current.outputs[roles[old.ref]]
        for attribute in fields(old):
            if attribute.name=="ref":continue
            old_value=getattr(old,attribute.name);new_value=getattr(new,attribute.name)
            if isinstance(old_value,np.ndarray):np.testing.assert_array_equal(new_value,old_value)
            else:assert new_value==old_value


@pytest.mark.parametrize("outcome",["empty","ambiguous"])
@pytest.mark.parametrize("direct",[False,True])
def test_feature_attachment_refusal_is_atomic(outcome,direct,monkeypatch):
    project,stack,parent,child,fixed,pressure=attached_sketch()
    before=deepcopy(project_to_dict(project));history=stack.history()
    history_type=type(project.geometry.features);original=history_type.resolve
    def resolve(self,anchor,geometry):
        if getattr(anchor,"feature_id",None)==child.feature_id and getattr(anchor,"kind",None)=="vertex":
            if outcome=="empty":return ()
            current=self.get(child.feature_id)
            return (current.outputs["point/p2"],current.outputs["point/p3"])
        return original(self,anchor,geometry)
    monkeypatch.setattr(history_type,"resolve",resolve)
    parameters=dict(parent.parameters);parameters["origin"]=(1,0,0)
    if direct:
        report=project.regenerate_geometry_features()
        assert not report.success and "uniquely rebind" in report.diagnostic
    else:
        with pytest.raises(GeometryError,match="uniquely rebind"):
            stack.run(cmd.EditFeature(parent.feature_id,parameters=parameters))
    assert stack.history()==history
    assert project_to_dict(project)==before


def test_valid_representative_keeps_multi_target_scope_without_cloning():
    project,stack,parent,child,fixed,pressure=attached_sketch()
    region=project.regions.add(Region("Sketch vertices","geometry","vertex",ManualRegion((
        FeatureOutputRef(child.feature_id,"point/p1","vertex"),
        FeatureOutputRef(child.feature_id,"point/p2","vertex"),
    ))))
    project.supports[0]=replace(project.supports[0],region=RegionRef(region.id))
    before=project.supports[0]
    report=project.regenerate_geometry_features()
    assert report.success,report.diagnostic
    assert project.supports==[before]
    refs=project.regions.resolve(region.id,geometry=project.geometry,
        feature_resolver=lambda anchor:project.geometry.features.resolve(anchor,project.geometry))
    assert len(refs)==2 and before.ref in refs
