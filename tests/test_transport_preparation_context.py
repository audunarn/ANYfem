"""Transport invalidation belongs to ANYfem, never the geometry definition."""
from types import SimpleNamespace
import gc
import os
import weakref

import pytest
from anygeometry import (
    GeometryError, GeometryModel, query_trimmed_surface_charts, to_dict,
)
from anyfem.job_process import dumps, loads
from anyfem.structural_preparation import (
    StructuralPreparationError, prepare_structural_connectivity, source_edge_paths,
)


def crossing_plates():
    model = GeometryModel()
    for points in (
        ((-1, -1, 0), (1, -1, 0), (1, 1, 0), (-1, 1, 0)),
        ((-1, 0, -1), (1, 0, -1), (1, 0, 1), (-1, 0, 1)),
    ):
        model.add_plate(model.add_points(points))
    return model


def transported_prepared():
    model = crossing_plates()
    prepare_structural_connectivity(model)
    return loads(dumps(model))


def assert_requires_preparation(model):
    with pytest.raises(StructuralPreparationError, match="requires fresh owner preparation"):
        source_edge_paths(SimpleNamespace(working_model=model))


def test_transport_records_invalidation_without_geometry_extras():
    model = transported_prepared()
    assert_requires_preparation(model)
    assert not any(name.startswith('_anyfem') for name in vars(model))


def test_transport_context_does_not_keep_discarded_geometry_alive():
    model = transported_prepared()
    reference = weakref.ref(model)
    del model
    gc.collect()
    assert reference() is None


def inspect_geometry_in_worker(*, geometry, **kwargs):
    """Importable real job: the receiving process must restore its own context."""
    assert_requires_preparation(geometry)
    assert not any(name.startswith('_anyfem') for name in vars(geometry))
    return os.getpid()


def test_job_worker_receives_owner_context_without_geometry_extras():
    from anyfem import Project
    from anyfem.document import DocumentSession
    from anyfem.jobs import JobManager
    from anyfem.model.records import AnalysisDefinition, JobStatus
    model = transported_prepared()
    project = Project('transport preparation context')
    manager = JobManager(project, executor='process')
    try:
        record = manager.submit(AnalysisDefinition('Job'), DocumentSession(project).snapshot(),
            inspect_geometry_in_worker, kwargs={'geometry': model})
        assert manager.wait(record.id, timeout=30).status == JobStatus.COMPLETED
        assert manager.result(record.id) != os.getpid()
    finally:
        manager.shutdown()


@pytest.mark.parametrize('failure_kind', ['cancellation', 'owner_failure'])
def test_failed_preparation_keeps_invalidation_across_repeated_transport(monkeypatch, failure_kind):
    model = transported_prepared()
    failure = RuntimeError('cancel owner preparation')
    if failure_kind == 'owner_failure':
        def refuse(*args, **kwargs):
            raise GeometryError('owner refused preparation')
        monkeypatch.setattr('anygeometry.plan_intersections', refuse)
        expected = StructuralPreparationError
        callback = None
    else:
        def callback(phase):
            raise failure
        expected = RuntimeError
    before = to_dict(model)
    with pytest.raises(expected):
        prepare_structural_connectivity(model, cancellation_check=callback)
    assert to_dict(model) == before
    assert_requires_preparation(model)
    again = loads(dumps(model))
    assert_requires_preparation(again)
    assert not any(name.startswith('_anyfem') for name in vars(again))


def test_successful_owner_preparation_clears_only_that_model(monkeypatch):
    model = transported_prepared()
    other = loads(dumps(model))
    phases = []
    query_trimmed_surface_charts(model, tuple(model.faces))
    prepare_structural_connectivity(model, cancellation_check=lambda phase: phases.append(phase))
    assert phases
    # An unavailable optional mapping contract still returns the established
    # empty path after owner success, while the independent transport is stale.
    def unavailable(model):
        raise GeometryError('edge subcurve provenance is unavailable')
    monkeypatch.setattr('anygeometry.query_prepared_edge_subcurve_preimages', unavailable)
    assert source_edge_paths(SimpleNamespace(working_model=model)) == {}
    assert_requires_preparation(other)


@pytest.mark.parametrize('error_type', [RuntimeError, KeyboardInterrupt])
def test_post_apply_report_failure_preserves_preparation_obligation(monkeypatch, error_type):
    import anygeometry
    from anyfem import structural_preparation

    model = transported_prepared()
    applied = []
    original_apply = anygeometry.apply_intersections
    failure = error_type('report construction interrupted after owner apply')

    def record_apply(*args, **kwargs):
        result = original_apply(*args, **kwargs)
        applied.append(result)
        return result

    def fail_report(*args, **kwargs):
        assert applied, 'the regression must exercise failure after successful apply'
        raise failure

    monkeypatch.setattr(anygeometry, 'apply_intersections', record_apply)
    monkeypatch.setattr(structural_preparation, 'PreparedConnection', fail_report)
    with pytest.raises(error_type) as caught:
        prepare_structural_connectivity(model)
    assert caught.value is failure
    assert len(applied) == 1
    assert_requires_preparation(model)
    again = loads(dumps(model))
    assert_requires_preparation(again)
    assert not any(name.startswith('_anyfem') for name in vars(again))


@pytest.mark.parametrize('error_type', [RuntimeError, KeyboardInterrupt])
@pytest.mark.parametrize('transported', [True, False])
def test_completion_interruption_restores_prior_obligation(monkeypatch, error_type, transported):
    from anyfem import _preparation_context as context

    model = transported_prepared() if transported else crossing_plates()
    required_before = context.requires_preparation(model)
    assert required_before is transported
    original_complete = context.preparation_completed
    observed = []
    failure = error_type('interrupted immediately after obligation removal')

    def interrupted_complete(geometry):
        original_complete(geometry)
        observed.append(context.requires_preparation(geometry))
        raise failure

    monkeypatch.setattr(context, 'preparation_completed', interrupted_complete)
    with pytest.raises(error_type) as caught:
        prepare_structural_connectivity(model)
    assert caught.value is failure
    assert observed == [False]
    assert context.requires_preparation(model) is required_before


def test_unprepared_transport_does_not_invent_preparation_requirement():
    model = loads(dumps(crossing_plates()))
    assert source_edge_paths(SimpleNamespace(working_model=model)) == {}


def test_warmed_chart_caller_preserves_ordinary_callback_failure():
    model = crossing_plates()
    query_trimmed_surface_charts(model, tuple(model.faces))
    before = to_dict(model)
    failure = RuntimeError('ordinary closure cancellation')
    def cancel(phase):
        if phase == 'structural intersection batch':
            raise failure
    with pytest.raises(RuntimeError) as caught:
        prepare_structural_connectivity(model, cancellation_check=cancel)
    assert caught.value is failure
    assert to_dict(model) == before
