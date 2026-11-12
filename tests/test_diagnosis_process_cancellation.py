"""The diagnosis hard-stop opt-in cannot silently become an unowned stop."""
import pytest

from anyfem import job_process


def test_hard_stop_requires_containment_before_serialization_or_spawn(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("No serialization or process creation was authorised")
    monkeypatch.setattr(job_process, "dumps", forbidden)
    monkeypatch.setattr(job_process.multiprocessing, "get_context", forbidden)
    with pytest.raises(ValueError, match="contained process tree"):
        job_process.run_in_worker_process(function=None, kwargs={}, snapshot=None,
                                         project_override=None, progress=None, token=None,
                                         terminate_on_cancel=True)


def test_worker_priority_does_not_accept_high_or_unknown_modes(monkeypatch):
    monkeypatch.setattr(job_process, "dumps", lambda *args: (_ for _ in ()).throw(AssertionError("must not serialize")))
    for priority in ("high", "realtime", "unknown", None):
        with pytest.raises(ValueError, match="worker_priority"):
            job_process.run_in_worker_process(function=None, kwargs={}, snapshot=None,
                                             project_override=None, progress=None, token=None,
                                             worker_priority=priority)
