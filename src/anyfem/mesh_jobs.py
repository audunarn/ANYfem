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
from copy import deepcopy
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from dataclasses import replace
import queue
import threading
import traceback
from typing import Any, Mapping

from .document import ProjectSnapshot, canonical_hash
from .job_process import WorkerProcessError, run_in_worker_process
from .jobs import _resolve_executor
from .mesh_controls import MeshControls, StructuredMeshControls
from .mesh_numbering import MeshNumbering, number_full_mesh

__all__ = [
    "clone_mesh_for_job",
    "MeshJobEvent",
    "MeshJobResult",
    "MeshProgress",
    "MeshSettings",
    "MeshTaskManager",
    "RegionalJobInput",
    "mesh_job_entry",
    "mesh_semantic_hash",
    "run_mesh_job",
]


# Explicit public strategies that share the quad-first pipeline.  The owner's
# MeshingStrategy enum is not consulted for them, so validation, hashing and
# reopen do not depend on which ANYmesher revision is installed.
_QUAD_PIPELINE_STRATEGIES = ("quad_first", "smart_mapped")


def clone_mesh_for_job(mesh: Any) -> Any:
    """Return a detached, solver-ready mesh through its public wire format.

    A current ANYmesher mesh may retain immutable ANYgeometry provenance such
    as ``mappingproxy`` objects.  Those values are intentionally not pickle or
    ``deepcopy`` compatible.  The versioned mesh codec is the supported
    boundary for background jobs and also guarantees that the worker receives
    no mutable objects shared with the live document.
    """

    from anymesher.serialize import mesh_from_dict, mesh_to_dict

    detached = mesh_from_dict(mesh_to_dict(mesh))
    if hasattr(mesh, "_anyfem_mesh_record_id"):
        detached._anyfem_mesh_record_id = mesh._anyfem_mesh_record_id
    return detached


def _exception_diagnostic(error: BaseException) -> dict[str, Any]:
    """Preserve public structured evidence exposed by a meshing error."""

    payload: dict[str, Any] = {
        "type": type(error).__name__,
        "message": str(error),
        "traceback": traceback.format_exc(),
    }
    provider = getattr(error, "to_diagnostic", None)
    if callable(provider):
        try:
            details = provider()
        except BaseException as diagnostic_error:  # noqa: BLE001
            payload["diagnostic_error"] = (
                f"{type(diagnostic_error).__name__}: {diagnostic_error}"
            )
        else:
            if isinstance(details, Mapping):
                payload["details"] = dict(details)
    return payload


