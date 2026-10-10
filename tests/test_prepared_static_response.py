"""Public app response source integration, with original-time canonical truth."""
from copy import copy
import math
import numpy as np
import pytest
import anysolver
from anysolver import RecoveryConfig
from anyfem.solve import (PatternLoadRequest, PeriodicLoadSource, PeriodicSample,
    StaticLoadRequest, prepare_linear_static_evaluator,
    prepare_linear_static_response_evaluator, PreparedStaticUnavailableError)
from anyfem.solve.prepared_static_response import _periodic_expression, _phase
from test_static_batch_envelope_factors import _project


def fixture(expression="max(0, 2 + sin(omega*t - x) - z)", times=None, envelope=None):
    project, series, points, face = _project()
    series.times = tuple(times or (0., .5, 1., 1.5, 2., 2.5, 3., 3.5))
    series.parameters["omega"] = math.pi
    series.add_wet_pressure(expression=expression, envelope=envelope or {"expression": "t/2"})
    connection = project.connection_for("end").id
    series.add_connection_load(connection, moment=(0, 3, 0),
        envelope={"expression": "1+t*t"})
    mesh = project.generate_mesh(1.)
    return project, series, mesh


def sample(series, time):
    case = copy(series)
    case.times = None
    case.time = time
    case.step = series.times.index(time)
    case._window_times = series.times
    return case


def recovery(mesh):
    return RecoveryConfig(element_ids=list(mesh.quads or mesh.tris),
        components=("membrane_xx", "bending_xx"), include_reactions=True)


def assert_nested(actual, expected):
    if isinstance(expected, dict):
        assert actual.keys() == expected.keys()
        for key in expected:
            assert_nested(actual[key], expected[key])
    elif isinstance(expected, (str, type(None))):
        assert actual == expected
    else:
        np.testing.assert_allclose(actual, expected, rtol=1e-9, atol=1e-9)


def test_missing_response_api_fails_closed(monkeypatch):
    monkeypatch.delattr(anysolver, "prepare_static_response_evaluator", raising=False)
    with pytest.raises(PreparedStaticUnavailableError, match="prepare_static_response_evaluator"):
        prepare_linear_static_response_evaluator()


@pytest.mark.parametrize("expression", ["max(0,2+sin(w*t-x)-z)", "cos(2*w*t+y)*max(0,1-z)"])
def test_periodic_expression_admits_clipped_safe_harmonics(expression):
    _periodic_expression(expression, {"w": math.pi}, 2.)


@pytest.mark.parametrize("expression", ["t+sin(w*t)", "sin(t*t)", "sin(w*t*x)", "sin(.7*t)"])
def test_nonperiodic_or_unproved_source_is_not_admitted(expression):
    with pytest.raises(ValueError):
        _periodic_expression(expression, {"w": math.pi}, 2.)


def test_grid_never_snaps_nextafter():
    assert _phase(.3, 0., .1, 60) == 3
    assert _phase(np.nextafter(.3, 1.), 0., .1, 60) is None


def test_periodic_sparse_original_identity_full_library_and_nonempty_recovery():
    project, series, mesh = fixture(envelope={"by": "number", "start": 3, "stop": 7,
                                             "expression": "1+tau"})
    times = (3., .5, 2.)  # shuffled subset: original numbers 7,2,5
    config = recovery(mesh)
    requests = [StaticLoadRequest("same", load_case=sample(series, t), time=t) for t in times]
    with prepare_linear_static_evaluator(project, mesh=mesh, recovery_config=config,
            envelope_recovery_config=config) as direct:
        expected = list(direct.iter_batches(requests, batch_size=2))
        expected_envelope = direct.envelope_result()
    with prepare_linear_static_response_evaluator(project, mesh=mesh,
            periodic_source=PeriodicLoadSource("series", 2., .5, times),
            recovery_config=config, envelope_recovery_config=config) as owner:
        assert owner.diagnostics()["response"]["pattern_build_count"] == 5
        assert owner.diagnostics()["response"]["periodic"]["phase_times"] == (0., .5, 1., 1.5)
        series.wet_pressures.clear()  # caller source no longer drives the owner
        actual = list(owner.iter_batches([PeriodicSample("same", t) for t in times], batch_size=2))
        summary = owner.envelope_result()
        assert summary.complete and summary.cases_processed == 3
        assert owner.diagnostics()["response"]["periodic_pattern_samples"] == 3
        assert owner.diagnostics()["snapshot"]["application_retained_input_bytes"] > 0
    for got, wanted in zip(actual, expected):
        np.testing.assert_allclose(got.displacements, wanted.displacements, rtol=1e-9, atol=1e-9)
        assert got.case_ids == wanted.case_ids
        for index in range(len(got.case_ids)):
            assert got.stresses(index) and got.reactions(index)
            assert_nested(got.stresses(index), wanted.stresses(index))
            assert_nested(got.reactions(index), wanted.reactions(index))
    for got, wanted in zip(summary.extrema, expected_envelope.extrema):
        for mode in ("minimum", "maximum", "absolute"):
            assert_nested(getattr(got, mode).values, getattr(wanted, mode).values)
            np.testing.assert_array_equal(getattr(got, mode).input_indices, getattr(wanted, mode).input_indices)


