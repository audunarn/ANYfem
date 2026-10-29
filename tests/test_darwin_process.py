"""Darwin process inspection and guardian logic: portable unit tests.

Everything here runs on any platform: psutil is replaced by a small fake for
the ownership rules, and the real-psutil controls only use portable calls.
The native macOS lifecycle scenarios live in ``test_macos_containment.py``.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time

import pytest

import anyfem.job_process as job_process
from anyfem import _darwin_guardian, _darwin_process


# ----------------------------------------------------------------------
# a fake psutil with the same exception hierarchy
# ----------------------------------------------------------------------
class FakePsutil:
    STATUS_ZOMBIE = "zombie"
    STATUS_RUNNING = "running"

    class Error(Exception):
        pass

    class NoSuchProcess(Error):
        pass

    class ZombieProcess(NoSuchProcess):
        pass

    class AccessDenied(Error):
        pass

    def __init__(self, table, enumerate_error=None):
        # table: pid -> (pgid, behaviour) with behaviour in running/zombie/gone/denied/<float>
        self.table = table
        self.enumerate_error = enumerate_error
        self.inspected = []
        outer = self

        class Process:
            def __init__(self, pid):
                self.pid = pid

            def status(self):
                outer.inspected.append(self.pid)
                behaviour = outer.table[self.pid][1]
                if behaviour == "gone":
                    raise outer.NoSuchProcess()
                if behaviour == "zombie":
                    return outer.STATUS_ZOMBIE
                if behaviour == "denied":
                    raise outer.AccessDenied()
                return outer.STATUS_RUNNING

            def create_time(self):
                behaviour = outer.table[self.pid][1]
                if behaviour == "denied":
                    raise outer.AccessDenied()
                if behaviour == "gone":
                    raise outer.NoSuchProcess()
                return 1234.5

        self.Process = Process

    def pids(self):
        if self.enumerate_error is not None:
            raise self.enumerate_error
        return list(self.table)


def _getpgid_for(fake, errors=None):
    errors = errors or {}

    def getpgid(pid):
        if pid in errors:
            raise errors[pid]
        return fake.table[pid][0]

    return getpgid


def _survivor(fake, group, deadline=None, errors=None, clock=time.monotonic):
    return _darwin_process.group_survivor(
        group, deadline, psutil_module=fake, getpgid=_getpgid_for(fake, errors), clock=clock
    )


# ----------------------------------------------------------------------
# group ownership proof
# ----------------------------------------------------------------------
def test_survivor_is_the_live_member_of_the_owned_group():
    fake = FakePsutil({10: (500, "zombie"), 11: (500, "running"), 12: (777, "running")})
    assert _survivor(fake, 500) == 11


def test_only_members_of_the_group_are_inspected_further():
    fake = FakePsutil({10: (900, "running"), 11: (901, "running"), 12: (500, "zombie")})
    assert _survivor(fake, 500) is None
    assert fake.inspected == [12], "an unrelated process was inspected beyond its group"


def test_zombies_and_vanished_members_are_retired():
    fake = FakePsutil({10: (500, "zombie"), 11: (500, "gone")})
    assert _survivor(fake, 500) is None


def test_a_process_vanishing_between_enumeration_and_getpgid_is_benign():
    fake = FakePsutil({10: (500, "running"), 11: (500, "running")})
    errors = {10: ProcessLookupError(3, "No such process")}
    assert _survivor(fake, 500, errors=errors) == 11


def test_unknown_is_never_dead_access_denied_fails_closed():
    fake = FakePsutil({10: (500, "denied")})
    with pytest.raises(OSError, match="inspecting pid 10 failed"):
        _survivor(fake, 500)


def test_getpgid_refusal_fails_closed():
    fake = FakePsutil({10: (500, "running")})
    with pytest.raises(OSError, match="getpgid"):
        _survivor(fake, 500, errors={10: PermissionError(1, "Operation not permitted")})


def test_enumeration_failure_fails_closed():
    fake = FakePsutil({}, enumerate_error=RuntimeError("sysctl failed"))
    with pytest.raises(OSError, match="enumerating processes failed"):
        _survivor(fake, 500)


def test_deadline_covers_enumeration_and_the_scan():
    fake = FakePsutil({10: (900, "running"), 11: (900, "running")})
    with pytest.raises(TimeoutError):
        _survivor(fake, 500, deadline=1.0, clock=lambda: 2.0)  # expired before enumeration
    ticks = iter([0.0, 0.5, 5.0, 5.0])  # start, first pid, second pid expired
    with pytest.raises(TimeoutError):
        _survivor(fake, 500, deadline=1.0, clock=lambda: next(ticks))  # expires mid-scan


def test_a_non_positive_group_has_no_members():
    fake = FakePsutil({10: (500, "running")})
    assert _survivor(fake, -1) is None
    assert _survivor(fake, 0) is None


def test_group_survivor_without_psutil_is_a_failure_not_retirement(monkeypatch):
    monkeypatch.setitem(sys.modules, "psutil", None)  # import raises ImportError
    with pytest.raises(OSError, match="psutil is required"):
        _darwin_process.group_survivor(500)
    assert "psutil is required" in _darwin_process.preflight()


# ----------------------------------------------------------------------
# pid_state / start token
# ----------------------------------------------------------------------
@pytest.mark.parametrize("behaviour, expected", [("running", "running"), ("zombie", "zombie"),
                                                 ("gone", "absent")])
def test_pid_state_classifies_with_the_fake(behaviour, expected):
    fake = FakePsutil({7: (1, behaviour)})
    assert _darwin_process.pid_state(7, psutil_module=fake) == expected


def test_pid_state_refuses_to_guess_when_inspection_fails():
    fake = FakePsutil({7: (1, "denied")})
    with pytest.raises(OSError):
        _darwin_process.pid_state(7, psutil_module=fake)


def test_start_token_is_conservative():
    assert _darwin_process.process_start_token(7, psutil_module=FakePsutil({7: (1, "running")})) \
        == "darwin-start:1234.500000"
    assert _darwin_process.process_start_token(7, psutil_module=FakePsutil({7: (1, "denied")})) is None
    assert _darwin_process.process_start_token(7, psutil_module=FakePsutil({7: (1, "gone")})) is None


# ----------------------------------------------------------------------
# real psutil controls (portable calls only)
# ----------------------------------------------------------------------
def test_real_live_child_is_running_and_exited_child_is_absent():
    pytest.importorskip("psutil")
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        assert _darwin_process.pid_state(child.pid) == "running"  # positive control
        assert _darwin_process.process_start_token(child.pid) is not None
    finally:
        child.kill()
        child.wait(timeout=10)
    assert _darwin_process.pid_state(child.pid) == "absent"  # reaped: actually exited


@pytest.mark.skipif(os.name != "posix", reason="zombies are a POSIX concept")
def test_real_zombie_is_dead_but_not_absent():
    pytest.importorskip("psutil")
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    try:
        deadline = time.monotonic() + 15.0
        state = _darwin_process.pid_state(child.pid)
        while state == "running" and time.monotonic() < deadline:
            time.sleep(0.05)
            state = _darwin_process.pid_state(child.pid)
        assert state == "zombie", "an exited, unreaped child must read as a zombie"
    finally:
        child.wait(timeout=10)


# ----------------------------------------------------------------------
# guardian script
# ----------------------------------------------------------------------
def test_guardian_script_is_packaged_and_standalone():
    path = _darwin_process.guardian_script_path()
    assert os.path.isabs(path) and os.path.isfile(path)
    source = open(path, encoding="utf-8").read()
    for line in source.splitlines():
        if line.startswith(("import ", "from ")):
            assert "anyfem" not in line, "the guardian must not import the anyfem package"


@pytest.mark.skipif(os.name != "posix", reason="the guardian watches POSIX pipes with select")
class TestGuardianWatch:
    def test_outer_worker_eof_fires(self):
        live_r, live_w = os.pipe()
        sent_r, sent_w = os.pipe()
        os.close(live_w)
        try:
            assert _darwin_guardian.watch(live_r, sent_r, poll=0.01) == "outer"
        finally:
            for fd in (live_r, sent_r, sent_w):
                os.close(fd)

    def test_original_parent_sentinel_eof_fires(self):
        live_r, live_w = os.pipe()
        sent_r, sent_w = os.pipe()
        os.close(sent_w)
        try:
            assert _darwin_guardian.watch(live_r, sent_r, poll=0.01) == "parent"
        finally:
            for fd in (live_r, live_w, sent_r):
                os.close(fd)

    def test_keeps_watching_while_both_live_and_ignores_data(self):
        live_r, live_w = os.pipe()
        sent_r, sent_w = os.pipe()
        result = {}
        thread = threading.Thread(
            target=lambda: result.setdefault("why", _darwin_guardian.watch(live_r, sent_r, poll=0.01))
        )
        thread.start()
        try:
            os.write(live_w, b"x")  # data is not a death signal
            time.sleep(0.3)
            assert thread.is_alive() and "why" not in result
            os.close(live_w)
            live_w = None
            thread.join(5.0)
            assert result.get("why") == "outer"
        finally:
            if live_w is not None:
                os.close(live_w)
            thread.join(5.0)
            for fd in (live_r, sent_r, sent_w):
                os.close(fd)

    def test_a_broken_descriptor_fails_closed(self):
        live_r, live_w = os.pipe()
        os.close(live_r)
        os.close(live_w)
        assert _darwin_guardian.watch(live_r, live_r, poll=0.01) == "watch-failed"

    @pytest.mark.parametrize("group_offset", [0, 1])
    def test_refuses_a_group_that_is_not_its_own(self, group_offset):
        ack_r, ack_w = os.pipe()
        live_r, live_w = os.pipe()
        sent_r, sent_w = os.pipe()
        try:
            # group == own pid (a leader cannot be the guardian) or a foreign group
            group = os.getpid() if group_offset == 0 else os.getpgrp() + 1
            code = _darwin_guardian.main([str(group), str(live_r), str(sent_r), str(ack_w)])
            assert code == 4
            assert os.read(ack_r, 16) == b"refused"
        finally:
            for fd in (ack_r, live_r, live_w, sent_r, sent_w):
                try:
                    os.close(fd)
                except OSError:
                    pass

    def test_bad_arguments_are_rejected(self):
        assert _darwin_guardian.main(["x", "1", "2", "3"]) == 2


# ----------------------------------------------------------------------
# worker tree: no stale group signalled, margins recorded
# ----------------------------------------------------------------------
def test_a_terminated_or_closed_tree_never_signals_again(monkeypatch):
    class FakeProcess:
        pid = 321

        def is_alive(self):
            return True

    signalled = []
    monkeypatch.setattr(job_process.os, "name", "posix")
    monkeypatch.setattr(job_process.signal, "SIGKILL", 9, raising=False)
    monkeypatch.setattr(job_process.os, "killpg", lambda g, s: signalled.append(g), raising=False)
    tree = job_process.WorkerTree(FakeProcess(), group=321)
    tree.terminate()
    tree.terminate()
    assert signalled == [321], "a stale numeric group was signalled twice"
    closed = job_process.WorkerTree(FakeProcess(), group=321)
    closed.close()
    closed.terminate()
    assert signalled == [321]
    # a reported group that is not the worker's own pid is never signalled
    foreign = job_process.WorkerTree(FakeProcess(), group=999)
    foreign.terminate()
    assert signalled == [321] and not foreign.terminated


def test_group_inspection_dispatches_by_platform(monkeypatch):
    calls = []
    monkeypatch.setattr(job_process, "_linux_group_survivor", lambda g, d=None: calls.append("linux"))
    monkeypatch.setattr(job_process, "_darwin_group_survivor", lambda g, d=None: calls.append("darwin"))
    monkeypatch.setattr(job_process.sys, "platform", "darwin")
    job_process._group_survivor(1)
    monkeypatch.setattr(job_process.sys, "platform", "linux")
    job_process._group_survivor(1)
    assert calls == ["darwin", "linux"]


# The startup/ACK margins are MEASURED on Darwin, not asserted from constants:
# see test_macos_containment.py::test_startup_and_ack_durations_are_recorded_within_the_deadlines
# (platform evidence pending a real Mac run).  The deadlines themselves are unchanged.


# ----------------------------------------------------------------------
# worker-side watch fails closed
# ----------------------------------------------------------------------
class _Parent:
    def __init__(self, alive=True, error=None):
        self.alive, self.error = alive, error

    def is_alive(self):
        if self.error is not None:
            raise self.error
        return self.alive


class _Guardian:
    def __init__(self, exit_code=None, error=None):
        self.exit_code, self.error = exit_code, error

    def poll(self):
        if self.error is not None:
            raise self.error
        return self.exit_code


def _watch(parent, guardian, iterations=3):
    aborts, sleeps = [], []
    job_process._darwin_watch(
        parent, guardian, abort=lambda code, owned: aborts.append((code, owned)),
        sleep=sleeps.append, iterations=iterations,
    )
    return aborts, sleeps


def test_watch_keeps_watching_while_both_are_alive():
    aborts, sleeps = _watch(_Parent(), _Guardian())
    assert aborts == [] and len(sleeps) == 3


@pytest.mark.parametrize("parent, guardian", [
    (_Parent(alive=False), _Guardian()),
    (_Parent(), _Guardian(exit_code=0)),
])
def test_watch_kills_the_owned_group_when_supervision_is_lost(parent, guardian):
    assert _watch(parent, guardian)[0] == [(3, True)]


@pytest.mark.parametrize("parent, guardian", [
    (_Parent(error=ValueError("is_alive broke")), _Guardian()),
    (_Parent(), _Guardian(error=OSError("poll broke"))),
], ids=["parent-watch-error", "guardian-watch-error"])
def test_unexpected_watch_failure_kills_only_the_owned_group(parent, guardian):
    aborts, sleeps = _watch(parent, guardian)
    assert aborts == [(3, True)], "an unexpected watch failure must fail closed on the owned group"
    assert sleeps == []


def test_watch_abort_signals_only_a_group_the_worker_leads(monkeypatch):
    signalled, exits = [], []
    monkeypatch.setattr(job_process.os, "getpgrp", lambda: 50, raising=False)
    monkeypatch.setattr(job_process.os, "getpid", lambda: 51)  # not the leader: nothing to signal
    monkeypatch.setattr(job_process.os, "killpg", lambda g, s: signalled.append(g), raising=False)
    monkeypatch.setattr(job_process.signal, "SIGKILL", 9, raising=False)
    monkeypatch.setattr(job_process.os, "_exit", lambda code: exits.append(code))
    job_process._abort_contained(3, True)
    assert signalled == [] and exits == [3]
    monkeypatch.setattr(job_process.os, "getpid", lambda: 50)
    job_process._abort_contained(3, True)
    assert signalled == [50] and exits == [3, 3]


# ----------------------------------------------------------------------
# session-wide retirement (ANYmesh pool members live in their own groups)
# ----------------------------------------------------------------------
def _fake_with_sessions(rows):
    """rows: pid -> (pgid, sid, behaviour)."""

    fake = FakePsutil({pid: (sid, behaviour) for pid, (_, sid, behaviour) in rows.items()})
    pgids = {pid: pgid for pid, (pgid, _, _) in rows.items()}
    sids = {pid: sid for pid, (_, sid, _) in rows.items()}
    return fake, pgids.__getitem__, sids.__getitem__


def test_session_scan_sees_a_pool_group_that_the_group_scan_misses():
    fake, getpgid, getsid = _fake_with_sessions({
        321: (321, 321, "zombie"),     # outer worker, unreaped
        400: (400, 321, "running"),    # pool worker: own group, same session
        900: (900, 900, "running"),    # unrelated session
    })
    assert _darwin_process.group_survivor(321, psutil_module=fake, getpgid=getpgid) is None
    assert _darwin_process.session_survivor(321, psutil_module=fake, getsid=getsid) == 400


def test_session_scan_ignores_unrelated_sessions_and_never_inspects_them():
    fake, _, getsid = _fake_with_sessions({
        321: (321, 321, "zombie"),
        900: (900, 900, "running"),
        901: (321 + 1000, 901, "running"),
    })
    assert _darwin_process.session_survivor(321, psutil_module=fake, getsid=getsid) is None
    assert fake.inspected == [321], "a process of another session was inspected beyond getsid"


def test_session_scan_fails_closed_like_the_group_scan():
    fake, _, _ = _fake_with_sessions({10: (10, 321, "running")})
    with pytest.raises(OSError, match="getsid"):
        _darwin_process.session_survivor(
            321, psutil_module=fake,
            getsid=lambda pid: (_ for _ in ()).throw(PermissionError(1, "denied")),
        )
    with pytest.raises(TimeoutError):
        _darwin_process.session_survivor(321, 1.0, psutil_module=fake, getsid=lambda p: 321, clock=lambda: 2.0)
    assert _darwin_process.session_survivor(0, psutil_module=fake, getsid=lambda p: 0) is None


def _terminated_tree(monkeypatch, group=321):
    class FakeProcess:
        pid = group

        def is_alive(self):
            return True

    monkeypatch.setattr(job_process.os, "name", "posix")
    monkeypatch.setattr(job_process.signal, "SIGKILL", 9, raising=False)
    monkeypatch.setattr(job_process.os, "killpg", lambda g, s: None, raising=False)
    tree = job_process.WorkerTree(FakeProcess(), group=group)
    tree.terminate()
    assert tree.terminated
    return tree


def _route_darwin(monkeypatch, rows):
    fake, _, getsid = _fake_with_sessions(rows)
    monkeypatch.setattr(job_process.sys, "platform", "darwin")
    monkeypatch.setattr(_darwin_process, "_load_psutil", lambda module=None: fake)
    monkeypatch.setattr(job_process.os, "getsid", getsid, raising=False)


def test_darwin_retirement_fails_while_another_group_of_the_owned_session_lives(monkeypatch):
    tree = _terminated_tree(monkeypatch)
    _route_darwin(monkeypatch, {321: (321, 321, "zombie"), 400: (400, 321, "running")})
    assert job_process._retirement_survivor(321) == 400
    failure = tree.confirm_retirement(time.monotonic() + 0.3)
    assert failure is not None and "cleanup deadline" in failure, failure


def test_darwin_retirement_ignores_unrelated_sessions(monkeypatch):
    tree = _terminated_tree(monkeypatch)
    _route_darwin(monkeypatch, {321: (321, 321, "zombie"), 900: (900, 900, "running")})
    assert tree.confirm_retirement(time.monotonic() + 5.0) is None


def test_retirement_routes_by_platform(monkeypatch):
    calls = []
    monkeypatch.setattr(job_process, "_group_survivor", lambda g, d=None: calls.append("group"))
    monkeypatch.setattr(job_process, "_darwin_session_survivor", lambda g, d=None: calls.append("session"))
    monkeypatch.setattr(job_process.sys, "platform", "darwin")
    job_process._retirement_survivor(1)
    monkeypatch.setattr(job_process.sys, "platform", "linux")
    job_process._retirement_survivor(1)
    assert calls == ["session", "group"], "Darwin must scan the session; Linux keeps the group scan"


def test_reaped_darwin_worker_never_signals_a_reusable_group(monkeypatch):
    class ExitedProcess:
        pid = 321

        def is_alive(self):
            return False

    signalled = []
    monkeypatch.setattr(job_process.os, "name", "posix")
    monkeypatch.setattr(job_process.sys, "platform", "darwin")
    monkeypatch.setattr(job_process.os, "killpg", lambda g, s: signalled.append(g), raising=False)
    tree = job_process.WorkerTree(ExitedProcess(), group=321)
    tree.terminate()
    assert signalled == []
    monkeypatch.setattr(job_process, "_darwin_session_survivor", lambda *args: None)
    assert tree.confirm_retirement(time.monotonic() + 1) is None
    monkeypatch.setattr(job_process, "_darwin_session_survivor", lambda *args: 400)
    assert tree.confirm_retirement(time.monotonic() + 0.01) is not None
