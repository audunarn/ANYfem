"""Real public prepared envelopes alongside independently selected histories."""

from dataclasses import replace

import numpy as np
import pytest

from anyfem import Project, StaticLoadRequest, fixed, iter_linear_static_batches, steel
from anyfem.model import BeamSection
from anyfem.solve import prepare_linear_static_evaluator
from anysolver import CancellationToken, PreparedStaticError, RecoveryConfig, SolveCancelled


TIMES = (0.0, 0.13, 0.31, 0.56, 0.92)


def _beams(free_free=False, disconnected=False):
    project = Project(name="whole model envelope with selected history")
    project.add_material(steel("S355", 0.01))
    project.add_beam_section(BeamSection(name="beam", profile="Flatbar",
        material="S355", flange_width=0.2, flange_thickness=0.02,
        web_direction=(0, 0, 1)))
    series = project.add_load_series("samples", list(TIMES))
    for offset in range(2 if disconnected else 1):
        points = project.geometry.add_points([(0, 2*offset, 0), (1, 2*offset, 0)])
        edge = project.geometry.add_line(*points)
        project.assign_beam(edge, "beam")
        if not free_free:
            project.add_support(fixed(project.point(points[0])))
        for point, sign in ((points[0], -1), (points[1], 1)):
            series.add_point_load(project.point(point),
                force=(sign*100*(offset+1), 0, 0),
                envelope={"expression": "1 - 4*t + t*t"})
            series.add_point_load(project.point(point),
                moment=(sign*2*(offset+1), 0, 0),
                envelope={"expression": "2 - 3*t + t*t*t"})
    mesh = project.generate_mesh(0.5)
    requests = tuple(StaticLoadRequest("repeated-id", load_case=step.name, time=t)
        for step, t in zip(project.series_children("samples"), TIMES))
    return project, mesh, requests


def _configs(mesh):
    all_recovery = RecoveryConfig(components=["axial_stress", "torsional_stress"],
        include_displacements=True, include_stresses=True, include_reactions=False)
    selected = replace(all_recovery, element_ids=[next(iter(mesh.beams))],
                       include_displacements=False)
    return selected, all_recovery


@pytest.mark.parametrize("free_free,disconnected", [(False, False), (True, False),
                                                       (False, True), (True, True)])
def test_whole_model_envelope_matches_live_nonproportional_history(free_free, disconnected):
    project, mesh, requests = _beams(free_free, disconnected)
    selected, envelope = _configs(mesh)
    live = list(iter_linear_static_batches(project, mesh=mesh, load_cases=requests,
        recovery_config=envelope, batch_size=2))
    with prepare_linear_static_evaluator(project, mesh=mesh,
            output_nodes=[next(iter(mesh.nodes))], recovery_config=selected,
            envelope_recovery_config=envelope) as owner:
        blocks = list(owner.iter_batches(requests, batch_size=2))
        result = owner.envelope_result()
    assert [len(block.case_ids) for block in blocks] == [2, 2, 1]
    assert result.complete and result.cases_processed == 5
    assert result.snapshot_identity and result.provenance["owned_snapshot"]
    assert all(set(case["element_stresses"]) == set(selected.element_ids)
               for block in blocks for case in block.case_results)
    _assert_stress_envelope(result, live, set(mesh.beams))
    cases = [case for block in live for case in block.case_results]
    motions = [entry for entry in result.extrema if entry.quantity == "displacements"]
    assert {entry.entity_id for entry in motions} == set(mesh.nodes)
    assert any(np.any(entry.absolute_maximum.values) for entry in motions)
    for entry in motions:
        history = np.asarray([case["node_displacements"][entry.entity_id] for case in cases])
        for governing, indices in ((entry.minimum, np.argmin(history, axis=0)),
                                   (entry.maximum, np.argmax(history, axis=0)),
                                   (entry.absolute_maximum, np.argmax(np.abs(history), axis=0))):
            expected = np.take_along_axis(history, indices[None, :], axis=0)[0]
            np.testing.assert_allclose(governing.values, expected, rtol=1e-9, atol=1e-9)
            np.testing.assert_allclose(governing.associated_fields["displacements"],
                                       history[indices], rtol=1e-9, atol=1e-9)