@dataclass(frozen=True)
class RegionalJobInput:
    """Frozen inputs of one regional update, transported to the worker.

    ``baseline`` is the captured regional baseline mapping and ``old_mesh`` the
    versioned ``anymesher.serialize.mesh_to_dict`` payload of the mesh being
    updated.  The high-water marks are the largest node/element identifiers
    ever issued, so new entities never reuse a retired identifier.  Both
    mappings are deep-copied on construction and must be treated as read-only.
    """

    baseline: Mapping[str, Any]
    old_mesh: Mapping[str, Any]
    node_high_water: int
    element_high_water: int

    def __post_init__(self) -> None:
        if not isinstance(self.baseline, Mapping):
            raise TypeError("regional baseline must be a mapping")
        if not isinstance(self.old_mesh, Mapping):
            raise TypeError("regional old_mesh must be a mapping")
        for name in ("node_high_water", "element_high_water"):
            value = getattr(self, name)
            if type(value) is not int or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        object.__setattr__(self, "baseline", deepcopy(dict(self.baseline)))
        object.__setattr__(self, "old_mesh", deepcopy(dict(self.old_mesh)))


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
    controls: MeshControls | None = None
    structured_controls: StructuredMeshControls | None = None
    quad_options: object | None = None
    layout_policy: str | None = None
    quad_t3_minimum_angle: float = 15.0
    quad_t3_angle_action: str = "error"
    automation: object | None = None
    # Execution-only: how much parallelism the *submission* asks for.  It is
    # deliberately excluded from ``input_hash`` (a serial and a parallel run of
    # the same mesh inputs must share the reproducibility hash) and never
    # enters the project or the mesh artifact.
    execution_policy: object | None = None
    # Constructor None infers legacy raw tuples; materialized settings always
    # hold an exact bool. An explicit False survives replace/default expansion.
    quality_policy_explicit: bool | None = dataclass_field(default=None, kw_only=True)
    # Execution-only: a regional update request.  Never part of input_hash or of
    # the full-generation semantic/settings hashes.
    regional: RegionalJobInput | None = dataclass_field(
        default=None, kw_only=True, compare=False
    )
    publication_allocation: MeshNumbering | None = dataclass_field(default=None, kw_only=True, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self.regional is not None and not isinstance(self.regional, RegionalJobInput):
            raise TypeError("regional must be a RegionalJobInput")
        if self.publication_allocation is not None and (
            not isinstance(self.publication_allocation, MeshNumbering) or self.regional is not None
        ):
            raise ValueError("a full publication allocation requires an independent full generation")
        explicit = self.quality_policy_explicit
        if explicit is None:
            explicit = bool(self.quality_policy)
        elif type(explicit) is not bool:
            raise TypeError("quality_policy_explicit must be a bool or None")
        object.__setattr__(self, "quality_policy_explicit", explicit)
        if self.execution_policy is not None:
            from .mesh_execution import MeshExecutionPolicy

            if not isinstance(self.execution_policy, MeshExecutionPolicy):
                raise TypeError("execution_policy must be a MeshExecutionPolicy")
        if self.automation is not None:
            try:
                from anymesher import MeshAutomationOptions
            except ImportError as error:
                raise RuntimeError(
                    "automatic mesh recovery requires a compatible ANYmesher installation"
                ) from error
            if not isinstance(self.automation, MeshAutomationOptions):
                raise TypeError("automation must be MeshAutomationOptions")
        if self.controls is not None and not isinstance(self.controls, MeshControls):
            raise TypeError("mesh controls must be MeshControls")
        if self.structured_controls is not None and not isinstance(
            self.structured_controls, StructuredMeshControls
        ):
            raise TypeError(
                "structured mesh controls must be StructuredMeshControls"
            )
        quad_selected = str(self.strategy).strip().lower() in _QUAD_PIPELINE_STRATEGIES
        if quad_selected or (self.quad_t3_minimum_angle != 15.0 or
                             self.quad_t3_angle_action != "error"):
            from anymesher.quad import validate_triangle_angle_rule

            angle, action = validate_triangle_angle_rule(
                self.quad_t3_minimum_angle, self.quad_t3_angle_action,
            )
            object.__setattr__(self, "quad_t3_minimum_angle", angle)
            object.__setattr__(self, "quad_t3_angle_action", action)
            if not quad_selected:
                raise ValueError(
                    "quad triangle angle controls require strategy='quad_first' or 'smart_mapped'"
                )
        if quad_selected:
            from .quad_first import effective_quad_options

            object.__setattr__(self, "quad_options", effective_quad_options(self.quad_options))
        elif self.quad_options is not None:
            raise ValueError("quad_options requires strategy='quad_first' or 'smart_mapped'")
        if self.layout_policy is None:
            object.__setattr__(self, "layout_policy", "adaptive" if quad_selected else "existing")
        if self.layout_policy not in ("existing", "adaptive"):
            raise ValueError("layout_policy must be 'existing' or 'adaptive'")
        if self.layout_policy == "adaptive" and not quad_selected:
            raise ValueError("adaptive layout requires strategy='quad_first' or 'smart_mapped'")
        from anymesher.hybrid import MeshingStrategy
        from anymesher.structured import StructurePreference
        from anymesher.structured import MeshQualityPolicy

        if self.strategy is not None:
            try:
                token = str(self.strategy).strip().lower()
                strategy = (
                    token if token in _QUAD_PIPELINE_STRATEGIES
                    else MeshingStrategy(token).value
                )
            except ValueError as error:
                values = [item.value for item in MeshingStrategy]
                choices = ", ".join(
                    values + [item for item in _QUAD_PIPELINE_STRATEGIES
                              if item not in values]
                )
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
        if not explicit and quality.to_dict() != MeshQualityPolicy.create({}).to_dict():
            # replace() retains the presence bit: an edited, nondefault policy
            # must not be silently discarded because that inherited bit is False.
            object.__setattr__(self, "quality_policy_explicit", True)

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
        controls: MeshControls | None = None,
        structured_controls: StructuredMeshControls | None = None,
        quad_options: object | None = None,
        layout_policy: str | None = None,
        quad_t3_minimum_angle: float = 15.0,
        quad_t3_angle_action: str = "error",
        automation: object | None = None,
        execution_policy: object | None = None,
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
            controls=controls,
            structured_controls=structured_controls,
            quad_options=quad_options,
            layout_policy=layout_policy,
            quad_t3_minimum_angle=quad_t3_minimum_angle,
            quad_t3_angle_action=quad_t3_angle_action,
            automation=automation,
            structure_preference=structure_preference,
            execution_policy=execution_policy,
            quality_policy=tuple(
                sorted((str(key), float(value)) for key, value in (quality_policy or {}).items())
            ),
            quality_policy_explicit=quality_policy is not None,
        )

    @property
    def input_hash(self) -> str:
        payload = {
                "target_size": self.target_size,
                "element_order": self.element_order,
                "overrides": dict(self.overrides),
                "strategy": self.strategy,
                "quad_options": (
                    None if self.quad_options is None else self.quad_options.to_dict()
                ),
                "layout_policy": (
                    self.layout_policy if self.strategy in _QUAD_PIPELINE_STRATEGIES else None
                ),
                "controls": (None if self.controls is None else
                             self.controls.effective_dict(self.strategy)),
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
                "structured_controls": (
                    None
                    if self.strategy not in (None, "auto")
                    or self.structured_controls is None
                    else self.structured_controls.effective_dict()
                ),
            }
        if self.quality_policy_explicit:
            # Explicit final-admission constraints apply to every producer.
            # Omitted policies retain the entire established hash payload.
            payload["quality_policy"] = dict(self.quality_policy)
            payload["quality_policy_explicit"] = True
        if self.strategy in _QUAD_PIPELINE_STRATEGIES and (
            self.quad_t3_minimum_angle != 15.0 or self.quad_t3_angle_action != "error"
        ):
            payload["quad_t3_angle_rule"] = {
                "minimum_angle": self.quad_t3_minimum_angle,
                "action": self.quad_t3_angle_action,
            }
        if self.automation is not None:
            payload["automation"] = self.automation.to_dict()
        return canonical_hash(payload)


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
    # Execution-only route record (route/workers/reason, parallel summary);
    # never part of any hash or the persisted project.
    execution: Mapping[str, Any] = dataclass_field(default_factory=dict)
    # A regional job returns a numbered baseline. A full job's publisher
    # captures the baseline after assigning the project numbers.
    regional_baseline: Mapping[str, Any] | None = None
    # Identifiers carried over unchanged by a regional update; empty otherwise.
    retained_node_ids: tuple[int, ...] = ()
    retained_element_ids: tuple[int, ...] = ()
    mesh_input_hash: str = ""


