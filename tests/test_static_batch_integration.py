"""Production solver integration for selected arbitrary static histories."""

from copy import copy

import numpy as np
import pytest

from anysolver import RecoveryConfig
from anysolver.assembly import compute_reactions
from anyfem import StaticLoadRequest, iter_linear_static_batches, solve_linear_static
from anyfem.solve.build import build_fe_model
from test_static_batch_envelope_factors import _project


def test_selected_nonproportional_public_stream_matches_independent_cases():
    project, series, points, face = _project()
    series.add_point_load(project.point(points[0]), force=(2, 0, 0), envelope={"expression": "t*t"})
    series.add_pressure(project.face(face), 11, envelope={"start": 2, "expression": "1+t"})
    series.add_wet_pressure(expression="17*(x+t)")
    series.add_connection_load(project.connection_for("end").id, force=(0, 7, 0),
                               envelope={"expression": "1+t*t*t"})
    series.add_line_load(project.edge(next(iter(project.geometry.edges))), (0, 3, 0))
    series.add_surface_traction(project.face(face), (1, 0, 2))
    series.set_acceleration(0.0, 0.0, -0.4)
    mesh = project.generate_mesh(1.0)
    node = mesh.node_of_vertex[points[1]]
    element = next(iter(mesh.quads or mesh.tris))
    step = project.series_children("series")[1]
    times = (0.0, 0.3, 0.8)
    expected = []
    for time in times:
        case = copy(project.resolve_case(step))
        case.time = time
        expected.append(solve_linear_static(project, mesh=mesh, load_case=case))
    blocks = list(iter_linear_static_batches(
        project, mesh=mesh, batch_size=2,
        load_cases=(StaticLoadRequest("history", load_case=step.name, time=time) for time in times),
        output_nodes=[node], output_connections=["end"],
        recovery_config=RecoveryConfig(element_ids=[element], components=["von_mises"],
                                       include_displacements=False, include_reactions=True),
    ))
    from anysolver.static_envelopes import EnvelopeReducer
    envelopes = EnvelopeReducer()
    for block in blocks:
        envelopes.update_batch(block)
    envelopes.finish()
    assert envelopes.complete and envelopes.case_count == len(times)
    assert [len(block.case_ids) for block in blocks] == [2, 1]
    assert tuple(index for block in blocks for index in block.input_indices) == (0, 1, 2)
    assert tuple(time for block in blocks for time in block.times) == times
    index = 0
    for block in blocks:
        assert not block.built.fe_model.load_cases
        assert block.displacements.shape[0] == 12
        for column, result in enumerate(block.case_results):
            reference = expected[index]
            np.testing.assert_allclose(block.node_displacement(node, column), reference.node_displacement(node),
                                       rtol=1.0e-10, atol=1.0e-13)
            motion = block.connection_motion("end", column)
            reference_motion = reference.connection_motion("end")
            for component in ("displacement", "rotation"):
                np.testing.assert_allclose(motion[component], reference_motion[component],
                                           rtol=1.0e-10, atol=1.0e-13)
            assert result["status"] == "converged"
            assert tuple(result["element_stresses"]) == (element,)
            reference_stresses = reference.stresses().element_stresses[element]["von_mises"]
            np.testing.assert_allclose(block.stresses(column)[element]["von_mises"], reference_stresses,
                                       rtol=1.0e-10, atol=1.0e-9)
            actual_reactions = block.reactions(column)
            reference_reactions = compute_reactions(reference.built.fe_model, reference.displacements,
                                                    reference.built.load_case)
            assert actual_reactions.keys() == reference_reactions.keys()
            for support, force in reference_reactions.items():
                np.testing.assert_allclose(actual_reactions[support], force, rtol=1.0e-10, atol=1.0e-10)
            index += 1
    first_dof = int(blocks[0].output_dofs[0])
    expected_values = [reference.displacements[first_dof] for reference in expected]
    extreme = envelopes.extrema[("displacement", first_dof)]
    assert extreme.minimum.value == pytest.approx(min(expected_values), rel=1.0e-10, abs=1.0e-13)
    assert extreme.maximum.value == pytest.approx(max(expected_values), rel=1.0e-10, abs=1.0e-13)
    assert extreme.absolute_maximum.time in times


