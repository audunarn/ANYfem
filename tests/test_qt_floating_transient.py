"""Offscreen floating-transient dialog: controls -> immutable input -> real async job -> reopen (no solve) -> persistent
undoable Apply -> project-file roundtrip -> production consumer.

Manufactured operators of test_floating_transient (real bar FE model); nothing here is a native qualification.
"""
import os
import time
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np  # noqa: E402
import pytest  # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

from anysolver.floating_transient import numeric_array_digest  # noqa: E402

try:
    from tests.test_floating_transient import DT, canonical_mean, make_input, screening_record, world  # noqa: F401
except ImportError:  # pragma: no cover
    from test_floating_transient import DT, canonical_mean, make_input, screening_record, world  # noqa: F401

from anyfem.ui.qt.floating_transient import (  # noqa: E402
    DEVELOPMENT_MARKER, FloatingTransientDialog, parse_curve, parse_fairleads, wind_history)


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


class Panel(QWidget):
    def __init__(self):
        super().__init__()
        from anyfem.model import Project
        self.commands = []
        project = Project()

        def run(cmd):
            self.commands.append(cmd)
            return cmd.do(project)
        self.app = SimpleNamespace(project=project, run=run, set_status=lambda text: None)


def _dialog(qapp, world, tmp_path, inp, **services):
    base, fk, res = tmp_path / "base.npz", tmp_path / "fk.npz", tmp_path / "res.npz"
    inp.save(str(base)); world["fk"].save(str(fk))
    panel = Panel()
    d = FloatingTransientDialog(panel, fe_model_provider=lambda: world["model"], **services)
    d.fields["base_input"].setText(str(base)); d.fields["field_kernel"].setText(str(fk)); d.fields["result"].setText(str(res))
    assert d.turbine.currentData() is None                        # "No turbine" stays the valid default option
    d.fields["window_end"].setText("50")
    import json
    modal = tmp_path / "modal.json"
    modal.write_text(json.dumps(screening_record(world).to_dict()), encoding="utf-8")    # manufactured fixture
    d.fields["modal"].setText(str(modal))
    return d, panel, res


def _use_steady_turbine(d, world):
    """Create the saved turbine load (former dialog defaults) through the command path and select it by name."""
    from anyfem.commands import AddWindTurbineLoad
    from anyfem.model.wind_turbine import WindTurbineLoad
    load = WindTurbineLoad.create("T1", thrust_speeds=[0.0, 10.0, 20.0], thrust_values=[0.0, 1000.0, 1500.0],
                                  hub_body=[0.0, 0.0, 10.0], hub_node_id=int(world["dry"].node_ids[-1]),
                                  wind_mode="steady", reference_mean_world=[10.0, 0.0, 0.0],
                                  rotor_yaw_deg=0.0, rotor_tilt_deg=0.0)
    d.app.run(AddWindTurbineLoad(load))
    assert d.select_turbine("T1") == load and d.turbine.currentData() == load.id
    return load


def _bar_with_modulus(E, n=4):
    from anysolver import BeamElement, FEModel
    model = FEModel("floating-transient-bar")
    model.add_material("steel", E, 0.3, density=5.0)                 # same density / geometry: same dry mass
    for i, x in enumerate(np.linspace(0.0, 2.0, n + 1), 1):
        model.add_node(i, x, 0.0, 0.0)
    for i in range(1, n + 1):
        model.add_element(i, BeamElement(i, [i, i + 1], "steel", {"area": 0.2, "Iy": 0.001, "Iz": 0.002, "J": 0.001,
                                                                    "consistent_mass": True}))
    return model


def _wait(qapp, d, timeout=60.0):
    end = time.monotonic() + timeout
    while d.runner is not None and d.runner.isRunning() and time.monotonic() < end:
        qapp.processEvents(); time.sleep(0.01)
    for _ in range(20):
        qapp.processEvents()


