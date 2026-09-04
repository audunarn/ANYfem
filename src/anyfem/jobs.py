"""FIFO numerical jobs over immutable project snapshots."""

from __future__ import annotations

from collections import deque
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
import inspect
import queue
import threading
import traceback
from typing import Any, Callable, Deque, Mapping

from .document import ProjectSnapshot, canonical_hash
from .model.records import AnalysisDefinition, JobRecord, JobStatus, OutputRequest

__all__ = ["JobEvent", "JobManager", "JobRequest", "analysis_hash"]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _cancellation_token():
    try:
        from anysolver import CancellationToken

        return CancellationToken()
    except ImportError:
        class CancellationToken:
            def __init__(self) -> None:
                self._event = threading.Event()

            def cancel(self) -> None:
                self._event.set()

            @property
            def cancelled(self) -> bool:
                return self._event.is_set()

            def raise_if_cancelled(self) -> None:
                if self.cancelled:
                    raise RuntimeError("solve cancelled")

        return CancellationToken()


def analysis_hash(
    analysis: AnalysisDefinition,
    output_requests: Mapping[str, OutputRequest] | Any | None = None,
    *,
    document: Mapping[str, Any] | None = None,
) -> str:
    """Hash analysis, selected loading and requested output semantics.

    ``document`` is the immutable submission document when available.  Older
    callers may omit it; their hash remains deterministic but can only carry
    the analysis' target identity, not the selected load record itself.
    """

    payload = analysis.to_dict()
    payload.pop("id", None)
    payload.pop("name", None)
    request_ids = tuple(payload.pop("output_request_ids", ()))
    payload["output_requests"] = _legacy_request_semantics(
        payload.get("output_requests", {})
    )
    if output_requests is None:
        # Backward-compatible callers have no registry with which to resolve
        # non-empty IDs.  Keep that uncertainty explicit; the empty case is
        # nevertheless identical to passing an empty registry.
        payload["typed_output_requests"] = [
            {"unresolved_request_id": str(request_id)}
            for request_id in request_ids
        ]
    else:
        if isinstance(output_requests, Mapping):
            registry = dict(output_requests)
        else:
            registry = {}
            for raw in output_requests:
                try:
                    item = raw if isinstance(raw, OutputRequest) else OutputRequest.from_dict(raw)
                except (TypeError, ValueError):
                    continue
                registry[item.id] = item
        semantics: list[dict[str, Any]] = []
        for request_id in request_ids:
            raw = registry.get(request_id)
            if raw is None:
                semantics.append({"unresolved_request_id": str(request_id)})
                continue
            request = raw if isinstance(raw, OutputRequest) else OutputRequest.from_dict(raw)
            semantics.append(request.semantic_dict())
        payload["typed_output_requests"] = semantics
    payload["selected_loading"] = _selected_loading_semantics(
        analysis, document
    )
    return canonical_hash(payload)


def _legacy_request_semantics(value: Any) -> Any:
    """Remove presentation labels while retaining unknown legacy semantics."""

    if isinstance(value, Mapping):
        return {
            str(key): _legacy_request_semantics(item)
            for key, item in value.items()
            if str(key) not in ("label", "name")
        }
    if isinstance(value, (list, tuple)):
        return [_legacy_request_semantics(item) for item in value]
    return value


def _without_presentation_identity(value: Any) -> Any:
    """Canonical numerical semantics without record UUIDs or labels."""

    if isinstance(value, Mapping):
        return {
            str(key): _without_presentation_identity(item)
            for key, item in value.items()
            if str(key) not in ("id", "name", "label")
        }
    if isinstance(value, (list, tuple)):
        return [_without_presentation_identity(item) for item in value]
    return value


def _find_named_or_identified(
    records: Any, target: str
) -> Mapping[str, Any] | None:
    if not isinstance(records, (list, tuple)):
        return None
    for item in records:
        if not isinstance(item, Mapping):
            continue
        if target in (str(item.get("id", "")), str(item.get("name", ""))):
            return item
    return None


