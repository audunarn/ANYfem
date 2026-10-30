"""Exact cut-to-physical-beam orchestration, with reversible project attributes."""
import numpy as np
import pytest
from anygeometry import Cylinder, OrientedEdge, Arc, to_dict
from anyfem import commands
from anyfem.io.project_file import project_to_dict, project_from_dict
from test_physical_beams import axis


def content(p):
    """Undo publishes a fresh revision; compare all persisted design content."""
    result = project_to_dict(p)
    result["geometry"].pop("revision", None)
    result["geometry"].pop("checksum", None)
    result["geometry"].pop("id_state", None)
    return result


def plate(geometry, points):
    vertices = [geometry.add_point(*point) for point in points]
    return geometry.add_face(geometry.add_polyline(vertices, close=True), (0,1,2,3))


def project():
    p, _ = axis()
    # Keep unrelated axes: a cut must not purge unrelated orphan topology.
    face = plate(p.geometry, ((0,0,0),(4,0,0),(4,4,0),(0,4,0)))
    p.add_plate_section("plate", thickness=.01, material="S355")
    p.assign_plate(face, "plate")
    return p, face


def test_plane_cut_beam_undo_reopen_mesh():
    p, face = project(); stack = commands.CommandStack(p)
    before = content(p)
    before_ids = project_to_dict(p)["geometry"]["id_state"]
    result = stack.run(commands.CutPlate(face, origin=(2,0,0), normal=(1,0,0)))
    assert len(result.target_faces) == 2 and not result.cutter_faces
    assert set(p.face_sections) == set(result.target_faces)
    assert set(p.face_sections.values()) == {"plate"}
    for edge in result.trace_edges:
        assert set(p.geometry.faces_using_edge(edge)) == set(result.target_faces)
    cut = content(p)
    stack.undo()
    actual = content(p)
    assert actual == before
    after_ids = project_to_dict(p)["geometry"]["id_state"]
    assert all(after_ids[key] >= value for key, value in before_ids.items())
    stack.redo(); assert content(p) == cut
    stack.run(commands.AddBeamMember(result.trace_edges, "beam"))
    restored = project_from_dict(project_to_dict(p))
    assert restored.edge_sections == dict.fromkeys(result.trace_edges, "beam")
    assert restored.generate_mesh(1).num_elements > 0
    commands.CommandStack(restored).run(commands.AddPoint(30,30,30))
    assert len(restored.geometry.members) == 1
    assert restored.edge_sections == dict.fromkeys(result.trace_edges, "beam")
    assert restored.generate_mesh(1).num_elements > 0
    stack.run(commands.AddPoint(20,20,20))
    assert len(p.geometry.members) == 1
    assert set(p.face_sections) == set(p.geometry.faces)
    assert p.edge_sections == dict.fromkeys(result.trace_edges, "beam")
    assert not p.resolve_section_assignments(strict=True)


def test_plate_cutter_finite_extent_and_assignments():
    p, face = project()
    cutter = plate(p.geometry, ((2,-1,-1),(2,5,-1),(2,5,1),(2,-1,1)))
    p.add_plate_section("cutter", thickness=.02, material="S355")
    p.assign_plate(cutter, "cutter")
    result = commands.CommandStack(p).run(commands.CutPlate(face, cutter_face_id=cutter))
    assert set(result.target_faces).isdisjoint(result.cutter_faces)
    assert all(p.face_sections[f] == "plate" for f in result.target_faces)
    assert all(p.face_sections[f] == "cutter" for f in result.cutter_faces)
    for edge in result.trace_edges:
        users = set(p.geometry.faces_using_edge(edge))
        assert users & set(result.target_faces) and users & set(result.cutter_faces)
        assert users <= set((*result.target_faces,*result.cutter_faces))
    p, face = project()
    remote = plate(p.geometry, ((2,5,-1),(2,6,-1),(2,6,1),(2,5,1)))
    before = project_to_dict(p)
    with pytest.raises(ValueError): commands.CommandStack(p).run(commands.CutPlate(face, cutter_face_id=remote))
    assert project_to_dict(p) == before


@pytest.mark.parametrize("origin,normal", [((0,0,0),(0,0,1)), ((0,0,1),(0,0,1)), ((0,0,0),(0,0,0)), ((float('nan'),0,0),(1,0,0))])
def test_refusals_are_atomic(origin, normal):
    p, face = project(); before = project_to_dict(p)
    with pytest.raises(ValueError):commands.CommandStack(p).run(commands.CutPlate(face, origin=origin, normal=normal))
    assert project_to_dict(p) == before