# Execution-route diagnostics the owner parallel block records beside the
# mesh (see anyfem.mesh_execution: the policy is execution-only and never
# enters the semantic mesh hash).  Unknown parallel keys stay hashed.
_PARALLEL_ROUTE_KEYS = frozenset(
    {
        "used",
        "route",
        "reason",
        "components",
        "separation",
        "merge_reasons",
        "runtime",
    }
)


def _seeding_hash_order(preparation, *, text_keys):
    """Canonicalize the two geometry-ID maps that JSON key serialization changes."""
    source = preparation.get("prepared_boundary_provenance")
    seeding = source.get("prepared_seeding") if isinstance(source, Mapping) else None
    if not isinstance(seeding, Mapping):
        return
    seeding = dict(seeding)
    for name in ("divisions", "classes"):
        if name not in seeding:
            continue
        if not isinstance(seeding[name], Mapping):
            raise ValueError(f"invalid prepared seeding {name} map")
        values = {}
        for key, value in seeding[name].items():
            if type(key) not in (int, str) or str(int(key)) != str(key):
                raise ValueError(f"invalid geometry key in prepared seeding {name}")
            canonical = str(key) if text_keys else int(key)
            if canonical in values:
                raise ValueError(f"duplicate geometry key in prepared seeding {name}")
            values[canonical] = value
        seeding[name] = values
    preparation["prepared_boundary_provenance"] = dict(source, prepared_seeding=seeding)


