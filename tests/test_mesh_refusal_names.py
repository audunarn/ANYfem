"""A quad-first refusal names the source face, its builder groups and a location (probe diagnostics)."""

import pytest
from anygeometry import EntityRef, OrientedEdge

from anymesher.errors import MeshError


def _project_with_sliver():
    from anyfem import Project, steel

    project = Project("sliver refusal")
    project.add_material(steel("S355", .01))
    project.add_plate_section("plate", .01, "S355")
    g = project.geometry
    faces = []
    for points, corners in ((((0., 0., 0.), (2., 0., 0.), (2., 1., 0.), (0., 1., 0.)), (0, 1, 2, 3)),
                            (((5., 0., 0.), (6., 0., 0.), (6., .05, 0.)), (0, 1, 2))):
        edges = g.add_polyline(g.add_points(points), close=True)
        loop = tuple(OrientedEdge(e, True) for e in edges)
        face = g.add_face_from_loop(loop, corners=corners) if len(corners) == 4 else g.add_face_from_loop(loop)
        g.add_sheet((face,))
        project.assign_plate(face, "plate")
        faces.append(face)
    g.add_to_group("rectangle plate", [EntityRef("face", faces[0])])
    g.add_to_group("sliver plate", [EntityRef("face", faces[1])])
    g.add_to_group("all plates", [EntityRef("face", item) for item in faces])
    return project, faces[1]


def _assert_named(error, sliver):
    text = str(error)
    assert text.startswith("T3 cell "), text
    assert "[quad-first face " in text and " near (" in text, text
    assert f"[source face {sliver}; groups: all plates; sliver plate]" in text, text
    assert error.source_face_ids == (sliver,)
    assert error.builder_groups == ("all plates", "sliver plate")
    x, y, _z = error.quad_first_location
    assert 5. <= x <= 6. and 0. <= y <= .05


def test_project_generate_mesh_refusal_names_source_face_and_groups():
    project, sliver = _project_with_sliver()
    with pytest.raises(MeshError) as caught:
        project.generate_mesh(.5, strategy="quad_first")
    _assert_named(caught.value, sliver)


def test_mesh_session_refusal_names_source_face_and_groups():
    from anyfem.mesh_jobs import MeshSettings

    project, sliver = _project_with_sliver()
    with pytest.raises(MeshError) as caught:
        project.mesh_session().generate_mesh(MeshSettings.create(.5, element_order="linear", strategy="quad_first"))
    _assert_named(caught.value, sliver)


def test_refusal_without_a_working_face_is_untouched():
    from anyfem.mesh_refusal_names import call_naming_source_faces

    def refuse():
        raise MeshError("quad-first requires at least one selected face")

    with pytest.raises(MeshError) as caught:
        call_naming_source_faces(refuse, {"face:1": ("face:2",)}, None)
    assert str(caught.value) == "quad-first requires at least one selected face"
    assert not hasattr(caught.value, "source_face_ids")