def _selected_loading_semantics(
    analysis: AnalysisDefinition,
    document: Mapping[str, Any] | None,
) -> Any:
    """Return only loading selected by this analysis definition."""

    if analysis.target_kind == "none":
        return {"kind": "none"}
    if not isinstance(document, Mapping):
        return {
            "kind": str(analysis.target_kind),
            "unresolved_target": str(analysis.target_id),
        }
    load_cases = document.get("load_cases", ())
    if analysis.target_kind == "load_case":
        case = _find_named_or_identified(load_cases, str(analysis.target_id))
        if case is None:
            return {
                "kind": "load_case",
                "unresolved_target": str(analysis.target_id),
            }
        return {
            "kind": "load_case",
            "case": _without_presentation_identity(case),
        }
    combination = _find_named_or_identified(
        document.get("combinations", ()), str(analysis.target_id)
    )
    if combination is None:
        return {
            "kind": "combination",
            "unresolved_target": str(analysis.target_id),
        }
    factors = combination.get("factors", {})
    resolved: list[dict[str, Any]] = []
    if isinstance(factors, Mapping):
        for case_target, factor in factors.items():
            case = _find_named_or_identified(load_cases, str(case_target))
            resolved.append(
                {
                    "factor": factor,
                    "case": (
                        {"unresolved_target": str(case_target)}
                        if case is None
                        else _without_presentation_identity(case)
                    ),
                }
            )
    return {"kind": "combination", "terms": resolved}


@dataclass(frozen=True)
class JobEvent:
    job_id: str
    kind: str
    message: str = ""
    payload: Any = None


@dataclass
class JobRequest:
    record: JobRecord
    analysis: AnalysisDefinition
    snapshot: ProjectSnapshot
    function: Callable[..., Any]
    kwargs: dict[str, Any] = field(default_factory=dict)
    cancellation: Any = None
    project_override: Any = None