def mesh_semantic_hash(
    mesh: Any,
    *,
    model_hash: str,
    mesh_input_hash: str,
    structural_preparation: Mapping[str, Any] | None = None,
    _text_seeding_keys: bool = False,
) -> str:
    """Hash solver-affecting mesh semantics, excluding owner bookkeeping."""

    from anymesher.serialize import mesh_to_dict

    mesh_payload = dict(mesh_to_dict(mesh))
    mesh_payload.pop("geometry_model_id", None)
    mesh_payload.pop("geometry_revision", None)
    # ANYmesher 0.4 records its full preparation provenance in the neutral
    # mesh.  That block contains the detached working model UUID and hashes
    # derived from it, so it changes on every otherwise identical mesh job.
    # The canonicalized preparation report below already carries the
    # solver-affecting preparation semantics; keep the mesh portion limited to
    # materialized nodes, elements, associations, and coupling equations.
    mesh_payload.pop("structural_preparation", None)
    hybrid_diagnostics = mesh_payload.get("hybrid_diagnostics")
    if isinstance(hybrid_diagnostics, Mapping):
        hybrid_diagnostics = dict(hybrid_diagnostics)
        hybrid_diagnostics.pop("geometry_model_id", None)
        hybrid_diagnostics.pop("geometry_revision", None)
        hybrid_diagnostics.pop("structural_preparation_hash", None)
        # Candidate counts and phase measurements describe execution, not the
        # materialized mesh or its geometry/quality authority.
        hybrid_diagnostics.pop("runtime", None)
        parallel = hybrid_diagnostics.get("parallel")
        if isinstance(parallel, Mapping):
            # The owner's whole ``parallel`` block is the execution-route
            # record (used, route, reason, components, separation,
            # merge_reasons, runtime).  The execution policy is
            # execution-only and never enters the semantic mesh hash, so a
            # serial and a parallel run of the same mesh must hash
            # identically whether the block is present or absent.  Unknown
            # keys stay hashed; when nothing semantic remains the block is
            # dropped so its presence cannot change the hash either.
            semantic_parallel = {
                key: value
                for key, value in parallel.items()
                if key not in _PARALLEL_ROUTE_KEYS
            }
            if semantic_parallel:
                hybrid_diagnostics["parallel"] = semantic_parallel
            else:
                hybrid_diagnostics.pop("parallel", None)

        def without_runtime_timings(value: Any) -> Any:
            if isinstance(value, Mapping):
                return {
                    key: without_runtime_timings(item)
                    for key, item in value.items()
                    if not str(key).endswith("_seconds")
                }
            if isinstance(value, list):
                return [without_runtime_timings(item) for item in value]
            if isinstance(value, tuple):
                return tuple(without_runtime_timings(item) for item in value)
            return value

        mesh_payload["hybrid_diagnostics"] = without_runtime_timings(
            hybrid_diagnostics
        )
    preparation = dict(structural_preparation or {})
    _seeding_hash_order(preparation, text_keys=_text_seeding_keys)
    preparation.pop("working_model_id", None)
    preparation.pop("working_revision", None)
    preparation.pop("source_revision", None)
    preparation.pop("source_model_id", None)
    qualified_s3 = preparation.get("qualified_s3")
    if isinstance(qualified_s3, Mapping):
        qualified_s3 = dict(qualified_s3)
        authority_model = qualified_s3.get("authority_model")
        if isinstance(authority_model, Mapping):
            authority_model = dict(authority_model)
            # The authority remains the same physical model after exact undo;
            # its transaction revision is provenance rather than FE semantics.
            authority_model.pop("source_revision", None)
            qualified_s3["authority_model"] = authority_model
        preparation["qualified_s3"] = qualified_s3
    structural_closure = preparation.get("structural_closure")
    if isinstance(structural_closure, Mapping):
        structural_closure = dict(structural_closure)
        # Both values are derived from the detached job clone and therefore
        # change on an otherwise identical remesh.
        structural_closure.pop("model_id", None)
        structural_closure.pop("preparation_hash", None)
        preparation["structural_closure"] = structural_closure
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


