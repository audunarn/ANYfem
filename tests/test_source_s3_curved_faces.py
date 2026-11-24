"""Default ``s3_reference_surface="source"`` on curved faces (owner decision 2026-11-24).

ANYfeSolver's default triangle (S3 V2D) is a flat facet and refuses a reference
normal that is not the facet normal.  The mesher's source normals are true
surface normals, so before this change every triangle model on a curved face
was refused under the default policy.  Triangles whose source normal the
solver would refuse now get the source-oriented facet normal; every triangle
the solver accepts keeps its source normal, so flat faces are bit-identical.
"""

from dataclasses import replace

import numpy as np
import pytest

from anyfem import pinned, solve_linear_static
from anyfem.solve import build
from anyfem.solve.build import build_fe_model
from test_mesh_general_update import panel


def _loaded(family, rise):
    project, settings, face, edges = panel(family, rise=rise)
    for edge in edges:
        project.add_support(pinned(project.edge(edge)))
    project.load_case().add_pressure(project.face(face), 1.e4)
    mesh = project.mesh_session().generate_mesh(settings)
    return project, mesh


def _facet(mesh, nodes):
    a, b, c = (np.asarray(mesh.nodes[n], dtype=float) for n in nodes)
    facet = np.cross(b - a, c - a)
    return facet / np.linalg.norm(facet)


def _accepted_by_solver(corners, normal, tolerance=1.e-10):
    """Independent copy of the S3 V2D facet-normal acceptance test."""

    e12, e13, e23 = corners[1] - corners[0], corners[2] - corners[0], corners[2] - corners[1]
    scale = max(e12 @ e12, e13 @ e13, e23 @ e23, np.finfo(float).tiny)
    return max(abs(e12 @ normal), abs(e13 @ normal)) <= tolerance * np.sqrt(scale)


def test_curved_nurbs_triangle_panel_solves_under_the_default_policy():
    project, mesh = _loaded('nurbs', .125)
    assert project.shell_formulation_policy.s3_reference_surface == 'source'
    assert mesh.tris
    built = build_fe_model(project, mesh)
    # Refused before: "S3 V2D reference normal is not facet-normal".
    solved = solve_linear_static(built=built)
    assert any(abs(solved.node_displacement(n)[2]) > 0 for n in mesh.nodes)
    sources = mesh.structural_preparation['qualified_s3']['element_owner_normals']
    switched = 0
    for eid, nodes in mesh.tris.items():
        source = np.asarray(sources[str(eid)], dtype=float)
        director = built.fe_model.mesh.elements[eid].physical_reference_director
        corners = [np.asarray(mesh.nodes[n], dtype=float) for n in nodes]
        if _accepted_by_solver(corners, source):
            np.testing.assert_array_equal(director, source)
        else:
            switched += 1
            facet = _facet(mesh, nodes)
            assert np.dot(facet, source) > 0
            np.testing.assert_allclose(director, facet, atol=1.e-13)
    assert switched > 0


@pytest.mark.parametrize('family', ['nurbs'])
def test_flat_triangle_panels_are_bit_identical(monkeypatch, family):
    project, mesh = _loaded(family, 0.)
    if not mesh.tris:
        pytest.skip('mesher produced no triangles for this flat panel')
    new = solve_linear_static(built=build_fe_model(project, mesh))
    # The previous rule: always pass the source normal under the "source" policy.
    monkeypatch.setattr(build, '_source_normal_is_facet_normal', lambda corners, reference: True)
    old = solve_linear_static(built=build_fe_model(project, mesh))
    for n in mesh.nodes:
        assert new.node_displacement(n).tobytes() == old.node_displacement(n).tobytes()


def test_faceted_policy_is_unchanged():
    project, mesh = _loaded('nurbs', .125)
    project.shell_formulation_policy = replace(project.shell_formulation_policy, s3_reference_surface='faceted')
    built = build_fe_model(project, mesh)
    for eid, nodes in mesh.tris.items():
        np.testing.assert_allclose(built.fe_model.mesh.elements[eid].physical_reference_director,
                                   _facet(mesh, nodes), atol=1.e-13)


def test_tolerance_matches_the_solver():
    from anyfesolver import e4_pl_s3_v2d_element as v2d

    assert build._S3_V2D_FACET_NORMAL_TOLERANCE == v2d._NORMAL_TOLERANCE


def _solver_accepts(corners, normal):
    from anyfesolver import FEModel
    from anyfesolver.e4_pl_s3_v2d_element import NativeParityCapabilityError
    from anyfesolver.elements import create_shell_element

    model = FEModel("boundary")
    model.add_material("steel", 210.e9, .3)
    for k, point in enumerate(corners, start=1):
        model.add_node(k, *map(float, point))
    element = create_shell_element(1, [1, 2, 3], "steel", thickness=.01, reference_normal=tuple(normal))
    model.add_element(1, element)
    try:
        element._geometry(model.mesh)
    except NativeParityCapabilityError as error:
        assert "facet-normal" in str(error)
        return False
    return True


def test_acceptance_mirror_agrees_with_the_solver_at_the_tolerance_boundary():
    """Codex review P2: the solver tests the normalised vector; so must ANYfem."""

    corners = [np.array([0., 0., 0.]), np.array([1., 0., 0.]), np.array([0., 1., 0.])]
    threshold = build._S3_V2D_FACET_NORMAL_TOLERANCE * np.sqrt(2.)
    disagreements_without_normalising = 0
    for t_scale in np.linspace(1. - 2.e-12, 1. + 2.e-12, 21):
        for length in np.linspace(1. - 9.e-13, 1. + 9.e-13, 7):
            unit = np.array([threshold * t_scale, 0., 1.])
            unit /= np.linalg.norm(unit)
            normal = unit * length
            solver = _solver_accepts(corners, normal)
            assert build._source_normal_is_facet_normal(corners, normal) == solver, (t_scale, length)
            raw = max(abs((corners[1] - corners[0]) @ normal), abs((corners[2] - corners[0]) @ normal))
            disagreements_without_normalising += (raw <= threshold) != solver
    assert disagreements_without_normalising > 0