class JobManager:
    """One active local solve with a persistent FIFO queue.

    Worker threads only publish :class:`JobEvent` objects.  A desktop frontend
    drains them with :meth:`poll`, so no widget is ever touched off its owner
    thread.
    """

    def __init__(self, project=None) -> None:
        self.project = project
        self._pending: Deque[JobRequest] = deque()
        self._requests: dict[str, JobRequest] = {}
        self._results: dict[str, Any] = {}
        self._logs: dict[str, list[dict[str, Any]]] = {}
        self._events: "queue.Queue[JobEvent]" = queue.Queue()
        self._thread: threading.Thread | None = None
        self._active_id: str | None = None
        self._lock = threading.RLock()

    @property
    def active_job_id(self) -> str | None:
        return self._active_id

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def queued(self) -> tuple[str, ...]:
        return tuple(request.record.id for request in self._pending)

    def submit(
        self,
        analysis: AnalysisDefinition,
        snapshot: ProjectSnapshot,
        function: Callable[..., Any],
        *,
        mesh_hash: str = "",
        kwargs: Mapping[str, Any] | None = None,
        name: str | None = None,
        project_override: Any = None,
    ) -> JobRecord:
        submitted_analysis_hash = analysis_hash(
            analysis,
            snapshot.document.get("output_requests", ()),
            document=snapshot.document,
        )
        input_hash = canonical_hash(
            {
                "model_hash": snapshot.revision.model_hash,
                "mesh_hash": mesh_hash,
                "analysis_hash": submitted_analysis_hash,
            }
        )
        record = JobRecord(
            analysis_id=analysis.id,
            name=name or analysis.name,
            model_hash=snapshot.revision.model_hash,
            mesh_hash=mesh_hash,
            analysis_hash=submitted_analysis_hash,
            input_hash=input_hash,
            status=JobStatus.QUEUED,
            created_utc=_now(),
        )
        request = JobRequest(
            record=record,
            analysis=analysis,
            snapshot=snapshot,
            function=function,
            kwargs=dict(kwargs or {}),
            cancellation=_cancellation_token(),
            # Imported mesh-only projects cannot always round-trip through the
            # geometry validator because their scopes intentionally address
            # mesh entities.  Capture an owned clone at submission time for
            # that case; normal geometry jobs continue to use ProjectSnapshot.
            project_override=deepcopy(project_override),
        )
        with self._lock:
            self._requests[record.id] = request
            self._logs[record.id] = []
            self._append_log(record.id, "queued", "queued")
            self._pending.append(request)
            if self.project is not None:
                self.project.jobs[record.id] = record
            self._events.put(JobEvent(record.id, "queued", "queued"))
            self._start_next_locked()
        return record

    def cancel(self, job_id: str) -> bool:
        with self._lock:
            request = self._requests.get(job_id)
            if request is None:
                return False
            if job_id == self._active_id:
                request.record.status = JobStatus.CANCELLING
                request.cancellation.cancel()
                self._append_log(job_id, "status", "cancelling")
                self._events.put(JobEvent(job_id, "status", "cancelling"))
                return True
            for index, queued in enumerate(self._pending):
                if queued.record.id == job_id:
                    del self._pending[index]
                    queued.record.status = JobStatus.CANCELLED
                    queued.record.finished_utc = _now()
                    self._append_log(job_id, "cancelled", "cancelled before start")
                    self._events.put(JobEvent(job_id, "cancelled", "cancelled before start"))
                    return True
        return False

    def rerun(self, job_id: str, snapshot: ProjectSnapshot | None = None) -> JobRecord:
        original = self._requests[job_id]
        return self.submit(
            original.analysis,
            snapshot or original.snapshot,
            original.function,
            mesh_hash=original.record.mesh_hash,
            kwargs=original.kwargs,
            name=f"{original.record.name} rerun",
            project_override=original.project_override,
        )

    def result(self, job_id: str) -> Any:
        if job_id not in self._results:
            raise KeyError(f"job {job_id!r} has no completed in-memory result")
        return self._results[job_id]

    def log(self, job_id: str) -> tuple[dict[str, Any], ...]:
        """Return an immutable copy of one job's structured JSONL log."""

        if job_id not in self._requests:
            raise KeyError(f"no job {job_id!r}")
        with self._lock:
            return tuple(deepcopy(self._logs.get(job_id, ())))

    def poll(self, limit: int | None = None) -> list[JobEvent]:
        events: list[JobEvent] = []
        while limit is None or len(events) < limit:
            try:
                events.append(self._events.get_nowait())
            except queue.Empty:
                break
        return events

    def wait(self, job_id: str, timeout: float | None = None) -> JobRecord:
        request = self._requests[job_id]
        deadline = None
        if timeout is not None:
            import time
            deadline = time.monotonic() + timeout
        while request.record.status in (
            JobStatus.QUEUED,
            JobStatus.RUNNING,
            JobStatus.CANCELLING,
        ):
            if deadline is not None:
                import time
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(f"job {job_id} did not finish")
                if self._thread is not None:
                    self._thread.join(min(remaining, 0.05))
            elif self._thread is not None:
                self._thread.join(0.05)
        # A solver sets the terminal record state immediately before it posts
        # the terminal event.  ``wait`` promises both, so close that tiny
        # publication window before returning to a polling caller.
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            if deadline is None:
                thread.join()
            else:
                import time

                remaining = deadline - time.monotonic()
                if remaining > 0.0:
                    thread.join(remaining)
        return request.record

    # ------------------------------------------------------------------
    def _start_next_locked(self) -> None:
        if self.running or not self._pending:
            return
        request = self._pending.popleft()
        self._active_id = request.record.id
        request.record.status = JobStatus.RUNNING
        request.record.started_utc = _now()
        self._append_log(request.record.id, "started", "running")
        self._thread = threading.Thread(
            target=self._run,
            args=(request,),
            name=f"anyfem-job-{request.record.id[:8]}",
            daemon=True,
        )
        self._thread.start()
        self._events.put(JobEvent(request.record.id, "started", "running"))

    def _run(self, request: JobRequest) -> None:
        def progress(value: Any) -> None:
            message = getattr(value, "message", None) or str(value)
            raw_payload = getattr(value, "payload", value)
            structured = (
                dict(raw_payload)
                if isinstance(raw_payload, Mapping)
                else {}
            )
            self._append_log(
                request.record.id,
                "progress",
                message,
                **({"progress": structured} if structured else {}),
            )
            self._events.put(JobEvent(request.record.id, "progress", message, value))

        try:
            kwargs = dict(request.kwargs)
            project = (
                deepcopy(request.project_override)
                if request.project_override is not None
                else request.snapshot.thaw()
            )
            kwargs.setdefault("project", project)
            kwargs.setdefault("progress", progress)
            parameters = inspect.signature(request.function).parameters
            accepts_kwargs = any(
                parameter.kind == inspect.Parameter.VAR_KEYWORD
                for parameter in parameters.values()
            )
            if accepts_kwargs or "cancellation_token" in parameters:
                kwargs.setdefault("cancellation_token", request.cancellation)
            result = request.function(**kwargs)
            outcome = _result_outcome(result)
            if outcome is not None:
                request.record.outcome = outcome.to_dict()
                request.record.summary["outcome"] = outcome.to_dict()
                request.record.summary["termination"] = outcome.termination
            if outcome is not None and outcome.cancelled:
                request.record.status = JobStatus.CANCELLED
                request.record.partial = bool(outcome.has_results)
                message = outcome.message or outcome.termination
                self._append_log(request.record.id, "cancelled", message)
                event = JobEvent(request.record.id, "cancelled", message, result)
            elif outcome is not None and outcome.failed:
                request.record.status = JobStatus.FAILED
                request.record.partial = False
                message = outcome.message or outcome.termination
                self._append_log(request.record.id, "failed", message)
                event = JobEvent(request.record.id, "failed", message, result)
            elif outcome is not None and outcome.partial:
                self._results[request.record.id] = result
                request.record.status = JobStatus.PARTIAL
                request.record.partial = True
                message = outcome.message or outcome.termination
                summary = getattr(result, "summary", None)
                if callable(summary):
                    request.record.summary["text"] = str(summary())
                self._append_log(request.record.id, "partial", message)
                event = JobEvent(request.record.id, "partial", message, result)
            elif outcome is not None and outcome.completed:
                self._results[request.record.id] = result
                request.record.status = JobStatus.COMPLETED
                summary = getattr(result, "summary", None)
                if callable(summary):
                    request.record.summary["text"] = str(summary())
                message = outcome.message or outcome.termination
                self._append_log(request.record.id, "completed", message)
                event = JobEvent(request.record.id, "completed", message, result)
            elif _is_cancelled(request.cancellation):
                request.record.status = JobStatus.CANCELLED
                request.record.partial = result is not None
                self._append_log(request.record.id, "cancelled", "cancelled")
                event = JobEvent(request.record.id, "cancelled", "cancelled", result)
            else:
                # JobManager remains useful for non-solver background callables;
                # qualified ANYsolver results always take the outcome branches
                # above.
                self._results[request.record.id] = result
                request.record.status = JobStatus.COMPLETED
                summary = getattr(result, "summary", None)
                if callable(summary):
                    request.record.summary["text"] = str(summary())
                self._append_log(request.record.id, "completed", "completed")
                event = JobEvent(request.record.id, "completed", "completed", result)
        except BaseException as error:  # noqa: BLE001 - preserved in job diagnostics
            cancelled = _is_cancelled(request.cancellation)
            request.record.status = JobStatus.CANCELLED if cancelled else JobStatus.FAILED
            request.record.diagnostics.append(
                {
                    "type": type(error).__name__,
                    "message": str(error),
                    "traceback": traceback.format_exc(),
                }
            )
            self._append_log(
                request.record.id,
                "cancelled" if cancelled else "failed",
                str(error),
                error_type=type(error).__name__,
                traceback=request.record.diagnostics[-1]["traceback"],
            )
            event = JobEvent(
                request.record.id,
                "cancelled" if cancelled else "failed",
                str(error),
                error,
            )
        finally:
            request.record.finished_utc = _now()
            self._events.put(event)
            with self._lock:
                self._active_id = None
                self._thread = None
                self._start_next_locked()

    def _append_log(
        self, job_id: str, kind: str, message: str, **details: Any
    ) -> None:
        entry = {
            "timestamp": _now(),
            "kind": str(kind),
            "message": str(message),
            **deepcopy(details),
        }
        with self._lock:
            self._logs.setdefault(job_id, []).append(entry)


def _is_cancelled(token: Any) -> bool:
    """Support both the public ANYsolver token and the legacy fallback."""

    value = getattr(token, "is_cancelled", None)
    if value is None:
        value = getattr(token, "cancelled", False)
    return bool(value() if callable(value) else value)


def _result_outcome(result: Any):
    """Resolve the common ANYsolver outcome carried by an ANYfem wrapper."""

    try:
        from anysolver import SolveOutcome, solve_outcome
    except ImportError:  # pragma: no cover - legacy fallback environment
        return None

    candidates = [result]
    info = getattr(result, "info", None)
    if isinstance(info, Mapping):
        raw = info.get("raw")
        if raw is not None:
            candidates.append(raw)
        encoded = info.get("outcome")
        if isinstance(encoded, Mapping):
            return SolveOutcome.from_dict(encoded)
    raw_result = getattr(result, "raw_result", None)
    if raw_result is not None:
        candidates.append(raw_result)
    for candidate in candidates:
        try:
            return solve_outcome(candidate)
        except TypeError:
            continue
    return None
