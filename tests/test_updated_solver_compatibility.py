"""Application integration with the updated owner laws (not qualification)."""
from copy import deepcopy

import numpy as np
import pytest

from anyfem import Project, fixed, solve_linear_static, solve_nonlinear_static, steel, support
from anyfem.io.project_file import project_to_dict
from anyfem.native_meshing import NativeMeshSettings
from anyfem.post.fields import evaluate_field
from anyfem.solve.build import build_fe_model
from anysolver import CancellationToken, SolveCancelled, QUALIFIED_Q4_FORMULATION_ID, S3_V2D_FORMULATION_ID


def _panel(*, triangles=False, plastic=False, force=None):
    project = Project("updated owner law")
    project.add_material(steel("S355", .01, nonlinear=plastic))
    project.add_plate_section("plate", thickness=.01, material="S355")
    geometry = project.geometry
    points = geometry.add_points([(0, 0, 0), (1, 0, 0), (1, .2, 0), (0, .2, 0)])
    edges = geometry.add_polyline(points, close=True)
    face = geometry.add_face(edges)
    project.assign_plate(face, "plate")
    project.add_support(support(project.edge(edges[3]), ux=0))
    project.add_support(support(project.point(points[0]), uy=0))
    project.add_support(support(project.face(face), uz=0, rx=0, ry=0, rz=0))
    # A uniform tensile traction, above yield only in the plastic case.
    if force is None:
        force = 800000. if plastic else 4000.
    project.load_case().add_line_load(project.edge(edges[1]), (force/.2, 0, 0))
    if triangles:
        project.set_native_triangulation_backend("python")
        project.set_native_mesh_settings(NativeMeshSettings.create(
            .2, backend="native", element_order="linear",
            parameters={"recombine": False}))
        mesh = project.generate_mesh()
    else:
        mesh = project.generate_mesh(.2, strategy="mapped")
    return project, mesh, face


@pytest.mark.parametrize("plastic", [False, True])
def test_updated_q4_public_solve_committed_recovery_and_project_nonmutation(plastic):
    project, mesh, _ = _panel(plastic=plastic)
    before = deepcopy(project_to_dict(project))
    built = build_fe_model(project, mesh)
    assert {e.formulation_id for e in built.fe_model.mesh.elements.values()} == {QUALIFIED_Q4_FORMULATION_ID}
    solution = solve_nonlinear_static(built=built, num_steps=4, num_layers=3,
                                     record_increment_snapshots=True)
    assert solution.status == "completed"
    assert solution.load_factor == pytest.approx(1.)
    assert solution.shapes
    recovered = solution.stresses()
    assert recovered is solution.stresses()
    assert recovered.provenance.mode == "material_history"
    assert all(np.isfinite(v).all() for fields in recovered.element_stresses.values()
               for v in fields.values() if isinstance(v, np.ndarray))
    plastic_field = evaluate_field(solution, "equivalent_plastic_strain")
    maximum_plastic_strain = max(plastic_field.element_values.values())
    if plastic:
        assert maximum_plastic_strain > 0
    else:
        assert maximum_plastic_strain == 0
    for shape in solution.shapes:
        assert shape.stresses().provenance.mode == "material_history"
    assert project_to_dict(project) == before


def test_updated_s3_von_karman_public_build_solve_and_recovery():
    project, mesh, _ = _panel(triangles=True)
    assert mesh.tris and all(len(nodes) == 3 for nodes in mesh.shells.values())
    built = build_fe_model(project, mesh)
    assert {e.formulation_id for e in built.fe_model.mesh.elements.values()} == {S3_V2D_FORMULATION_ID}
    solution = solve_nonlinear_static(built=built, num_steps=2, kinematics="von_karman",
                                     num_layers=3, record_increment_snapshots=True)
    assert solution.status == "completed"
    assert solution.load_factor == pytest.approx(1.)
    assert solution.info["kinematics"] == "von_karman"
    recovered = solution.stresses()
    assert recovered.provenance.analysis_context["kinematics"] == "von_karman"
    assert set(recovered.element_stresses) == set(mesh.tris)
    for shape in solution.shapes:
        assert shape.stresses().provenance.analysis_context["kinematics"] == "von_karman"


