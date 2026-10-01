"""Feature replay preserves engineering records or refuses before publication."""
from copy import copy, deepcopy
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
    assert project.supports[0].ref==fixed.ref
    assert project.geometry.resolve_ref(fixed.ref)==(current.outputs["point/p1"],)
    assert project.geometry.vertex_position(current.outputs["point/p1"].id)==pytest.approx((1.5,.5,0))
    actual=project.load_case().pressures[0]
    assert (actual.id,actual.region,actual.value)==(pressure.id,pressure.region,1234)
    assert all(project.face_sections[ref.id]=="plate" for ref in project.geometry.resolve_ref(actual.ref))
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
    assert project.supports[0].ref==fixed.ref
    assert project.geometry.resolve_ref(fixed.ref)==(current.outputs["point/p1"],)
    assert project.geometry.vertex_position(current.outputs["point/p1"].id)==pytest.approx((1.5,.5,0))
    assert project.load_case().pressures[0].id==pressure.id
    assert all(project.face_sections[ref.id]=="plate" for ref in project.geometry.resolve_ref(project.load_case().pressures[0].ref))


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
        assert new.ref==old.ref
        assert project.geometry.resolve_ref(new.ref)==(current.outputs[roles[old.ref]],)
        for attribute in fields(old):
            if attribute.name=="ref":continue
            old_value=getattr(old,attribute.name);new_value=getattr(new,attribute.name)
            if isinstance(old_value,np.ndarray):np.testing.assert_array_equal(new_value,old_value)
            else:assert new_value==old_value
    from anyfem.presentation.scene import build_attribute_overlay
    actual_overlay=build_attribute_overlay(project)
    display_project=copy(project)
    for name in ("supports","masses","imperfections"):
        setattr(display_project,name,[replace(item,ref=current.outputs[roles[item.ref]]) for item in getattr(project,name)])
    display_case=copy(project.load_case())
    for name in ("point_loads","pressures","line_loads","surface_tractions"):
        setattr(display_case,name,[replace(item,ref=current.outputs[roles[item.ref]],region=None) for item in getattr(project.load_case(),name)])
    display_project.load_cases={display_case.name:display_case}
    expected_overlay=build_attribute_overlay(display_project)
    for name,attributes in (("points",("position",)),("lines",("points",)),("arrows",("start","end"))):
        actual_items=getattr(actual_overlay,name);expected_items=getattr(expected_overlay,name)
        assert len(actual_items)==len(expected_items)>0
        for actual_item,expected_item in zip(actual_items,expected_items):
            assert actual_item.color==expected_item.color
            for attribute in attributes:
                np.testing.assert_allclose(getattr(actual_item,attribute),getattr(expected_item,attribute))


@pytest.mark.parametrize("direct",[False,True])
def test_explicit_parent_suppression_retains_unresolved_attachments(direct,tmp_path):
    from anyfem import ProjectError
    from anyfem.presentation.scene import build_attribute_overlay
    project,stack,parent,child,fixed,pressure=attached_sketch()
    for face_id in project.geometry.faces:
        project.assign_plate(face_id,"plate")
    before=(list(project.supports),list(project.load_case().pressures),
            list(project.imperfections),list(project.refinements))
    if direct:
        project.geometry.features.set_suppressed(parent.feature_id,True)
        report=project.regenerate_geometry_features();assert report.success,report.diagnostic
    else:stack.run(cmd.SuppressFeature(parent.feature_id))
    assert project.geometry.features.get(child.feature_id).state=="blocked"
    assert before==(project.supports,project.load_case().pressures,project.imperfections,project.refinements)
    with pytest.raises(ProjectError,match="unresolved"):
        project.validate(require_loads=False,require_supports=False)
    overlay=build_attribute_overlay(project)
    assert not overlay.points and not overlay.lines and not overlay.arrows
    report=project.regenerate_geometry_features();assert report.success,report.diagnostic
    from anyfem.io.project_file import save_project,load_project
    reopened=load_project(save_project(project,tmp_path/"suppressed.anyfem"))
    report=reopened.regenerate_geometry_features();assert report.success,report.diagnostic
    reopened.geometry.features.set_suppressed(parent.feature_id,False)
    report=reopened.regenerate_geometry_features();assert report.success,report.diagnostic
    reopened.validate(require_loads=False,require_supports=False)
    assert build_attribute_overlay(reopened).arrows
    if direct:
        project.geometry.features.set_suppressed(parent.feature_id,False)
        report=project.regenerate_geometry_features();assert report.success,report.diagnostic
    else:stack.undo()
    project.validate(require_loads=False,require_supports=False)
    assert build_attribute_overlay(project).arrows


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


