"""The menu's example is a connected, editable and persistable section."""
import numpy as np

from anyfem.application.examples import floating_barge_section
from anyfem.io.project_file import project_from_dict, project_to_dict


def test_barge_section_roundtrip():
    project = floating_barge_section()
    positions = np.array([project.geometry.vertex_position(identifier) for identifier in project.geometry.vertices])
    np.testing.assert_allclose(positions.max(axis=0)-positions.min(axis=0), (5, 11, 2.5))
    assert len(project.geometry.faces) == len(project.face_sections) == 5
    assert not project.geometry.validate_topology()
    restored = project_from_dict(project_to_dict(project))
    assert restored.face_sections == project.face_sections
    assert restored.edge_sections == project.edge_sections
    assert restored.geometry.attachments == project.geometry.attachments
    assert restored.supports == project.supports
    assert {key: value.name for key,value in restored.analyses.items()} == {
        key: value.name for key,value in project.analyses.items()}
    assert restored.target_size == .7
    for original, reopened in zip(project.load_case().line_loads, restored.load_case().line_loads):
        assert original.ref == reopened.ref
        np.testing.assert_allclose(original.force_per_length, reopened.force_per_length)
    sets = {region.name: region for region in restored.regions}
    assert {region.name: region.definition for region in restored.regions} == {
        region.name: region.definition for region in project.regions}
    for name in ("Deck", "Bottom", "Port side", "Starboard side", "Transverse bulkhead"):
        assert name in sets
    assert restored.plate_sections["Deck 12 mm"].thickness == .012
    assert restored.plate_sections["Hull 10 mm"].thickness == .010
    assert restored.plate_sections["Bulkhead 8 mm"].thickness == .008


def test_beam_spacing_shared_topology_and_orientation():
    project = floating_barge_section()
    geometry = project.geometry
    stiffeners, girders = [], []
    for identifier, name in project.edge_sections.items():
        edge = geometry.edges[identifier]
        points = np.array([geometry.vertex_position(edge.start), geometry.vertex_position(edge.end)])
        members = geometry.members_using_edge(identifier)
        assert len(members) == 1
        attachments = geometry.attachments_for_member(members[0])
        assert len(attachments) == 1
        attachment = geometry.attachments[attachments[0]]
        assert attachment.kind.value == "member_on_face"
        for point in points:
            assert geometry.project_to_face(attachment.target_id, point)[2] < 1.e-9
        section = project.beam_sections[name]
        axis = points[1]-points[0]
        assert abs(np.dot(axis, section.web_direction)) < 1.e-12
        if "stiffeners" in name:
            assert np.allclose(axis[1:], 0)
            stiffeners.append((name, points))
        else:
            assert points[0,0] == points[1,0]
            girders.append(points)
    assert len(stiffeners) == 108 and len(girders) == 80
    for surface, coordinate in (("Deck",1),("Bottom",1),("Port side",2),("Starboard side",2)):
        positions = sorted({round(points[0,coordinate], 6) for name,points in stiffeners if name.startswith(surface)})
        np.testing.assert_allclose(np.diff(positions), .7)
    np.testing.assert_allclose(sorted({points[0,0] for points in girders}), (0,4))
    assert {region.name for region in project.regions} >= {
        "Longitudinal stiffeners — 700 mm", "Transverse girders — 4000 mm"}
    joints = [attachment for attachment in geometry.attachments.values()
              if attachment.kind.value == "vertex_on_face"]
    assert len(joints) == 44
    for attachment in joints:
        point = geometry.vertex_position(attachment.source_id)
        assert point[0] == 2.5
        assert geometry.project_to_face(attachment.target_id, point)[2] < 1.e-9


def test_continuous_plates_and_only_user_point_markers():
    from anyfem.ui.scene import build_geometry_scene
    from anyfem.presentation.points import displayed_geometry_point_ids
    from anyfem import commands
    project = floating_barge_section()
    scene = build_geometry_scene(project, show_beam_sections=False)
    assert len(scene.faces) == 5
    assert len(scene.points) == 8
    assert all(point.position[0] in (0,5) and abs(point.position[1]) == 5.5
               and point.position[2] in (0,2.5) for point in scene.points)
    restored = project_from_dict(project_to_dict(project))
    assert displayed_geometry_point_ids(restored.geometry) == displayed_geometry_point_ids(project.geometry)
    stack = commands.CommandStack(project)
    identifier = stack.run(commands.AddPoint(6,0,3))
    assert identifier in displayed_geometry_point_ids(project.geometry)
    assert len(build_geometry_scene(project, show_beam_sections=False).points) == 9
    stack.undo()
    assert len(build_geometry_scene(project, show_beam_sections=False).points) == 8