def test_fixed_pattern_mixed_direct_empty_repeat_and_early_close():
    project, series, mesh = fixture()
    config = recovery(mesh)
    pattern = sample(series, 1.)
    with prepare_linear_static_response_evaluator(project, mesh=mesh,
            patterns={"p": pattern}, recovery_config=config, envelope_recovery_config=config) as owner:
        stream = owner.iter_batches([PatternLoadRequest("a", {"p": 1.}),
                                     StaticLoadRequest("b", load_case=pattern)], batch_size=1)
        first = next(stream)
        assert first.stresses(0)
        stream.close()
        assert not owner.envelope_result().complete
        assert list(owner.iter_batches([])) == []
        assert owner.envelope_result().complete and owner.envelope_result().cases_processed == 0
        result = list(owner.iter_batches([PatternLoadRequest("a", {"p": 1.}),
                                         StaticLoadRequest("b", load_case=pattern)], batch_size=2))[0]
        np.testing.assert_allclose(result.displacements[:, 0], result.displacements[:, 1], rtol=1e-9, atol=1e-9)


def test_nonperiodic_and_off_decimal_grid_declared_sources_use_direct():
    for expression, times in (("2+t+x", (0., .5)), ("sin(omega*t)", (0., np.nextafter(.5, 1.)))):
        project, series, mesh = fixture(expression, times)
        with prepare_linear_static_response_evaluator(project, mesh=mesh,
                periodic_source=PeriodicLoadSource("series", 2., .5), recovery_config=recovery(mesh)) as owner:
            assert owner.diagnostics()["response"]["periodic"]["direct_reason"]
            assert owner.diagnostics()["response"]["pattern_build_count"] == 0
            assert len(list(owner.iter_batches())) == 1
            assert owner.diagnostics()["response"]["periodic_direct_samples"] == 2


def test_periodic_absent_time_refuses_without_snapping():
    project, series, mesh = fixture()
    with prepare_linear_static_response_evaluator(project, mesh=mesh,
            periodic_source=PeriodicLoadSource("series", 2., .5), recovery_config=recovery(mesh)) as owner:
        with pytest.raises(anysolver.PreparedStaticError, match="static case provider failed") as caught:
            list(owner.iter_batches([PeriodicSample("absent", np.nextafter(.5, 1.))]))
        assert isinstance(caught.value.__cause__, ValueError)
        assert "outside the declared original grid" in str(caught.value.__cause__)


def test_balanced_free_source_response_keeps_canonical_recovery():
    from pathlib import Path
    import runpy
    example = runpy.run_path(str(Path(__file__).parents[1] / "examples" / "prepared_static_time_history.py"))
    project, mesh, node, element, requests = example["build_example"](True)
    config = RecoveryConfig(element_ids=[element], components=("axial_stress", "torsional_stress"),
                            include_reactions=True)
    with prepare_linear_static_response_evaluator(project, mesh=mesh, output_nodes=[node],
            patterns={"balanced": requests[0]}, recovery_config=config, envelope_recovery_config=config) as owner:
        result = list(owner.iter_batches([PatternLoadRequest("pattern", {"balanced": 1.}),
                                         requests[0]], batch_size=2))[0]
        assert result.stresses(0) and result.stresses(1)
        np.testing.assert_allclose(result.displacements[:, 0], result.displacements[:, 1], rtol=1e-9, atol=1e-9)
        assert_nested(result.stresses(0), result.stresses(1))
        assert result.case_results[0]["constraint_forces"]
        assert owner.envelope_result().complete


def test_cancellation_and_phase_library_resource_refusal():
    from anysolver import CancellationToken, SolveCancelled, ResourceConfig
    token = CancellationToken()
    token.cancel("before app capture")
    with pytest.raises(SolveCancelled):
        prepare_linear_static_response_evaluator(cancellation_token=token)
    project, series, mesh = fixture()
    with pytest.raises(Exception, match="memory_limit_bytes"):
        prepare_linear_static_response_evaluator(project, mesh=mesh,
            periodic_source=PeriodicLoadSource("series", 1e10, .5),
            resource_config=ResourceConfig(memory_limit_bytes=1000000))
