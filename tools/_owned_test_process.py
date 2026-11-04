"""Stdlib-only containment for the test runner, never application imports.

The kernel32/create/query primitives below are mechanically copied from
anyfem.job_process. Keep their SDK layouts/prototypes identical to that owner.
The bootstrap runs isolated without site, and starts the logical command only
when its parent has assigned this actual process handle to a retained Job.
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import queue
import subprocess
import sys
import tempfile
import threading
import time
from typing import Any

def _windows_kernel32():
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.CreateJobObjectW.argtypes = (wintypes.LPVOID, wintypes.LPCWSTR)
    kernel32.SetInformationJobObject.restype = wintypes.BOOL
    kernel32.SetInformationJobObject.argtypes = (
        wintypes.HANDLE, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD
    )
    kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
    kernel32.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
    kernel32.TerminateJobObject.restype = wintypes.BOOL
    kernel32.TerminateJobObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    kernel32.QueryInformationJobObject.restype = wintypes.BOOL
    kernel32.QueryInformationJobObject.argtypes = (
        wintypes.HANDLE, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD)
    )
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    return kernel32


def _create_kill_on_close_job() -> Any:
    """A new Job Object that dies with its last handle (fail closed)."""

    import ctypes
    from ctypes import wintypes

    kernel32 = _windows_kernel32()
    job = kernel32.CreateJobObjectW(None, None)
    if not job:
        raise OSError(ctypes.get_last_error(), "CreateJobObjectW failed")

    class _IOCounters(ctypes.Structure):
        _fields_ = [
            ("ReadOperationCount", ctypes.c_ulonglong),
            ("WriteOperationCount", ctypes.c_ulonglong),
            ("OtherOperationCount", ctypes.c_ulonglong),
            ("ReadTransferCount", ctypes.c_ulonglong),
            ("WriteTransferCount", ctypes.c_ulonglong),
            ("OtherTransferCount", ctypes.c_ulonglong),
        ]

    class _BasicLimits(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", wintypes.LARGE_INTEGER),
            ("PerJobUserTimeLimit", wintypes.LARGE_INTEGER),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.POINTER(wintypes.ULONG)),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class _ExtendedLimits(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", _BasicLimits),
            ("IoInfo", _IOCounters),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
    JobObjectExtendedLimitInformation = 9
    info = _ExtendedLimits()
    info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if not kernel32.SetInformationJobObject(
        job,
        JobObjectExtendedLimitInformation,
        ctypes.byref(info),
        ctypes.sizeof(info),
    ):
        kernel32.CloseHandle(job)
        raise OSError(ctypes.get_last_error(), "SetInformationJobObject failed")
    return job


def _windows_job_active_processes(job: Any) -> int:
    """The job object's active process count (checked query, exact SDK struct).

    ``JOBOBJECT_BASIC_ACCOUNTING_INFORMATION`` (winnt.h) carries four
    ``LARGE_INTEGER`` time fields before the accounting ``DWORD``s;
    ``ActiveProcesses`` is the number of processes currently assigned to the
    job.  It reaches 0 when every owned process has terminated -- even while
    the parent still holds open handles to them -- so retirement can be
    verified before the outer worker is joined.  A failed query raises: an
    inability to inspect never reads as retirement.
    """

    import ctypes
    from ctypes import wintypes

    class _BasicAccounting(ctypes.Structure):
        _fields_ = [
            ("TotalUserTime", wintypes.LARGE_INTEGER),
            ("TotalKernelTime", wintypes.LARGE_INTEGER),
            ("ThisPeriodTotalUserTime", wintypes.LARGE_INTEGER),
            ("ThisPeriodTotalKernelTime", wintypes.LARGE_INTEGER),
            ("TotalPageFaultCount", wintypes.DWORD),
            ("TotalProcesses", wintypes.DWORD),
            ("ActiveProcesses", wintypes.DWORD),
            ("TotalTerminatedProcesses", wintypes.DWORD),
        ]

    kernel32 = _windows_kernel32()
    info = _BasicAccounting()
    JobObjectBasicAccountingInformation = 1
    if not kernel32.QueryInformationJobObject(
        job,
        JobObjectBasicAccountingInformation,
        ctypes.byref(info),
        ctypes.sizeof(info),
        None,
    ):
        error = ctypes.get_last_error()
        raise OSError(error, f"QueryInformationJobObject failed ({error})")
    return int(info.ActiveProcesses)


def _assign_owned_process(job, process):
    """Use the retained Popen handle, never reopen a potentially recycled PID."""
    import ctypes
    if not _windows_kernel32().AssignProcessToJobObject(job, int(process._handle)):
        raise OSError(ctypes.get_last_error(), "AssignProcessToJobObject failed")


def _retire_job(job):
    """Retire and query the entire owned tree before closing its handle."""
    import ctypes
    kernel = _windows_kernel32()
    try:
        if not kernel.TerminateJobObject(job, 1):
            raise OSError(ctypes.get_last_error(), "TerminateJobObject failed")
        deadline = time.monotonic() + 5
        while _windows_job_active_processes(job):
            if time.monotonic() >= deadline:
                raise RuntimeError("owned pytest Job did not become empty")
            time.sleep(0.01)
    finally:
        # Kill-on-close is also a backstop after a failed termination/query.
        # Such failure still raises; closing never stands in for verification.
        if not kernel.CloseHandle(job):
            raise OSError(ctypes.get_last_error(), "CloseHandle failed")


class WindowsOwnedProcess:
    def __init__(self, process, job, directory, gate, command):
        self._process = process
        self._job = job
        self._directory = directory
        self._gate = gate
        self.bootstrap_command = command

    def __getattr__(self, name):
        return getattr(self._process, name)

    def stop(self):
        if self._job is not None:
            job, self._job = self._job, None
            try:
                _retire_job(job)
                self._process.wait(timeout=5)
            finally:
                self._directory.cleanup()


def start_windows_process(command, *, cwd, env, gate_root=None, **options):
    directory = tempfile.TemporaryDirectory(prefix="pytest-owned-", dir=gate_root)
    gate = Path(directory.name) / "GO"
    bootstrap = [sys.executable, "-I", "-S", str(Path(__file__).resolve()),
                 "--bootstrap", str(gate), "15", json.dumps(list(command))]
    job = None
    process = None
    try:
        job = _create_kill_on_close_job()
        process = subprocess.Popen(bootstrap, cwd=cwd, env=env, **options)
        _assign_owned_process(job, process)
        # The bootstrap cannot launch any logical-command work before this.
        pending = gate.with_name("GO.pending")
        pending.write_text("GO", encoding="ascii")
        os.replace(pending, gate)
        return WindowsOwnedProcess(process, job, directory, gate, bootstrap)
    except BaseException:
        try:
            if process is not None:
                # Before GO this is the sole owned stdlib-only process.
                process.kill()
                process.wait(timeout=5)
        finally:
            try:
                if job is not None:
                    _retire_job(job)
            finally:
                directory.cleanup()
                if process is not None and getattr(process, "stdout", None) is not None:
                    process.stdout.close()
        raise


def owned_output(process):
    """Drain output while noticing bootstrap exit even if a child holds stdout."""
    messages = queue.Queue(maxsize=256)
    cancelled = threading.Event()

    def send(kind, value):
        while not cancelled.is_set():
            try:
                messages.put((kind, value), timeout=0.05)
                return
            except queue.Full:
                continue

    def read():
        try:
            for line in process.stdout:
                send("line", line)
        except BaseException as error:
            send("error", error)
        finally:
            send("eof", None)

    reader = threading.Thread(target=read, name="owned-pytest-output", daemon=True)
    process._output_reader = reader
    reader.start()
    reached_eof = False
    try:
        while True:
            if process.poll() is not None:
                # Retire descendants before waiting for pipe EOF. Already-read
                # output remains queued and is still delivered to the caller.
                process.stop()
            try:
                kind, value = messages.get(timeout=0.05)
            except queue.Empty:
                continue
            if kind == "line":
                yield value
            elif kind == "error":
                raise value
            else:
                reached_eof = True
                return
    finally:
        # On interruption/read error, retirement releases any blocked read.
        # Ordinary EOF alone must not kill a still-running logical command.
        try:
            if not reached_eof:
                process.stop()
        finally:
            cancelled.set()
            reader.join(timeout=5)
        if reader.is_alive():
            raise RuntimeError("owned pytest output reader did not retire")


def close_owned_output(process, output):
    try:
        output.close()
    finally:
        reader = getattr(process, "_output_reader", None)
        if reader is not None and reader.is_alive():
            # TextIO.close can itself block on the live reader's lock. Preserve
            # failure instead of entering an unbounded close after failed join.
            raise RuntimeError("cannot close output while owned reader remains active")
        process.stdout.close()


def _bootstrap(arguments):
    if len(arguments) != 4 or arguments[0] != "--bootstrap":
        raise ValueError("invalid owned pytest bootstrap arguments")
    gate = Path(arguments[1])
    timeout = float(arguments[2])
    command = json.loads(arguments[3])
    if (not math.isfinite(timeout) or not 0 < timeout <= 15
            or type(command) is not list or not command
            or any(type(item) is not str for item in command)):
        raise ValueError("invalid owned pytest bootstrap command or deadline")
    deadline = time.monotonic() + timeout
    while not gate.is_file():
        if time.monotonic() >= deadline:
            print("owned pytest bootstrap GO deadline expired", file=sys.stderr, flush=True)
            return 125
        time.sleep(0.01)
    if gate.read_text(encoding="ascii") != "GO":
        raise ValueError("invalid owned pytest bootstrap GO record")
    # This is a child, not execv: Windows must propagate its actual exit code.
    return subprocess.Popen(command).wait()


if __name__ == "__main__":
    sys.exit(_bootstrap(sys.argv[1:]))
