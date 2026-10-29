"""Darwin process inspection for contained workers and project-lock identity.

psutil is the only process-inspection library (no private libproc/sysctl
structures); ``os.getpgid`` supplies the process group.  Rules shared by every
function here:

* a process that disappears while being inspected is benign (already dead);
* a zombie is dead but unreaped, so it is excluded from survivors;
* any other failure to inspect raises :class:`OSError`: an unknown state is
  never read as "dead" or "retired";
* a deadline covers the PID enumeration as well as the per-process checks.

The module imports nothing heavy; psutil is imported on first use so that a
missing runtime dependency is a refusal, not an import-time crash.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from typing import Any

__all__ = [
    "GUARDIAN_SCRIPT",
    "group_survivor",
    "guardian_script_path",
    "pid_state",
    "preflight",
    "process_start_token",
    "session_survivor",
]

GUARDIAN_SCRIPT = "_darwin_guardian.py"


def _load_psutil(module: Any = None) -> Any:
    if module is not None:
        return module
    try:
        import psutil
    except ImportError as error:
        raise OSError(f"psutil is required for Darwin process inspection: {error}") from error
    return psutil


def guardian_script_path() -> str:
    """Absolute path of the packaged, stdlib-only guardian script."""

    return str(Path(__file__).resolve().with_name(GUARDIAN_SCRIPT))


def preflight() -> str | None:
    """Why Darwin containment cannot start, or ``None`` when its prerequisites exist.

    Checked in the parent before any worker is spawned so that a refusal never
    follows a dispatch.
    """

    try:
        _load_psutil()
    except OSError as error:
        return str(error)
    if not os.path.isabs(sys.executable or ""):
        return "the Python executable path is not absolute; the guardian cannot be launched"
    if not Path(guardian_script_path()).is_file():
        return f"the packaged guardian script is missing: {guardian_script_path()}"
    return None


def pid_state(pid: int, *, psutil_module: Any = None) -> str:
    """``"running"``, ``"zombie"`` or ``"absent"``; raises ``OSError`` if unknown."""

    psutil = _load_psutil(psutil_module)
    try:
        status = psutil.Process(int(pid)).status()
    except psutil.ZombieProcess:  # a subclass of NoSuchProcess: check it first
        return "zombie"
    except psutil.NoSuchProcess:
        return "absent"
    except psutil.Error as error:
        raise OSError(f"inspecting pid {pid} failed: {error}") from error
    return "zombie" if status == psutil.STATUS_ZOMBIE else "running"


def process_start_token(pid: int, *, psutil_module: Any = None) -> str | None:
    """PID-reuse marker from the process creation time; ``None`` when unavailable.

    ``None`` is the conservative answer for every failure: it never makes a
    project lock stale.
    """

    try:
        psutil = _load_psutil(psutil_module)
        created = float(psutil.Process(int(pid)).create_time())
    except Exception:  # noqa: BLE001 - absence of a marker is never authority
        return None
    return f"darwin-start:{created:.6f}"


def _platform_call(name: str, injected: Any) -> Any:
    """The injected getter, else ``os.<name>`` resolved when first needed.

    The lookup is lazy because not every platform has these calls (Windows has
    neither ``getpgid`` nor ``getsid``): importing this module must work
    everywhere, and only a real Darwin inspection needs the real function.
    """

    if injected is not None:
        return injected
    function = getattr(os, name, None)
    if function is None:
        raise OSError(f"os.{name} is not available on this platform")
    return function


def _scan(
    label: str,
    getter: Any,
    wanted: int,
    deadline: float | None,
    psutil: Any,
    clock: Any,
) -> int | None:
    """First live member whose ``getter(pid)`` equals ``wanted`` (see the public wrappers)."""

    if wanted <= 0:
        return None
    if deadline is not None and clock() >= deadline:
        raise TimeoutError("owned tree inspection exceeded the cleanup deadline")
    try:
        pids = list(psutil.pids())
    except Exception as error:  # noqa: BLE001 - enumeration failure is not retirement
        raise OSError(f"enumerating processes failed: {error}") from error
    for pid in pids:
        if deadline is not None and clock() >= deadline:
            raise TimeoutError("owned tree inspection exceeded the cleanup deadline")
        try:
            value = getter(int(pid))
        except ProcessLookupError:
            continue  # gone: already dead
        except OSError as error:
            # A refusal is not proof of death.  A zombie (dead, unreaped) may
            # refuse the lookup; only psutil can prove that, and if psutil
            # cannot tell either the failure stands.
            if pid_state(pid, psutil_module=psutil) in ("zombie", "absent"):
                continue
            raise OSError(f"{label}({pid}) failed: {error}") from error
        if value != int(wanted):
            continue
        if pid_state(pid, psutil_module=psutil) == "running":
            return int(pid)
    return None


def group_survivor(
    group: int,
    deadline: float | None = None,
    *,
    psutil_module: Any = None,
    getpgid: Any = None,
    clock: Any = time.monotonic,
) -> int | None:
    """The first live (non-zombie) member of process group ``group``, or ``None``.

    PIDs are enumerated first and the group of each is determined BEFORE any
    deeper inspection, so unrelated processes are never examined further.
    Raises ``TimeoutError`` (an ``OSError``) once ``deadline`` passes,
    including during enumeration, and ``OSError`` for every inspection failure
    other than a process vanishing.
    """

    psutil = _load_psutil(psutil_module)
    return _scan("getpgid", _platform_call("getpgid", getpgid), int(group), deadline, psutil, clock)


def session_survivor(
    session: int,
    deadline: float | None = None,
    *,
    psutil_module: Any = None,
    getsid: Any = None,
    clock: Any = time.monotonic,
) -> int | None:
    """The first live (non-zombie) member of session ``session``, or ``None``.

    A contained worker is its session's leader (its own ``setsid``), and the
    ANYmesh pool deliberately puts each pool worker and its guardian into
    separate process groups inside that session, so the group scan alone would
    miss them.  Same rules as :func:`group_survivor`; only the key differs.
    Processes of other sessions are never inspected beyond ``getsid``.
    """

    psutil = _load_psutil(psutil_module)
    return _scan("getsid", _platform_call("getsid", getsid), int(session), deadline, psutil, clock)