def test_unchanged_input_reuses_the_bound_mean_runs_reopens_and_applies_persistently(qapp, world, tmp_path, monkeypatch):
    def no_mean(*a, **k):
        raise AssertionError("an unchanged mean must not be re-solved")
    d, panel, res = _dialog(qapp, world, tmp_path, make_input(world), solve_mean=no_mean)
    d.start(); _wait(qapp, d)
    assert d.mean_solves == 0
    assert d.summary and d.summary["published"] and d.summary["status"] == "complete", d.status.text()
    assert res.exists() and d.summary["n_frames"] == 6                     # window 0..50 of frames every 10 samples
    assert "quasistatic" in d.diagnostics.text().lower()
    import anysolver.floating_transient as sft
    import anyfem.solve.floating_batch as fb

    def forbidden(*a, **k):
        raise AssertionError("reopen must not solve")
    monkeypatch.setattr(sft, "solve_floating_transient", forbidden)
    monkeypatch.setattr(fb, "iter_residual_relief", forbidden)
    first = d.summary["result_hash"]
    d.reopen()
    assert d.summary["result_hash"] == first
    d.apply()
    project = panel.app.project
    ref = project.floating_transient_results["Floating transient"]
    assert ref.result_hash == first and ref.accepted and not ref.marker
    # persistence: artifact reference + provenance in the project file (no large arrays), exact roundtrip
    from anyfem.io.project_file import project_from_dict, project_to_dict
    data = project_to_dict(project)
    stored = data["floating_transient_results"]["Floating transient"]
    assert stored["result_hash"] == first and "total_loads" not in stored
    back = project_from_dict(data)
    assert back.floating_transient_results["Floating transient"] == ref
    # production consumer: hash-verified artifact + current mass identity
    from anyfem.application.floating_transient_job import applied_floating_transient_frames
    frames = applied_floating_transient_frames(project, world["model"], "Floating transient")
    assert frames["accepted"] and frames["total_loads"].shape[0] == 6 and "quasistatic" in frames["stress_label"]
    # dialog inspection of a saved applied frame (no solve: solver/relief are forbidden above)
    frame = d.inspect_applied(ref.frame_times[2])
    assert frame is not None and frame["frame_index"] == 20 and frame["status"] == "complete"
    assert "quasistatic" in d.inspection.text()
    assert d.inspect_applied(0.12345) is None and "not a saved" in d.inspection.text()
    # same mass, different stiffness (E): stale displacements / stresses are refused
    stiffer = _bar_with_modulus(2.0e7)
    with pytest.raises(ValueError, match="structural"):
        applied_floating_transient_frames(project, stiffer, "Floating transient")
    # undo / redo
    cmd = panel.commands[-1]
    cmd.undo(project)
    assert project.floating_transient_results == {} and "floating_transient_results" not in project_to_dict(project)
    cmd.redo(project)
    assert project.floating_transient_results["Floating transient"] == ref


def test_unverified_hydro_needs_explicit_development_and_is_attached_only_as_development(qapp, world, tmp_path):
    binding = dict(kernel_digest=numeric_array_digest(np.zeros((2, 6, 6))), ainf_digest=numeric_array_digest(world["A_inf"]),
                   kernel_dt=DT)
    inp = make_input(world, hydrodynamic_verification="unverified", hydro_admission="development_unverified",
                     unpassed_hydro_gates=("source_passivity_omega_1",), admission_omegas=np.array([0.5, 1.0, 2.0]),
                     native_source_binding=binding)
    d, panel, _ = _dialog(qapp, world, tmp_path, inp)
    d.start()
    assert d.runner is None and "UNVERIFIED" in d.status.text()
    d.development.setChecked(True)
    d.start(); _wait(qapp, d)
    assert d.summary["status"] == "completed_unverified_hydrodynamics"
    assert DEVELOPMENT_MARKER in d.diagnostics.text() and "source_passivity_omega_1" in d.diagnostics.text()
    d.apply()
    ref = panel.app.project.floating_transient_results["Floating transient"]
    assert ref.marker == DEVELOPMENT_MARKER and ref.development_attached and not ref.accepted
    assert ref.unpassed_gates == ("source_passivity_omega_1",)
    from anyfem.application.floating_transient_job import applied_floating_transient_frames
    with pytest.raises(ValueError, match="UNVERIFIED"):
        applied_floating_transient_frames(panel.app.project, world["model"], "Floating transient")
    dev = applied_floating_transient_frames(panel.app.project, world["model"], "Floating transient",
                                            allow_development=True)
    assert not dev["accepted"] and dev["marker"] == DEVELOPMENT_MARKER


