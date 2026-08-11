"""Forwarding contracts between ANYfem analysis wrappers and ANYsolver."""

from __future__ import annotations

import inspect
from types import SimpleNamespace

import numpy as np
import pytest

import anysolver
import anyfem.solve.run as solve_run


class _StopAtSolver(RuntimeError):
    """Stop a wrapper after its solver call has been inspected."""


def _built():
    return SimpleNamespace(
        fe_model=SimpleNamespace(),
        load_case=SimpleNamespace(),
        mesh=SimpleNamespace(),
    )


@pytest.mark.parametrize(
    "wrapper_name",
    [
        "solve_linear_static",
        "solve_modal",
        "solve_buckling",
        "solve_capacity",
        "solve_nonlinear_static",
        "solve_arc_length",
        "solve_transient",
        "solve_impact",
    ],
)
def test_analysis_wrappers_expose_an_explicit_cancellation_token(wrapper_name):
    signature = inspect.signature(getattr(solve_run, wrapper_name))
    assert "cancellation_token" in signature.parameters


@pytest.mark.parametrize(
    ("wrapper_name", "solver_name", "call_options", "records_snapshots"),
    [
        ("solve_linear_static", "solve_linear", {}, False),
        ("solve_modal", "solve_free_vibration", {}, False),
        (
            "solve_capacity",
            "run_nonlinear_capacity_workflow",
            {},
            True,
        ),
        ("solve_nonlinear_static", "solve_static_nonlinear", {}, True),
        ("solve_arc_length", "solve_static_arc_length", {}, True),
        (
            "solve_transient",
            "solve_transient_newmark",
            {"dt": 0.01, "t_end": 0.1},
            False,
        ),
    ],
)
def test_wrappers_forward_cancellation_and_structured_progress(
    monkeypatch,
    wrapper_name,
    solver_name,
    call_options,
    records_snapshots,
):
    observed = {}

    def stop_at_solver(*args, **kwargs):
        observed.update(kwargs)
        raise _StopAtSolver

    monkeypatch.setattr(anysolver, solver_name, stop_at_solver)
    token = anysolver.CancellationToken()
    messages = []
    options = {
        "built": _built(),
        "progress": messages.append,
        "cancellation_token": token,
        **call_options,
    }
    if records_snapshots:
        options["record_increment_snapshots"] = True

    with pytest.raises(_StopAtSolver):
        getattr(solve_run, wrapper_name)(**options)

    assert observed["cancellation_token"] is token
    callback = observed["progress_callback"]
    assert callable(callback)
    callback(
        anysolver.ProgressEvent(
            "test_step",
            "test.integration",
            completed=1,
            total=2,
            metadata={
                "step_index": 1,
                "load_factor": 0.5,
                "load_increment": 0.05,
            },
        )
    )
    assert any("load factor 0.5" in message for message in messages)
    assert any("target 2 (25.0%)" in message for message in messages)
    assert any("load increment 0.05" in message for message in messages)
    if wrapper_name == "solve_nonlinear_static":
        assert any("converged increment 1" in message for message in messages)
    if records_snapshots:
        assert observed["record_increment_snapshots"] is True


def test_buckling_forwards_one_token_and_progress_to_both_solver_stages(
    monkeypatch,
):
    calls = {}

    def reference_solver(*args, **kwargs):
        calls["reference"] = kwargs
        return np.zeros(1), {}

    def recover(*args, **kwargs):
        return {}, {"source": "test"}

    def buckling_solver(*args, **kwargs):
        calls["buckling"] = kwargs
        raise _StopAtSolver

    monkeypatch.setattr(anysolver, "solve_linear", reference_solver)
    monkeypatch.setattr(anysolver, "recover_prestress_from_static_result", recover)
    monkeypatch.setattr(anysolver, "solve_eigenvalue_buckling", buckling_solver)

    token = anysolver.CancellationToken()
    messages = []
    with pytest.raises(_StopAtSolver):
        solve_run.solve_buckling(
            built=_built(),
            progress=messages.append,
            cancellation_token=token,
        )

    for stage in ("reference", "buckling"):
        assert calls[stage]["cancellation_token"] is token
        callback = calls[stage]["progress_callback"]
        assert callable(callback)
        callback(
            anysolver.ProgressEvent(
                "complete",
                f"{stage}.complete",
                completed=1,
                total=1,
                metadata={"status": "ok"},
            )
        )
    assert any("reference solve: ok" in message for message in messages)
    assert any("buckling solve: ok" in message for message in messages)


