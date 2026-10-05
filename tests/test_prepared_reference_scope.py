"""Bound external-reference visibility; no meshing or numerical acceptance."""
from dataclasses import replace
from hashlib import sha256
import json
import pytest

from anygeometry import GeometryModel,plan_intersections,apply_intersections
from anygeometry.entities import EntityRef
from anygeometry.serialization import to_dict as geometry_to_dict
from anyfem import Project,steel
from anyfem.model.regions import ManualRegion,Region,RegionDomain,RegionRef
from anyfem.prepared_reference_scope import (
    PreparedReferenceScopeError,query_prepared_project_reference_scope,
    validate_prepared_project_reference_scope,assert_authored_root_face_scope,
    extract_prepared_project_closure,
)


def split_plate_project():
    geometry=GeometryModel()
    root=geometry.add_plate(geometry.add_points(((0,0,0),(2,0,0),(2,1,0),(0,1,0))))
    cutter=geometry.add_plate(geometry.add_points(((1,-1,-1),(1,2,-1),(1,2,1),(1,-1,1))))
    apply_intersections(geometry,plan_intersections(geometry,(root,cutter),policy="connect"),policy="connect")
    children=tuple(ref.id for ref in geometry.resolve_ref(EntityRef("face",root)))
    left=next(face for face in children if all(geometry.vertex_position(geometry.edges[use.edge].start)[0]<=1
             for use in geometry.faces[face].loop))
    return Project(geometry=geometry),root,children,left


def child_pressure(project,left):
    region=Region("Left child",RegionDomain.GEOMETRY,"face",ManualRegion((EntityRef("face",left),)))
    project.regions.add(region)
    pressure=project.load_case().add_pressure(EntityRef("face",left),1200,region=RegionRef(region.id))
    return pressure,region


def test_left_child_load_cannot_alias_root_wide_crossing_cell():
    project,root,children,left=split_plate_project();pressure,region=child_pressure(project,left)
    before=geometry_to_dict(project.geometry),tuple(project.regions.to_list())
    manifest=query_prepared_project_reference_scope(project,project.geometry)
    record=next(item for item in manifest.references if item.key.endswith(pressure.id))
    assert record.source_targets==(EntityRef("face",left),)
    assert record.current_prepared_targets==(EntityRef("face",left),)
    scope=next(item for item in record.face_scopes if item.authored_face_id==root)
    assert scope.current_face_ids==(left,) and not scope.whole_authored_root
    assert set(scope.required_boundary_edge_ids)=={use.edge for use in project.geometry.faces[left].loop}
    assert set(scope.required_boundary_edge_ids).issubset(manifest.required_boundary_edge_ids)
    with pytest.raises(PreparedReferenceScopeError,match="child-local"):
        assert_authored_root_face_scope(manifest,root,children)
    with pytest.raises(PreparedReferenceScopeError,match="partial domain"):
        assert_authored_root_face_scope(manifest,root,(left,))
    validate_prepared_project_reference_scope(project,project.geometry,manifest)
    assert (geometry_to_dict(project.geometry),tuple(project.regions.to_list()))==before


def test_whole_root_scope_and_explicit_empty_inventory():
    project,root,children,left=split_plate_project()
    region=Region("Whole panel",RegionDomain.GEOMETRY,"face",ManualRegion((EntityRef("face",root),)))
    project.regions.add(region)
    pressure=project.load_case().add_pressure(EntityRef("face",root),500,region=RegionRef(region.id))
    manifest=query_prepared_project_reference_scope(project,project.geometry)
    record=next(item for item in manifest.references if item.key.endswith(pressure.id))
    assert set(record.current_prepared_targets)=={EntityRef("face",face) for face in children}
    assert next(item for item in record.face_scopes if item.authored_face_id==root).whole_authored_root
    assert dict(manifest.inventory_counts)["supports"]==0
    assert dict(manifest.inventory_counts)["masses"]==0
    assert dict(manifest.inventory_counts)["seeding_overrides"]==0
    assert_authored_root_face_scope(manifest,root,children)