def test_turbine_change_uses_the_production_runtime_and_refuses_without_it(qapp, world, tmp_path):
    d, _, _ = _dialog(qapp, world, tmp_path, make_input(world))
    _use_steady_turbine(d, world)
    d.start()                                    # default service = isolated runtime; not configured here
    assert d.runner is None and "operating mean" in d.status.text() and "runtime" in d.status.text()


@pytest.mark.parametrize("move", ["rotation", "translation"])
def test_changed_mean_is_resolved_in_the_worker_and_a_moved_pose_is_refused_as_stale(qapp, world, tmp_path, move):
    calls = []

    def moved(spec, base, cancel):
        calls.append(spec)
        if move == "rotation":
            th = np.radians(1.0)
            Q = [[np.cos(th), 0.0, np.sin(th)], [0.0, 1.0, 0.0], [-np.sin(th), 0.0, np.cos(th)]]
            return canonical_mean(world, mean_thrust=0.5, Q=Q)
        rec = canonical_mean(world, mean_thrust=0.5)
        rec["translation_world"] = [0.0, 0.0, 0.1]                   # same rotation, moved CG: still stale
        return rec
    d, _, res = _dialog(qapp, world, tmp_path, make_input(world), solve_mean=moved)
    _use_steady_turbine(d, world)
    d.start(); _wait(qapp, d)
    assert calls and calls[0]["hub_body"] == [0.0, 0.0, 10.0]
    assert "stale" in d.status.text() and d.summary is None and not res.exists()


def test_mean_worker_uses_only_frozen_inputs_and_user_edits_cannot_redirect_the_job(qapp, world, tmp_path):
    import threading
    gate, seen = threading.Event(), []

    def gated_mean(spec, base, cancel):
        seen.append(threading.current_thread() is not threading.main_thread())
        gate.wait(20)
        return canonical_mean(world, mean_thrust=0.0, hub_body=[0.0, 0.0, 10.0], rotor_axis=[1.0, 0.0, 0.0],
                              curve={"speeds": [0.0, 10.0, 20.0], "thrusts": [0.0, 1000.0, 1500.0]},
                              wind_ref=[10.0, 0.0, 0.0])
    d, _, res = _dialog(qapp, world, tmp_path, make_input(world), solve_mean=gated_mean)
    _use_steady_turbine(d, world)
    d.start()
    end = time.monotonic() + 10
    while not seen and time.monotonic() < end:
        qapp.processEvents(); time.sleep(0.01)
    assert seen == [True]                                        # the mean runs in the worker thread
    other = tmp_path / "redirected.npz"
    d.fields["result"].setText(str(other))                       # user edits DURING the mean ...

    def live(*a, **k):
        raise AssertionError("the worker must not read the live FE provider after start")
    d.fe_model_provider = live
    d.select_turbine(None)                                       # ... and switching to "No turbine" mid-run
    gate.set(); _wait(qapp, d)
    assert d.mean_solves == 1                                    # committed on the GUI thread via the signal
    assert res.exists() and not other.exists()                   # ... cannot redirect the job or change its inputs
    assert not (tmp_path / "redirected.input.npz").exists()


