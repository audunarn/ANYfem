"""Ordinary curved source charts connected along an authored common edge."""
from dataclasses import replace

import numpy as np
import pytest
from anygeometry import RuledSurface
from anygeometry.entities import OrientedEdge
from anyfem import Project, steel
from anyfem.mesh_controls import MeshControls
from anyfem.mesh_jobs import MeshSettings
from anyfem.native_meshing import NativeMeshSettings


def network():
    project = Project("curved adjacent plates")
    project.add_material(steel("S355", .01))
    project.add_plate_section("plate", .01, "S355")
    g = project.geometry
    points = {}
    edges = {}
    faces = []
    for x0, z0, z1 in ((0., 0., .0625), (1., .0625, -.0625), (4., 0., .0625)):
        a, b, c, d = ((x0, 0., 0.), (x0+1, 0., 0.), (x0+1, 1., z1), (x0, 1., z0))
        loop = []
        for start, end in zip((a,b,c,d), (b,c,d,a)):
            for xyz in (start,end):
                if xyz not in points:
                    points[xyz] = g.add_point(*xyz)
            if (end,start) in edges:
                loop.append(OrientedEdge(edges[end,start], False))
            else:
                edge = edges[start,end] = g.add_line(points[start], points[end])
                loop.append(OrientedEdge(edge, True))
        face = g.add_face_from_loop(tuple(loop), corners=(0,1,2,3), surface=RuledSurface((a,b), (d,c)))
        faces.append(face)
    g.add_sheet(tuple(faces[:2]))
    g.add_sheet((faces[-1],))
    for face in faces:
        project.assign_plate(face, "plate")
    project.set_native_triangulation_backend("python")
    project.native_mesh_settings = NativeMeshSettings.create(.5, backend="mapped",
        parameters={"geometry_approximation_error": .02})
    settings = MeshSettings.create(.5, strategy="mapped", element_order="linear", controls=MeshControls())
    shared, = set(u.edge for u in g.faces[faces[0]].loop) & set(u.edge for u in g.faces[faces[1]].loop)
    return project, settings, faces, shared


def common_chart_network():
    from anygeometry.nurbs import CurveOnSurface, NurbsCurve, NurbsSurface
    project = Project("shared curved source chart")
    project.add_material(steel("S355", .01))
    project.add_plate_section("plate", .01, "S355")
    project.add_plate_section("stiff", .012, "S355")
    g = project.geometry
    knot = (0.,0.,1.,1.)
    chart = NurbsSurface(1,1,knot,knot,(((0.,0.,0.),(0.,1.,0.)),((4.,0.,0.),(4.,1.,.125))))
    vertices, edges, faces = {}, {}, []
    for u in (0., .25, .75):
        trim = ((u,0.),(u+.25,0.),(u+.25,1.),(u,1.))
        loop = []
        for a,b in zip(trim, (*trim[1:],trim[0])):
            for uv in (a,b):
                if uv not in vertices:
                    vertices[uv] = g.add_point(*chart.evaluate(*uv))
            if (b,a) in edges:
                loop.append(OrientedEdge(edges[b,a], False))
            else:
                edge = edges[a,b] = g.add_curve(vertices[a],vertices[b],CurveOnSurface(chart,NurbsCurve(1,knot,(a,b))))
                loop.append(OrientedEdge(edge,True))
        face = g.add_face_from_loop(tuple(loop), corners=(0,1,2,3), surface=chart)
        g.update_face_metadata(face, material_assignment="stiff" if u == .25 else "plate")
        faces.append(face)
    g.add_sheet(tuple(faces[:2]))
    g.add_sheet((faces[-1],))
    for face, section in zip(faces, ("plate","stiff","plate")):
        project.assign_plate(face,section)
    project.set_native_triangulation_backend("python")
    seam = edges[(.25,0.),(.25,1.)]
    project.native_mesh_settings = NativeMeshSettings.create(1.5, backend="native",
        parameters={"geometry_approximation_error": .02})
    settings = MeshSettings.create(1.5, strategy="native", element_order="linear", overrides={seam: 2},
        controls=MeshControls(point_placement="frontal_delaunay", recombine=False))
    return project,settings,faces,seam


@pytest.fixture(scope="module", params=[network, common_chart_network], ids=["ruled","nurbs"])
def published(request, tmp_path_factory):
    project, settings, faces, seam = request.param()
    session = project.mesh_session()
    parent = session.generate_mesh(settings)
    assert set(parent.elements_of_face) == set(faces)
    assert project.mesh_records[session.mesh_record_id].summary["regional_baseline"]["source"]["components"] == [
        faces[:2], [faces[-1]]]
    path = session.save(tmp_path_factory.mktemp(request.param.__name__) / "network.anyfem")
    session.close()
    return path, settings, faces, seam


def _check_sides(mesh, faces, seam):
    stations = mesh.nodes_of_edge[seam]
    assert len(stations) >= 3
    for a, b in zip(stations, stations[1:]):
        sides = []
        for f in faces[:2]:
            hits = []
            for e in mesh.elements_of_face[f]:
                cell = mesh.shells[e]
                hits.extend((i,j) for i,j in zip(cell, cell[1:]+cell[:1]) if {i,j} == {a,b})
            side, = hits
            sides.append(side)
        assert sides[0] == sides[1][::-1]




def test_missing_member_of_station_chain_is_refused(published):
    from anyfem.io.project_file import load_project
    from anyfem.mesh_general_update import GeneralSurfaceUnsupported, _inputs, _mesh_owners
    from anymesher.serialize import mesh_to_dict, mesh_from_dict

    path, settings, _, seam = published
    project = load_project(path)
    session = project.mesh_session()
    mesh = mesh_from_dict(mesh_to_dict(session.mesh))
    del mesh.nodes_of_edge[seam][1]
    records,_ = _inputs(project, settings)
    with pytest.raises(GeneralSurfaceUnsupported, match="ordered source edge stations"):
        _mesh_owners(mesh, records)