def _assert_stress_envelope(result, live, expected_elements):
    """Compare signed extrema, labels, times and coherent associated values."""
    cases = [case for block in live for case in block.case_results]
    times = np.asarray([time for block in live for time in block.times])
    case_ids = np.asarray([case_id for block in live for case_id in block.case_ids])
    entries = [entry for entry in result.extrema if entry.quantity == "stress"]
    assert entries and {entry.entity_id for entry in entries} == expected_elements
    for entry in entries:
        history = np.asarray([case["element_stresses"][entry.entity_id][entry.component]
                              for case in cases])
        assert np.any(history)
        for name, indices in (("minimum", np.argmin(history, axis=0)),
                              ("maximum", np.argmax(history, axis=0)),
                              ("absolute_maximum", np.argmax(np.abs(history), axis=0))):
            governing = getattr(entry, name)
            expected = np.take_along_axis(history, np.asarray(indices)[None, ...], axis=0)[0]
            np.testing.assert_allclose(governing.values, expected, rtol=1e-9, atol=1e-9)
            np.testing.assert_array_equal(governing.input_indices, indices)
            expected_times = times[indices]
            np.testing.assert_array_equal(governing.times, expected_times)
            np.testing.assert_array_equal(governing.case_ids, case_ids[indices])
            assert governing.associated_fields
            for field, values in governing.associated_fields.items():
                associated = np.asarray([case["element_stresses"][entry.entity_id][field]
                                         for case in cases])
                np.testing.assert_allclose(values, associated[indices], rtol=1e-9, atol=1e-9)


def test_whole_shell_envelope_with_mixed_public_loads_and_selected_history():
    from test_prepared_static_integration import _fixture_project
    project, series, points, face = _fixture_project()
    series.add_connection_load(project.connection_for("end").id, force=(0, 7, 0),
                               envelope={"expression": "2-t*t"})
    mesh = project.generate_mesh(1.0)
    requests = tuple(StaticLoadRequest("same", load_case=step.name, time=t)
        for step, t in zip(project.series_children("series"), (0.0, 0.5)))
    elements = set(mesh.quads) | set(mesh.tris)
    envelope = RecoveryConfig(components=["membrane_xx", "bending_xx"],
        include_displacements=False, include_stresses=True, include_reactions=True)
    selected = replace(envelope, element_ids=[min(elements)])
    live = list(iter_linear_static_batches(project, mesh=mesh, load_cases=requests,
        recovery_config=envelope, batch_size=1))
    with prepare_linear_static_evaluator(project, mesh=mesh,
            output_nodes=[mesh.node_of_vertex[points[1]]], recovery_config=selected,
            envelope_recovery_config=envelope) as owner:
        blocks = list(owner.iter_batches(requests, batch_size=1))
        summary = owner.envelope_result()
    assert summary.complete and summary.cases_processed == 2
    assert all(set(block.stresses()) == set(selected.element_ids) for block in blocks)
    assert any(entry.quantity == "support_reactions" for entry in summary.extrema)
    _assert_stress_envelope(summary, live, elements)
    cases = [case for block in live for case in block.case_results]
    reaction_entries = [entry for entry in summary.extrema
                        if entry.quantity == "support_reactions"]
    assert any(np.any(entry.absolute_maximum.values) for entry in reaction_entries)
    for entry in reaction_entries:
        history = np.asarray([case["constraint_forces"][entry.quantity].get(
            entry.entity_id, np.zeros(6)) for case in cases])
        for governing, indices in ((entry.minimum, np.argmin(history, axis=0)),
                                   (entry.maximum, np.argmax(history, axis=0)),
                                   (entry.absolute_maximum, np.argmax(np.abs(history), axis=0))):
            expected = np.take_along_axis(history, indices[None, :], axis=0)[0]
            np.testing.assert_allclose(governing.values, expected, rtol=1e-9, atol=1e-9)
            np.testing.assert_allclose(governing.associated_fields["force"],
                                       history[indices], rtol=1e-9, atol=1e-9)


