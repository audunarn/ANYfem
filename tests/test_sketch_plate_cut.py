"""Linked sketch openings through the public document command path."""
from dataclasses import replace
import json
import numpy as np
import pytest
from anygeometry import EntityRef, SketchDefinition, SketchEntity, SketchWire
from anyfem import Project, commands, steel
from anyfem.io.project_file import project_to_dict, project_from_dict


def plate_project():
    project = Project()
    vertices = project.geometry.add_points(((0, 0, 0), (8, 0, 0), (8, 6, 0), (0, 6, 0)))
    face = project.geometry.add_plate(vertices)
    project.add_material(steel())
    material = next(iter(project.materials))
    project.add_plate_section("Plate", .012, material)
    project.assign_plate(face, "Plate")
    return project, face, commands.CommandStack(project)


def circle_definition(x=3., radius=.75):
    return SketchDefinition(
        points={"centre": (x, 3.), "rim": (x + radius, 3.)}, version=2,
        entities=(SketchEntity("circle", "circle", ("centre", "rim")),),
        wires=(SketchWire("opening", ("circle",), True),),
    )


def test_arc_chord_cut_keeps_driving_sketch_and_replays():
    project, face, stack = plate_project()
    definition = SketchDefinition(
        points={"a": (2., 3.), "via": (4., 5.), "b": (6., 3.)}, version=2,
        entities=(SketchEntity("arc", "arc", ("a", "via", "b")),
                  SketchEntity("chord", "line", ("b", "a"))),
        wires=(SketchWire("opening", ("arc", "chord"), True),))
    source = add_plane_sketch(stack, definition)
    stack.run(commands.CutPlateWithSketch(EntityRef("face", face), source.feature_id))
    assert len(project.geometry.faces[face].holes[0]) == 3
    assert len([key for key in source.outputs if key.startswith("entity/")]) == 2
    changed = replace(definition, points={**definition.points, "via": (4., 4.5)})
    stack.run(commands.EditFeature(source.feature_id, parameters=changed.to_parameters() | {"plane": source.parameters["plane"]}))
    assert len(project.geometry.faces[face].holes[0]) == 3
    restored = project_from_dict(json.loads(json.dumps(project_to_dict(project))))
    assert restored.regenerate_geometry_features().success


def add_plane_sketch(stack, definition):
    return stack.run(commands.AddPlaneSketch(definition, (0, 0, 0), (0, 0, 1), (1, 0, 0), "Opening"))


def test_circle_cut_updates_retains_assignments_undo_and_reopen():
    project, face, stack = plate_project()
    source = add_plane_sketch(stack, circle_definition())
    cut = stack.run(commands.CutPlateWithSketch(EntityRef("face", face), source.feature_id))
    assert cut.parameters["wire_ids"] == ["opening"]
    assert len(project.geometry.faces[face].holes) == 1
    assert project.face_sections[face] == "Plate"
    assert project.plate_sections["Plate"].thickness == .012
    stack.run(commands.EditFeature(source.feature_id, parameters=circle_definition(4., 1.).to_parameters() | {
        "plane": {"origin": [0, 0, 0], "normal": [0, 0, 1], "x_axis": [1, 0, 0]}}))
    ring = project.geometry.faces[face].holes[0]
    samples = np.vstack([project.geometry.sample_edge(item.edge, np.linspace(0, 1, 8)) for item in ring])
    np.testing.assert_allclose(np.linalg.norm(samples[:, :2] - (4., 3.), axis=1), 1., atol=1e-10)
    restored = project_from_dict(json.loads(json.dumps(project_to_dict(project))))
    assert len(restored.geometry.faces[face].holes) == 1
    assert restored.face_sections[face] == "Plate"
    assert restored.regenerate_geometry_features().success
    stack.undo()
    stack.undo()
    assert not project.geometry.faces[face].holes
    stack.redo()
    assert len(project.geometry.faces[face].holes) == 1


def test_source_delete_suppress_and_invalid_edit_are_non_mutating():
    project, face, stack = plate_project()
    source = add_plane_sketch(stack, circle_definition())
    stack.run(commands.CutPlateWithSketch(EntityRef("face", face), source.feature_id, name="Access hole"))
    before = json.dumps(project_to_dict(project), sort_keys=True)
    for command in (commands.DeleteFeature(source.feature_id), commands.SuppressFeature(source.feature_id)):
        with pytest.raises(ValueError, match="Access hole"):
            stack.run(command)
        assert json.dumps(project_to_dict(project), sort_keys=True) == before
    parameters = circle_definition(.1, 1.).to_parameters() | {"plane": source.parameters["plane"]}
    with pytest.raises(ValueError):
        stack.run(commands.EditFeature(source.feature_id, parameters=parameters))
    assert json.dumps(project_to_dict(project), sort_keys=True) == before


