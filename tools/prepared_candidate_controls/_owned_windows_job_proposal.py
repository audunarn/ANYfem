"""Windows Job Object guard for the disabled material-provenance smoke.

The job configures a 2 GiB aggregate committed-memory allocation limit; its
queried peak is separate evidence, not proof of a measured ceiling. The caller
owns the 120-second wall deadline and must terminate the job on timeout. A child is
created suspended, assigned to the job, then resumed; closing the job kills
any remaining assigned descendants.
"""

import ctypes
from ctypes import wintypes
import os


JOB_OBJECT_LIMIT_JOB_MEMORY = 0x00000200
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
JOB_OBJECT_BASIC_ACCOUNTING_INFORMATION = 1
THREAD_SUSPEND_RESUME = 0x0002
CREATE_SUSPENDED = 0x00000004
JOB_OBJECT_QUERY = 0x0004


class _BasicLimitInformation(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_int64),
        ("PerJobUserTimeLimit", ctypes.c_int64),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    ]


class _IoCounters(ctypes.Structure):
    _fields_ = [(name, ctypes.c_uint64) for name in (
        "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
        "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]


class _ExtendedLimitInformation(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _BasicLimitInformation),
        ("IoInfo", _IoCounters),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


class _BasicAccountingInformation(ctypes.Structure):
    _fields_ = [
        ("TotalUserTime", ctypes.c_int64),
        ("TotalKernelTime", ctypes.c_int64),
        ("ThisPeriodTotalUserTime", ctypes.c_int64),
        ("ThisPeriodTotalKernelTime", ctypes.c_int64),
        ("TotalPageFaultCount", wintypes.DWORD),
        ("TotalProcesses", wintypes.DWORD),
        ("ActiveProcesses", wintypes.DWORD),
        ("TotalTerminatedProcesses", wintypes.DWORD),
    ]


def _windows_kernel():
    if os.name != "nt" or ctypes.sizeof(ctypes.c_void_p) != 8:
        raise RuntimeError("owned job supervisor requires 64-bit Windows")
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateJobObjectW.argtypes = (ctypes.c_void_p, wintypes.LPCWSTR)
    kernel.CreateJobObjectW.restype = wintypes.HANDLE
    kernel.OpenJobObjectW.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR)
    kernel.OpenJobObjectW.restype = wintypes.HANDLE
    kernel.GetCurrentProcess.argtypes = ()
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.IsProcessInJob.argtypes = (
        wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL))
    kernel.IsProcessInJob.restype = wintypes.BOOL
    kernel.SetInformationJobObject.argtypes = (
        wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD)
    kernel.SetInformationJobObject.restype = wintypes.BOOL
    kernel.QueryInformationJobObject.argtypes = (
        wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD))
    kernel.QueryInformationJobObject.restype = wintypes.BOOL
    kernel.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
    kernel.AssignProcessToJobObject.restype = wintypes.BOOL
    kernel.OpenThread.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel.OpenThread.restype = wintypes.HANDLE
    kernel.ResumeThread.argtypes = (wintypes.HANDLE,)
    kernel.ResumeThread.restype = wintypes.DWORD
    kernel.TerminateJobObject.argtypes = (wintypes.HANDLE, wintypes.UINT)
    kernel.TerminateJobObject.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel.CloseHandle.restype = wintypes.BOOL
    return kernel


def _checked(ok, label):
    if not ok:
        raise OSError(ctypes.get_last_error(), label)