def test_envelope_lifecycle_empty_early_close_reset_and_source_mutation():
    project, mesh, requests = _beams()
    selected, envelope = _configs(mesh)
    with prepare_linear_static_evaluator(project, mesh=mesh, recovery_config=selected,
            envelope_recovery_config=envelope) as owner:
        assert not owner.envelope_result().complete
        stream = owner.iter_batches(requests, batch_size=2)
        next(stream)
        competing = owner.iter_batches(requests, batch_size=2)
        with pytest.raises(RuntimeError, match="one.*iterator"):
            next(competing)
        competing.close()
        stream.close()
        partial = owner.envelope_result()
        assert not partial.complete and partial.cases_processed == 2
        project.load_cases.clear()
        mesh.nodes[next(iter(mesh.nodes))][0] += 99
        list(owner.iter_batches(requests, batch_size=2))
        full = owner.envelope_result()
        assert full.complete and full.cases_processed == 5
        assert partial.cases_processed == 2 and not partial.complete
        full.provenance["source_project"] = "caller mutation"
        assert owner.envelope_result().provenance["source_project"] == project.name
        governing = full.extrema[0].maximum
        preserved = governing.values.copy()
        governing.values[...] = 999
        governing.case_ids[...] = "changed"
        governing.input_indices[...] = 999
        governing.times[...] = -999
        for values in governing.associated_fields.values():
            values[...] = 999
        fresh = owner.envelope_result().extrema[0].maximum
        np.testing.assert_array_equal(fresh.values, preserved)
        assert np.all(fresh.case_ids == "repeated-id")
        assert np.all(fresh.input_indices < 5)
        assert np.all(fresh.times >= 0)
        assert all(not np.all(values == 999) for values in fresh.associated_fields.values())
        list(owner.iter_batches([], batch_size=2))
        empty = owner.envelope_result()
        assert empty.complete and empty.cases_processed == 0
        for entry in empty.extrema:
            assert np.all(entry.minimum.input_indices == -1)
            assert np.all(np.isnan(entry.minimum.values))


@pytest.mark.parametrize("failure", ["cancel", "callback", "close"])
def test_envelope_does_not_publish_uncertified_block(failure):
    project, mesh, requests = _beams()
    selected, envelope = _configs(mesh)
    token = CancellationToken()
    with prepare_linear_static_evaluator(project, mesh=mesh, recovery_config=selected,
            envelope_recovery_config=envelope) as owner:
        stream = owner.iter_batches(requests, batch_size=2, cancellation_token=token)
        next(stream)
        if failure == "cancel":
            token.cancel("test cancellation")
            with pytest.raises(SolveCancelled):
                next(stream)
        elif failure == "close":
            owner.close()
        else:
            stream.close()
            def failing_progress(event):
                raise RuntimeError("progress failed")
            with pytest.raises(RuntimeError, match="progress failed"):
                list(owner.iter_batches(requests, batch_size=2,
                                        progress_callback=failing_progress))
        stream.close()
        summary = owner.envelope_result()
        assert not summary.complete
        assert summary.cases_processed == (0 if failure == "callback" else 2)


def test_ingress_error_keeps_only_previously_certified_blocks():
    project, mesh, requests = _beams()
    selected, envelope = _configs(mesh)
    finalized = []
    def failing_requests():
        try:
            yield from requests[:3]
            raise RuntimeError("input provider failed")
        finally:
            finalized.append(True)
    with prepare_linear_static_evaluator(project, mesh=mesh, recovery_config=selected,
            envelope_recovery_config=envelope) as owner:
        stream = owner.iter_batches(failing_requests(), batch_size=2)
        next(stream)
        with pytest.raises(PreparedStaticError, match="static case provider failed") as caught:
            next(stream)
        assert isinstance(caught.value.__cause__, RuntimeError)
        assert str(caught.value.__cause__) == "input provider failed"
        summary = owner.envelope_result()
        assert summary.cases_processed == 2 and not summary.complete
        assert finalized == [True]