def test_historical_imperfection_ref_builds_same_physical_geometry_as_live_ref():
    from anyfem.solve.build import build_fe_model
    project=Project();project.add_material(steel("S355",.01))
    project.add_plate_section("plate",thickness=.01,material="S355")
    stack=cmd.CommandStack(project)
    feature=stack.run(cmd.AddFeature("generator.plate",parameters={"length":2,"width":1}))
    face=next(ref for ref in feature.outputs.values() if ref.kind=="face")
    project.assign_plate(face.id,"plate")
    imperfection=project.add_imperfection(Imperfection(face,kind="plate_mode",amplitude=.004))
    stack.run(cmd.EditFeature(feature.feature_id,parameters={**feature.parameters,"origin":(1,0,0)}))
    assert project.imperfections==[imperfection]
    live,=project.geometry.resolve_ref(face)
    mesh=project.generate_mesh(.25)
    original_coordinates={node:position.copy() for node,position in mesh.nodes.items()}
    historical=build_fe_model(project,mesh,require_loads=False,require_supports=False)
    stack.run(cmd.SuppressFeature(feature.feature_id))
    from anyfem.io.project_file import project_from_dict
    reopened=project_from_dict(project_to_dict(project))
    reopened.geometry.features.set_suppressed(feature.feature_id,False)
    report=reopened.regenerate_geometry_features();assert report.success,report.diagnostic
    resumed_mesh=reopened.generate_mesh(.25)
    resumed=build_fe_model(reopened,resumed_mesh,require_loads=False,require_supports=False)
    resumed_coordinates=np.array([[node.x,node.y,node.z] for node in resumed.fe_model.mesh.nodes.values()])
    expected_coordinates=np.array([[node.x,node.y,node.z] for node in historical.fe_model.mesh.nodes.values()])
    np.testing.assert_array_equal(resumed_coordinates,expected_coordinates)
    stack.undo()
    project.imperfections[:]=[replace(imperfection,ref=live)]
    current=build_fe_model(project,mesh,require_loads=False,require_supports=False)
    historical_coordinates=np.array([[node.x,node.y,node.z] for node in historical.fe_model.mesh.nodes.values()])
    current_coordinates=np.array([[node.x,node.y,node.z] for node in current.fe_model.mesh.nodes.values()])
    np.testing.assert_array_equal(historical_coordinates,current_coordinates)
    assert max(historical_coordinates[:,2])==pytest.approx(.004)
    for node,position in mesh.nodes.items():np.testing.assert_array_equal(position,original_coordinates[node])


def test_deleting_live_descendant_detaches_historical_attributes_and_undo_restores_them():
    project,stack,parent,child,fixed,pressure=attached_sketch()
    imperfection=Imperfection(pressure.ref,amplitude=.002)
    refinement=Refinement(size=.1,ref=pressure.ref)
    project.imperfections.append(imperfection);project.refinements.append(refinement)
    stack.run(cmd.EditFeature(parent.feature_id,parameters={**parent.parameters,"origin":(1,0,0)}))
    live,=project.geometry.resolve_ref(pressure.ref)
    assert live!=pressure.ref
    stack.run(cmd.DeleteEntity(live))
    assert not project.load_case().pressures and not project.imperfections and not project.refinements
    assert project.supports==[fixed]
    stack.undo()
    assert project.load_case().pressures==[pressure]
    assert project.imperfections==[imperfection] and project.refinements==[refinement]
    assert project.geometry.resolve_ref(pressure.ref)==(live,)