@pytest.mark.parametrize("change",["pressure","new_load","document","units"])
def test_project_changes_stale_even_without_geometry_revision(change):
    project,root,children,left=split_plate_project();pressure,region=child_pressure(project,left)
    manifest=query_prepared_project_reference_scope(project,project.geometry);revision=project.geometry.revision
    if change=="pressure":project.load_case().pressures[0]=replace(pressure,value=1300)
    elif change=="new_load":project.load_case().add_pressure(EntityRef("face",left),400,region=RegionRef(region.id))
    elif change=="document":project.document_id="replacement"
    else:project.name="different input"
    assert project.geometry.revision==revision
    with pytest.raises(ValueError):validate_prepared_project_reference_scope(project,project.geometry,manifest)


def test_altered_and_rechecksummed_inventory_refuses():
    import anyfem.prepared_reference_scope as api
    project,root,children,left=split_plate_project();child_pressure(project,left)
    manifest=query_prepared_project_reference_scope(project,project.geometry)
    omitted=replace(manifest,references=(),required_boundary_edge_ids=())
    with pytest.raises(ValueError,match="altered"):validate_prepared_project_reference_scope(project,project.geometry,omitted)
    forged=replace(omitted,manifest_checksum=api._manifest_checksum(omitted))
    with pytest.raises(ValueError,match="inventory was altered"):validate_prepared_project_reference_scope(project,project.geometry,forged)


@pytest.mark.parametrize("change",["geometry_revision","same_revision_geometry","manifest_owner"])
def test_geometry_or_owner_scope_changes_refuse(change):
    project,root,children,left=split_plate_project()
    manifest=query_prepared_project_reference_scope(project,project.geometry)
    if change=="geometry_revision":project.geometry.add_point(9,9,9)
    elif change=="same_revision_geometry":
        point=next(iter(project.geometry.vertices))
        project.geometry.vertices[point].position.flags.writeable=True
        project.geometry.vertices[point].position[0]+=0.125
    else:
        owner=replace(manifest.owner_scope,current_document_json="{}")
        manifest=replace(manifest,owner_scope=owner)
    with pytest.raises(ValueError):validate_prepared_project_reference_scope(project,project.geometry,manifest)


@pytest.mark.parametrize("case",["missing_ref","unknown_record","unknown_external","mesh_region"])
def test_absent_or_unsupported_inputs_are_not_negative_evidence(case):
    project,root,children,left=split_plate_project()
    if case=="missing_ref":project.load_case().add_pressure(EntityRef("face",999999),500)
    elif case=="unknown_record":project.supports.append(object())
    elif case=="unknown_external":project.external_child_loads=[EntityRef("face",left)]
    else:
        from anyfem.model.regions import MeshEntityRef
        project.regions.add(Region("Mesh subset",RegionDomain.MESH,"node",ManualRegion((MeshEntityRef("m","node",1),)),mesh_id="m"))
    with pytest.raises((ValueError,TypeError)):query_prepared_project_reference_scope(project,project.geometry)


