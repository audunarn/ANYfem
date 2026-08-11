from types import SimpleNamespace

import numpy as np

from anyfem.post.history import history_series
from anyfem.post.results import NonlinearSolution
from anyfem.ui.result_summary import nonlinear_path_summary


def _step(index, factor, *, iterations=3, displacement=0.01, peeq=0.0):
    return SimpleNamespace(
        step_index=index,
        load_factor=factor,
        iterations=iterations,
        residual_norm=1.0e-7,
        displacement_norm=displacement,
        max_equivalent_plastic_strain=peeq,
    )


def test_nonlinear_outcome_distinguishes_last_target_and_failed_trial():
    steps = [_step(1, 0.1), _step(2, 0.68, iterations=9, peeq=0.012)]
    raw = SimpleNamespace(
        status="stopped_at_limit",
        steps=steps,
        snapshots=(object(), object()),
        info={
            "status_category": "stopped",
            "stop_reason": "minimum_increment_reached",
            "last_converged_load_factor": 0.68,
            "peak_load_factor": 0.71,
            "first_failed_load_factor": 0.69,
            "total_newton_iterations": 41,
            "result_case": {"settings": {"max_load_factor": 1.0}},
        },
    )
    solution = SimpleNamespace(
        status="stopped_at_limit", steps=steps, raw_result=raw,
        load_factor=0.68, peak_load_factor=0.71,
    )

    summary = nonlinear_path_summary(solution)

    assert summary.severity == "warning"
    assert summary.start_load_factor == 0.0
    assert summary.first_converged_load_factor == 0.1
    assert summary.peak_load_factor == 0.71
    assert summary.last_converged_load_factor == 0.68
    assert summary.target_load_factor == 1.0
    assert summary.first_failed_load_factor == 0.69
    assert summary.progress_fraction == 0.68
    assert summary.max_peeq == 0.012
    assert summary.saved_increments == 2
    assert "Minimum increment" in summary.stop_reason


def test_load_displacement_history_exposes_unloaded_start_but_not_fake_shape():
    solution = SimpleNamespace(
        steps=[_step(1, 0.1, displacement=0.005)],
        history=lambda: {
            "displacement_norm": np.array([0.005, 0.01]),
            "load_factor": np.array([0.1, 0.2]),
        },
    )
    series = history_series(solution)[0]
    assert tuple(series.x) == (0.0, 0.005, 0.01)
    assert tuple(series.y) == (0.0, 0.1, 0.2)


def test_nonlinear_solution_exposes_only_true_committed_increment_shapes():
    step = _step(4, 0.4, iterations=6, displacement=0.02, peeq=0.003)
    snapshot = SimpleNamespace(
        step_index=4,
        load_factor=0.4,
        displacements=np.arange(12.0),
        element_states={7: {"alpha": np.array([0.003])}},
    )
    solution = NonlinearSolution(
        displacements=np.arange(12.0),
        built=object(),
        steps=[step],
        raw_result=SimpleNamespace(snapshots=(snapshot,), element_states={}),
    )

    assert len(solution.shapes) == 1
    shape = solution.shapes[0]
    assert shape.step is step
    assert shape.value == 0.4
    assert shape.element_states[7]["alpha"][0] == 0.003
    assert "increment 1/1" in shape.label

    without_snapshots = NonlinearSolution(
        displacements=np.zeros(6), built=object(), steps=[step],
        raw_result=SimpleNamespace(snapshots=(), element_states={}),
    )
    assert without_snapshots.shapes == []
