"""M20: owned centroid/normal table and compiled pressure loads for periodic samples."""
import numpy as np
from anyfem.solve import (PeriodicLoadSource, PeriodicSample, StaticLoadRequest,
    prepare_linear_static_evaluator, prepare_linear_static_response_evaluator)
from anyfem.solve import build as builder
from test_prepared_static_response import fixture, sample, recovery, assert_nested


def test_centroid_table_matches_builder_and_no_per_case_recomputation():
    project, series, mesh = fixture(envelope={"by": "number", "start": 3, "stop": 7, "expression": "1+tau"})
    times = (3., .5, 2.)
    config = recovery(mesh)
    requests = [StaticLoadRequest("same", load_case=sample(series, t), time=t) for t in times]
    with prepare_linear_static_evaluator(project, mesh=mesh, recovery_config=config,
            envelope_recovery_config=config) as direct:
        expected = list(direct.iter_batches(requests, batch_size=2))
    with prepare_linear_static_response_evaluator(project, mesh=mesh,
            periodic_source=PeriodicLoadSource("series", 2., .5, times), recovery_config=config,
            envelope_recovery_config=config, stress_execution="certified_vectorized") as owner:
        context = owner._context
        table = context._geometry_normals
        assert table and len(table) == len(mesh.quads) + len(mesh.tris)
        for element_id, (centroid, normal) in list(table.items())[:5]:
            made_centroid, made_normal = builder._element_centroid_normal(context.built.mesh, element_id)
            context.built.mesh.__dict__.pop("_anyfem_centroid_normal", None)
            assert np.array_equal(centroid, made_centroid) and np.array_equal(normal, made_normal)
            copied = context.centroid_normal(element_id)
            assert copied[0] is not centroid and np.array_equal(copied[0], centroid)
        recomputed = []
        original = context._uncached_centroid_normal
        context._uncached_centroid_normal = lambda element_id: recomputed.append(element_id) or original(element_id)
        actual = list(owner.iter_batches([PeriodicSample("same", t) for t in times], batch_size=2))
        solver = owner.diagnostics()["solver"]["source_response"]
    assert recomputed == []
    assert solver["load_execution"] == "compiled_pressure_bases"
    # A sample outside the wet-pressure window has no pressure and stays native.
    assert solver["compiled_load_cases"] >= 1 and solver["compiled_load_cases"] + solver["native_load_cases"] == 3
    assert solver["source_agreement_fallback_cases"] == 0 and solver["reconstructed_cases"] == 3
    assert solver["max_source_discrepancy_ratio"] <= 1e-12
    for got, wanted in zip(actual, expected):
        np.testing.assert_allclose(got.displacements, wanted.displacements, rtol=1e-9, atol=1e-9)
        for index in range(len(got.case_ids)):
            assert_nested(got.stresses(index), wanted.stresses(index))
            assert_nested(got.reactions(index), wanted.reactions(index))
