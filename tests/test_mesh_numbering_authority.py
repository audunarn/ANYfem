import os

import numpy as np
import pytest

from anyfem.mesh_numbering import MeshNumbering, number_full_mesh
from anygeometry import GeometryModel
from anygeometry.nurbs import CurveOnSurface, NurbsCurve, NurbsSurface
from anymesher.boundary import GlobalEdgeBoundaryRegistry
from anymesher.mesh import Mesh
from anymesher.meshing_view import GeometryMeshingView
from anymesher.serialize import mesh_from_dict, mesh_to_dict


ALLOCATED = MeshNumbering(12, 5, 1, "parent")


def shell(*, explicit_surface=False):
    source = GeometryModel()
    uv = ((0., 0.), (1., 0.), (1., 1.), (0., 1.))
    knots = (0., 0., 1., 1.)
    if explicit_surface:
        support = NurbsSurface(1, 1, knots, knots,
            (((0., 0., 0.), (0., 1., 0.)), ((1., 0., 0.), (1., 1., .1))))
        positions = [support.evaluate(*point) for point in uv]
        points = source.add_points(positions)
        edges = [source.add_curve(points[i], points[(i+1)%4], CurveOnSurface(support,
            NurbsCurve(1, knots, (uv[i], uv[(i+1)%4])))) for i in range(4)]
        face = source.add_face(edges, surface=support)
    else:
        positions = [(u, v, 0.) for u, v in uv]
        edges = source.add_polyline(source.add_points(positions), close=True)
        face = source.add_face(edges)
    mesh = Mesh(geometry_model_id=source.model_id, geometry_revision=source.revision,
                nodes={i: np.array(xyz) for i, xyz in enumerate(positions, 1)},
                quads={1: (1, 2, 3, 4)}, elements_of_face={face: [1]})
    return mesh, source, face, edges[0]


def test_source_routed_shells_refuse_number_changes_before_and_after_transport():
    mesh, source, _, _ = shell(explicit_surface=True)
    mesh.boundary_registry = GlobalEdgeBoundaryRegistry(GeometryMeshingView(source))
    assert number_full_mesh(mesh, MeshNumbering(), source_geometry=source) is mesh
    persisted = mesh_to_dict(mesh)
    with pytest.raises(ValueError, match="live material source authority"):
        number_full_mesh(mesh, ALLOCATED)
    recovered = mesh_from_dict(persisted)
    with pytest.raises(ValueError, match="live material source authority"):
        number_full_mesh(recovered, ALLOCATED, source_geometry=source)
    with pytest.raises(ValueError, match="current source geometry"):
        number_full_mesh(recovered, ALLOCATED)
    assert mesh_to_dict(mesh) == mesh_to_dict(recovered) == persisted


def test_planar_boundary_registry_translates_nodes_and_preserves_exact_stations():
    mesh, source, face, edge = shell()
    registry = GlobalEdgeBoundaryRegistry(GeometryMeshingView(source))
    mesh.boundary_registry = registry
    registry.register(edge, 0., node_id=1, owner=source.handle("face", face))
    registry.register(edge, 0., node_id=1, owner=source.handle("edge", edge))
    registry.register(edge, 1./3.)
    assert number_full_mesh(mesh, MeshNumbering()) is mesh
    result = number_full_mesh(mesh, ALLOCATED)
    assert result.quads == {6: (13, 14, 15, 16)}
    assert result.boundary_registry is not registry
    assert result.boundary_registry.view is registry.view
    entries = result.boundary_registry.entries()
    assert [entry.node_id for entry in entries] == [13, None]
    for old, new in zip(registry.entries(), entries, strict=True):
        assert (new.key, new.owners) == (old.key, old.owners)
        np.testing.assert_array_equal(new.point, old.point)
    assert registry.require(edge, 0.).node_id == 1


def test_foreign_source_and_unknown_station_nodes_are_refused():
    mesh, source, _, edge = shell()
    _, other, _, _ = shell()
    with pytest.raises(ValueError, match="current source geometry"):
        number_full_mesh(mesh, ALLOCATED, source_geometry=other)
    registry = GlobalEdgeBoundaryRegistry(GeometryMeshingView(source))
    mesh.boundary_registry = registry
    registry.register(edge, 0., node_id=99)
    with pytest.raises(ValueError, match="absent mesh node"):
        number_full_mesh(mesh, ALLOCATED)
    assert registry.require(edge, 0.).node_id == 99


