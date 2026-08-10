"""Background meshing over immutable document snapshots.

The mapped mesher is intentionally a synchronous, headless API.  This module
keeps that useful contract while providing the desktop application with a
single-worker adapter that never calls Tk from its worker thread.  Cancellation
is cooperative: checks surround the mesher and quality pass, so an
uninterruptible topology operation remains visibly ``cancelling`` until it
returns to a safe point.
"""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
import queue
import threading
import traceback
from typing import Any, Mapping

from .document import ProjectSnapshot, canonical_hash

__all__ = [
    "MeshJobEvent",
    "MeshJobResult",
    "MeshProgress",
    "MeshSettings",
    "MeshTaskManager",
]


@dataclass(frozen=True)
class MeshSettings:
    """All explicit inputs needed to reproduce one mesh submission."""

    target_size: float
    element_order: str = "linear"
    overrides: tuple[tuple[int, int], ...] = ()

    @classmethod
    def create(
        cls,
        target_size: float,
        *,
        element_order: str,
        overrides: Mapping[int, int] | None = None,
    ) -> "MeshSettings":
        size = float(target_size)
        if size <= 0.0:
            raise ValueError("element size must be positive")
        return cls(
            target_size=size,
            element_order=str(element_order),
            overrides=tuple(
                sorted((int(key), int(value)) for key, value in (overrides or {}).items())
            ),
        )

    @property
    def input_hash(self) -> str:
        return canonical_hash(
            {
                "target_size": self.target_size,
                "element_order": self.element_order,
                "overrides": dict(self.overrides),
            }
        )


@dataclass(frozen=True)
class MeshProgress:
    """Structured progress suitable for both a status bar and future logs."""

    stage: str
    message: str
    fraction: float


@dataclass(frozen=True)
class MeshJobResult:
    mesh: Any
    mesh_hash: str
    quality: Mapping[str, Any]


@dataclass(frozen=True)
class MeshJobEvent:
    job_id: str
    kind: str
    message: str = ""
    payload: Any = None


@dataclass
class _ActiveMeshJob:
    job_id: str
    cancellation: Any
    future: Future


class MeshTaskManager:
    """Run at most one local mesh operation without blocking the UI thread."""

    def __init__(self) -> None:
        self._executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="anyfem-mesh"
        )
        self._events: "queue.Queue[MeshJobEvent]" = queue.Queue()
        self._lock = threading.RLock()
        self._active: _ActiveMeshJob | None = None
        self._closing = False

    @property
    def active_job_id(self) -> str | None:
        with self._lock:
            return None if self._active is None else self._active.job_id

    @property
    def running(self) -> bool:
        with self._lock:
            return self._active is not None and not self._active.future.done()

    @property
    def busy(self) -> bool:
        """Whether a submission still owns the manager, including final polling."""

        with self._lock:
            return self._active is not None

    def submit(
        self,
        job_id: str,
        snapshot: ProjectSnapshot,
        settings: MeshSettings,
    ) -> Future:
        with self._lock:
            if self._closing:
                raise RuntimeError("mesh task manager is closed")
            if self._active is not None:
                raise ValueError("a mesh is already being generated")
            cancellation = _cancellation_token()
            future = self._executor.submit(
                self._run, str(job_id), snapshot, settings, cancellation
            )
            self._active = _ActiveMeshJob(str(job_id), cancellation, future)
            return future

    def cancel(self, job_id: str | None = None) -> bool:
        with self._lock:
            active = self._active
            if active is None or (job_id is not None and job_id != active.job_id):
                return False
            if active.future.done():
                return False
            changed = active.cancellation.cancel("mesh cancellation requested")
            if changed:
                self._events.put(
                    MeshJobEvent(
                        active.job_id,
                        "cancelling",
                        "cancelling mesh; waiting for the current safe phase",
                    )
                )
            return bool(changed)

    def poll(self, limit: int | None = None) -> list[MeshJobEvent]:
        events: list[MeshJobEvent] = []
        while limit is None or len(events) < limit:
            try:
                event = self._events.get_nowait()
            except queue.Empty:
                break
            events.append(event)
            if event.kind in ("completed", "cancelled", "failed"):
                with self._lock:
                    if self._active is not None and self._active.job_id == event.job_id:
                        self._active = None
        return events

    def shutdown(self) -> None:
        with self._lock:
            self._closing = True
            active = self._active
            if active is not None:
                active.cancellation.cancel("application is closing")
        self._executor.shutdown(wait=False, cancel_futures=True)

    def _run(
        self,
        job_id: str,
        snapshot: ProjectSnapshot,
        settings: MeshSettings,
        cancellation: Any,
    ) -> None:
        def progress(stage: str, message: str, fraction: float) -> None:
            self._events.put(
                MeshJobEvent(
                    job_id,
                    "progress",
                    message,
                    MeshProgress(stage, message, float(fraction)),
                )
            )

        self._events.put(MeshJobEvent(job_id, "started", "preparing mesh snapshot"))
        try:
            cancellation.raise_if_cancelled("mesh snapshot")
            project = snapshot.thaw()
            progress("snapshot", "prepared immutable model snapshot", 0.1)
            cancellation.raise_if_cancelled("mesh generation")
            progress("generation", "generating mesh elements", 0.2)
            mesh = project.generate_mesh(
                settings.target_size,
                overrides=dict(settings.overrides),
                order=settings.element_order,
            )
            cancellation.raise_if_cancelled("mesh quality")
            progress("quality", "checking element quality", 0.85)
            from anymesher import verify_mesh_quality

            quality = verify_mesh_quality(mesh).as_dict()
            cancellation.raise_if_cancelled("mesh hashing")
            progress("hash", "hashing immutable mesh result", 0.94)
            from anymesher.serialize import mesh_to_dict

            mesh_hash = canonical_hash(mesh_to_dict(mesh))
            cancellation.raise_if_cancelled("mesh completion")
            progress("complete", "mesh and quality checks complete", 1.0)
        except BaseException as error:  # noqa: BLE001 - retained as diagnostics
            if _is_cancelled(cancellation):
                self._events.put(
                    MeshJobEvent(job_id, "cancelled", "mesh generation cancelled")
                )
            else:
                self._events.put(
                    MeshJobEvent(
                        job_id,
                        "failed",
                        str(error),
                        {
                            "type": type(error).__name__,
                            "message": str(error),
                            "traceback": traceback.format_exc(),
                        },
                    )
                )
            return
        self._events.put(
            MeshJobEvent(
                job_id,
                "completed",
                "mesh generation complete",
                MeshJobResult(mesh=mesh, mesh_hash=mesh_hash, quality=quality),
            )
        )


def _cancellation_token():
    try:
        from anysolver import CancellationToken

        return CancellationToken()
    except ImportError:  # pragma: no cover - ANYsolver is a normal dependency
        class Token:
            def __init__(self) -> None:
                self._cancelled = threading.Event()

            @property
            def is_cancelled(self) -> bool:
                return self._cancelled.is_set()

            def cancel(self, _reason: str = "") -> bool:
                changed = not self._cancelled.is_set()
                self._cancelled.set()
                return changed

            def raise_if_cancelled(self, _stage: str = "") -> None:
                if self.is_cancelled:
                    raise RuntimeError("mesh cancelled")

        return Token()


def _is_cancelled(token: Any) -> bool:
    value = getattr(token, "is_cancelled", None)
    if value is None:
        value = getattr(token, "cancelled", False)
    return bool(value() if callable(value) else value)