def test_string_progress_adapter_preserves_a_direct_structured_callback(
    monkeypatch,
):
    structured = []
    messages = []

    def linear_solver(*args, **kwargs):
        event = anysolver.ProgressEvent(
            "linear_static_complete",
            "linear_static.complete",
            completed=1,
            total=1,
            metadata={"status": "converged"},
        )
        kwargs["progress_callback"](event)
        raise _StopAtSolver

    monkeypatch.setattr(anysolver, "solve_linear", linear_solver)
    with pytest.raises(_StopAtSolver):
        solve_run.solve_linear_static(
            built=_built(),
            progress=messages.append,
            progress_callback=structured.append,
        )

    assert len(structured) == 1
    assert isinstance(structured[0], anysolver.ProgressEvent)
    assert any("converged" in message for message in messages)


@pytest.mark.parametrize("nonlinear", [False, True])
def test_impact_forwards_controls_to_linear_and_nonlinear_paths(
    monkeypatch,
    nonlinear,
):
    observed = {}

    def impact_solver(*args, **kwargs):
        observed.update(kwargs)
        raise _StopAtSolver

    monkeypatch.setattr(anysolver, "solve_transient_sphere_impact", impact_solver)
    monkeypatch.setattr(
        anysolver,
        "validate_contact_configuration",
        lambda *args, **kwargs: SimpleNamespace(issues=()),
    )

    collision = SimpleNamespace(
        radius=0.5,
        to_solver=lambda: SimpleNamespace(),
    )
    contact = SimpleNamespace(penalty_stiffness=1.0)
    token = anysolver.CancellationToken()
    messages = []
    with pytest.raises(_StopAtSolver):
        solve_run.solve_impact(
            built=_built(),
            collision=collision,
            contact=contact,
            dt=0.001,
            t_end=0.01,
            nonlinear=nonlinear,
            progress=messages.append,
            cancellation_token=token,
        )

    assert observed["cancellation_token"] is token
    assert callable(observed["progress_callback"])
    observed["progress_callback"](
        anysolver.ProgressEvent(
            "sphere_impact_live_step",
            "sphere_impact.integration",
            completed=0.005,
            total=0.01,
            metadata={"step_index": 5, "time_s": 0.005},
        )
    )
    assert any("t = 0.005 s" in message for message in messages)
    nonlinear_config = observed["nonlinear_config"]
    if nonlinear:
        assert nonlinear_config is not None and nonlinear_config.enabled
    else:
        assert nonlinear_config is None


def test_transient_and_impact_keep_the_complete_raw_solver_results(monkeypatch):
    transient_raw = SimpleNamespace(
        times=np.zeros(0),
        displacements=np.zeros((0, 0)),
        velocities=np.array([[1.0]]),
        accelerations=np.array([[2.0]]),
        impulse=np.array([3.0]),
        status="completed",
        diagnostics={"history": "retained"},
        peak_displacement=0.0,
        peak_displacement_node=None,
    )
    monkeypatch.setattr(
        anysolver,
        "solve_transient_newmark",
        lambda *args, **kwargs: transient_raw,
    )
    transient = solve_run.solve_transient(
        built=_built(),
        dt=0.01,
        t_end=0.1,
    )
    assert transient.info["raw"] is transient_raw
    assert transient.info["raw"].velocities is transient_raw.velocities
    assert transient.info["raw"].accelerations is transient_raw.accelerations

    impact_raw = SimpleNamespace(
        times=np.zeros(0),
        displacements=np.zeros((0, 0)),
        sphere_positions=np.zeros((0, 3)),
        sphere_velocities=np.zeros((0, 3)),
        contact_force_history=np.zeros((0, 3)),
        impulses=np.array([4.0]),
        energies={"kinetic": np.array([5.0])},
        status="completed",
        diagnostics={"damage_history": ("retained",)},
        peak_contact_force=0.0,
        contact_duration=0.0,
        max_penetration=0.0,
        max_penetration_ratio=0.0,
        sphere_momentum_balance_error=0.0,
        peak_displacement=0.0,
        peak_displacement_node=None,
        info={},
    )
    monkeypatch.setattr(
        anysolver,
        "solve_transient_sphere_impact",
        lambda *args, **kwargs: impact_raw,
    )
    monkeypatch.setattr(
        anysolver,
        "validate_contact_configuration",
        lambda *args, **kwargs: SimpleNamespace(issues=()),
    )
    monkeypatch.setattr(
        solve_run,
        "_contact_resolution",
        lambda *args, **kwargs: {"elements_per_radius": 4.0},
    )
    collision = SimpleNamespace(
        radius=0.5,
        mass=1.0,
        to_solver=lambda: SimpleNamespace(),
    )
    impact = solve_run.solve_impact(
        built=_built(),
        collision=collision,
        contact=SimpleNamespace(penalty_stiffness=1.0),
        dt=0.001,
        t_end=0.01,
    )
    assert impact.info["raw"] is impact_raw
    assert impact.info["raw"].impulses is impact_raw.impulses
    assert impact.info["raw"].energies is impact_raw.energies