@pytest.mark.parametrize(("execution", "recombine"), [("auto", False), ("off", False), ("auto", True)])
def test_rebuilt_native_plate_preserves_stations_and_numbered_admission_in_artifact(tmp_path, execution, recombine):
    from anyfem import Project, steel
    from anyfem.io.artifacts import ArtifactStore
    from anyfem.io.project_file import load_project
    from anyfem.mesh_controls import MeshControls
    from anyfem.mesh_execution import MeshExecutionPolicy
    from anyfem.mesh_jobs import MeshSettings

    project = Project("native planar plate")
    project.add_material(steel("S355", .01))
    project.add_plate_section("plate", .01, "S355")
    points = project.geometry.add_points(((0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)))
    face = project.geometry.add_plate(points)
    project.geometry.add_sheet((face,))
    project.assign_plate(face, "plate")
    settings = MeshSettings.create(.4, strategy="native", element_order="linear",
                                   controls=MeshControls(recombine=recombine),
                                   execution_policy=MeshExecutionPolicy(mode=execution))
    session = project.mesh_session()
    first = session.generate_mesh(settings)
    path = session.save(tmp_path / "rebuilt.anyfem")
    rebuilt = session.generate_mesh(settings)
    assert (rebuilt.quads if recombine else rebuilt.tris)
    assert first.shells.keys().isdisjoint(rebuilt.shells)
    before = first.boundary_registry.entries()
    after = rebuilt.boundary_registry.entries()
    assert before and any(entry.node_id is not None for entry in before)
    for old, new in zip(before, after, strict=True):
        assert old.key == new.key
        np.testing.assert_array_equal(old.point, new.point)
        if old.node_id is not None:
            assert new.node_id in rebuilt.nodes and new.node_id not in first.nodes
            np.testing.assert_array_equal(first.nodes[old.node_id], rebuilt.nodes[new.node_id])
    authority = rebuilt.structural_preparation["qualified_s3"]
    assert authority["status"] == ("ADMITTED" if rebuilt.tris else "NOT_APPLICABLE_NO_TRIANGLES")
    assert set(authority["element_ids"]) == set(rebuilt.tris)
    assert {row["element_id"] for row in authority["admission"]["elements"]} == set(rebuilt.tris)
    record_id = session.mesh_record_id
    assert project.mesh_records[record_id].structural_preparation["qualified_s3"] == authority
    session.save()
    session.close()
    restored = load_project(path).mesh_session(path)
    record = restored.project.mesh_records[record_id]
    stored = ArtifactStore(path).read_mesh_metadata(restored.project.artifacts[record.artifact_id])
    assert restored.mesh.tris == rebuilt.tris and restored.mesh.quads == rebuilt.quads
    assert stored["structural_preparation"]["qualified_s3"] == record.structural_preparation["qualified_s3"]
    assert set(stored["structural_preparation"]["qualified_s3"]["element_ids"]) == set(rebuilt.tris)


@pytest.mark.skipif(os.environ.get("ANYFEM_RUN_PARALLEL_MESH_TESTS") != "1",
                    reason="enable the real two-worker mesher explicitly")
def test_parallel_join_full_publications_preserve_global_ids_and_worker_evidence(tmp_path):
    from anyfem.io.project_file import load_project
    from anyfem.mesh_execution import MeshExecutionPolicy
    from anyfem.mesh_jobs import MeshSettings
    from parallel_mesh_contract import separated_plates

    # Boundary beams keep this real Project.generate_mesh call on the hybrid
    # public path. Each separated component still selects its mapped plate.
    project = separated_plates(2, beams=True)
    settings = MeshSettings.create(.5, strategy="auto", element_order="linear",
                                   execution_policy=MeshExecutionPolicy.explicit(2))
    session = project.mesh_session()
    first = session.generate_mesh(settings)
    parent = session.mesh_record_id
    path = session.save(tmp_path / "joined.anyfem")
    rebuilt = session.generate_mesh(settings)
    for mesh in (first, rebuilt):
        diag = mesh.hybrid_diagnostics
        assert diag["parallel"]["used"] is True and diag["parallel"]["components"] == 2
        assert diag["parallel"]["runtime"]["workers"] == 2
        assert len(set(diag["parallel"]["runtime"]["worker_pids"])) == 2
        assert diag["parallel"]["runtime"]["lifecycle"][-1][::2] == ["shutdown-end", 0]
        assert mesh.quads and mesh.beams and not mesh.tris
        assert set(diag["strategy_by_face"].values()) == {"mapped"}
    assert first.nodes.keys().isdisjoint(rebuilt.nodes)
    old_elements = first.quads.keys() | first.beams.keys() | first.couplings.keys()
    new_elements = rebuilt.quads.keys() | rebuilt.beams.keys() | rebuilt.couplings.keys()
    assert old_elements.isdisjoint(new_elements)
    for face, old in first.elements_of_face.items():
        for old_id, new_id in zip(old, rebuilt.elements_of_face[face], strict=True):
            np.testing.assert_array_equal([first.nodes[n] for n in first.quads[old_id]],
                                          [rebuilt.nodes[n] for n in rebuilt.quads[new_id]])
    session.save()
    session.close()
    restored = load_project(path).mesh_session(path)
    assert restored.mesh_record_id != parent
    assert restored.mesh.quads == rebuilt.quads and restored.mesh.beams == rebuilt.beams
    assert restored.mesh.elements_of_face == rebuilt.elements_of_face
    assert restored.mesh.nodes_of_member == rebuilt.nodes_of_member
    assert mesh_to_dict(restored.mesh)["hybrid_diagnostics"]["parallel"] == (
        mesh_to_dict(rebuilt)["hybrid_diagnostics"]["parallel"])
