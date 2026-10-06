"""Run one numerical job in a separate worker process.

Why a process and not a thread
------------------------------
The solvers are largely Python code.  A worker *thread* shares the GUI's
interpreter lock, so while it computes the GUI thread has to win the lock back
from it on every call that releases it -- painting the viewport, handling a
click, a timer.  Each hand-off can cost milliseconds, a paint needs hundreds of
them. A separate process has its own
lock and its own memory, so the GUI and the solver stop competing.

What crosses the boundary
-------------------------
* in: the job function (by reference), its keyword arguments, and the immutable
  project snapshot (or the detached project clone), pickled once;
* out, one pipe, pickled messages: ``progress`` (message and structured
  payload), then exactly one of ``result`` or ``error``;
* cancellation: the parent forwards its cancellation token to the worker, which
  trips its own token, so the solver's cooperative safe points work unchanged.

The pickler used for transport knows how to carry read-only mapping views
(``mappingproxy``), which the solution objects hold.  That is registered on a
private pickler only; ANYfem's rule that such views are not silently copyable
elsewhere (``deepcopy``, global ``copyreg``) is untouched.

The worker runs at below-normal priority so the GUI wins any contention, and it
exits when the GUI process disappears.  A crash in native solver code ends the
worker, not the application, and is reported as a failed job.
"""

from __future__ import annotations

import copyreg
import dataclasses
import inspect
import io
import multiprocessing
import os
import pickle
import sys
import threading
import traceback
import types
from copy import deepcopy
from typing import Any, Callable, Mapping

__all__ = ["WorkerProcessError", "dumps", "loads", "prepare_call", "run_in_worker_process"]

_POLL_SECONDS = 0.05


class WorkerProcessError(RuntimeError):
    """A job failed inside the worker process (or the worker itself died).

    ``remote_type`` and ``remote_traceback`` carry the original exception's name
    and formatted traceback so diagnostics read the same as for a thread job.
    """

    def __init__(
        self,
        message: str,
        *,
        remote_type: str = "WorkerProcessError",
        remote_traceback: str = "",
        remote_diagnostic: Any = None,
    ) -> None:
        super().__init__(message)
        self.remote_type = remote_type
        self.remote_traceback = remote_traceback
        # Structured evidence computed in the worker by the job's own ``error_diagnostic``
        # hook (see _worker_main); None when the job has none or the worker died.
        self.remote_diagnostic = remote_diagnostic


# ----------------------------------------------------------------------
# transport serialisation
# ----------------------------------------------------------------------
def _rebuild_mappingproxy(contents: dict) -> types.MappingProxyType:
    return types.MappingProxyType(contents)


def _new_thread_local() -> threading.local:
    return threading.local()


class _TransportPickler(pickle.Pickler):
    # Private dispatch table; nothing else about pickling changes.
    #  * Read-only mapping views travel as their contents and are rebuilt as read-only views.
    #  * Thread-local storage (solver work buffers inside cached contact geometry) travels
    #    as a fresh, empty one.  That is exact, not a loss: by definition no other thread
    #    ever sees what a thread stored in it, so the receiving process starts where any
    #    other thread of the worker would have.
    dispatch_table = copyreg.dispatch_table.copy()
    dispatch_table[types.MappingProxyType] = lambda view: (_rebuild_mappingproxy, (dict(view),))
    dispatch_table[type(threading.local())] = lambda local: (_new_thread_local, ())


def dumps(value: Any) -> bytes:
    buffer = io.BytesIO()
    _TransportPickler(buffer, protocol=pickle.HIGHEST_PROTOCOL).dump(value)
    return buffer.getvalue()


def loads(data: bytes) -> Any:
    return pickle.loads(data)


# ----------------------------------------------------------------------
# preparing the call (shared by the thread and the process path)
# ----------------------------------------------------------------------
def prepare_call(
    function: Callable[..., Any],
    kwargs: Mapping[str, Any],
    snapshot: Any,
    project_override: Any,
    progress: Callable[[Any], None],
    token: Any,
) -> dict[str, Any]:
    """Keyword arguments for the job function: a fresh project, progress and token."""

    call = dict(kwargs)
    project = deepcopy(project_override) if project_override is not None else snapshot.thaw()
    call.setdefault("project", project)
    call.setdefault("progress", progress)
    parameters = inspect.signature(function).parameters
    accepts_kwargs = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in parameters.values())
    if accepts_kwargs or "cancellation_token" in parameters:
        call.setdefault("cancellation_token", token)
    return call


# ----------------------------------------------------------------------
# worker side
# ----------------------------------------------------------------------
def _lower_priority() -> None:
    """Best effort: the GUI should win any CPU contention with the solver."""

    try:
        if sys.platform == "win32":
            import ctypes
            from ctypes import wintypes

            BELOW_NORMAL_PRIORITY_CLASS = 0x00004000
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            # Declared types matter: GetCurrentProcess() returns the pseudo-handle -1, which
            # ctypes would otherwise pass as a 32-bit int, an invalid 64-bit handle.
            kernel32.GetCurrentProcess.restype = wintypes.HANDLE
            kernel32.SetPriorityClass.argtypes = (wintypes.HANDLE, wintypes.DWORD)
            kernel32.SetPriorityClass.restype = wintypes.BOOL
            kernel32.SetPriorityClass(kernel32.GetCurrentProcess(), BELOW_NORMAL_PRIORITY_CLASS)
        else:
            os.nice(5)
    except Exception:  # pragma: no cover - priority is a courtesy, never a requirement
        pass


