import numpy as np

from anyfem.ui.live_progress import GRAPH_CHOICES, LiveProgressData


def test_live_progress_parses_newton_trial_and_converged_paths():
    data = LiveProgressData()
    assert data.ingest(
        "Increment trial 1460 | load factor 0.6896 / 1 | increment 4.7e-05 | "
        "Newton iteration 1 | residual 8.94e+03"
    )
    data.ingest(
        "Increment trial 1460 | load factor 0.6896 / 1 | increment 4.7e-05 | "
        "Newton iteration 2 | residual 6.45e+02"
    )
    data.ingest(
        "converged increment 1460: load factor 0.6896 / target 1 (69.0%); "
        "7 iteration(s), max |u| 0.1237 m, max PEEQ 0.0369, "
        "load increment 4.7e-05"
    )

    residual = data.series(GRAPH_CHOICES[0])
    assert tuple(residual.x) == (1.0, 2.0)
    np.testing.assert_allclose(residual.y, np.log10([8.94e3, 6.45e2]))
    assert tuple(data.series(GRAPH_CHOICES[1]).y) == (0.6896,)
    assert tuple(data.series(GRAPH_CHOICES[2]).y) == (0.1237,)
    assert tuple(data.series(GRAPH_CHOICES[3]).y) == (0.0369,)
    assert tuple(data.series(GRAPH_CHOICES[4]).y) == (4.7e-5,)
    assert "Trial increment 1460" in data.caption(GRAPH_CHOICES[0])


def test_new_trial_resets_only_residual_curve_and_paths_are_bounded():
    data = LiveProgressData(max_path_points=5)
    for index in range(1, 20):
        data.ingest(
            f"converged increment {index}: load factor {index / 20} / target 1; "
            f"max |u| {index / 1000} m, max PEEQ {index / 10000}, "
            f"load increment 0.05"
        )
    data.ingest(
        "Increment trial 20 | load factor 1 / 1 | increment 0.05 | "
        "Newton iteration 3 | residual 2e-4"
    )

    assert len(data.increments) <= 5
    assert data.increments[-1] == 19
    assert tuple(data.trial_iterations) == (3,)
    assert len(data.series(GRAPH_CHOICES[1]).x) <= 5


def test_structured_converged_event_is_accepted_without_text_parsing():
    data = LiveProgressData()
    changed = data.ingest(
        "solver event",
        {
            "type": "nonlinear_static_step",
            "step_index": 2,
            "load_factor": 0.25,
            "total": 1.0,
            "max_translation": 0.004,
            "max_equivalent_plastic_strain": 0.001,
            "load_increment": 0.05,
            "support_reactions": {
                "fixed edge": [1200.0, -300.0, 0.0, 0.0, 0.0, 0.0],
                "driven edge": [-1200.0, 300.0, 0.0, 0.0, 0.0, 0.0],
            },
        },
    )
    assert changed
    assert data.target_load_factor == 1.0
    assert data.increments == [2]
    assert data.max_displacements == [0.004]
    assert data.series(GRAPH_CHOICES[5]).y[0] == np.hypot(1200.0, 300.0)
    assert "Reaction: fixed edge | Fx" in data.graph_choices
    assert data.series("Reaction: fixed edge | Fx").y[0] == 1200.0
    assert data.series("Reaction: driven edge | Fy").y[0] == 300.0
