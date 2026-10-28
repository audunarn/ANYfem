"""Headless transient job: hash-verified inputs, atomic publication, no publication on cancel, no-solve reopen."""
import numpy as np
import pytest

from anyfem.application import floating_transient as ft
from anyfem.application import floating_transient_job as job
from anyfem.application.floating_transient_record import FloatingTransientRecordError

try:                                                   # module fixture reused (package or rootdir import modes)
    from tests.test_floating_transient import make_input, screening_record, world  # noqa: F401
except ImportError:  # pragma: no cover
    from test_floating_transient import make_input, screening_record, world  # noqa: F401


def _paths(tmp_path, world, inp):
    ip, kp, rp = str(tmp_path / "in.npz"), str(tmp_path / "fk.npz"), str(tmp_path / "res.npz")
    world["fk"].save(kp)
    return ip, kp, rp, inp.save(ip)


def test_input_roundtrip_is_exact(world, tmp_path):
    inp = make_input(world)
    ip, _, _, digest = _paths(tmp_path, world, inp)
    back = ft.load_floating_transient_input(ip, expected_hash=digest)
    assert back.content_hash() == inp.content_hash() == digest
    assert back.node_ids == inp.node_ids and np.array_equal(back.panel_to_nodal, inp.panel_to_nodal)


def test_job_runs_publishes_and_reopens_without_solving(world, tmp_path, monkeypatch):
    inp = make_input(world)
    ip, kp, rp, digest = _paths(tmp_path, world, inp)
    out = job.run_floating_transient_job(ip, kp, rp, fe_model=world["model"], expected_input_hash=digest,
                                         screening=screening_record(world))
    assert out["published"] and out["status"] == "complete" and out["accepted"]
    import anysolver.floating_transient as sft
    import anyfem.solve.floating_batch as fb

    def forbidden(*a, **k):
        raise AssertionError("reopen must not solve")

    monkeypatch.setattr(sft, "solve_floating_transient", forbidden)
    monkeypatch.setattr(fb, "iter_residual_relief", forbidden)
    again = job.reopen_floating_transient_result(rp, expected_hash=out["result_hash"])
    assert again["result_hash"] == out["result_hash"] and again["status"] == "complete"


def test_stale_inputs_and_cancellation(world, tmp_path):
    inp = make_input(world)
    ip, kp, rp, digest = _paths(tmp_path, world, inp)
    with pytest.raises(FloatingTransientRecordError):
        job.run_floating_transient_job(ip, kp, rp, fe_model=world["model"], expected_input_hash="0" * 64)
    other = make_input(world, field_kernel_hash="1" * 64)
    ip2 = str(tmp_path / "in2.npz"); other.save(ip2)
    with pytest.raises(FloatingTransientRecordError, match="stale"):
        job.run_floating_transient_job(ip2, kp, rp, fe_model=world["model"])

    class SolveCancelled(Exception):
        pass

    def cancelling_relief(*a, **k):
        raise SolveCancelled("stop")
        yield  # pragma: no cover

    out = job.run_floating_transient_job(ip, kp, rp, fe_model=world["model"], relief=cancelling_relief)
    assert out["status"] == "cancelled" and not out["published"]
    import os
    assert not os.path.exists(rp)                                          # nothing published on cancel


def test_cancellation_during_serialisation_never_replaces_the_existing_artifact(world, tmp_path, monkeypatch):
    from anysolver import CancellationToken
    inp = make_input(world)
    ip, kp, rp, digest = _paths(tmp_path, world, inp)
    first = job.run_floating_transient_job(ip, kp, rp, fe_model=world["model"], expected_input_hash=digest,
                                           screening=screening_record(world))
    assert first["published"]
    before = open(rp, "rb").read()
    token = CancellationToken()
    real_savez = np.savez

    def late(handle, **payload):                 # the temp is fully serialised, THEN the user cancels
        real_savez(handle, **payload)
        token.cancel("late cancel during publication")
    monkeypatch.setattr(np, "savez", late)
    out = job.run_floating_transient_job(ip, kp, rp, fe_model=world["model"], expected_input_hash=digest,
                                         screening=screening_record(world), cancellation_token=token)
    assert out["status"] == "cancelled" and out["published"] is False and out["result_path"] is None
    assert open(rp, "rb").read() == before                                  # old artifact untouched
    import os
    assert not [n for n in os.listdir(tmp_path) if n.endswith(".tmp")]     # owned temp removed


# ----------------------------------------------------------------------------- mean preparation in the job

def _prepare_args(world, Q, t, calls):
    from anyfem.application.floating_transient_prepare import CoarseTransientOperators
    base = make_input(world)

    def solve_mean():
        calls.append("mean")
        return {"mean_equilibrium": {"identity": "mean-1", "status": "converged_canonical_development",
                                     "mean_thrust": 0.0, "rotation_body_to_world": np.eye(3).tolist(),
                                     "translation_world": [0.0, 0.0, 0.0]}}

    def prepare_at_pose(pose):
        calls.append(("prepare", np.asarray(pose.t).tolist()))
        return CoarseTransientOperators(node_ids=base.node_ids, panel_ids=tuple(f"p{i}" for i in range(8)),
                                        panel_to_nodal=world["T"], mean_panel_pressure=world["p0"],
                                        hydrostatic_pressure=world["Hs"],
                                        waterline_points_body=np.array([[1.0, 0.0, 0.0]]), pose_Q0=Q,
                                        pose_t=np.asarray(t, float), cg_body=world["dry"].cg,
                                        identities={"source_digest": "coarse"},
                                        mean_hydrostatic_nodal=base.mean_hydrostatic_nodal,
                                        hydrostatic_nodal=base.hydrostatic_nodal, waterline_point_ids=("w0",))

    hydro = dict(added_mass_infinite=base.added_mass_infinite, radiation_kernel=base.radiation_kernel,
                 kernel_horizon=base.kernel_horizon, hydrodynamic_verification="accepted", hydro_admission="strict",
                 field_kernel_hash=base.field_kernel_hash)
    loads = dict(wave_pressure=world["p_wave"], waterline_limit=np.array([10.0]),
                 incident_elevation=np.zeros((world["t"].size, 1)), wave_provenance=base.wave_provenance)
    return dict(solve_mean=solve_mean, prepare_at_pose=prepare_at_pose, hydro_fields=hydro, load_fields=loads,
                times=world["t"], frame_indices=np.arange(0, world["t"].size, 10), dry=world["dry"], gravity=9.81,
                length_scale=2.0)


def test_job_preparation_calls_the_mean_and_binds_operators_at_that_pose(world, tmp_path):
    calls = []
    ip = str(tmp_path / "prep.npz")
    out = job.prepare_floating_transient_job(ip, **_prepare_args(world, np.eye(3), [0.0, 0.0, 0.0], calls))
    assert calls[0] == "mean" and calls[1][0] == "prepare" and out["published"]
    back = ft.load_floating_transient_input(ip, expected_hash=out["input_hash"])
    assert back.mean_equilibrium["identity"] == "mean-1"
    assert back.identities["coarse_prepare"]["source_digest"] == "coarse"
    assert back.content_hash() == out["input_hash"]


def test_job_preparation_refuses_operators_from_another_pose(world, tmp_path):
    from anyfem.application.floating_transient_prepare import FloatingTransientPrepareError
    with pytest.raises(FloatingTransientPrepareError, match="different pose"):
        job.prepare_floating_transient_job(str(tmp_path / "stale.npz"),
                                           **_prepare_args(world, np.eye(3), [0.0, 0.0, 0.3], []))
    import os
    assert not os.path.exists(tmp_path / "stale.npz")