def matches_mesh_hash(mesh, expected, *, model_hash, mesh_input_hash, structural_preparation):
    inputs = dict(model_hash=model_hash, mesh_input_hash=mesh_input_hash,
                  structural_preparation=structural_preparation)
    if mesh_semantic_hash(mesh, **inputs) == expected:
        return True
    # An older save could first hash the neutral (text-keyed) transport.
    # Only the ordering of these two typed maps may differ on that route.
    origin = (structural_preparation or {}).get("prepared_boundary_provenance")
    return (isinstance(origin, Mapping) and isinstance(origin.get("prepared_seeding"), Mapping)
            and mesh_semantic_hash(mesh, _text_seeding_keys=True, **inputs) == expected)


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

    def __init__(self, *, executor: str | None = None) -> None:
        # "thread" (the library default) or "process"; see anyfem.job_process.
        self.executor = _resolve_executor(executor)
        self._process: Any = None  # the worker process while a "process" mesh runs
        self._tree: Any = None  # the owned worker tree while a contained mesh runs
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
            if self._process is not None and self._process.is_alive():
                # A worker that outlived its bounded cleanup stays registered
                # so shutdown can still stop it.  A new submission must not
                # replace that live registration and lose shutdown reachability.
                raise RuntimeError(
                    "a previous mesh worker is still running; shutdown must "
                    "stop it before a new mesh may start"
                )
            # Freeze the effective default/environment choice before deciding
            # whether the submitted job needs owned process containment.
            from .mesh_execution import resolve_policy

            settings = replace(settings, execution_policy=resolve_policy(settings.execution_policy))
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
            process = self._process
            tree = self._tree
        # A worker process does not wait for a cooperative safe point on exit.
        # The registered owned tree is retired FIRST: it covers the outer
        # worker AND any pool children, so touching the outer process
        # separately is out of order.  The standalone process is terminated
        # only when no tree is registered.
        if tree is not None:
            # Bounded and idempotent, and never an unowned PID or group.
            tree.terminate()
        elif process is not None and process.is_alive():
            process.terminate()
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
            if self.executor == "process":
                result = self._run_in_worker(snapshot, settings, cancellation, progress)
            else:
                serial_settings = self._thread_executor_settings(settings, progress)
                result = run_mesh_job(
                    snapshot.thaw(),
                    snapshot.revision.model_hash,
                    serial_settings,
                    progress,
                    cancellation,
                )
                if serial_settings is not settings:
                    execution = dict(result.execution)
                    execution["route"] = "serial"
                    execution["workers"] = None
                    execution["reason"] = (
                        "the thread executor cannot contain a parallel mesh worker tree"
                    )
                    result = replace(result, execution=execution)
        except BaseException as error:  # noqa: BLE001 - retained as diagnostics
            if _is_cancelled(cancellation):
                self._events.put(
                    MeshJobEvent(job_id, "cancelled", "mesh generation cancelled")
                )
            else:
                diagnostic = (
                    error.remote_diagnostic
                    if isinstance(error, WorkerProcessError) and error.remote_diagnostic
                    else _exception_diagnostic(error)
                )
                self._events.put(
                    MeshJobEvent(job_id, "failed", str(error), diagnostic)
                )
            return
        self._events.put(
            MeshJobEvent(job_id, "completed", "mesh generation complete", result)
        )

    def _thread_executor_settings(
        self,
        settings: MeshSettings,
        progress: Any,
    ) -> MeshSettings:
        """The in-process thread executor cannot own a worker tree: force serial."""

        policy = settings.execution_policy
        if policy is None or not policy.requests_parallel:
            return settings
        progress(
            "execution",
            "serial meshing: the thread executor cannot contain a parallel "
            "mesh worker tree",
            0.15,
        )
        from .mesh_execution import MeshExecutionPolicy

        return replace(settings, execution_policy=MeshExecutionPolicy.off())

    def _run_in_worker(
        self,
        snapshot: ProjectSnapshot,
        settings: MeshSettings,
        cancellation: Any,
        progress: Any,
    ) -> "MeshJobResult":
        """Mesh in a separate process, so meshing cannot take the GUI's interpreter lock.

        See :mod:`anyfem.job_process`.  The mesh comes back through the public codec.
        A submission whose execution policy asks for parallel meshing runs in a
        contained, non-daemonic worker tree (the ANYmesh pool creates children);
        when containment cannot be established the job falls back to an
        ordinary serial worker *before anything is dispatched*, with the reason
        recorded -- unless the manager is closing, in which case nothing is
        dispatched at all, or the refusal carries cleanup failures, in which
        case the job fails rather than spawn any further worker.  A failure
        after execution begins is never retried serially.  Registration is
        published and retracted per spawn attempt, so shutdown always
        observes one consistent worker/tree pair.
        """

        def forward(value: Any) -> None:
            if isinstance(value, MeshProgress):
                progress(value.stage, value.message, value.fraction)

        # The spawn attempt that currently owns the registration slots.
        # Ownership is published and retracted per attempt, so shutdown
        # never observes a stale tree from a refused contained attempt
        # beside a live fallback worker, and never observes empty slots
        # while a worker that outlived its bounded cleanup still runs.
        attempt: list[Any] = []

        def register(process: Any) -> None:
            with self._lock:
                if self._closing:
                    # shutdown() already ran: refuse ownership so no worker
                    # may run (or keep running) after shutdown.  The late
                    # worker is also stopped here, under the same lock: the
                    # caller's teardown is bounded and joins first, so a
                    # bare refusal could let it run a whole mesh first.
                    # The cancelled token reports the job as cancelled.
                    if process.is_alive():
                        process.terminate()
                    raise RuntimeError(
                        "mesh task manager is closing; no mesh worker may start"
                    )
                attempt[:] = [process, None]
                self._process = process

        def register_tree(process: Any, tree: Any) -> None:
            with self._lock:
                if self._closing:
                    # shutdown() ran before the tree was registered here:
                    # refuse the registration so nothing is dispatched after
                    # shutdown.  The caller terminates the tree.
                    raise RuntimeError(
                        "mesh task manager is closing; no parallel mesh tree may "
                        "be registered"
                    )
                if attempt and attempt[0] is process:
                    attempt[1] = tree
                self._tree = tree


        def retract() -> None:
            """Retract the finished attempt's registration, atomically.

            A worker confirmed dead is unregistered, exactly as before.  A
            worker that outlived its bounded cleanup stays registered, with
            its closed tree dropped so shutdown's standalone branch can
            stop the survivor: nothing may keep running after shutdown,
            and no later attempt may inherit a stale tree.
            """

            with self._lock:
                if attempt:
                    process, tree = attempt
                else:
                    process = tree = None
                attempt[:] = []
                if tree is not None and self._tree is tree:
                    self._tree = None
                if (
                    process is not None
                    and self._process is process
                    and not process.is_alive()
                ):
                    self._process = None


        def spawn(worker_settings: Any, contained_worker: bool) -> Any:
            try:
                return self._spawn_worker(
                    snapshot,
                    worker_settings,
                    cancellation,
                    forward,
                    contained_worker,
                    register,
                    register_tree,
                )
            finally:
                retract()

        policy = settings.execution_policy
        contained = False
        if policy is not None and policy.requests_parallel:
            from .mesh_execution import parallel_mesh_api

            if parallel_mesh_api() is not None:
                # Only a route that can actually go parallel needs the
                # contained tree; a missing API resolves serial inside the
                # ordinary daemon worker.
                contained = True
            else:
                progress(
                    "execution",
                    "serial meshing: this ANYmesher installation does not "
                    "provide the parallel meshing API",
                    0.15,
                )
        try:
            transport = spawn(settings, contained)
        except WorkerProcessError as error:
            if not contained or error.remote_type != "ContainmentUnavailable":
                raise
            with self._lock:
                closing = self._closing
            if closing:
                # Containment failed while the application is closing: never
                # dispatch a fallback worker after shutdown.
                raise
            if getattr(error, "cleanup_failures", None):
                # Containment failed AND its worker tree could not be
                # cleaned up: the environment cannot be trusted to run any
                # worker right now, so the serial fallback is refused and
                # the job fails with the original refusal (cleanup details
                # attached).  A pure predispatch refusal carries no cleanup
                # failures and still falls back.
                raise
            # Containment failed at startup, before any pool dispatch: one
            # serial fallback is allowed and the reason is surfaced.
            reason = f"contained worker unavailable: {error}"
            progress("execution", f"serial meshing fallback: {reason}", 0.15)
            from .mesh_execution import MeshExecutionPolicy

            serial_settings = replace(
                settings, execution_policy=MeshExecutionPolicy.off()
            )
            transport = spawn(serial_settings, False)
            execution = dict(transport.execution)
            execution["route"] = "serial"
            execution["workers"] = None
            execution["reason"] = reason
            transport = replace(transport, execution=execution)
        from anymesher.serialize import mesh_from_dict

        return MeshJobResult(
            mesh=mesh_from_dict(transport.mesh),
            mesh_hash=transport.mesh_hash,
            mesh_input_hash=transport.mesh_input_hash,
            quality=transport.quality,
            structural_preparation=transport.structural_preparation,
            execution=transport.execution,
            regional_baseline=transport.regional_baseline,
            retained_node_ids=tuple(transport.retained_node_ids),
            retained_element_ids=tuple(transport.retained_element_ids),
        )

    def _spawn_worker(
        self,
        snapshot: ProjectSnapshot,
        settings: MeshSettings,
        cancellation: Any,
        progress: Any,
        contained: bool,
        register: Any,
        register_tree: Any,
    ) -> Any:
        return run_in_worker_process(
            function=mesh_job_entry,
            kwargs={"settings": settings, "model_hash": snapshot.revision.model_hash},
            snapshot=snapshot,
            project_override=None,
            progress=progress,
            token=cancellation,
            on_process=register,
            containment=contained,
            on_tree=register_tree if contained else None,
        )