def test_explicit_point_policy_omits_generated_mesh_node_markers():
    from types import SimpleNamespace
    from anyfem.model.project import Project
    from anyfem.presentation.points import displayed_geometry_point_ids
    from anyfem.ui.scene import build_mesh_scene
    project = floating_barge_section()
    identifier = displayed_geometry_point_ids(project.geometry)[0]
    mesh = SimpleNamespace(nodes={1: project.geometry.vertex_position(identifier), 2: np.array((2.,1.,1.))},
                           node_of_vertex={identifier: 1}, shells={}, beams={}, elements_of_face={}, elements_of_edge={})
    assert [point.ref.id for point in build_mesh_scene(project, mesh).points] == [1]
    # Without the explicit policy, mesh-only nodes retain their normal markers.
    assert len(build_mesh_scene(Project(), mesh).points) == 2


def test_bending_setup_has_pure_end_couple_and_full_clamp():
    from anyfem.application.examples import barge_bending_moment
    project = floating_barge_section()
    assert project.materials["S355"].yield_stress >= 355.e6
    assert project.target_size == .7
    assert next(iter(project.analyses.values())).type == "linear_static"
    force, moment = np.zeros(3), np.zeros(3)
    for load in project.load_case().line_loads:
        edge = project.geometry.edges[load.ref.id]
        a, b = (project.geometry.vertex_position(v) for v in (edge.start, edge.end))
        assert a[0] == b[0] == 5.
        resultant = load.force_per_length*np.linalg.norm(b-a)
        force += resultant
        moment += np.cross((a+b)/2., resultant)
    np.testing.assert_allclose(force, 0., atol=1.e-8)
    np.testing.assert_allclose(moment, (0.,barge_bending_moment(project),0.), atol=1.e-8)
    for support in project.supports:
        edge = project.geometry.edges[support.ref.id]
        assert project.geometry.vertex_position(edge.start)[0] == project.geometry.vertex_position(edge.end)[0] == 0.
        assert len(support.constraints) == 6 and all(value == 0. for value in support.constraints.values())


def test_barge_end_moment_can_be_one_load_on_a_connection_at_the_section_centre():
    import pytest
    from anyfem.application.examples import barge_bending_moment

    reference = floating_barge_section()
    moment = barge_bending_moment(reference)
    for kind in ("distributing", "rigid"):
        project = floating_barge_section(kind)
        assert not project.load_case().line_loads
        (connection,) = project.connections
        assert connection.kind == kind
        assert len(project.connection_targets(connection)) == 2
        # Deck and bottom end lines: the centre is mid-depth at the end.
        np.testing.assert_allclose(
            project.connection_reference_point(connection), (5., 0., 1.25), atol=1.e-12)
        (load,) = project.load_case().connection_loads
        np.testing.assert_allclose(load.moment, (0., moment, 0.))
        np.testing.assert_allclose(load.force, 0.)
        project.validate()
        restored = project_from_dict(project_to_dict(project))
        assert restored.connections == project.connections
    with pytest.raises(ValueError, match="end_loading"):
        floating_barge_section("elastic")