def test_cancelled_updated_q4_solve_preserves_project_and_can_be_solved_again():
    project, mesh, _ = _panel()
    before = deepcopy(project_to_dict(project))
    built = build_fe_model(project, mesh)
    token = CancellationToken()
    token.cancel("integration cancellation")
    with pytest.raises(SolveCancelled, match="integration cancellation"):
        solve_nonlinear_static(built=built, cancellation_token=token)
    assert project_to_dict(project) == before
    assert solve_nonlinear_static(built=built, num_steps=1).status == "completed"


def _bending_panel(*, triangles=False, plastic=False, pressure=5000.):
    project = Project("transverse integration")
    project.add_material(steel("S355", .003, nonlinear=plastic))
    project.add_plate_section("plate", thickness=.003, material="S355")
    geometry = project.geometry
    points = geometry.add_points([(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)])
    edges = geometry.add_polyline(points, close=True)
    face = geometry.add_face(edges)
    project.assign_plate(face, "plate")
    for edge in edges:
        project.add_support(fixed(project.edge(edge)))
    project.load_case().add_pressure(project.face(face), pressure)
    if triangles:
        project.set_native_triangulation_backend("python")
        project.set_native_mesh_settings(NativeMeshSettings.create(
            .25, backend="native", element_order="linear", parameters={"recombine": False}))
        mesh = project.generate_mesh()
    else:
        mesh = project.generate_mesh(.25, strategy="mapped")
    return build_fe_model(project, mesh)


@pytest.mark.parametrize("triangles", [False, True], ids=["q4", "s3"])
def test_von_karman_transverse_loading_retains_membrane_stiffening(triangles):
    built = _bending_panel(triangles=triangles)
    linear = solve_linear_static(built=built)
    nonlinear = solve_nonlinear_static(built=built, num_steps=4, kinematics="von_karman",
                                      num_layers=3, record_increment_snapshots=True)
    assert nonlinear.status == "completed"
    # This is an integration invariant, not an accuracy/convergence claim.
    assert 0 < nonlinear.max_translation()[1] < linear.max_translation()[1]
    if triangles:
        assert any(np.linalg.norm(state["station_generalized_strain"][:, :3]) > 0
                   for state in nonlinear.element_states.values())
    assert nonlinear.stresses().provenance.analysis_context["kinematics"] == "von_karman"
    assert nonlinear.shapes[-1].stresses().provenance.analysis_context["kinematics"] == "von_karman"


def test_updated_s3_build_refuses_missing_mesher_authority_without_mutation():
    from anyfem.model.project import ProjectError

    project, mesh, _ = _panel(triangles=True)
    mesh.structural_preparation = {}
    before = deepcopy(project_to_dict(project))
    with pytest.raises(ProjectError, match="qualified S3 mesh authority"):
        build_fe_model(project, mesh)
    assert project_to_dict(project) == before
    assert mesh.structural_preparation == {}


def test_q4_zero_plastic_hardening_material_uses_the_same_elastic_law():
    solutions = []
    for plastic_capable in (False, True):
        built = _bending_panel(plastic=plastic_capable, pressure=500.)
        solution = solve_nonlinear_static(built=built, num_steps=2, num_layers=3)
        assert solution.status == "completed"
        assert max(evaluate_field(solution, "equivalent_plastic_strain").element_values.values()) == 0
        solutions.append(solution)
    # The only material difference is the unused hardening curve. Both must
    # follow the owner's single elastic law before any point yields.
    np.testing.assert_allclose(solutions[0].displacements, solutions[1].displacements,
                               rtol=1e-10, atol=1e-14)


def test_updated_plastic_q4_warped_reference_refuses_without_fallback_or_project_mutation():
    from anysolver._q4_assumed_strain_plasticity import UnsupportedQ4PlasticRouteError

    project, mesh, _ = _panel(plastic=True)
    before = deepcopy(project_to_dict(project))
    built = build_fe_model(project, mesh)
    element = next(iter(built.fe_model.mesh.elements.values()))
    built.fe_model.mesh.nodes[element.node_ids[0]].z = .001
    coordinates = {key: node.coords() for key, node in built.fe_model.mesh.nodes.items()}
    with pytest.raises(UnsupportedQ4PlasticRouteError, match="warped facet.*formulation='legacy'"):
        solve_nonlinear_static(built=built, num_steps=1)
    assert project_to_dict(project) == before
    assert {e.formulation_id for e in built.fe_model.mesh.elements.values()} == {QUALIFIED_Q4_FORMULATION_ID}
    for key, node in built.fe_model.mesh.nodes.items():
        np.testing.assert_array_equal(node.coords(), coordinates[key])