class OwnedWindowsJob:
    def __init__(self, committed_bytes, *, name=None):
        self.kernel = _windows_kernel()
        self.handle = self.kernel.CreateJobObjectW(None, name)
        _checked(self.handle, "CreateJobObjectW")
        self.name = name
        try:
            limits = _ExtendedLimitInformation()
            limits.BasicLimitInformation.LimitFlags = (
                JOB_OBJECT_LIMIT_JOB_MEMORY | JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE)
            limits.JobMemoryLimit = committed_bytes
            _checked(self.kernel.SetInformationJobObject(
                self.handle, JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
                ctypes.byref(limits), ctypes.sizeof(limits)),
                "SetInformationJobObject")
        except BaseException:
            self.close()
            raise

    def assign_suspended(self, process, thread_id):
        """Assign a Popen child before any of its user code can execute."""
        self.assign_suspended_only(process)
        self.resume_suspended(process, thread_id)

    def assign_suspended_only(self, process):
        """Leave the owned child suspended until a separate deadline check."""
        if process.poll() is not None:
            raise RuntimeError("suspended child exited before job assignment")
        _checked(self.kernel.AssignProcessToJobObject(
            self.handle, int(process._handle)), "AssignProcessToJobObject")

    def resume_suspended(self, process, thread_id):
        if process.poll() is not None:
            raise RuntimeError("owned suspended child exited before resume")
        thread = self.kernel.OpenThread(THREAD_SUSPEND_RESUME, False, thread_id)
        _checked(thread, "OpenThread")
        try:
            prior = self.kernel.ResumeThread(thread)
            if prior != 1:
                raise RuntimeError(f"unexpected suspended-thread count {prior}")
        finally:
            _checked(self.kernel.CloseHandle(thread), "CloseHandle(thread)")

    def snapshot(self):
        limits = _ExtendedLimitInformation()
        accounting = _BasicAccountingInformation()
        returned = wintypes.DWORD()
        _checked(self.kernel.QueryInformationJobObject(
            self.handle, JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
            ctypes.byref(limits), ctypes.sizeof(limits), ctypes.byref(returned)),
            "QueryInformationJobObject(limits)")
        _checked(self.kernel.QueryInformationJobObject(
            self.handle, JOB_OBJECT_BASIC_ACCOUNTING_INFORMATION,
            ctypes.byref(accounting), ctypes.sizeof(accounting),
            ctypes.byref(returned)),
            "QueryInformationJobObject(accounting)")
        return {
            "configured_limit_flags": int(limits.BasicLimitInformation.LimitFlags),
            "configured_job_memory_limit_bytes": int(limits.JobMemoryLimit),
            "peak_job_committed_bytes": int(limits.PeakJobMemoryUsed),
            "active_processes": int(accounting.ActiveProcesses),
            "total_processes": int(accounting.TotalProcesses),
        }

    def terminate(self):
        _checked(self.kernel.TerminateJobObject(self.handle, 1),
                 "TerminateJobObject")

    def close(self):
        if self.handle:
            handle, self.handle = self.handle, None
            _checked(self.kernel.CloseHandle(handle), "CloseHandle(job)")


def current_process_job_facts(name):
    """Query limits and membership of this exact named Job, not any Job."""
    if not isinstance(name, str) or not name:
        raise ValueError("owned Job name is required")
    kernel = _windows_kernel()
    handle = kernel.OpenJobObjectW(JOB_OBJECT_QUERY, False, name)
    _checked(handle, "OpenJobObjectW")
    try:
        limits = _ExtendedLimitInformation()
        returned = wintypes.DWORD()
        _checked(kernel.QueryInformationJobObject(
            handle, JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
            ctypes.byref(limits), ctypes.sizeof(limits), ctypes.byref(returned)),
            "QueryInformationJobObject(limits)")
        inside = wintypes.BOOL()
        _checked(kernel.IsProcessInJob(kernel.GetCurrentProcess(), handle,
                                       ctypes.byref(inside)), "IsProcessInJob")
        return {
            "member": bool(inside.value),
            "configured_limit_flags": int(limits.BasicLimitInformation.LimitFlags),
            "configured_job_memory_limit_bytes": int(limits.JobMemoryLimit),
        }
    finally:
        _checked(kernel.CloseHandle(handle), "CloseHandle(queried job)")