def run_mesh_job(
    project: Any,
    model_hash: str,
    settings: "MeshSettings",
    progress: Any,
    cancellation: Any,
    *,
    _regional_stage_check=None,
) -> "MeshJobResult":
    """Generate, check and hash one mesh.

    Shared by the in-process path and the worker process, so both run exactly the
    same meshing.  ``progress(stage, message, fraction)`` reports stages; the
    cancellation token is checked between them.  Raises on failure or cancellation.
    """

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
    resolved_strategy = (
        requested_strategy if requested_strategy in _QUAD_PIPELINE_STRATEGIES
        else MeshingStrategy(requested_strategy).value
    )
    resolved_controls = settings.controls or MeshControls.from_settings(project.native_mesh_settings)
    classify_source = getattr(project, "automatic_source_mesh_candidate", None)
    auto_source = classify_source is not None and classify_source(resolved_strategy, settings.element_order)
    stored_parameters = dict(getattr(project.native_mesh_settings, "parameters", ()))
    inherit_native = auto_source and settings.controls is None and not any(
        key.startswith("native_") for key in stored_parameters
    )
    inherit_structured = auto_source and settings.structured_controls is None and not any(
        key.startswith("structured_") for key in stored_parameters
    )
    resolved_structured_controls = (
        settings.structured_controls
        or StructuredMeshControls.from_settings(
            project.native_mesh_settings
        )
    )
    progress("snapshot", "prepared immutable model snapshot", 0.1)
    cancellation.raise_if_cancelled("mesh generation")
    progress("preparation", "Preparing structural connections", 0.2)
    last_parallel_stage = None

    def check_and_report(stage):
        nonlocal last_parallel_stage
        cancellation.raise_if_cancelled(stage)
        if _regional_stage_check is not None:
            _regional_stage_check(stage)
        # The owner polls cancellation repeatedly while waiting. Forward only
        # changed public component stages so the run monitor remains useful
        # without flooding the GUI thread with identical updates.
        text = str(stage)
        if (text.startswith('structural ') or text == 'cached structural preparation'):
            if text != last_parallel_stage:
                progress('preparation', text, 0.2)
                last_parallel_stage = text
            return
        if text == 'mesh element generation':
            progress('generation', 'generating mesh elements', 0.2)
            last_parallel_stage = text
            return
        if text.startswith("parallel ") and text != last_parallel_stage:
            fraction = 0.2
            words = text.split()
            if len(words) == 5 and words[:2] == ["parallel", "component"] and words[3] == "of":
                try:
                    done, total = int(words[2]), int(words[4])
                except ValueError:
                    pass  # unfamiliar owner stage stays a useful text update
                else:
                    if total > 0:
                        fraction += 0.6 * min(1.0, max(0.0, done / total))
            progress("generation", text, fraction)
            last_parallel_stage = text

    from .native_meshing import NativeMeshingCancellation

    native_token = cancellation if type(cancellation) is NativeMeshingCancellation else None
    regional_result = None
    if settings.regional is not None:
        # Explicit regional update; there is no silent fallback to a full rebuild.
        from .regional_dispatch import generate_regional_update

        regional_result = generate_regional_update(
            project,
            settings,
            settings.regional.baseline,
            settings.regional.old_mesh,
            node_high_water=settings.regional.node_high_water,
            element_high_water=settings.regional.element_high_water,
            cancellation_check=check_and_report,
            **({"mesh_cancellation": native_token} if native_token is not None else {}),
        )
    mesh = regional_result.mesh if regional_result is not None else project.generate_mesh(
        settings.target_size,
        overrides=dict(settings.overrides),
        order=settings.element_order,
        strategy=settings.strategy,
        structure_preference=settings.structure_preference,
        quality_policy=(dict(settings.quality_policy) if settings.quality_policy_explicit else None),
        # An omitted native point-placement choice lets the source owner select
        # its chart algorithm. Project resolves the remaining controls as usual.
        mesh_controls=settings.controls if inherit_native else resolved_controls,
        structured_controls=None if inherit_structured else resolved_structured_controls,
        quad_options=settings.quad_options,
        layout_policy=None if auto_source else settings.layout_policy,
        quad_t3_minimum_angle=(
            settings.quad_t3_minimum_angle
            if settings.strategy in _QUAD_PIPELINE_STRATEGIES else None
        ),
        quad_t3_angle_action=(
            settings.quad_t3_angle_action
            if settings.strategy in _QUAD_PIPELINE_STRATEGIES else None
        ),
        automation=settings.automation,
        cancellation_check=check_and_report,
        **({"_source_cancellation": native_token.geometry_cancellation}
           if native_token is not None and auto_source else {}),
        execution_policy=settings.execution_policy,
    )
    if regional_result is not None:
        execution = {"route": "regional", "workers": None, "reason": "regional update"}
    else:
        execution = dict(getattr(project, "_last_mesh_execution", {}) or {})
    parallel_diagnostics = getattr(mesh, "hybrid_diagnostics", {})
    if isinstance(parallel_diagnostics, Mapping):
        runtime = parallel_diagnostics.get("runtime")
        if isinstance(runtime, Mapping):
            execution["meshing"] = deepcopy(dict(runtime))
        parallel_summary = parallel_diagnostics.get("parallel")
        if isinstance(parallel_summary, Mapping):
            # Deterministic route/component metadata only; the runtime block
            # (counts/timings/estimates) stays execution-only.
            execution["parallel"] = {
                key: value
                for key, value in parallel_summary.items()
                if key != "runtime"
            }
    route = execution.get("route", "serial")
    workers = execution.get("workers")
    reason = execution.get("reason", "")
    progress(
        "execution",
        (
            f"mesh route: {route}"
            + (f" with {workers} worker(s)" if workers else "")
            + (f" ({reason})" if reason else "")
        ),
        0.8,
    )
    cancellation.raise_if_cancelled("mesh quality")
    progress("quality", "checking element quality", 0.85)
    from anymesher import verify_mesh_quality

    if settings.publication_allocation is not None and mesh.hybrid_diagnostics.get("automation", {}).get("status") != "inspection_only":
        # Source-cell receipts belong to this generation and cannot be pickled.
        mesh = number_full_mesh(mesh, settings.publication_allocation, source_geometry=project.geometry)
    quality = verify_mesh_quality(mesh).as_dict()
    cancellation.raise_if_cancelled("mesh hashing")
    progress("hash", "hashing immutable mesh result", 0.94)
    if regional_result is not None:
        structural_preparation = dict(getattr(mesh, "structural_preparation", {}) or {})
    else:
        structural_preparation = dict(project._last_mesh_preparation)
    # Inherited source methods and explicitly authored legacy options can
    # select different algorithms. Preserve that distinction in the input hash.
    resolved_settings_hash = replace(
        settings, strategy=resolved_strategy,
        controls=None if inherit_native else resolved_controls,
        structured_controls=None if inherit_structured else resolved_structured_controls,
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
        model_hash=model_hash,
        mesh_input_hash=semantic_input_hash,
        structural_preparation=structural_preparation,
    )
    regional_baseline = None
    retained_nodes: tuple[int, ...] = ()
    retained_elements: tuple[int, ...] = ()
    if regional_result is not None:
        baseline = regional_result.baseline
        regional_baseline = None if baseline is None else dict(baseline)
        retained_nodes = tuple(sorted(int(i) for i in regional_result.retained_nodes))
        retained_elements = tuple(sorted(int(i) for i in regional_result.retained_elements))
    elif settings.publication_allocation is not None and mesh.hybrid_diagnostics.get("automation", {}).get("status") != "inspection_only":
        from .regional_dispatch import capture_regional_baseline
        regional_baseline = capture_regional_baseline(project, settings, mesh)
    cancellation.raise_if_cancelled("mesh completion")
    progress("complete", "mesh and quality checks complete", 1.0)
    return MeshJobResult(
        mesh=mesh,
        mesh_hash=mesh_hash,
        mesh_input_hash=semantic_input_hash,
        quality=quality,
        structural_preparation=structural_preparation,
        execution=execution,
        regional_baseline=regional_baseline,
        retained_node_ids=retained_nodes,
        retained_element_ids=retained_elements,
    )