def test_seaway_example_is_one_load_series_of_600_steps_with_two_stored_loads():
    import pytest
    from anyfem.application.examples import barge_bending_moment, floating_barge_seaway
    from anyfem.io.project_file import project_from_dict, project_to_dict

    project = floating_barge_seaway()
    definition = project.load_cases["Seaway"]
    steps = project.series_children("Seaway")
    assert len(steps) == 600 and "default" not in project.load_cases
    assert len(project.load_cases) == 601 and definition.is_series
    np.testing.assert_allclose([s.time for s in steps], np.arange(600) * 0.1, atol=1.e-9)
    assert [s.step for s in steps] == list(range(600)) and steps[-1].time == pytest.approx(59.9)
    # The loads exist once, on the definition, each with its own envelope.
    assert all(not s.wet_pressures and not s.connection_loads for s in steps)
    assert len(project.wet_surfaces) == 3  # bottom and both sides; the deck is dry
    (wave,) = definition.wet_pressures
    (moment,) = definition.connection_loads
    assert wave.envelope.points == ((0., 0.), (6., 1.))
    assert (moment.envelope.by, moment.envelope.start, moment.envelope.stop) == ("time", 10., 50.)
    assert project.connections[0].kind == "rigid" and moment.connection == project.connections[0].id
    np.testing.assert_allclose(moment.moment, (0., barge_bending_moment(project), 0.))

    def factor(step, load):
        return project.resolve_case(steps[step]).envelope_factor(load.envelope)

    omega = 2.*np.pi/6.
    assert factor(50, moment) == 0.                 # t = 5 s: before the window
    assert factor(100, moment) == 0.                # t = 10 s: opens with the ramp at zero
    t = steps[300].time                              # 30 s: ramp done, inside
    assert factor(300, moment) == pytest.approx(0.3 + 0.4*np.sin(omega*t - 0.8))
    assert factor(501, moment) == 0.                # t = 50.1 s: after the window
    assert factor(30, wave) == pytest.approx(0.5) and factor(400, wave) == 1.
    (analysis,) = project.analyses.values()
    assert analysis.type == "batch_linear_static" and analysis.target_kind == "load_cases"
    project.validate()
    data = project_to_dict(project)
    assert data["anyfem"]["format"] == 11
    series_entry = next(c for c in data["load_cases"] if c["name"] == "Seaway")
    assert len(series_entry["times"]) == 600 and len(series_entry["connection_loads"]) == 1
    restored = project_from_dict(data)
    assert len(restored.series_children("Seaway")) == 600
    assert restored.load_cases["Seaway"].connection_loads[0].envelope == moment.envelope
    with pytest.raises(Exception, match="whole number"):
        floating_barge_seaway(duration=1.0, step=0.3)
    # Another sampling gives another number of steps.
    assert len(floating_barge_seaway(10.0, 0.5).series_children("Seaway")) == 20