def test_cylinder_horizontal_cut_retains_exact_arc():
    p, _ = axis(); g = p.geometry
    surface = Cylinder(np.zeros(3), np.array((0.,0.,1.)), np.array((1.,0.,0.)), 2., 3., sweep_angle=1.4)
    lower = [g.add_point(*surface.evaluate(u,0)) for u in (0,.5,1)]
    upper = [g.add_point(*surface.evaluate(u,1)) for u in (0,.5,1)]
    bottom = g.add_arc(*lower); right = g.add_line(lower[2], upper[2])
    top = g.add_arc(*upper); left = g.add_line(lower[0],upper[0])
    face = g.add_face_from_loop((OrientedEdge(bottom,True),OrientedEdge(right,True),OrientedEdge(top,False),OrientedEdge(left,False)), (0,1,2,3), surface=surface)
    p.add_plate_section("plate", thickness=.01, material="S355")
    p.assign_plate(face, "plate")
    stack = commands.CommandStack(p)
    result = stack.run(commands.CutPlate(face, origin=(0,0,1.5), normal=(0,0,1)))
    assert all(isinstance(g.edges[e].curve, Arc) for e in result.trace_edges)
    stack.run(commands.AddBeamMember(result.trace_edges, "beam"))
    restored = project_from_dict(project_to_dict(p))
    assert len(restored.geometry.members) == 1
    assert all(isinstance(restored.geometry.edges[e].curve, Arc) for e in result.trace_edges)
    mesh = restored.generate_mesh(.75)
    shell_nodes = {face_id: {node for element in mesh.elements_of_face[face_id]
        for node in mesh.shells[element]} for face_id in result.target_faces}
    for edge in result.trace_edges:
        nodes = {node for element in mesh.elements_of_edge[edge] for node in mesh.beams[element]}
        assert nodes and all(nodes <= face_nodes for face_nodes in shell_nodes.values())


def test_command_authored_plate_attributes_and_history():
    from anyfem.model.attributes import Support
    from anyfem.model.regions import Region, ManualRegion
    p, _ = axis(); stack = commands.CommandStack(p)
    vertices = [stack.run(commands.AddPoint(*point)) for point in ((0,0,0),(4,0,0),(4,4,0),(0,4,0))]
    face = stack.run(commands.AddPlate(vertices))
    p.add_plate_section("plate", thickness=.01, material="S355"); p.assign_plate(face, "plate")
    ref = p.geometry.entity_ref("face", face)
    stack.run(commands.AddPressure(ref, 12.0))
    stack.run(commands.AddSupport(Support("plate support", ref, {"uz":0.0})))
    region = Region("cut target", "geometry", "face", ManualRegion((ref,)))
    p.regions.add(region)
    result = stack.run(commands.CutPlate(face, origin=(2,0,0), normal=(1,0,0)))
    targets = {p.geometry.entity_ref("face", f) for f in result.target_faces}
    assert {load.ref for load in p.load_case("default").pressures} == targets
    assert all(load.value == 12 for load in p.load_case("default").pressures)
    assert {support.ref for support in p.supports} == targets
    assert set(p.regions.resolve(region.id, geometry=p.geometry)) == targets
    assert not p.resolve_section_assignments(strict=True)
    stack.run(commands.AddBeamMember(result.trace_edges, "beam"))
    restored = project_from_dict(project_to_dict(p))
    assert len(restored.geometry.features.records) == 6
    assert restored.generate_mesh(1).num_elements > 0


def test_mixed_cutter_and_plane_arguments_refused():
    p, face = project(); cutter = plate(p.geometry, ((2,-1,-1),(2,5,-1),(2,5,1),(2,-1,1)))
    before = project_to_dict(p)
    for kwargs in ({"origin":(0,0,0)}, {"normal":(1,0,0)}, {"origin":(0,0,0),"normal":(1,0,0)}):
        with pytest.raises(ValueError):commands.CommandStack(p).run(commands.CutPlate(face, cutter_face_id=cutter, **kwargs))
    assert project_to_dict(p) == before


def test_second_cut_and_later_modelling():
    p, face = project(); stack = commands.CommandStack(p)
    result = stack.run(commands.CutPlate(face, origin=(2,0,0), normal=(1,0,0)))
    result = stack.run(commands.CutPlate(result.target_faces[0], origin=(0,2,0), normal=(0,1,0)))
    stack.run(commands.AddPoint(20,20,20))
    stack.run(commands.AddBeamMember(result.trace_edges, "beam"))
    assert project_from_dict(project_to_dict(p)).generate_mesh(1).num_elements > 0


