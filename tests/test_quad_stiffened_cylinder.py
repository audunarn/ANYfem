"""Public generated-cylinder station, section, offset and publication contracts."""
from collections import Counter

import numpy as np
import pytest
from anyfem import commands
from anyfem.application.controller import default_project
from anyfem.io import project_to_dict, project_from_dict
from anymesher.quad.public_integration import QuadPublicUnsupported


def cylinder(*, rings=None, reverse=False, height=5., segments=12, longitudinal=.5):
    project = default_project()
    commands.AddCylinder(1., height, circumferential_segments=segments,
        longitudinal_spacing=longitudinal, ring_spacing=rings,
        beam_section='stiffener', plate_section='plate').do(project)
    if reverse:
        for member in tuple(project.geometry.members):
            project.geometry.reverse_member(member)
    return project


def assert_conforming(project, mesh):
    shell_nodes = {n for e in mesh.shells for n in mesh.corners_of(e)}
    owners = Counter(e for edge in project.beam_edges for e in mesh.elements_of_edge[edge])
    assert set(owners) == set(mesh.beams)
    assert set(owners.values()) == {1}
    couplings = {c.beam_node: c for c in mesh.couplings.values()}
    assert len(couplings) == len(mesh.couplings)
    for edge in project.beam_edges:
        assert edge in project.edge_sections
        axis = mesh.nodes_of_edge[edge]
        chain = mesh.offset_nodes_of_edge.get(edge, axis)
        assert set(axis) <= shell_nodes
        assert len(axis) == len(chain)
        assert [mesh.beams[e] for e in mesh.elements_of_edge[edge]] == list(zip(chain, chain[1:]))
        for plate, beam in zip(axis, chain):
            if beam == plate:
                continue
            coupling = couplings[beam]
            point = sum((w * mesh.nodes[n] for n, w in zip(coupling.plate_nodes, coupling.weights)), np.zeros(3))
            np.testing.assert_allclose(point, mesh.nodes[plate], atol=1e-10, rtol=0)
            np.testing.assert_allclose(mesh.nodes[beam] - point, coupling.eccentricity, atol=1e-10, rtol=0)
            np.testing.assert_allclose(coupling.eccentricity, project.beam_offsets[edge], atol=1e-10, rtol=0)


@pytest.mark.parametrize('rings,reverse', [(None, False), (.5, False), (.5, True)])
def test_generated_cylinder_default_automatic_offsets(rings, reverse):
    options = {'height': 1., 'segments': 4, 'longitudinal': 1.} if rings is not None else {}
    project = cylinder(rings=rings, reverse=reverse, **options)
    assert project.beam_sections['stiffener'].offset_mode == 'automatic'
    before = repr(project.geometry.topology_snapshot()), project.geometry.revision
    mesh = project.generate_mesh(.25, order='linear', strategy='quad_first')
    assert mesh.quads and mesh.beams
    assert_conforming(project, mesh)
    assert (repr(project.geometry.topology_snapshot()), project.geometry.revision) == before
    assert project.mesh_records == {}
    restored = project_from_dict(project_to_dict(project))
    assert set(restored.beam_edges) == set(project.beam_edges)


def test_quadratic_cylinder_refuses_without_publication():
    project = cylinder()
    before = repr(project.geometry.topology_snapshot()), project.geometry.revision
    with pytest.raises(QuadPublicUnsupported, match='source-boundary'):
        project.generate_mesh(.25, order='quadratic', strategy='quad_first')
    assert (repr(project.geometry.topology_snapshot()), project.geometry.revision) == before
    assert project.mesh_records == {}


def test_cancelled_cylinder_does_not_publish_or_mutate():
    project = cylinder()
    before = repr(project.geometry.topology_snapshot()), project.geometry.revision
    marker = RuntimeError('cancel cylinder before merge')
    def cancel(stage):
        if stage == 'quad-first:beam-generation':
            raise marker
    with pytest.raises(RuntimeError) as caught:
        project.generate_mesh(.25, order='linear', strategy='quad_first', cancellation_check=cancel)
    assert caught.value is marker
    assert (repr(project.geometry.topology_snapshot()), project.geometry.revision) == before
    assert project.mesh_records == {}


def test_persisted_linear_settings_reopen_and_run_recoverable_job():
    from anyfem.native_meshing import NativeMeshSettings
    from anyfem.mesh_jobs import MeshSettings, run_mesh_job
    from anyfem.jobs import _cancellation_token
    from anymesher import MeshAutomationOptions
    project = cylinder()
    project.set_native_mesh_settings(NativeMeshSettings.create(.25,
        element_order='linear', backend='quad_first', parameters={'layout_policy': 'adaptive'}))
    reopened = project_from_dict(project_to_dict(project))
    assert reopened.native_mesh_settings.element_order == 'linear'
    before = repr(reopened.geometry.topology_snapshot()), reopened.geometry.revision
    settings = MeshSettings.create(.25, element_order='linear', strategy='quad_first',
        layout_policy='adaptive', automation=MeshAutomationOptions(strict_method=False, allow_inspection=True))
    result = run_mesh_job(reopened, 'cylinder-regression', settings, lambda *args: None, _cancellation_token())
    assert_conforming(reopened, result.mesh)
    assert (repr(reopened.geometry.topology_snapshot()), reopened.geometry.revision) == before




@pytest.mark.parametrize('family', ['custom_parameterization', 'cone'])
def test_other_source_boundary_families_remain_refused(family):
    if family == 'cone':
        project = default_project()
        commands.AddCone(1., .5, 5., longitudinal_spacing=.5,
            beam_section='stiffener', plate_section='plate').do(project)
    else:
        project = cylinder()
        face = next(iter(project.geometry.faces.values()))
        project.geometry.set_face_parameterization(face.id, face.surface)
    before = repr(project.geometry.topology_snapshot()), project.geometry.revision
    with pytest.raises(QuadPublicUnsupported, match='source-boundary'):
        project.generate_mesh(.25, order='linear', strategy='quad_first')
    assert (repr(project.geometry.topology_snapshot()), project.geometry.revision) == before
    assert project.mesh_records == {}


def test_large_generated_ring_atlas_retains_typed_resource_refusal_without_publication():
    from anygeometry import CylinderAtlasError
    project = cylinder(rings=1.)
    assert len(project.geometry.faces) == 65
    before = repr(project.geometry.topology_snapshot()), project.geometry.revision
    with pytest.raises(CylinderAtlasError, match='qualification_budget_exhausted:interval_operations'):
        project.generate_mesh(.25, order='linear', strategy='quad_first')
    assert (repr(project.geometry.topology_snapshot()), project.geometry.revision) == before
    assert project.mesh_records == {}
