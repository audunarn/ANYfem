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
    "clone_mesh_for_job",
    "MeshJobEvent",
    "MeshJobResult",
    "MeshProgress",
    "MeshSettings",
    "MeshTaskManager",
    "mesh_semantic_hash",
]


def clone_mesh_for_job(mesh: Any) -> Any:
    """Return a detached, solver-ready mesh through its public wire format.

    A current ANYmesher mesh may retain immutable ANYgeometry provenance such
    as ``mappingproxy`` objects.  Those values are intentionally not pickle or
    ``deepcopy`` compatible.  The versioned mesh codec is the supported
    boundary for background jobs and also guarantees that the worker receives
    no mutable objects shared with the live document.
    """

    from anymesher.serialize import mesh_from_dict, mesh_to_dict

    return mesh_from_dict(mesh_to_dict(mesh))


@dataclass(frozen=True)
class MeshSettings:
    """All explicit inputs needed to reproduce one mesh submission."""

    target_size: float
    element_order: str = "linear"
    overrides: tuple[tuple[int, int], ...] = ()
    # None preserves the established headless behaviour: inherit the strategy
    # from the snapshotted project, whose legacy default is Automatic.
    strategy: str | None = None
    structure_preference: str = "balanced"
    quality_policy: tuple[tuple[str, float], ...] = ()

    def __post_init__(self) -> None:
        from anymesher.hybrid import MeshingStrategy
        from anymesher.structured import StructurePreference
        from anymesher.structured import MeshQualityPolicy

        if self.strategy is not None:
            try:
                strategy = MeshingStrategy(str(self.strategy).strip().lower()).value
            except ValueError as error:
                choices = ", ".join(item.value for item in MeshingStrategy)
                raise ValueError(
                    f"unknown meshing strategy {self.strategy!r}; expected one of {choices}"
                ) from error
            object.__setattr__(self, "strategy", strategy)
        try:
            preference = StructurePreference(
                str(self.structure_preference).strip().lower()
            ).value
        except ValueError as error:
            choices = ", ".join(item.value for item in StructurePreference)
            raise ValueError(
                "unknown structure preference "
                f"{self.structure_preference!r}; expected one of {choices}"
            ) from error
        object.__setattr__(self, "structure_preference", preference)
        quality = MeshQualityPolicy.create(dict(self.quality_policy))
        object.__setattr__(
            self,
            "quality_policy",
            tuple(sorted((key, float(value)) for key, value in quality.to_dict().items())),
        )

    @classmethod
    def create(
        cls,
        target_size: float,
        *,
        element_order: str,
        overrides: Mapping[int, int] | None = None,
        strategy: str | None = None,
        structure_preference: str = "balanced",
        quality_policy: Mapping[str, float] | None = None,
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
            strategy=strategy,
            structure_preference=structure_preference,
            quality_policy=tuple(
                sorted((str(key), float(value)) for key, value in (quality_policy or {}).items())
            ),
        )

    @property
    def input_hash(self) -> str:
        return canonical_hash(
            {
                "target_size": self.target_size,
                "element_order": self.element_order,
                "overrides": dict(self.overrides),
                "strategy": self.strategy,
                "structure_preference": (
                    self.structure_preference
                    if self.strategy in (None, "auto")
                    else None
                ),
                "quality_policy": (
                    dict(self.quality_policy)
                    if self.strategy in (None, "auto")
                    else None
                ),
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
    structural_preparation: Mapping[str, Any]


def mesh_semantic_hash(
    mesh: Any,
    *,
    model_hash: str,
    mesh_input_hash: str,
    structural_preparation: Mapping[str, Any] | None = None,
) -> str:
    """Hash solver-affecting mesh semantics, excluding owner bookkeeping."""

    from anymesher.serialize import mesh_to_dict

    mesh_payload = dict(mesh_to_dict(mesh))
    mesh_payload.pop("geometry_model_id", None)
    mesh_payload.pop("geometry_revision", None)
    preparation = dict(structural_preparation or {})
    preparation.pop("working_model_id", None)
    preparation.pop("working_revision", None)
    preparation.pop("source_revision", None)
    preparation.pop("source_model_id", None)
    structured_layout = preparation.get("structured_layout")
    if isinstance(structured_layout, Mapping):
        structured_layout = dict(structured_layout)
        plan = structured_layout.get("plan")
        if isinstance(plan, Mapping):
            plan = dict(plan)
            # The detached clone receives a new UUID on every mesh job.  It is
            # provenance, not solver-affecting mesh semantics.  The plan hash
            # already captures its canonical topology/options without either
            # transaction identifier.
            plan.pop("model_id", None)
            plan.pop("revision", None)
            structured_layout["plan"] = plan
        structured_layout.pop("source_to_working_faces", None)
        structured_layout.pop("source_to_working_edges", None)
        preparation["structured_layout"] = structured_layout
    return canonical_hash(
        {
            "model_hash": str(model_hash),
            "mesh_input_hash": str(mesh_input_hash),
            "structural_preparation": preparation,
            "mesh": mesh_payload,
        }
    )


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
            from anymesher.hybrid import MeshingStrategy

            requested_strategy = settings.strategy
            if requested_strategy is None:
                project_settings = project.native_mesh_settings
                backend = (
                    "automatic"
                    if project_settings is None
                    else str(
                        getattr(
                            project_settings.backend,
                            "value",
                            project_settings.backend,
                        )
                    )
                )
                requested_strategy = {
                    "automatic": "auto",
                    "auto": "auto",
                    "mapped": "mapped",
                    "native": "native",
                }.get(backend, backend)
            resolved_strategy = MeshingStrategy(requested_strategy).value
            progress("snapshot", "prepared immutable model snapshot", 0.1)
            cancellation.raise_if_cancelled("mesh generation")
            progress("generation", "generating mesh elements", 0.2)
            mesh = project.generate_mesh(
                settings.target_size,
                overrides=dict(settings.overrides),
                order=settings.element_order,
                strategy=settings.strategy,
                structure_preference=settings.structure_preference,
                quality_policy=dict(settings.quality_policy),
                cancellation_check=cancellation.raise_if_cancelled,
            )
            cancellation.raise_if_cancelled("mesh quality")
            progress("quality", "checking element quality", 0.85)
            from anymesher import verify_mesh_quality

            quality = verify_mesh_quality(mesh).as_dict()
            cancellation.raise_if_cancelled("mesh hashing")
            progress("hash", "hashing immutable mesh result", 0.94)
            structural_preparation = dict(project._last_mesh_preparation)
            resolved_settings_hash = settings.input_hash
            if settings.strategy is None:
                # Older/headless callers inherit the snapshotted project
                # strategy. Canonicalize that resolved value so their hash is
                # identical to an explicit UI submission of the same method.
                resolved_settings_hash = MeshSettings.create(
                    settings.target_size,
                    element_order=settings.element_order,
                    overrides=dict(settings.overrides),
                    strategy=resolved_strategy,
                    structure_preference=settings.structure_preference,
                    quality_policy=dict(settings.quality_policy),
                ).input_hash
            semantic_input_hash = canonical_hash(
                {
                    "mesh_settings": resolved_settings_hash,
                    # The constrained triangulator is not consulted by an
                    # explicitly mapped job and therefore must not affect its
                    # reproducibility hash.
                    "native_backend": (
                        None
                        if resolved_strategy == "mapped"
                        else project.native_triangulation_backend
                    ),
                }
            )
            mesh_hash = mesh_semantic_hash(
                mesh,
                model_hash=snapshot.revision.model_hash,
                mesh_input_hash=semantic_input_hash,
                structural_preparation=structural_preparation,
            )
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
                MeshJobResult(
                    mesh=mesh,
                    mesh_hash=mesh_hash,
                    quality=quality,
                    structural_preparation=structural_preparation,
                ),
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