def test_closure_maps_and_occurrences_are_retained_without_editing_design():
    geometry=GeometryModel()
    # Nonconsecutive source IDs exercise the actual owner map, not ID equality.
    geometry.add_point(-10,-10,-10)
    root=geometry.add_plate(geometry.add_points(((0,0,0),(2,0,0),(2,1,0),(0,1,0))))
    cutter=geometry.add_plate(geometry.add_points(((1,-1,-1),(1,2,-1),(1,2,1),(1,-1,1))))
    project=Project(geometry=geometry)
    project.add_material(steel("S355",.01));project.add_plate_section("Shell",thickness=.01,material="S355")
    project.assign_plate(root,"Shell")
    handles=tuple(geometry.handle("face",key) for key in geometry.faces)+tuple(geometry.handle("sheet",key) for key in geometry.sheets)
    closure=extract_prepared_project_closure(project,handles)
    work=closure.working_model;before=geometry_to_dict(geometry)
    work_root=closure.source_to_work[geometry.handle("face",root)].id
    work_cutter=closure.source_to_work[geometry.handle("face",cutter)].id
    apply_intersections(work,plan_intersections(work,(work_root,work_cutter),policy="connect"),policy="connect")
    manifest=query_prepared_project_reference_scope(project,work,closure=closure)
    assert ("face",root,work_root) in manifest.source_to_prepared_ids
    assert (work_root,"project_current",root) in manifest.authored_face_sources
    assert manifest.authored_document["structural"]["face_uses"]
    assert manifest.current_document["structural"]["sheets"]
    assert any(row[0]=="face_use" for row in manifest.source_to_prepared_ids)
    validate_prepared_project_reference_scope(project,work,manifest,closure=closure)
    assert geometry_to_dict(geometry)==before
    # Detached dictionary access cannot mutate the receipt or the project.
    view=manifest.current_document;view["structural"]["face_uses"].clear()
    assert manifest.current_document["structural"]["face_uses"]
    with pytest.raises(ValueError):validate_prepared_project_reference_scope(project,work,manifest)
    project.geometry.add_point(20,20,20)
    with pytest.raises(ValueError):validate_prepared_project_reference_scope(project,work,manifest,closure=closure)


@pytest.mark.parametrize("change", ["source_before_capture", "swapped_mapping", "work_before_preparation", "unwitnessed"])
def test_detached_extraction_provenance_cannot_be_reconstructed(change):
    from anygeometry import extract_model_closure
    from anyfem.prepared_reference_scope import PreparedProjectClosure
    geometry=GeometryModel()
    root=geometry.add_plate(geometry.add_points(((0,0,0),(2,0,0),(2,1,0),(0,1,0))))
    cutter=geometry.add_plate(geometry.add_points(((1,-1,-1),(1,2,-1),(1,2,1),(1,-1,1))))
    project=Project(geometry=geometry);handles=tuple(geometry.handle("face",key) for key in geometry.faces)
    basis=extract_prepared_project_closure(project,handles)
    work=basis.working_model
    if change=="source_before_capture":
        vertex=geometry.vertices[next(iter(geometry.vertices))]
        vertex.position.flags.writeable=True;vertex.position[0]+=.125
    elif change=="swapped_mapping":
        forward=dict(basis.source_to_work);sources=[key for key in forward if key.kind=="face"]
        forward[sources[0]],forward[sources[1]]=forward[sources[1]],forward[sources[0]]
        forged=replace(basis.closure,source_to_work=forward,work_to_source={v:k for k,v in forward.items()})
        basis=PreparedProjectClosure(forged)
    elif change=="work_before_preparation":work.add_point(30,30,30)
    else:
        basis=extract_model_closure(geometry,handles)
        work=basis.working_model
    apply_intersections(work,plan_intersections(work,tuple(work.faces),policy="connect"),policy="connect")
    with pytest.raises(PreparedReferenceScopeError):query_prepared_project_reference_scope(project,work,closure=basis)


