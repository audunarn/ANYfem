"""Darwin support must be a no-op elsewhere and fail closed before any spawn.

These run on every platform.  Native macOS lifecycle scenarios live in
``test_macos_containment.py`` and are skipped off Darwin.
"""
from __future__ import annotations

import os
import sys

import pytest

import anyfem.job_process as job_process
from anyfem.document import DocumentSession
from anyfem.job_process import WorkerProcessError, run_in_worker_process
from anyfem.jobs import _cancellation_token
from anyfem.model.project import Project


class _Exit(BaseException):
    def __init__(self, code):
        self.code = code


def test_abort_without_an_owned_group_is_exactly_os_exit(monkeypatch):
    """The Windows/Linux paths pass owned_group=False: only ``os._exit(code)`` runs."""

    calls = []
    monkeypatch.setattr(os, "_exit", lambda code: (_ for _ in ()).throw(_Exit(code)))
    for name in ("killpg", "getpgrp"):
        monkeypatch.setattr(os, name, lambda *a, _n=name: calls.append(_n), raising=False)
    with pytest.raises(_Exit) as raised:
        job_process._abort_contained(4, False)
    assert raised.value.code == 4
    assert calls == [], "a non-owned abort must never signal or inspect a group"


@pytest.mark.skipif(sys.platform == "darwin", reason="routing is Darwin-specific")
def test_non_darwin_retirement_inspection_is_the_linux_group_scan(monkeypatch):
    seen = []
    monkeypatch.setattr(job_process, "_linux_group_survivor", lambda group, deadline=None: seen.append(group) or 77)
    monkeypatch.setattr(job_process, "_darwin_session_survivor", lambda *a: pytest.fail("darwin scan on this platform"))
    monkeypatch.setattr(job_process, "_darwin_group_survivor", lambda *a: pytest.fail("darwin scan on this platform"))
    assert job_process._retirement_survivor(5) == 77
    assert seen == [5]


@pytest.mark.parametrize("failure", ["missing psutil or guardian", None])
def test_darwin_refuses_before_any_spawn(monkeypatch, failure):
    """A missing prerequisite is ``ContainmentUnavailable`` and nothing is created."""

    class _Proxy:  # the module's own view only; multiprocessing keeps the real platform
        def __init__(self, real, **override):
            self._real, self._override = real, override

        def __getattr__(self, name):
            return self._override[name] if name in self._override else getattr(self._real, name)

    monkeypatch.setattr(job_process, "sys", _Proxy(sys, platform="darwin"))
    monkeypatch.setattr(job_process, "os", _Proxy(os, name="posix"))
    if failure is None:
        def boom():
            raise RuntimeError("cannot inspect")
        monkeypatch.setattr(job_process, "_darwin_containment_refusal", boom)
    else:
        monkeypatch.setattr(job_process, "_darwin_containment_refusal", lambda: failure)
    created = []
    real = job_process.multiprocessing.get_context

    def spying(*args, **kwargs):
        context = real(*args, **kwargs)

        class Guard:
            def __getattr__(self, name):
                if name in ("Process", "Pipe", "Event"):
                    # Event/Pipe register a SemLock/handle with multiprocessing's
                    # resource tracker, which may start a process: also "a spawn".
                    created.append(name)
                    pytest.fail(f"context.{name} was used before the refusal")
                return getattr(context, name)

        return Guard()

    monkeypatch.setattr(job_process.multiprocessing, "get_context", spying)
    with pytest.raises(WorkerProcessError) as error:
        run_in_worker_process(
            function=os.getpid,
            kwargs={},
            snapshot=DocumentSession(Project("gate")).snapshot(),
            project_override=None,
            progress=lambda value: None,
            token=_cancellation_token(),
            containment=True,
        )
    assert error.value.remote_type == "ContainmentUnavailable"
    assert created == []