def test_free_free_public_stream_preserves_rejection_and_balanced_response():
    from anysolver import StaticBatchError
    from anyfem import Project, steel
    from anyfem.model import BeamSection

    project = Project("free beam")
    project.add_material(steel("S355", 0.01))
    project.add_beam_section(BeamSection(name="beam", profile="Flatbar", material="S355",
                                         flange_width=0.2, flange_thickness=0.02,
                                         web_direction=(0, 0, 1)))
    points = project.geometry.add_points([(0, 0, 0), (1, 0, 0)])
    edge = project.geometry.add_line(*points)
    project.assign_beam(edge, "beam")
    balanced = project.load_case("balanced")
    balanced.add_point_load(project.point(points[0]), force=(10, 0, 0))
    balanced.add_point_load(project.point(points[1]), force=(-10, 0, 0))
    bad = project.load_case("bad")
    bad.add_point_load(project.point(points[0]), force=(10, 0, 0))
    mesh = project.generate_mesh(1.0)
    nodes = sorted(mesh.nodes)
    blocks = list(iter_linear_static_batches(project, mesh=mesh, load_cases=["balanced"], output_nodes=nodes))
    assert blocks[0].case_results[0]["status"] == "converged"
    with pytest.raises(StaticBatchError, match="incompatible|free.free"):
        list(iter_linear_static_batches(project, mesh=mesh, load_cases=["bad"], output_nodes=nodes))


def test_cancelled_public_stream_does_not_consume_load_provider():
    from anysolver import CancellationToken, SolveCancelled
    project, series, points, _face = _project()
    series.add_point_load(project.point(points[0]), force=(1, 0, 0))
    token = CancellationToken()
    token.cancel("stop before build")
    consumed = []
    def requests():
        consumed.append(True)
        yield project.series_children("series")[0].name
    with pytest.raises(SolveCancelled):
        next(iter_linear_static_batches(project, target_size=1.0, load_cases=requests(),
                                         cancellation_token=token))
    assert not consumed


def test_explicit_rotation_row_is_independent_of_full_node_recovery():
    project, series, points, _face = _project()
    series.add_point_load(project.point(points[0]), force=(1, 0, 0), moment=(0, 2, 0))
    mesh = project.generate_mesh(1.0)
    node = mesh.node_of_vertex[points[0]]
    built = build_fe_model(project, mesh, load_case=None, require_loads=False)
    rotation = built.fe_model.mesh.nodes[node].dofs[3]
    blocks = list(iter_linear_static_batches(
        built=built, load_cases=[project.series_children("series")[0].name], output_dofs=[rotation],
        recovery_config=RecoveryConfig(node_ids=[node], include_displacements=True),
    ))
    assert tuple(blocks[0].output_dofs) == (rotation,)
    assert blocks[0].displacements.shape == (1, 1)
    recovered = blocks[0].case_results[0]["node_displacements"][node]
    assert recovered.shape == (6,)
    assert blocks[0].displacements[0, 0] == recovered[3]


def test_resource_limit_is_forwarded_after_only_bounded_first_record_peek():
    from anysolver import ResourceConfig, ResourcePolicyError
    project, series, points, _face = _project()
    series.add_point_load(project.point(points[0]), force=(1, 0, 0))
    consumed = []
    def requests():
        for index in range(3):
            consumed.append(index)
            yield project.series_children("series")[0].name
    with pytest.raises(ResourcePolicyError):
        list(iter_linear_static_batches(project, target_size=1.0, load_cases=requests(),
                                        resource_config=ResourceConfig(memory_limit_bytes=1)))
    assert consumed == [0]