def test_cancel_during_the_mean_solve_publishes_nothing(qapp, world, tmp_path):
    started = []

    def slow_mean(spec, base, cancel):
        started.append(1)
        while not cancel():
            time.sleep(0.01)
        raise RuntimeError("isolated mean process killed")
    d, _, res = _dialog(qapp, world, tmp_path, make_input(world), solve_mean=slow_mean)
    _use_steady_turbine(d, world)
    d.start()
    end = time.monotonic() + 10
    while not started and time.monotonic() < end:
        qapp.processEvents(); time.sleep(0.01)
    assert d.runner.isRunning()                                  # the mean solve runs off the UI thread
    d.cancel(); _wait(qapp, d)
    assert d.summary is None and not res.exists() and d.mean_solves == 0 and "cancelled" in d.status.text()


def test_kaimal_controls_retain_the_same_anyloads_realization_under_refinement(world):
    t = world["t"]
    a = wind_history("kaimal", t, mean_world=np.array([10.0, 0.0, 0.0]), ti=0.1, length=340.2, seed=7)
    b = wind_history("kaimal", t, mean_world=np.array([10.0, 0.0, 0.0]), ti=0.1, length=340.2, seed=7)
    assert np.array_equal(a[1], b[1]) and a[3]["seed"] == 7 and a[3]["kind"] == "kaimal"
    assert len(a[3]["amplitudes"]) == len(a[3]["phases"]) > 0 and "missing_high_variance" in a[3]
    np.testing.assert_array_equal(a[2], [10.0, 0.0, 0.0])            # declared reference mean, not the sample mean
    # refined grid (half dt): the SAME retained realization is re-sampled (frequencies do not follow the new Nyquist)
    fine = np.linspace(t[0], t[-1], 2 * t.size - 1)
    r = wind_history("kaimal", fine, mean_world=np.array([10.0, 0.0, 0.0]), ti=0.1, length=340.2, seed=7,
                     realization=a[4])
    assert r[4] is a[4] and r[3]["frequencies"] == a[3]["frequencies"]
    np.testing.assert_allclose(r[1][::2], a[1], rtol=0, atol=1e-12)
    c = wind_history("kaimal", t, mean_world=np.array([10.0, 0.0, 0.0]), ti=0.1, length=340.2, seed=8)
    assert not np.array_equal(a[1], c[1])


def test_cancel_publishes_nothing(qapp, world, tmp_path):
    def slow_job(*, cancellation_token, **kw):
        while not cancellation_token.is_cancelled:
            time.sleep(0.01)
        return dict(status="cancelled", published=False)
    d, _, res = _dialog(qapp, world, tmp_path, make_input(world), run_job=slow_job)
    d.start()
    assert d.cancel_run.isEnabled(), d.status.text()
    d.cancel(); _wait(qapp, d)
    assert d.summary is None and not res.exists() and "cancelled" in d.status.text()


def test_parsers():
    sp, th = parse_curve("0:0, 10:1000, 20:1500")
    assert sp.tolist() == [0.0, 10.0, 20.0] and th[1] == 1000.0
    with pytest.raises(ValueError):
        parse_curve("10:1, 5:2")
    (f,) = parse_fairleads("fl1; 3; 1 0 0; 10 0 -5; 100 100 0")
    assert f["node_id"] == 3 and f["stiffness"][0][0] == 100.0
    (g,) = parse_fairleads("fl2; 3; 1 0 0; 10 0 -5; 100 5 0 5 80 0 0 0 1; 2 0 0 0 2 0 0 0 0.5")
    assert g["stiffness"][0][1] == 5.0 and g["stiffness"][1][0] == 5.0 and g["damping"][2][2] == 0.5
    with pytest.raises(ValueError):
        parse_fairleads("fl3; 3; 1 0 0; 10 0 -5; 1 2 3 4")


def test_rotor_axis_reuses_the_anyloads_applicator():
    from anyloads.wind_turbine import TurbineLoadApplicator
    from anyfem.ui.qt.floating_transient import rotor_axis
    ref = TurbineLoadApplicator.from_yaw_tilt([0.0, 0.0, 10.0], yaw=np.radians(7.0), tilt=np.radians(-5.0))
    np.testing.assert_array_equal(rotor_axis([0.0, 0.0, 10.0], 7.0, -5.0), ref.rotor_axis_body)
