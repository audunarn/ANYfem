"""Portable extract of the reviewed geometry-driver control mechanics.

Provenance: the two definitions below are the exact source segments of
``OwnedDeadlineBackstop`` and ``_cleanup_owned`` from the reviewed geometry
driver at ``reports/prepared-mesh-preassembly/current-2cbb161/
control-source/reviewed_geometry_driver.py`` (original sha256
54149b83d7232e33a796a8ae19c18b201debc2ef7b32519be29ec17853387b40). Only those two definitions and the standard library
imports they require are carried here. The original driver module is
never imported or executed from this file: its ``main`` entry, its
module-level reviewed gates and its runtime acceptance are not inherited
and cannot run from this extract.
"""
from __future__ import annotations

import threading
import time


class OwnedDeadlineBackstop:
    """Independent timer for this one owned Job; no real-time precision claim."""

    def __init__(self, deadline, *, clock=time.monotonic,
                 timer_factory=threading.Timer):
        self.deadline = float(deadline)
        self.clock = clock
        self.timer_factory = timer_factory
        self.lock = threading.Lock()
        self.job_access_lock = threading.Lock()
        self.timer = None
        self.armed = False
        self.expired = False
        self.fired_at = None
        self.job = None
        self.child = None
        self.assigned = False
        self.termination_attempted = False
        self.disarmed = False
        self.job_quiesced = False
        self.errors = []

    def arm(self):
        with self.lock:
            if self.armed or self.timer is not None:
                raise RuntimeError("owned deadline backstop is already armed")
            timer = self.timer_factory(max(0.0, self.deadline - self.clock()),
                                       self._expire)
            timer.daemon = True
            self.timer = timer
            timer.start()
            self.armed = True
        self.require_live()

    def _expire(self):
        with self.lock:
            if self.expired:
                return
            self.expired = True
            self.fired_at = self.clock()
            job, child, assigned = self.job, self.child, self.assigned
        if job is not None:
            self._terminate_job(job)
        if child is not None and not assigned:
            self._kill_suspended_child(child)

    def _terminate_job(self, job):
        with self.job_access_lock:
            with self.lock:
                if self.disarmed:
                    return
                self.termination_attempted = True
            try:
                job.terminate()
            except BaseException as error:
                with self.lock:
                    self.errors.append(f"deadline job termination: {type(error).__name__}: {error}")

    def _kill_suspended_child(self, child):
        try:
            if child.poll() is None:
                child.kill()  # This runner created it suspended; no unrelated process.
        except BaseException as error:
            with self.lock:
                self.errors.append(f"deadline suspended-child kill: {type(error).__name__}: {error}")

    def register_job(self, job):
        with self.lock:
            if not self.armed or self.job is not None:
                raise RuntimeError("deadline backstop cannot register Job")
            self.job = job
            already_expired = self.expired
            expired = self.expired or self.clock() >= self.deadline
        if expired:
            if already_expired:
                self._terminate_job(job)
            else:
                self._expire()
            raise TimeoutError("owned Job registered after deadline")

    def register_child(self, child):
        with self.lock:
            if not self.armed or self.job is None or self.child is not None:
                raise RuntimeError("deadline backstop cannot register child")
            self.child = child
            already_expired = self.expired
            expired = self.expired or self.clock() >= self.deadline
        if expired:
            if already_expired:
                self._kill_suspended_child(child)
            else:
                self._expire()
            raise TimeoutError("suspended child registered after deadline")

    def mark_assigned(self):
        self.require_live()
        with self.lock:
            self.assigned = True
        self.require_live()

    def require_live(self):
        with self.lock:
            expired = self.expired or self.clock() >= self.deadline
            armed = self.armed
        if not armed:
            raise RuntimeError("deadline backstop is not armed")
        if expired:
            self._expire()
            raise TimeoutError("owned workload deadline expired")

    def snapshot(self):
        with self.lock:
            return {"armed": self.armed, "expired": self.expired,
                    "fired_at": self.fired_at,
                    "termination_attempted": self.termination_attempted,
                    "job_quiesced": self.job_quiesced,
                    "assigned": self.assigned, "errors": tuple(self.errors),
                    "deadline": self.deadline}

    def stop(self):
        # Quiesce any in-flight Job termination before the owner closes its
        # handle. A delayed timer callback may still run but cannot touch it.
        with self.job_access_lock:
            with self.lock:
                self.disarmed = True
                self.job = None
                self.job_quiesced = True
                timer = self.timer
        if timer is not None:
            timer.cancel()
            timer.join(timeout=1.0)
            if timer.is_alive():
                raise RuntimeError("deadline backstop did not stop after cleanup")



def _cleanup_owned(job, child, wait_empty, *, guard=None) -> tuple[object, object, list[str], BaseException | None]:
    """Attempt every owned cleanup action even when an earlier action fails."""
    errors: list[str] = []
    peak = None
    final = None
    interrupt = None

    def note(label, error):
        nonlocal interrupt
        errors.append(f"{label}: {type(error).__name__}: {error}")
        if interrupt is None and isinstance(error, (KeyboardInterrupt, SystemExit)):
            interrupt = error

    if job is not None:
        try:
            peak = job.snapshot()
        except BaseException as error:
            note("job snapshot", error)
        try:
            job.terminate()
        except BaseException as error:
            note("job termination", error)
    if child is not None:
        try:
            if child.poll() is None:
                child.kill()
        except BaseException as error:
            note("child kill", error)
        try:
            child.wait(timeout=5)
        except BaseException as error:
            note("child wait", error)
    if job is not None:
        try:
            final = wait_empty(job)
            if final["active_processes"] != 0:
                errors.append("owned job still has live descendants")
        except BaseException as error:
            note("job empty verification", error)
    # Stop the callback before closing the Job handle. stop() disarms access
    # even if its bounded timer join reports an error, so closure is still
    # independently attempted after a failed query or timer stop.
    close_is_safe = guard is None
    if guard is not None:
        try:
            guard.stop()
        except BaseException as error:
            note("deadline backstop stop", error)
        try:
            close_is_safe = guard.snapshot()["job_quiesced"] is True
        except BaseException as error:
            note("deadline backstop quiescence", error)
    if job is not None:
        if close_is_safe:
            try:
                job.close()
            except BaseException as error:
                note("job closure", error)
        else:
            errors.append("job closure deferred: deadline callback may still use handle")
    return peak, final, errors, interrupt
