"""M25f: the opt-in trusted capture (solver option trusted_capture, default off)
passes through the ANYfem factories and leaves results unchanged."""
import numpy as np
from anyfem.solve import (PeriodicLoadSource, PeriodicSample, StaticLoadRequest,
                          prepare_linear_static_evaluator, prepare_linear_static_response_evaluator)
from test_prepared_static_response import fixture, sample, recovery


def _envelope_values(summary):
    return [(entry.quantity, str(entry.entity_id), np.asarray(entry.maximum.values, dtype=float).tobytes())
            for entry in summary.extrema]


def test_trusted_capture_passes_through_anyfem_and_keeps_results():
    project, series, mesh = fixture()
    config = recovery(mesh)
    times = (.5, 2.)
    made = {}
    for flag in (False, True):
        with prepare_linear_static_response_evaluator(project, mesh=mesh,
                periodic_source=PeriodicLoadSource("series", 2., .5, times), recovery_config=config,
                envelope_recovery_config=config, stress_execution="certified_vectorized",
                trusted_capture=flag) as owner:
            assert owner.diagnostics()["solver"]["trusted_capture"] is flag
            blocks = list(owner.iter_batches([PeriodicSample("s", t) for t in times], batch_size=2))
            made[("response", flag)] = (np.concatenate([b.displacements for b in blocks], axis=1),
                                        _envelope_values(owner.envelope_result()))
        requests = [StaticLoadRequest("s", load_case=sample(series, t), time=t) for t in times]
        with prepare_linear_static_evaluator(project, mesh=mesh, recovery_config=config,
                envelope_recovery_config=config, trusted_capture=flag) as direct:
            assert direct.diagnostics()["solver"]["trusted_capture"] is flag
            blocks = list(direct.iter_batches(requests, batch_size=2))
            made[("direct", flag)] = (np.concatenate([b.displacements for b in blocks], axis=1),
                                      _envelope_values(direct.envelope_result()))
    for route in ("response", "direct"):
        assert np.array_equal(made[(route, False)][0], made[(route, True)][0])
        assert made[(route, False)][1] == made[(route, True)][1]