def test_short_finite_cutter_selects_only_physical_trace():
    p, face = project()
    cutter = plate(p.geometry, ((2,1,-1),(2,3,-1),(2,3,1),(2,1,1)))
    result = commands.CommandStack(p).run(commands.CutPlate(face, cutter_face_id=cutter))
    for edge in result.trace_edges:
        axis = p.geometry.edges[edge]
        for vertex in (axis.start,axis.end):
            x,y,z = p.geometry.vertex_position(vertex)
            assert x == pytest.approx(2) and 1 <= y <= 3 and z == pytest.approx(0)


def test_edit_cut_after_beam_preserves_member_or_refuses_atomically(record_property):
    p, face = project(); stack = commands.CommandStack(p)
    cut = commands.CutPlate(face, origin=(2,0,0), normal=(1,0,0))
    result = stack.run(cut)
    stack.run(commands.AddBeamMember(result.trace_edges, "beam"))
    before = project_to_dict(p)
    try:
        stack.run(commands.EditFeature(cut._feature_id, parameters={"origin":(1,0,0), "normal":(1,0,0)}))
    except ValueError as error:
        record_property("edit_outcome", "atomic refusal: " + str(error))
        assert str(error)
        assert project_to_dict(p) == before
    else:
        record_property("edit_outcome", "member retained and retargeted")
        assert len(p.geometry.members) == 1
        assert p.edge_sections
        for edge in p.edge_sections:
            axis = p.geometry.edges[edge]
            assert p.geometry.vertex_position(axis.start)[0] == pytest.approx(1)
            assert p.geometry.vertex_position(axis.end)[0] == pytest.approx(1)
        assert project_from_dict(project_to_dict(p)).generate_mesh(1).num_elements > 0


def test_reopened_cut_checksum_cannot_be_bypassed_by_executor_refresh():
    p, face = project()
    commands.CommandStack(p).run(commands.CutPlate(face, origin=(2,0,0), normal=(1,0,0)))
    p = project_from_dict(project_to_dict(p))
    vertex = next(iter(p.geometry.faces.values())).loop[0]
    identifier = p.geometry.edges[vertex.edge].start
    position = p.geometry.vertex_position(identifier)
    p.geometry.move_point(identifier, float(position[0]+.125), float(position[1]), float(position[2]))
    before = to_dict(p.geometry, include_features=False)
    with pytest.raises(ValueError, match="checksum"):
        commands.CommandStack(p).run(commands.AddPoint(30,30,30))
    assert to_dict(p.geometry, include_features=False) == before


def test_future_cut_feature_version_cannot_use_v1_executor():
    from anygeometry.features import FeatureRecord
    from anyfem.geometry_features import anyfem_feature_registry, PLATE_CUT_KIND
    p, _ = project()
    record = FeatureRecord(1, PLATE_CUT_KIND, "Future cut", kind_version=2)
    before = project_to_dict(p)
    with pytest.raises(ValueError, match="version"):
        anyfem_feature_registry().execute(p.geometry, record, {})
    assert project_to_dict(p) == before

    from dataclasses import replace
    stack = commands.CommandStack(p)
    stack.run(commands.CutPlate(next(iter(p.geometry.faces)), origin=(2,0,0), normal=(1,0,0)))
    snapshot = p.geometry.features.snapshot()
    snapshot["records"] = [replace(item, kind_version=2, state="frozen") for item in snapshot["records"]]
    p.geometry.features.restore(snapshot)
    before = project_to_dict(p)
    with pytest.raises(ValueError, match="version"):
        commands.CommandStack(p).run(commands.AddPoint(20,20,20))
    assert project_to_dict(p) == before


def test_invalid_cut_state_cannot_be_repaired_by_unrelated_append():
    from dataclasses import replace
    p, face = project()
    commands.CommandStack(p).run(commands.CutPlate(face, origin=(2,0,0), normal=(1,0,0)))
    snapshot = p.geometry.features.snapshot()
    snapshot["records"] = [replace(item, state="invalid", diagnostic="owner checksum is invalid") for item in snapshot["records"]]
    p.geometry.features.restore(snapshot)
    before = to_dict(p.geometry, include_features=False)
    records = p.geometry.features.records
    with pytest.raises(ValueError, match="owner checksum is invalid"):
        commands.CommandStack(p).run(commands.AddPoint(30,30,30))
    assert to_dict(p.geometry, include_features=False) == before
    assert [(r.state,r.diagnostic,r.materialization_checksum) for r in p.geometry.features.records] == [(r.state,r.diagnostic,r.materialization_checksum) for r in records]