@pytest.mark.parametrize("direct",[False,True])
def test_suppressed_anchor_does_not_mask_an_independent_missing_anchor(direct):
    from anygeometry import EntityRef
    project,stack,parent,child,fixed,pressure=attached_sketch()
    region=project.regions.add(Region("Mixed invalid intent","geometry","vertex",ManualRegion((
        FeatureOutputRef(child.feature_id,"point/p1","vertex"),EntityRef("vertex",999999)))))
    project.supports[0]=replace(fixed,region=RegionRef(region.id))
    if direct:project.geometry.features.set_suppressed(parent.feature_id,True)
    before=deepcopy(project_to_dict(project));history=stack.history()
    if direct:
        report=project.regenerate_geometry_features()
        assert not report.success and "uniquely rebind" in report.diagnostic
    else:
        with pytest.raises(GeometryError,match="uniquely rebind"):
            stack.run(cmd.SuppressFeature(parent.feature_id))
    assert project_to_dict(project)==before and stack.history()==history


@pytest.mark.parametrize("direct",[False,True])
@pytest.mark.parametrize("raw",["imperfection","refinement"])
def test_suppression_refuses_to_expire_raw_attachment_identity(direct,raw,monkeypatch):
    # Missing provenance must still fail atomically; durable adoption is tested below.
    monkeypatch.setattr(Project,"adopt_geometry_attachment_regions",lambda self:None)
    project,stack,parent,child,fixed,pressure=attached_sketch()
    if raw=="imperfection":project.imperfections.append(Imperfection(pressure.ref,amplitude=.002))
    else:project.refinements.append(Refinement(size=.1,ref=pressure.ref))
    if direct:project.geometry.features.set_suppressed(parent.feature_id,True)
    before=deepcopy(project_to_dict(project));history=stack.history()
    if direct:
        report=project.regenerate_geometry_features()
        assert not report.success and "persisted output anchor" in report.diagnostic
    else:
        with pytest.raises(GeometryError,match="persisted output anchor"):
            stack.run(cmd.SuppressFeature(parent.feature_id))
    assert project_to_dict(project)==before and stack.history()==history


@pytest.mark.parametrize("direct",[False,True])
@pytest.mark.parametrize("raw",["imperfection","refinement"])
def test_raw_attachment_survives_suppression_reopen_and_resume(direct,raw,tmp_path):
    from anyfem import ProjectError
    from anyfem.io.project_file import save_project,load_project
    project,stack,parent,child,fixed,pressure=attached_sketch()
    for face_id in project.geometry.faces:project.assign_plate(face_id,"plate")
    record=Imperfection(pressure.ref,amplitude=.002) if raw=="imperfection" else Refinement(size=.1,ref=pressure.ref)
    container="imperfections" if raw=="imperfection" else "refinements"
    getattr(project,container).append(record)
    assert not project.geometry_attachment_regions
    if direct:
        project.geometry.features.set_suppressed(parent.feature_id,True)
        report=project.regenerate_geometry_features();assert report.success,report.diagnostic
    else:stack.run(cmd.SuppressFeature(parent.feature_id))
    binding=project.geometry_attachment_regions[record.ref]
    assert getattr(project,container)==[record]
    with pytest.raises(ProjectError,match="unresolved"):
        project.validate(require_loads=False,require_supports=False)
    report=project.regenerate_geometry_features();assert report.success,report.diagnostic
    reopened=load_project(save_project(project,tmp_path/"raw-suppressed.anyfem"))
    assert getattr(reopened,container)==[record]
    assert reopened.geometry_attachment_regions[record.ref]==binding
    reopened.geometry.features.set_suppressed(parent.feature_id,False)
    reopened.geometry.features.update(parent.feature_id,parameters={**parent.parameters,"origin":(1,0,0)})
    report=reopened.regenerate_geometry_features();assert report.success,report.diagnostic
    reopened.validate(require_loads=False,require_supports=False)
    live,=reopened.resolve_geometry_attachment(record.ref)
    assert live!=record.ref
    assert live in reopened.geometry.features.get(child.feature_id).outputs.values()
    assert getattr(reopened,container)==[record]
    twice=load_project(save_project(reopened,tmp_path/"raw-resumed.anyfem"))
    assert getattr(twice,container)==[record]
    assert twice.resolve_geometry_attachment(record.ref)==(live,)
    if not direct:
        stack.undo();assert getattr(project,container)==[record]
        stack.redo();assert project.geometry_attachment_regions[record.ref]==binding


