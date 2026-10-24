"""Independent load conservation for the reported connected-panel assembly."""
import numpy as np
import pytest

from anygeometry import EntityRef
from anygeometry.serialization import to_dict as geometry_to_dict
from anyfem import commands
from anyfem.application.controller import default_project
from anyfem.solve.build import build_fe_model, _line_load_to_nodes
from anyfem.model.attributes import fixed
from anymesher.serialize import mesh_to_dict, mesh_from_dict


def connected_panel_problem():
    project = default_project()
    for origin, u in (((0, 0, 0), (1, 0, 0)), ((0, 0, 0), (0, 0, 1)),
                      ((0, 0, 5), (1, 0, 0))):
        commands.AddStiffenedPanel(5, 10, .5, origin=origin, u_direction=u,
                                  v_direction=(0, 1, 0), beam_section="stiffener",
                                  plate_section="plate").do(project)
    loaded = tuple(range(126, 184, 3))
    for edge in loaded:
        commands.AddLineLoad(EntityRef("edge", edge), (0, 0, -100000.)).do(project)
    # The report contains 38 restraints; these are the bottom free edge and
    # the interior 18 segments of the top free edge. No solve is run here.
    for edge in (*range(2, 60, 3), *range(128, 180, 3)):
        commands.AddSupport(fixed(EntityRef("edge", edge))).do(project)
    return project, loaded


def assert_edge_load_conservation(project, mesh, loaded):
    for edge in loaded:
        sequence = mesh.nodes_of_edge.get(edge)
        assert sequence, f"Missing published source-edge association: {edge}"
        points = np.array([mesh.nodes[node] for node in sequence])
        expected_ends = project.geometry.sample_edge(edge, [0, 1])
        np.testing.assert_allclose(points[[0, -1]], expected_ends, atol=1e-10)
        assert np.linalg.norm(np.diff(points, axis=0), axis=1).sum() == pytest.approx(.5)
        contributions = _line_load_to_nodes(mesh, EntityRef("edge", edge), np.array([0., 0., -100000.]))
        np.testing.assert_allclose(np.sum([force for _, force in contributions], axis=0), (0, 0, -50000.), atol=1e-8)
    built = build_fe_model(project, mesh)
    force = sum((values[:3] for values in built.load_case.nodal_loads.values()), np.zeros(3))
    moment = sum((np.cross(mesh.nodes[node], values[:3]) + values[3:]
                  for node, values in built.load_case.nodal_loads.items()), np.zeros(3))
    np.testing.assert_allclose(force, (0, 0, -1_000_000.), atol=1e-7)
    np.testing.assert_allclose(moment, (-5_000_000., 0, 0), atol=1e-6)


def test_reported_roof_wall_edge_loads_survive_meshing_and_worker_serialization():
    project, loaded = connected_panel_problem()
    before = geometry_to_dict(project.geometry)
    load_ids = tuple(load.id for load in project.load_case().line_loads)
    mesh = project.generate_mesh(.25, strategy="quad_first")
    assert_edge_load_conservation(project, mesh, loaded)
    assert_edge_load_conservation(project, mesh_from_dict(mesh_to_dict(mesh)), loaded)
    assert geometry_to_dict(project.geometry) == before
    assert tuple(load.id for load in project.load_case().line_loads) == load_ids