def test_existing_holes_and_new_unselected_contours_are_preserved():
    project, face, stack = plate_project()
    first = add_plane_sketch(stack, circle_definition(2., .5))
    stack.run(commands.CutPlateWithSketch(EntityRef("face", face), first.feature_id))
    second_definition = circle_definition(5., .5)
    second = add_plane_sketch(stack, second_definition)
    stack.run(commands.CutPlateWithSketch(EntityRef("face", face), second.feature_id))
    assert len(project.geometry.faces[face].holes) == 2
    changed = replace(second_definition,
        points={**second_definition.points, "extra-c": (6.5, 2.), "extra-r": (6.8, 2.)},
        entities=second_definition.entities + (SketchEntity("extra", "circle", ("extra-c", "extra-r")),),
        wires=second_definition.wires + (SketchWire("extra-wire", ("extra",), True),))
    stack.run(commands.EditFeature(second.feature_id, parameters=changed.to_parameters() | {"plane": second.parameters["plane"]}))
    assert len(project.geometry.faces[face].holes) == 2


def test_legacy_sketch_can_drive_cut_before_and_after_upgrade():
    from anygeometry import upgrade_sketch
    project, face, stack = plate_project()
    definition = SketchDefinition(points={"a": (1., 1.), "b": (2., 1.), "c": (2., 2.), "d": (1., 2.)},
                                  path=("a", "b", "c", "d"))
    source = add_plane_sketch(stack, definition)
    stack.run(commands.CutPlateWithSketch(EntityRef("face", face), source.feature_id))
    stack.run(commands.EditFeature(source.feature_id, parameters=upgrade_sketch(definition).to_parameters() | {"plane": source.parameters["plane"]}))
    assert len(project.geometry.faces[face].holes) == 1


def test_face_supported_sketch_cut_replays_without_cycle():
    project, face, stack = plate_project()
    source = stack.run(commands.AddSketch(EntityRef("face", face), circle_definition()))
    stack.run(commands.CutPlateWithSketch(EntityRef("face", face), source.feature_id))
    changed = circle_definition(4., .8)
    stack.run(commands.EditFeature(source.feature_id, parameters=changed.to_parameters()))
    assert len(project.geometry.faces[face].holes) == 1
    assert project.regenerate_geometry_features().success


def test_circular_opening_mesh_follows_curves_and_does_not_bridge_hole():
    from shapely.geometry import Point, Polygon
    project, face, stack = plate_project()
    source = add_plane_sketch(stack, circle_definition())
    stack.run(commands.CutPlateWithSketch(EntityRef("face", face), source.feature_id))
    mesh = project.generate_mesh(1.)
    ring = project.geometry.faces[face].holes[0]
    boundary_ids = {node for edge in ring for node in mesh.nodes_of_edge[edge.edge]}
    boundary = np.asarray([mesh.nodes[node][:2] for node in boundary_ids])
    np.testing.assert_allclose(np.linalg.norm(boundary - (3., 3.), axis=1), .75, atol=1e-8)
    angles = np.arctan2(boundary[:, 1] - 3., boundary[:, 0] - 3.)
    boundary = boundary[np.argsort(angles)]
    expected = 48. - Polygon(boundary).area
    polygons = [Polygon([mesh.nodes[node][:2] for node in connectivity[:4 if len(connectivity) in (4, 8, 9) else 3]])
                for connectivity in mesh.shells.values()]
    assert polygons and all(not polygon.covers(Point(3., 3.)) for polygon in polygons)
    assert sum(polygon.area for polygon in polygons) == pytest.approx(expected, rel=1e-8)


@pytest.mark.parametrize("kind", ["spline", "nurbs"])
def test_closed_spline_cut_reopens_and_meshes(kind):
    project, face, stack = plate_project()
    definition = SketchDefinition(
        points={"a": (2., 2.), "b": (4., 2.), "c": (4., 4.), "d": (2., 4.)}, version=2,
        entities=(SketchEntity("curve", kind, ("a", "b", "c", "d"), closed=True),),
        wires=(SketchWire("opening", ("curve",), True),))
    source = add_plane_sketch(stack, definition)
    stack.run(commands.CutPlateWithSketch(EntityRef("face", face), source.feature_id))
    restored = project_from_dict(json.loads(json.dumps(project_to_dict(project))))
    assert len(restored.geometry.faces[face].holes) == 1
    mesh = restored.generate_mesh(.75)
    from shapely.geometry import Point, Polygon
    assert mesh.shells
    assert all(not Polygon([mesh.nodes[node][:2] for node in connectivity[:4 if len(connectivity) in (4, 8, 9) else 3]]).covers(Point(3., 3.))
               for connectivity in mesh.shells.values())


def test_rounding_linked_contour_rebinds_piece_count_atomically():
    from anygeometry import upgrade_sketch, round_sketch_corner
    project, face, stack = plate_project()
    definition = upgrade_sketch(SketchDefinition(
        points={"a": (2., 2.), "b": (4., 2.), "c": (4., 4.), "d": (2., 4.)}, path=("a", "b", "c", "d")))
    source = add_plane_sketch(stack, definition)
    cut = stack.run(commands.CutPlateWithSketch(EntityRef("face", face), source.feature_id))
    changed = round_sketch_corner(definition, "legacy-0", "legacy-1", .25)
    stack.run(commands.EditFeature(source.feature_id, parameters=changed.to_parameters() | {"plane": source.parameters["plane"]}))
    current = project.geometry.features.get(cut.feature_id)
    assert sum(current.parameters["wire_sizes"]) == 5
    assert len(project.geometry.faces[face].holes[0]) == 5