@pytest.mark.parametrize("damage",["container","entry","missing-region","duplicate","wrong-kind","missing-feature","raw-anchor"])
def test_malformed_raw_attachment_binding_is_rejected(damage):
    from anyfem.io.project_file import project_from_dict,ProjectFileError
    project,stack,parent,child,fixed,pressure=attached_sketch()
    project.imperfections.append(Imperfection(pressure.ref,amplitude=.002))
    data=project_to_dict(project)
    entry=data["geometry_attachment_regions"][0]
    region=next(item for item in data["regions"] if item["id"]==entry["region"])
    if damage=="container":data["geometry_attachment_regions"]={}
    elif damage=="entry":data["geometry_attachment_regions"]=[None]
    elif damage=="missing-region":entry["region"]="missing"
    elif damage=="duplicate":data["geometry_attachment_regions"].append(deepcopy(entry))
    elif damage=="wrong-kind":entry["ref"]["kind"]="vertex"
    elif damage=="missing-feature":region["definition"]["anchors"][0]["feature_id"]=999999
    else:region["definition"]["anchors"][0]={"kind":"face","id":pressure.ref.id}
    with pytest.raises(ProjectFileError,match="geometry_attachment_regions"):
        project_from_dict(data)


@pytest.mark.parametrize("raw",["imperfection","refinement"])
def test_deleted_raw_attachment_does_not_persist_an_orphan_binding(raw):
    from anyfem.io.project_file import project_from_dict
    project=Project();stack=cmd.CommandStack(project)
    feature=stack.run(cmd.AddFeature("generator.plate",parameters={"length":2,"width":1}))
    face=next(ref for ref in feature.outputs.values() if ref.kind=="face")
    container=project.imperfections if raw=="imperfection" else project.refinements
    container.append(Imperfection(face,amplitude=.002) if raw=="imperfection" else Refinement(size=.1,ref=face))
    project_to_dict(project);assert face in project.geometry_attachment_regions
    container.clear()
    stack.run(cmd.DeleteFeature(feature.feature_id))
    # An unused binding must not block another delete before writer pruning.
    points=project.geometry.add_points(((5,0,0),(6,0,0),(6,1,0),(5,1,0)))
    unrelated=project.geometry.entity_ref("face",project.geometry.add_plate(points))
    stack.run(cmd.DeleteEntity(unrelated))
    data=project_to_dict(project)
    assert "geometry_attachment_regions" not in data
    reopened=project_from_dict(data)
    assert not reopened.geometry_attachment_regions