@dataclass(frozen=True)
class _MeshTransport:
    """A finished mesh as it leaves the worker process.

    A live mesh holds objects that cannot be pickled (immutable ANYgeometry views,
    local closures).  ANYmesher's public versioned codec is the supported boundary,
    the same one a saved project uses, so the mesh crosses as its codec dictionary.
    """

    mesh: Mapping[str, Any]
    mesh_hash: str
    quality: Mapping[str, Any]
    structural_preparation: Mapping[str, Any]
    execution: Mapping[str, Any] = dataclass_field(default_factory=dict)
    regional_baseline: Mapping[str, Any] | None = None
    retained_node_ids: tuple[int, ...] = ()
    retained_element_ids: tuple[int, ...] = ()
    mesh_input_hash: str = ""


def mesh_job_entry(*, project, progress, cancellation_token, settings, model_hash):
    """The worker-process entry point: mesh, then hand the result over as codec data."""

    from anymesher.serialize import mesh_to_dict

    def staged(stage: str, message: str, fraction: float) -> None:
        progress(MeshProgress(stage, message, float(fraction)))

    result = run_mesh_job(project, model_hash, settings, staged, cancellation_token)
    return _MeshTransport(
        mesh=mesh_to_dict(result.mesh),
        mesh_hash=result.mesh_hash,
        mesh_input_hash=result.mesh_input_hash,
        quality=dict(result.quality),
        structural_preparation=dict(result.structural_preparation),
        execution=dict(result.execution),
        regional_baseline=(
            None if result.regional_baseline is None else dict(result.regional_baseline)
        ),
        retained_node_ids=tuple(result.retained_node_ids),
        retained_element_ids=tuple(result.retained_element_ids),
    )


# Structured evidence a mesher error exposes is computed where the error is raised.
mesh_job_entry.error_diagnostic = _exception_diagnostic

def _cancellation_token():
    from .native_meshing import NativeMeshingCancellation
    return NativeMeshingCancellation()


mesh_job_entry.cancellation_token_factory = _cancellation_token


def _is_cancelled(token: Any) -> bool:
    value = getattr(token, "is_cancelled", None)
    if value is None:
        value = getattr(token, "cancelled", False)
    return bool(value() if callable(value) else value)