def test_all_engineering_reference_containers_are_read_only_and_visible():
    import numpy as np
    import anyfem.prepared_reference_scope as api
    from anyfem.model.attributes import Support,Mass,PointLoad,LineLoad,SurfaceTraction
    from anyfem.model.imperfections import Imperfection
    from anyfem.mesh.refinement import Refinement
    from anyfem.model.records import OutputRequest
    from anyfem.model.sections import SectionAssignment
    project,root,children,left=split_plate_project();pressure,region=child_pressure(project,left)
    vertex=next(iter(project.geometry.vertices));edge=project.geometry.faces[left].loop[0].edge
    face_ref=EntityRef("face",left);edge_ref=EntityRef("edge",edge);vertex_ref=EntityRef("vertex",vertex)
    project.supports.append(Support("Fixed",face_ref,{"uz":0}))
    project.masses.append(Mass(face_ref,5));project.imperfections.append(Imperfection(face_ref))
    project.refinements.append(Refinement(.1,ref=face_ref));project.seeding_overrides[edge]=3
    case=project.load_case();case.point_loads.append(PointLoad(vertex_ref,force=np.array([0.,0.,1.])))
    case.line_loads.append(LineLoad(edge_ref,np.array([0.,0.,2.])))
    case.surface_tractions.append(SurfaceTraction(face_ref,np.array([0.,0.,3.])))
    case.gravity=np.array([0.,0.,-9.81])
    request=OutputRequest(("displacement",),RegionRef(region.id),"node")
    project.output_requests[request.id]=request
    project.add_material(steel("S355",.01));project.add_plate_section("Shell",thickness=.01,material="S355")
    assignment=SectionAssignment("plate",project.plate_sections["Shell"].id,RegionRef(region.id))
    project.section_assignments[assignment.id]=assignment
    project.face_sections[left]="Shell";project.face_assignment_ids[left]=assignment.id
    project.geometry_attachment_regions[face_ref]=RegionRef(region.id)
    before=api._project_inputs(project)
    manifest=query_prepared_project_reference_scope(project,project.geometry)
    validate_prepared_project_reference_scope(project,project.geometry,manifest)
    assert api._project_inputs(project)==before
    assert {"supports","masses","imperfections","refinements","gravity","output_request","section_assignment","section_cache","raw_binding","seeding"} <= {ref.category for ref in manifest.references}
    assert vertex in manifest.required_vertex_ids and edge in manifest.required_boundary_edge_ids
    assert next(ref for ref in manifest.references if ref.category=="section_cache").authority=="derived_cache"
    project.face_sections[left]="Missing"
    with pytest.raises(PreparedReferenceScopeError):query_prepared_project_reference_scope(project,project.geometry)


@pytest.mark.parametrize("case", ["duplicate_load", "missing_coordinate", "analysis_reference", "analysis_object_array", "analysis_feature_reference"])
def test_incomplete_external_reference_inventory_refuses(case):
    from anyfem.model.attributes import Support
    from anyfem.model.records import AnalysisDefinition
    project,root,children,left=split_plate_project();pressure,region=child_pressure(project,left)
    if case=="duplicate_load":project.load_case().pressures.append(pressure)
    elif case=="missing_coordinate":project.supports.append(Support("Fixed",EntityRef("face",left),{"uz":0},coordinate_system_id="missing"))
    else:
        value=EntityRef("face",left)
        if case=="analysis_object_array":
            import numpy as np
            value=np.array([value],dtype=object)
        elif case=="analysis_feature_reference":
            from anygeometry.features import FeatureOutputRef
            value=FeatureOutputRef(1,"face","face")
        analysis=AnalysisDefinition("Unrecognized selector",settings={"contact_face":value})
        project.analyses[analysis.id]=analysis
    with pytest.raises(PreparedReferenceScopeError):query_prepared_project_reference_scope(project,project.geometry)


def test_persisted_geometry_does_not_reconstruct_preparation_authority():
    from anygeometry.serialization import from_dict
    project,root,children,left=split_plate_project();child_pressure(project,left)
    manifest=query_prepared_project_reference_scope(project,project.geometry)
    reopened=Project(geometry=from_dict(geometry_to_dict(project.geometry)))
    with pytest.raises(ValueError):validate_prepared_project_reference_scope(reopened,reopened.geometry,manifest)
    with pytest.raises(ValueError):query_prepared_project_reference_scope(reopened,reopened.geometry)


def test_reference_module_remains_headless():
    import subprocess,sys
    result=subprocess.run([sys.executable,"-c",
        "import sys; import anyfem.prepared_reference_scope; "
        "assert not any(k.startswith(('PySide6', 'tkinter', 'anyfem.ui')) for k in sys.modules)"],
        capture_output=True,text=True,check=False)
    assert result.returncode==0,result.stderr
