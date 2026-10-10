"""M16: periodic samples through the opt-in certified response route."""
import numpy as np
from anyfem.solve import (PeriodicLoadSource, PeriodicSample, StaticLoadRequest,
    prepare_linear_static_evaluator, prepare_linear_static_response_evaluator)
from test_prepared_static_response import fixture, sample, recovery, assert_nested


def test_periodic_recurrence_samples_pass_source_agreement_in_library_route():
    project, series, mesh = fixture(envelope={"by": "number", "start": 3, "stop": 7, "expression": "1+tau"})
    times = (3., .5, 2.)  # 3.0 and 2.0 recur on phases 1.0 and 0.0 of the 2 s period
    config = recovery(mesh)
    requests = [StaticLoadRequest("same", load_case=sample(series, t), time=t) for t in times]
    with prepare_linear_static_evaluator(project, mesh=mesh, recovery_config=config,
            envelope_recovery_config=config) as direct:
        expected = list(direct.iter_batches(requests, batch_size=2))
    with prepare_linear_static_response_evaluator(project, mesh=mesh,
            periodic_source=PeriodicLoadSource("series", 2., .5, times), recovery_config=config,
            envelope_recovery_config=config, stress_execution="certified_library") as owner:
        actual = list(owner.iter_batches([PeriodicSample("same", t) for t in times], batch_size=2))
        summary = owner.envelope_result()
        solver = owner.diagnostics()["solver"]["source_response"]
    print("M16 periodic source agreement:", {key: solver[key] for key in (
        "reconstructed_cases", "library_stress_cases", "source_agreement_fallback_cases",
        "certification_fallback_cases", "max_source_discrepancy_ratio")})
    assert solver["stress_execution"] == "certified_library"
    assert solver["reconstructed_cases"] == 3 and solver["library_stress_cases"] == 3
    assert solver["source_agreement_fallback_cases"] == 0
    assert solver["max_source_discrepancy_ratio"] <= 1e-12
    assert summary.complete and summary.cases_processed == 3
    for got, wanted in zip(actual, expected):
        np.testing.assert_allclose(got.displacements, wanted.displacements, rtol=1e-9, atol=1e-9)
        for index in range(len(got.case_ids)):
            assert_nested(got.stresses(index), wanted.stresses(index))
            assert_nested(got.reactions(index), wanted.reactions(index))