def test_delete_resumed_output_detaches_raw_bindings_and_undo_restores_saved_identity():
    project=Project();stack=cmd.CommandStack(project)
    feature=stack.run(cmd.AddFeature("generator.plate",parameters={"length":2,"width":1}))
    face=next(ref for ref in feature.outputs.values() if ref.kind=="face")
    imperfection=Imperfection(face,amplitude=.002);refinement=Refinement(size=.1,ref=face)
    stack.run(cmd.AddImperfection(imperfection));stack.run(cmd.AddRefinement(refinement))
    stack.run(cmd.SuppressFeature(feature.feature_id));stack.run(cmd.SuppressFeature(feature.feature_id,False))
    live,=project.resolve_geometry_attachment(face)
    binding=project.geometry_attachment_regions[face]
    assert live!=face and project.geometry.resolve_ref(face)==()
    stack.run(cmd.DeleteEntity(live))
    assert not project.imperfections and not project.refinements
    # The owner currently refuses persistence of deleted active feature outputs.
    # Even this refused writer must not lose the bindings needed by undo.
    with pytest.raises(GeometryError,match="references missing entity"):
        project_to_dict(project)
    assert not project.geometry_attachment_regions
    stack.undo()
    assert project.imperfections==[imperfection] and project.refinements==[refinement]
    assert project.geometry_attachment_regions[face]==binding
    assert project.resolve_geometry_attachment(face)==(live,)
    from anyfem.io.project_file import project_from_dict
    reopened=project_from_dict(project_to_dict(project))
    assert reopened.geometry_attachment_regions[face]==binding
    stack.redo();assert not project.imperfections and not project.refinements


def test_unrelated_delete_preserves_a_suppressed_raw_attachment():
    project=Project();stack=cmd.CommandStack(project)
    feature=stack.run(cmd.AddFeature("generator.plate",parameters={"length":2,"width":1}))
    face=next(ref for ref in feature.outputs.values() if ref.kind=="face")
    imperfection=Imperfection(face,amplitude=.002)
    stack.run(cmd.AddImperfection(imperfection));stack.run(cmd.SuppressFeature(feature.feature_id))
    points=project.geometry.add_points(((5,0,0),(6,0,0),(6,1,0),(5,1,0)))
    unrelated=project.geometry.entity_ref("face",project.geometry.add_plate(points))
    stack.run(cmd.DeleteEntity(unrelated))
    assert project.imperfections==[imperfection] and face in project.geometry_attachment_regions


@pytest.mark.parametrize("raw",["imperfection","refinement"])
def test_raw_canonical_rebinding_overrides_authored_cache_during_delete(raw):
    project=Project();stack=cmd.CommandStack(project)
    first=stack.run(cmd.AddFeature("generator.plate",parameters={"length":2,"width":1}))
    second=stack.run(cmd.AddFeature("generator.plate",parameters={"length":2,"width":1,"origin":(5,0,0)}))
    authored=next(ref for ref in first.outputs.values() if ref.kind=="face")
    target=next(ref for ref in second.outputs.values() if ref.kind=="face")
    region=project.singleton_region(target)
    record=Imperfection(authored,amplitude=.002) if raw=="imperfection" else Refinement(size=.1,ref=authored)
    container=project.imperfections if raw=="imperfection" else project.refinements
    container.append(record);project.geometry_attachment_regions[authored]=region
    from anyfem.model.attributes import Pressure
    pressure=Pressure(authored,123)
    project.load_case().pressures.append(pressure)
    from anyfem.ui.scene import _display_attributes
    from anyfem.solve.build import _attribute_targets
    assert _display_attributes(project,[pressure])==[pressure]
    assert _attribute_targets(project,None,authored)==(authored,)
    assert project.raw_geometry_attachment_region(pressure) is None
    from anyfem.io.project_file import project_from_dict
    reopened=project_from_dict(project_to_dict(project))
    loaded_pressure,=reopened.load_case().pressures
    assert _attribute_targets(reopened,loaded_pressure.region,loaded_pressure.ref)==(authored,)
    assert reopened.resolve_geometry_attachment(authored)==(target,)
    stack.run(cmd.DeleteEntity(authored))
    assert container==[record] and project.geometry_attachment_regions[authored]==region
    assert not project.load_case().pressures
    assert project.resolve_geometry_attachment(authored)==(target,)
    stack.undo()
    assert project.load_case().pressures==[pressure]
    stack.run(cmd.DeleteEntity(target))
    assert not container and authored not in project.geometry_attachment_regions
    assert project.load_case().pressures==[pressure]
    stack.undo()
    assert container==[record] and project.geometry_attachment_regions[authored]==region
    assert project.resolve_geometry_attachment(authored)==(target,)