def _exit_with_parent() -> None:
    """Do not outlive the GUI: a solver left running after a crash would burn CPU unseen."""

    parent = multiprocessing.parent_process()
    if parent is None:
        return

    def watch() -> None:
        while parent.is_alive():
            threading.Event().wait(2.0)
        os._exit(3)

    threading.Thread(target=watch, name="anyfem-parent-watch", daemon=True).start()


def _worker_main(connection: Any, cancel_event: Any, payload: bytes) -> None:
    from .jobs import _cancellation_token

    _lower_priority()
    _exit_with_parent()

    def send(message: tuple) -> None:
        connection.send_bytes(dumps(message))

    spec = None
    try:
        spec = loads(payload)
        token = _cancellation_token()

        def forward_cancellation() -> None:
            cancel_event.wait()
            token.cancel("cancelled by caller")

        threading.Thread(target=forward_cancellation, name="anyfem-cancel", daemon=True).start()

        def progress(value: Any) -> None:
            if dataclasses.is_dataclass(value) and not isinstance(value, type):
                # A structured progress record (e.g. a mesh stage) travels as itself.
                try:
                    send(("object", value))
                    return
                except Exception:
                    pass  # fall through to the readable text
            message = getattr(value, "message", None) or str(value)
            raw = getattr(value, "payload", None)
            structured = dict(raw) if isinstance(raw, Mapping) else None
            try:
                send(("progress", str(message), structured))
            except Exception:
                # Telemetry must never end a solve: fall back to the text alone.
                send(("progress", str(message), None))

        call = prepare_call(
            spec["function"], spec["kwargs"], spec["snapshot"], spec["project_override"], progress, token
        )
        result = spec["function"](**call)
        try:
            data = dumps(("result", result))
        except Exception as error:
            raise WorkerProcessError(
                f"the result could not be transferred to the application: {type(error).__name__}: {error}",
                remote_type="ResultTransferError",
            ) from error
        connection.send_bytes(data)
    except BaseException as error:  # noqa: BLE001 - reported to the parent, which decides
        kind = getattr(error, "remote_type", None) or type(error).__name__
        diagnostic = None
        try:
            hook = getattr(spec["function"], "error_diagnostic", None) if spec is not None else None
            if callable(hook):
                diagnostic = hook(error)
        except BaseException:  # noqa: BLE001 - a broken hook must not hide the job's own error
            diagnostic = None
        try:
            send(("error", kind, str(error), traceback.format_exc(), diagnostic))
        except Exception:  # pragma: no cover - the pipe itself is gone
            pass
    finally:
        try:
            connection.close()
        except Exception:  # pragma: no cover
            pass


# ----------------------------------------------------------------------
# application side
# ----------------------------------------------------------------------
def _progress_value(message: str, payload: Mapping[str, Any] | None) -> Any:
    """The same value a thread job reports: structured text when there is a payload."""

    if payload is None:
        return message
    try:
        from .solve.run import _StructuredProgressText

        return _StructuredProgressText(message, payload)
    except Exception:  # pragma: no cover - older layouts without the structured type
        return message


def run_in_worker_process(
    *,
    function: Callable[..., Any],
    kwargs: Mapping[str, Any],
    snapshot: Any,
    project_override: Any,
    progress: Callable[[Any], None],
    token: Any,
    on_process: Callable[[Any], None] | None = None,
) -> Any:
    """Run ``function`` in a fresh worker process and return its result.

    Blocks the calling (job) thread, which waits on a pipe and so does not hold
    the interpreter lock.  Raises :class:`WorkerProcessError` for a failure in
    the worker or the death of the worker.
    """

    try:
        payload = dumps(
            {"function": function, "kwargs": dict(kwargs), "snapshot": snapshot, "project_override": project_override}
        )
    except Exception as error:
        raise WorkerProcessError(
            "this job cannot run in a worker process (its function and arguments must be picklable, "
            f"module-level objects): {type(error).__name__}: {error}",
            remote_type="JobNotTransferable",
        ) from error

    context = multiprocessing.get_context("spawn")
    receiver, sender = context.Pipe(duplex=False)
    cancel_event = context.Event()
    process = context.Process(
        target=_worker_main, args=(sender, cancel_event, payload), name="anyfem-solve", daemon=True
    )
    process.start()
    sender.close()  # the worker owns its end; EOF here then means the worker is gone
    if on_process is not None:
        on_process(process)

    outcome: tuple | None = None
    try:
        while outcome is None:
            if _is_cancelled(token) and not cancel_event.is_set():
                cancel_event.set()
            if receiver.poll(_POLL_SECONDS):
                try:
                    message = loads(receiver.recv_bytes())
                except (EOFError, OSError):
                    break
                if message[0] == "progress":
                    progress(_progress_value(message[1], message[2]))
                elif message[0] == "object":
                    progress(message[1])
                else:
                    outcome = message
            elif not process.is_alive() and not receiver.poll(0):
                break
    finally:
        receiver.close()
        process.join(5.0)
        if process.is_alive():
            process.terminate()
            process.join(5.0)

    if outcome is None:
        raise WorkerProcessError(
            f"the worker process ended unexpectedly (exit code {process.exitcode}); "
            "the application and the model are unaffected",
            remote_type="WorkerProcessExited",
        )
    if outcome[0] == "result":
        return outcome[1]
    _, remote_type, message, remote_traceback, *rest = outcome
    raise WorkerProcessError(
        message,
        remote_type=remote_type,
        remote_traceback=remote_traceback,
        remote_diagnostic=rest[0] if rest else None,
    )


def _is_cancelled(token: Any) -> bool:
    value = getattr(token, "is_cancelled", None)
    if value is None:
        value = getattr(token, "cancelled", False)
    return bool(value() if callable(value) else value)