def test_large_barge_is_three_times_as_long_with_bulkhead_openings_and_flat_bar_edges():
    from anyfem.application.examples import (
        BARGE_LARGE_STRESS_SCALE, barge_bending_moment, floating_barge_large)

    project = floating_barge_large()
    geometry = project.geometry
    positions = np.array([geometry.vertex_position(identifier) for identifier in geometry.vertices])
    np.testing.assert_allclose(positions.max(axis=0)-positions.min(axis=0), (15, 11, 2.5))
    assert len(geometry.faces) == len(project.face_sections) == 6
    assert not geometry.validate_topology()
    project.validate()
    assert project.target_size == .25
    # Two bulkheads, three trimmed openings in each, nothing else is trimmed.
    assert sorted(region.name for region in project.regions if region.name.startswith("Bulkhead X")) == [
        "Bulkhead X = 10 m", "Bulkhead X = 5 m"]
    trimmed = {identifier: face for identifier, face in geometry.faces.items() if face.holes}
    assert len(trimmed) == 2
    assert {round(geometry.vertex_position(geometry.oriented_start_vertex(face.loop[0]))[0], 9)
            for face in trimmed.values()} == {5., 10.}
    for face in trimmed.values():
        assert len(face.holes) == 3
        for hole in face.holes:
            points = np.array([geometry.vertex_position(geometry.oriented_start_vertex(item)) for item in hole])
            assert len(points) == 8 and len({round(x, 9) for x in points[:, 0]}) == 1
            # Four straight sides joined by four quarter-circle corners of 250 mm radius.
            curves = [type(geometry.edges[item.edge].curve).__name__ for item in hole]
            assert curves == ["Straight", "Arc"]*4
            for item in hole[1::2]:
                np.testing.assert_allclose(geometry.edge_length(item.edge), np.pi/2*.25, rtol=1.e-6)
            np.testing.assert_allclose(np.ptp(points[:, 1]), 1.4)
            np.testing.assert_allclose(np.ptp(points[:, 2]), 1.1)
    assert project.plate_sections[project.face_sections[next(iter(trimmed))]].thickness == .008
    # A flat bar FB 120x10 stands off the plate along every opening edge, attached to its bulkhead.
    bars = [(identifier, name) for identifier, name in project.edge_sections.items() if name.startswith("FB")]
    assert len(bars) == 2*3*8
    hole_edges = {item.edge for face in trimmed.values() for hole in face.holes for item in hole}
    hole_face = {item.edge: face_id for face_id, face in trimmed.items()
                 for hole in face.holes for item in hole}
    assert {identifier for identifier, _ in bars} == hole_edges
    section = project.beam_sections[bars[0][1]]
    assert section.profile == "Flatbar"
    assert (section.flange_width, section.flange_thickness) == (.010, .120)
    for identifier, name in bars:
        (member,) = geometry.members_using_edge(identifier)
        (attachment,) = (geometry.attachments[key] for key in geometry.attachments_for_member(member))
        assert attachment.kind.value == "member_on_face_boundary"
        assert attachment.target_kind.value == "edge" and attachment.target_id == identifier
        assert (attachment.member_range.start, attachment.member_range.end) == (0., 1.)
        assert len(attachment.target_parameters) == 1
        target_range, = attachment.target_parameters
        assert (target_range.start, target_range.end) == (0., 1.)
        assert attachment.sheet_id in geometry.sheets
        assert hole_face[identifier] in {
            geometry.face_uses[use_id].face_id
            for use_id in geometry.sheets[attachment.sheet_id].face_use_ids}
        edge = geometry.edges[identifier]
        axis = geometry.vertex_position(edge.end) - geometry.vertex_position(edge.start)
        assert abs(np.dot(axis, project.beam_sections[name].web_direction)) < 1.e-12
    assert any(attachment.kind.value == "member_on_face"
               and attachment.target_kind.value == "face"
               for attachment in geometry.attachments.values())
    # Girders every 4 m, stiffeners continue through the bulkheads.
    girders = {round(geometry.vertex_position(geometry.edges[i].start)[0], 6)
               for i, name in project.edge_sections.items() if name.endswith("girders")}
    assert girders == {0., 4., 8., 12.}
    # Vertical T-bars on the 700 mm grid between the openings: none inside an opening or on its edge,
    # each standing between the deck and bottom stiffener vertices it meets.
    verticals = [i for i, name in project.edge_sections.items() if name == "Bulkhead stiffeners"]
    assert len(verticals) == 2*6
    ends = {name: {v for i, n in project.edge_sections.items() if n == name
                   for v in (geometry.edges[i].start, geometry.edges[i].end)}
            for name in ("Deck stiffeners", "Bottom stiffeners")}
    for identifier in verticals:
        edge = geometry.edges[identifier]
        a, b = sorted((geometry.vertex_position(edge.start), geometry.vertex_position(edge.end)), key=lambda p: p[2])
        assert a[0] == b[0] in (5., 10.) and a[1] == b[1] and (a[2], b[2]) == (0., 2.5)
        assert not any(abs(a[1]-centre) <= .7+1.e-9 for centre in (-3.4, .1, 3.6))
        low, high = (edge.start, edge.end) if geometry.vertex_position(edge.start)[2] == 0. else (edge.end, edge.start)
        assert low in ends["Bottom stiffeners"] and high in ends["Deck stiffeners"]
        (member,) = geometry.members_using_edge(identifier)
        (attachment,) = (geometry.attachments[key] for key in geometry.attachments_for_member(member))
        assert attachment.kind.value == "member_on_face" and attachment.target_id in trimmed
    assert len(project.edge_sections) == 36*6 + 4*40 + 48 + 12
    # Fixed at X = 0, moment at X = 15 calibrated on its own mesh.
    for support in project.supports:
        edge = geometry.edges[support.ref.id]
        assert geometry.vertex_position(edge.start)[0] == geometry.vertex_position(edge.end)[0] == 0.
    moment = barge_bending_moment(project, BARGE_LARGE_STRESS_SCALE)
    force, couple = np.zeros(3), np.zeros(3)
    for load in project.load_case().line_loads:
        edge = geometry.edges[load.ref.id]
        a, b = (geometry.vertex_position(v) for v in (edge.start, edge.end))
        assert a[0] == b[0] == 15.
        resultant = load.force_per_length*np.linalg.norm(b-a)
        force += resultant
        couple += np.cross((a+b)/2., resultant)
    np.testing.assert_allclose(force, 0., atol=1.e-6)
    np.testing.assert_allclose(couple, (0., moment, 0.), rtol=1.e-12)
    restored = project_from_dict(project_to_dict(project))
    assert restored.edge_sections == project.edge_sections
    assert restored.geometry.attachments == project.geometry.attachments
    assert [face.holes for face in restored.geometry.faces.values()] == [
        face.holes for face in project.geometry.faces.values()]
    for kind in ("distributing", "rigid"):
        other = floating_barge_large(kind)
        (connection,) = other.connections
        assert connection.name == "End section X = 15 m"
        np.testing.assert_allclose(other.connection_reference_point(connection), (15., 0., 1.25), atol=1.e-12)
        other.validate()


def test_the_basic_barge_is_unchanged_by_the_large_variant_parameters():
    project = floating_barge_section()
    assert [region.name for region in project.regions if "ulkhead" in region.name] == ["Transverse bulkhead"]
    assert not any(face.holes for face in project.geometry.faces.values())
    assert not any(name.startswith("FB") for name in project.edge_sections.values())
    assert project.target_size == .7
