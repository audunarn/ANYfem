"""Desktop entry point for shell and solid submodels.

``SubmodelTask`` is the stateful owner (preview, create, background staging,
cancellation, GUI-thread publication, history).  ``SubmodelDialog`` is its view.

Threading contract (see ``anyfem.submodel_jobs``): every document mutation happens
on the GUI thread.  Workers only receive immutable snapshots; ``stage_submodel_run``
touches no project, and ``publish_submodel_run`` runs from the poll on the GUI
thread that took the snapshot.  Nothing here mutates the parent mesh or results.

Headless call path (what the dialog itself uses)::

    task = submodel_task(window)                      # one SubmodelTask per window
    task.start_preview(SubmodelRequest(kind="solid", job_id=..., case=..., target_size=...,
                                       face_id=..., footprint=..., layers=..., order=1,
                                       pressure_skin="end"))   # 'start' | 'end' | None
    task.wait()
    definition_id = task.create()
    task.start_run(definition_id); task.wait()
    run_id = task.last_run_id
    task.inspect(run_id, "recovery_stress"); task.wait()   # task.inspection["table"]
    task.show_child_mesh(run_id); task.open_parent_result(run_id)

Shutdown (``shutdown_submodel(window)``, also run by the window's close) cancels the
in-flight run and removes whatever it still stages; nothing is published afterwards.
"""
from __future__ import annotations

import dataclasses
import json
import time
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QApplication, QComboBox, QDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
                               QPlainTextEdit, QPushButton, QSpinBox, QTabWidget, QTableWidget,
                               QTableWidgetItem, QVBoxLayout, QWidget, QAbstractItemView)

from ...commands import Command
from ...model.submodels import SourceReference
from ...submodel import SubmodelError
from ...submodel_cut import cut_submodel
from ...submodel_jobs import (add_submodel, discard_staged_run, publish_submodel_run, stage_submodel_run,
                              submodel_run_staleness, submodel_snapshot)
from .table import ResultTable

ROW_LIMIT = 5000
POLL_MS = 30
POLICIES = (("drive_all", "Drive all cut-face DOFs (default)"),
            ("release_normal_rotation", "Release normal rotation (alternative)"))
#: (stored value, label): the skin of the solid that carries the face pressure.  The start
#: skin is the face's -normal side and the end skin its +normal side.
SKINS = ((None, "None (the face carries no pressure)"),
         ("start", "Start skin (face -normal side)"),
         ("end", "End skin (face +normal side)"))
SOLID_FIELDS = ("displacement", "coefficients", "recovery_stress", "recovery_strain", "recovery_weights",
                "recovery_coordinates", "interface_force", "interface_moment")


# ----------------------------------------------------------------------
# pure helpers (no Qt state; unit-testable)
# ----------------------------------------------------------------------
class AddSubmodelDefinition(Command):
    """Register one previewed submodel definition; undo removes only that definition.

    The definition keeps its deterministic id, so redo re-registers the same record.  Stored
    runs, artifacts and every other project record are left untouched by undo.
    """

    label = "add submodel"

    def __init__(self, definition: Any) -> None:
        self.definition = definition
        self._added = False

    def do(self, project: Any) -> Any:
        add_submodel(project, self.definition)  # validates and refuses a duplicate id
        self._added = True
        return self.definition

    def undo(self, project: Any) -> None:
        if self._added and project.submodels.get(self.definition.id) is not None:
            del project.submodels[self.definition.id]
        self._added = False


class CancelToken:
    """Read by ``submodel_jobs._check_cancelled`` through its ``cancelled`` attribute."""

    def __init__(self) -> None:
        self.cancelled = False

    def cancel(self) -> None:
        self.cancelled = True


@dataclasses.dataclass(frozen=True)
class SubmodelRequest:
    kind: str  # "shell" | "solid"
    job_id: str
    case: str
    target_size: float
    name: str = "submodel"
    policy: str = "drive_all"
    lower: Tuple[float, float, float] = (0.0, 0.0, 0.0)
    upper: Tuple[float, float, float] = (0.0, 0.0, 0.0)
    face_id: Optional[int] = None
    footprint: Tuple[Tuple[float, float, float], ...] = ()
    layers: int = 1
    order: int = 1
    pressure_skin: Optional[str] = None  # 'start' | 'end' | None

    def __post_init__(self) -> None:
        if self.pressure_skin not in (None, "start", "end"):
            raise ValueError(f"pressure skin must be 'start', 'end' or None, not {self.pressure_skin!r}")

    @property
    def source(self) -> SourceReference:
        return SourceReference(self.job_id, self.case)


def _number(text: str, what: str) -> float:
    try:
        value = float(str(text).strip())
    except ValueError:
        raise ValueError(f"{what} must be a number, not {str(text).strip()!r}") from None
    if value != value or value in (float("inf"), float("-inf")):
        raise ValueError(f"{what} must be finite")
    return value


def parse_point(text: str, what: str) -> Tuple[float, float, float]:
    parts = str(text).replace(",", " ").split()
    if len(parts) != 3:
        raise ValueError(f"{what} needs three coordinates, found {len(parts)}")
    return tuple(_number(p, what) for p in parts)  # type: ignore[return-value]


def parse_footprint(text: str) -> Tuple[Tuple[float, float, float], ...]:
    lines = [line for line in str(text).splitlines() if line.strip()]
    if len(lines) != 4:
        raise ValueError(f"the footprint needs four corner points, one per line; found {len(lines)}")
    return tuple(parse_point(line, f"footprint point {i + 1}") for i, line in enumerate(lines))


def format_report(report: Any) -> str:
    """Faithful text of a cut report: carried, refused/dropped and skipped data, notes."""

    if report is None:
        return ""
    if dataclasses.is_dataclass(report):
        items = [(f.name, getattr(report, f.name)) for f in dataclasses.fields(report)]
    else:
        items = [(k, v) for k, v in vars(report).items() if not k.startswith("_")]
    lines: List[str] = []
    for name, value in items:
        if name in ("cut", "detail"):
            continue  # in-memory geometry result
        if name == "attached":
            for member in value:
                lines.append(f"Stiffener {member.parent_member} on plate {member.parent_face} "
                             f"continues as child member {member.child_member}.")
            continue
        title = name.replace("_", " ")
        if isinstance(value, Mapping):
            lines.append(f"{title}:" + ("" if value else " none"))
            lines.extend(f"  {k}: {v}" for k, v in value.items())
        elif isinstance(value, (list, tuple, set, frozenset)):
            lines.append(f"{title}:" + ("" if value else " none"))
            lines.extend(f"  - {v}" for v in value)
        else:
            lines.append(f"{title}: {value}")
    return "\n".join(lines)


def describe_error(error: BaseException) -> str:
    code = getattr(error, "code", None)
    if code and str(error).startswith(f"{code}:"):
        return str(error)
    return f"{code}: {error}" if code else f"{type(error).__name__}: {error}"


def source_choices(project: Any) -> List[Tuple[str, str, str, Optional[str]]]:
    """(job id, label, case, refusal) for every completed job; refusal is None when usable."""

    out = []
    for job in project.jobs.values():
        status = str(getattr(job.status, "value", job.status))
        if status != "completed":
            continue
        analysis = project.analyses.get(job.analysis_id)
        case = getattr(analysis, "target_id", "") if analysis is not None else ""
        refusal = None
        if job.partial:
            refusal = "the job is partial"
        elif analysis is None:
            refusal = "the job's analysis definition is missing"
        elif analysis.type != "linear_static" or analysis.target_kind != "load_case":
            refusal = f"{analysis.type}/{analysis.target_kind} is not a linear static load-case solve"
        out.append((job.id, f"{job.name} · case {case or '?'} · {status}", case, refusal))
    return out


def inspect_run(store: Any, project_artifacts: Mapping[str, Any], run: Any,
                field_key: Optional[str] = None) -> Dict[str, Any]:
    """Read a run's child artifacts (worker thread).  Returns plain data."""

    import numpy as np

    child = run.child or {}
    out: Dict[str, Any] = {"run_id": run.id, "diagnostics": dict(run.diagnostics or {}),
                           # ``SubmodelRun.identity`` is a hash string, not a mapping.
                           "identity": str(run.identity or ""), "record": run_record(run),
                           "fields": [], "mesh": {}, "table": None, "problems": []}
    out["child_kind"] = str(child.get("child_kind", "shell"))
    try:
        mesh_ref = project_artifacts.get(child["mesh_artifact"]["id"])
        result_ref = project_artifacts.get(child["result_artifact"]["id"])
    except (KeyError, TypeError) as error:
        out["problems"].append(f"the run record has no child artifact descriptor ({error!r})")
        return out
    if mesh_ref is None or result_ref is None:
        out["problems"].append("a child artifact is no longer registered")
        return out
    if out["child_kind"] == "solid":
        return _inspect_solid(store, mesh_ref, result_ref, field_key, out)
    try:
        mesh = store.read_mesh(mesh_ref)
        counts = {"nodes": len(mesh.nodes)}
        for family in ("quads", "tris", "beams", "couplings", "tets", "tet4", "tet10", "hexes"):
            value = getattr(mesh, family, None)
            if value is not None and hasattr(value, "__len__"):
                counts[family] = len(value)
        out["mesh"] = counts
        out["child_mesh"] = mesh
    except Exception as error:  # noqa: BLE001 - shown to the user, not swallowed
        out["problems"].append(f"child mesh: {describe_error(error)}")
    dataset = None
    try:
        dataset = store.open_result(result_ref)  # validates the sidecar
        keys = list(dataset.field_keys)
        for key in keys:
            result_field = dataset.field(key)
            desc = result_field.descriptor
            values = np.asarray(result_field.read(), dtype=float)
            finite = values[np.isfinite(values)]
            out["fields"].append({
                "key": key, "location": desc.location, "basis": desc.basis, "unit": desc.unit,
                "components": tuple(desc.components), "shape": tuple(values.shape),
                "min": float(finite.min()) if finite.size else None,
                "max": float(finite.max()) if finite.size else None,
            })
        chosen = field_key if field_key in keys else (keys[0] if keys else None)
        if chosen is not None:
            out["table"] = _field_table(dataset, chosen)
    except Exception as error:  # noqa: BLE001
        out["problems"].append(f"child result: {describe_error(error)}")
    finally:
        if dataset is not None:  # the ANYpost reader holds the HDF5 file open (locks it on Windows)
            try:
                dataset.close()
            except Exception:  # noqa: BLE001 - closing must not mask the inspection
                pass
    return out


def run_record(run: Any) -> Dict[str, str]:
    """The stored run record as typed strings: ids, hashes and provenance, never a mapping of the identity."""

    child, source = getattr(run, "child", None) or {}, getattr(run, "source", None) or {}
    versions = getattr(run, "producer_versions", None) or {}
    record = {
        "definition_id": getattr(run, "definition_id", ""), "definition_hash": getattr(run, "definition_hash", ""),
        "child_case": getattr(run, "child_case", ""), "created_utc": getattr(run, "created_utc", ""),
        "source_job": source.get("job_id", ""), "source_case": source.get("case", ""),
        "source_mesh_selection": source.get("mesh_selection", ""),
        "child_model_hash": child.get("model_hash", ""), "child_loading_hash": child.get("loading_hash", ""),
        "child_mesh_fingerprint": child.get("mesh_fingerprint", ""),
        "producer_job": json.dumps(versions.get("job", {}), sort_keys=True, default=str),
        "producer_run": json.dumps(versions.get("run", {}), sort_keys=True, default=str),
    }
    return {key: str(value) for key, value in record.items()}


def _cell(value: float) -> str:
    return f"{value:.9g}"


def _range(values: Any) -> Tuple[Optional[float], Optional[float]]:
    import numpy as np

    finite = np.asarray(values, dtype=float)
    finite = finite[np.isfinite(finite)]
    return (float(finite.min()), float(finite.max())) if finite.size else (None, None)


def _inspect_solid(store: Any, mesh_ref: Any, result_ref: Any, field_key: Optional[str],
                   out: Dict[str, Any]) -> Dict[str, Any]:
    """A solid run's stored mesh and result, read by the solid codecs (the shell codec refuses them)."""

    import numpy as np

    from ...io.solid_submodel_artifact import read_solid_mesh, read_solid_result

    try:
        mesh = read_solid_mesh(store, mesh_ref)
        out["mesh"] = {"nodes": len(mesh.node_ids), "cells": len(mesh.cell_ids),
                       "boundary_facets": len(mesh.boundary_ids)}
        out["child_mesh"] = mesh
    except Exception as error:  # noqa: BLE001 - shown to the user, not swallowed
        out["problems"].append(f"child mesh: {describe_error(error)}")
    try:
        result = read_solid_result(store, result_ref)
        declared = result["solid_fields"]
        for key in SOLID_FIELDS:
            if key == "displacement":
                spec, values = {"location": "node", "unit": "m", "components": ["ux", "uy", "uz"]}, result[key]
            else:
                spec, values = declared[key], result[key]
            low, high = _range(values)
            out["fields"].append({"key": key, "location": spec["location"], "basis": "global",
                                  "unit": spec["unit"], "components": tuple(spec["components"]),
                                  "shape": tuple(np.asarray(values).shape), "min": low, "max": high})
        keys = [f["key"] for f in out["fields"]]
        out["table"] = solid_field_table(result, field_key if field_key in keys else keys[0])
    except Exception as error:  # noqa: BLE001
        out["problems"].append(f"child result: {describe_error(error)}")
    return out


def solid_field_table(result: Mapping[str, Any], key: str) -> Dict[str, Any]:
    """Rows of one stored solid quantity, labelled by the stored ids.

    Integration-point quantities carry the external cell id, the point index and the
    stored physical point location; nodal and coefficient quantities carry their own
    ids/coordinates.  ``result`` is the mapping returned by ``read_solid_result``.
    """

    import numpy as np

    declared = result["solid_fields"]
    rows: List[List[Any]] = []
    if key == "displacement":
        ids = np.asarray(result["displacement_node_ids"]).reshape(-1)
        values = np.asarray(result["displacement"], dtype=float)
        if values.shape != (len(ids), 3):
            raise ValueError("the displacement does not match its node ids")
        headers, unit, location, total = ["Node", "ux", "uy", "uz"], "m", "node", len(ids)
        rows = [[int(ids[i]), *(_cell(v) for v in values[i])] for i in range(min(total, ROW_LIMIT))]
    elif key == "coefficients":
        values = np.asarray(result["coefficients"], dtype=float).reshape(-1)
        where = np.asarray(result["coefficient_coordinates"], dtype=float).reshape(len(values), 3)
        axis = np.asarray(result["coefficient_components"]).reshape(-1)
        headers, unit, location, total = ["DOF", "x", "y", "z", "axis", "value"], "m", "dof", len(values)
        rows = [[i, *(_cell(v) for v in where[i]), int(axis[i]), _cell(values[i])]
                for i in range(min(total, ROW_LIMIT))]
    elif key in ("interface_force", "interface_moment"):
        values = np.asarray(result[key], dtype=float).reshape(3)
        headers, unit, location, total = ["Quantity", "x", "y", "z"], declared[key]["unit"], "interface", 1
        rows = [["driven cut groups", *(_cell(v) for v in values)]]
    elif key in ("recovery_stress", "recovery_strain", "recovery_weights", "recovery_coordinates"):
        points = np.asarray(result["recovery_coordinates"], dtype=float)
        cells = np.asarray(result["recovery_cell_ids"]).reshape(-1)
        values = np.asarray(result[key], dtype=float)
        if points.ndim != 3 or cells.shape != (points.shape[0],) or values.shape[:2] != points.shape[:2]:
            raise ValueError("the recovery arrays do not share one (cell, point) layout")
        count, per_cell = points.shape[:2]
        flat = values.reshape(count * per_cell, -1)
        components = list(declared[key]["components"])
        if flat.shape[1] != len(components):
            raise ValueError(f"{key} has {flat.shape[1]} components, declared {len(components)}")
        located = key != "recovery_coordinates"  # the coordinates table is itself x y z
        headers = ["Cell", "Point", *(["x", "y", "z"] if located else []), *components]
        unit, location, total = declared[key]["unit"], "integration_point", count * per_cell
        for index in range(min(total, ROW_LIMIT)):
            cell, point = divmod(index, per_cell)
            rows.append([int(cells[cell]), point,
                         *((_cell(v) for v in points[cell, point]) if located else ()),
                         *(_cell(v) for v in flat[index])])
    else:
        raise ValueError(f"{key!r} is not a stored solid quantity")
    return {"key": key, "headers": headers, "rows": rows, "total": int(total), "frames": 1, "unit": unit,
            "location": location, "truncated": total > ROW_LIMIT}


def _field_table(dataset: Any, key: str) -> Dict[str, Any]:
    import numpy as np

    result_field = dataset.field(key)
    desc = result_field.descriptor
    values = np.asarray(result_field.read(), dtype=float)  # (frames, locations, components)
    if values.ndim == 1:  # one unframed vector of locations
        values = values[None, :, None]
    elif values.ndim == 2:
        values = values[:, :, None]
    elif values.ndim > 3:  # tensor-valued: flatten the component axes
        values = values.reshape(values.shape[0], values.shape[1], -1)
    ids = None
    for name in (f"{key}_node_ids", f"{key}_element_ids", f"{key}_ids"):
        try:
            ids = [int(v) for v in np.asarray(dataset.table(name)).reshape(-1)]
            break
        except Exception:  # noqa: BLE001 - the table naming is per field; fall back to indices
            continue
    if ids is not None and len(ids) != values.shape[1]:
        ids = None
    label = {"node": "Node", "element": "Element"}.get(str(desc.location), str(desc.location).title())
    components = list(desc.components) or [f"c{i}" for i in range(values.shape[2])]
    rows = []
    for index in range(min(values.shape[1], ROW_LIMIT)):
        rows.append([ids[index] if ids is not None else index, *(f"{v:.9g}" for v in values[0, index])])
    return {"key": key, "headers": [f"{label}{'' if ids is not None else ' index'}", *components],
            "rows": rows, "total": int(values.shape[1]), "frames": int(values.shape[0]), "unit": desc.unit,
            "location": str(desc.location), "truncated": values.shape[1] > ROW_LIMIT}


def solid_boundary_scene(mesh: Any) -> Any:
    """Scene of a stored solid mesh: its outward boundary triangles, element edges shown."""

    import numpy as np

    from ..scene import COLOR_MESH_EDGE, COLOR_MESH_FILL, FacePatch, Scene

    coordinates = np.asarray(mesh["coordinates"], dtype=float)
    facets = np.asarray(mesh["boundary_facets"], dtype=int)
    node_ids = np.asarray(mesh["node_ids"], dtype=int)
    if not len(facets):
        raise ValueError("the solid mesh has no boundary facets")
    patch = FacePatch(None, [coordinates[f] for f in facets], [COLOR_MESH_FILL] * len(facets),
                      outline=COLOR_MESH_EDGE,
                      polygon_node_ids=[tuple(int(n) for n in node_ids[f]) for f in facets])
    return Scene(faces=[patch], show_element_edges=True)


def _design() -> Any:
    """The shared design operations (``anyfem.submodel_design``), imported on first use."""

    try:
        from ... import submodel_design
    except ImportError as error:
        raise ValueError(f"Submodel design operations are unavailable: {error}") from error
    return submodel_design


def submodel_task(app: Any) -> "SubmodelTask":
    """The one task of a window (created on first use); the dialog and headless callers share it."""

    task = getattr(app, "_submodel_task", None)
    if task is None:
        task = SubmodelTask(app)
        app._submodel_task = task
    return task


def shutdown_submodel(app: Any) -> None:
    """Stop the window's submodel task and hide its dialog; safe to call more than once."""

    task = getattr(app, "_submodel_task", None)
    if task is not None:
        task.shutdown()
    dialog = getattr(app, "_submodel_dialog", None)
    if dialog is not None:
        dialog.hide()


# ----------------------------------------------------------------------
# stateful task
# ----------------------------------------------------------------------
class SubmodelTask:
    """State machine behind the dialog; everything slow runs on one worker thread."""

    IDLE, PREVIEWING, RUNNING, PUBLISHING = "idle", "previewing", "running", "publishing"

    def __init__(self, app: Any) -> None:
        self.app = app
        self.state = self.IDLE
        self.message = ""
        self.preview: Optional[Tuple[str, Any]] = None  # (revision id, CutSubmodel)
        self.history: List[Dict[str, Any]] = []
        self.inspection: Optional[Dict[str, Any]] = None
        self.last_run_id: Optional[str] = None
        self.listeners: List[Callable[[], None]] = []
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="anyfem-submodel")
        self._pending: List[Tuple[Any, Callable[[Any], None]]] = []
        self._staging: List[Future] = []  # run futures whose staged files nobody has taken over yet
        self._scheduled = False
        self._shut = False
        self._token: Optional[CancelToken] = None
        self._history_epoch = 0
        self._inspect_epoch = 0
        self._preview_epoch = 0
        self.preview_request: Optional[SubmodelRequest] = None  # the request the current preview answers

    # -- plumbing ------------------------------------------------------
    def add_listener(self, callback: Callable[[], None]) -> None:
        if callback not in self.listeners:
            self.listeners.append(callback)

    def remove_listener(self, callback: Callable[[], None]) -> None:
        try:
            self.listeners.remove(callback)
        except ValueError:
            pass

    def _notify(self) -> None:
        for callback in list(self.listeners):
            callback()

    def _say(self, text: str, *, error: bool = False) -> None:
        self.message = text
        try:
            self.app.set_status(text, error=error) if error else self.app.set_status(text)
        except Exception:  # noqa: BLE001 - a partial status adapter must not break the task
            pass
        self._notify()

    def _require_open(self) -> None:
        if self._shut:
            raise ValueError("The submodel task is shut down")

    def _submit(self, function: Callable[..., Any], callback: Callable[[Any], None], *args: Any,
                **kwargs: Any) -> Future:
        self._require_open()
        future = self._executor.submit(function, *args, **kwargs)
        self._pending.append((future, callback))
        self._schedule()
        return future

    def _schedule(self) -> None:
        if self._scheduled or not self._pending or self._shut or getattr(self.app, "_closing", False):
            return
        self._scheduled = True
        self.app.scheduler.call_later(POLL_MS, self._tick)

    def _tick(self) -> None:
        self._scheduled = False
        self.pump()
        self._schedule()

    def pump(self) -> bool:
        """Deliver finished background work on the calling (GUI) thread; True while work remains."""

        for item in [p for p in self._pending if p[0].done()]:
            self._pending.remove(item)
            future, callback = item
            try:
                callback(future)
            except Exception as error:  # noqa: BLE001
                self.state = self.IDLE
                self._say(describe_error(error), error=True)
        return bool(self._pending)

    @property
    def busy(self) -> bool:
        return self.state != self.IDLE

    def wait(self, timeout: float = 120.0) -> None:
        """Run the event loop and deliver finished work until the task is idle (GUI thread)."""

        deadline = time.monotonic() + timeout
        while True:
            application = QApplication.instance()
            if application is not None:
                application.processEvents()
            if not self.pump() and not self.busy:
                return
            if time.monotonic() > deadline:
                raise TimeoutError(f"submodel work did not finish in {timeout:g} s; "
                                   f"state={self.state} message={self.message!r}")
            time.sleep(0.005)

    @staticmethod
    def _discard_orphan(future: Future) -> None:
        """Remove the files of a staged run that nobody will publish (any thread; only unlinks files)."""

        if future.cancelled():
            return
        try:
            staged = future.result()
        except BaseException:  # noqa: BLE001 - the stager already removed its own partial files
            return
        if getattr(staged, "published", True) is False:
            try:
                discard_staged_run(staged)
            except SubmodelError:
                pass

    def shutdown(self) -> None:
        """Cancel, stop accepting work and never leave staged files behind.

        A stage still running finishes at its next cancellation point (or completes);
        its result is then discarded instead of published.  Idempotent.
        """

        if self._shut:
            return
        self._shut = True
        if self._token is not None:
            self._token.cancel()
        staging, self._staging = self._staging, []
        self._pending = []
        self._executor.shutdown(wait=False, cancel_futures=True)
        for future in staging:
            future.add_done_callback(self._discard_orphan)  # runs now when already finished

    # -- environment ---------------------------------------------------
    def store(self) -> Any:
        for name in ("artifact_store", "_artifact_store"):
            try:
                found = getattr(self.app, name, None)
                found = found() if callable(found) else found
            except Exception:  # noqa: BLE001
                found = None
            if found is not None and hasattr(found, "resolve"):
                return found
        if not getattr(self.app, "path", None):
            raise ValueError("Save the project first: submodel artifacts are stored beside the project file")
        from pathlib import Path

        from ...io.artifacts import ArtifactStore

        return ArtifactStore(Path(self.app.path))

    def mesh_record_for(self, job_id: Optional[str]) -> Optional[str]:
        """The mesh record to bind a source job to, or None to let the job's own mesh hash decide.

        A published (numbered) project has exactly one active mesh.  Otherwise the
        window's active record is used only when it is the mesh the job solved, so a
        historic job on an earlier mesh still resolves through its own hash.
        """

        project = self.app.project
        if project.mesh_numbering.generation:
            return project.mesh_numbering.active_mesh_id
        job = project.jobs.get(job_id) if job_id else None
        current = getattr(self.app, "mesh_record_id", None)
        record = project.mesh_records.get(current) if current else None
        if job is not None and record is not None and record.mesh_hash == job.mesh_hash:
            return record.id
        return None

    def active_mesh_id(self) -> Optional[str]:
        """The active mesh record id (numbered projects) or the window's current record."""

        numbering = self.app.project.mesh_numbering
        if numbering.generation:
            return numbering.active_mesh_id
        return getattr(self.app, "mesh_record_id", None) or None

    def _require_writable(self) -> None:
        if self.app.session.read_only:
            raise ValueError("The document is read-only")

    # -- preview / create ----------------------------------------------
    def start_preview(self, request: SubmodelRequest) -> None:
        """Read-only cut on a snapshot; nothing is registered."""

        self._require_open()
        if self.busy:
            raise ValueError(f"A submodel operation is already {self.state}")
        snapshot = self.app.session.snapshot()
        revision = self.app.session.revision.id
        self.preview = None
        self.preview_request = None
        self._preview_epoch += 1
        epoch = self._preview_epoch
        self.state = self.PREVIEWING
        self._say("Previewing the cut…")
        self._submit(self._cut, lambda future: self._previewed(future, revision, epoch, request), snapshot, request)

    def invalidate_preview(self, reason: str = "") -> None:
        """Drop a ready preview and discard one still being computed (the form it answered changed)."""

        self._preview_epoch += 1
        had = self.preview is not None
        self.preview = None
        self.preview_request = None
        if had or self.state == self.PREVIEWING:
            if reason:
                self.message = reason
            self._notify()

    @staticmethod
    def _cut(snapshot: Any, request: SubmodelRequest) -> Any:
        if request.kind == "solid":
            try:
                from anyfem.solid_submodel import cut_solid_submodel
            except ImportError as error:
                raise SubmodelError("SOLID_UNAVAILABLE", f"the solid submodel backend is not installed: {error}") from error
            return cut_solid_submodel(
                snapshot, source=request.source, face_id=request.face_id, footprint=request.footprint,
                target_size=request.target_size, layers=request.layers, order=request.order,
                pressure_skin=request.pressure_skin, name=request.name)
        from anygeometry.volume_cut import BoxVolume

        volume = BoxVolume(lower=list(request.lower), upper=list(request.upper), transform=None)
        return cut_submodel(snapshot, volume, source=request.source, policy=request.policy,
                            target_size=request.target_size, name=request.name)

    def _previewed(self, future: Any, revision: str, epoch: Optional[int] = None,
                   request: Optional[SubmodelRequest] = None) -> None:
        self.state = self.IDLE
        if epoch is not None and epoch != self._preview_epoch:
            self.preview = None  # the form changed or the preview was cancelled meanwhile: never publish it
            self.preview_request = None
            self._say("Preview discarded; preview again")
            return
        try:
            cut = future.result()
        except Exception as error:  # noqa: BLE001 - refusal text is the product
            self.preview = None
            self.preview_request = None
            self._say(f"Cut refused — {describe_error(error)}", error=True)
            return
        self.preview = (revision, cut)
        self.preview_request = request
        self._say(f"Cut ready: {cut.definition.name!r}. Create it to keep it in the project.")

    def preview_text(self) -> str:
        if self.preview is None:
            return ""
        return format_report(self.preview[1].report)

    def create(self) -> str:
        """Register the previewed definition in one session transaction (undoable)."""

        self._require_writable()
        if self.busy:
            raise ValueError(f"A submodel operation is already {self.state}")
        if self.preview is None:
            raise ValueError("Preview the cut first")
        revision, cut = self.preview
        if self.app.session.revision.id != revision:
            self.preview = None
            raise ValueError("The document changed since the preview; preview again")
        definition = cut.definition
        # One validated, reversible command (one revision): undo removes exactly this definition.
        self.app.session.execute(AddSubmodelDefinition(definition), solver_affecting=False)
        self.preview = None
        self.preview_request = None
        self._say(f"Submodel {definition.name!r} created")
        return definition.id

    def replace(self, definition_id: str, expected_record: Optional[Mapping[str, Any]] = None) -> str:
        """Replace one solid definition by the previewed one (same id and source; runs are untouched).

        ``expected_record`` (the ``to_dict()`` of the definition when editing began) refuses the
        replacement when the stored definition was changed meanwhile.
        """

        self._require_writable()
        if self.busy:
            raise ValueError(f"A submodel operation is already {self.state}")
        if self.preview is None:
            raise ValueError("Preview the replacement first")
        revision, cut = self.preview
        if self.app.session.revision.id != revision:
            self.preview = None
            self.preview_request = None
            raise ValueError("The document changed since the preview; preview again")
        previous = self.app.project.submodels.get(definition_id)
        if previous is None:
            raise ValueError("The solid submodel to replace no longer exists")
        if expected_record is not None and previous.to_dict() != expected_record:
            self.preview = None
            self.preview_request = None
            raise ValueError("The solid submodel changed since you began editing it; cancel the edit and reopen it")
        if getattr(previous, "child_kind", "shell") != "solid" or getattr(cut.definition, "child_kind", "shell") != "solid":
            raise ValueError("Only a solid submodel can be replaced by a solid preview")
        if previous.source != cut.definition.source:
            raise ValueError("A replacement keeps the original source job and case")
        replacement = dataclasses.replace(cut.definition, id=previous.id)
        replacement.validate()
        self.app.session.execute(_design().ReplaceSubmodelDefinition(previous, replacement), solver_affecting=False)
        self.preview = None
        self.preview_request = None
        self._say(f"Solid submodel {replacement.name!r} replaced; earlier runs are kept")
        return previous.id

    def remove(self, definition_id: str) -> None:
        """Delete one solid definition (undoable); its stored runs stay in the project history."""

        self._require_writable()
        if self.busy:
            raise ValueError(f"A submodel operation is already {self.state}")
        previous = self.app.project.submodels.get(definition_id)
        if previous is None:
            raise ValueError("That submodel is not in the project")
        if getattr(previous, "child_kind", "shell") != "solid":
            raise ValueError("Only a solid submodel is deleted here")
        self.app.session.execute(_design().RemoveSubmodelDefinition(previous), solver_affecting=False)
        self.preview = None
        self.preview_request = None
        self._say(f"Solid submodel {previous.name!r} deleted; its stored runs are kept")

    def definitions(self) -> List[Tuple[str, str]]:
        out = []
        for d in self.app.project.submodels.values():
            if getattr(d, "child_kind", "shell") == "solid":
                detail = f"solid · face {d.face_id} · P{d.order} · {d.layers} layers · skin {d.pressure_skin or 'none'}"
            else:
                detail = f"shell · {d.policy}"
            out.append((d.id, f"{d.name} · job {d.source.job_id[:8]} · case {d.source.case} · "
                              f"{detail} · size {d.target_size:g}"))
        return out

    # -- run -----------------------------------------------------------
    def start_run(self, definition_id: str) -> None:
        self._require_open()
        self._require_writable()
        if self.busy:
            raise ValueError(f"A submodel operation is already {self.state}")
        definition = self.app.project.submodels.get(definition_id)
        if definition is None:
            raise ValueError("Choose a created submodel to run")
        store = self.store()
        store.ensure()
        mesh_id = self.mesh_record_for(definition.source.job_id)
        snap = submodel_snapshot(self.app.session)  # owner thread; refused inside a transaction
        self._token = token = CancelToken()
        self.state = self.RUNNING
        self.last_run_id = None
        self._say("Staging submodel run in the background…")
        future = self._submit(stage_submodel_run, lambda done: self._staged(done, token), snap, definition_id, store,
                              mesh_record_id=mesh_id, cancellation_token=token)
        self._staging.append(future)

    def cancel(self) -> bool:
        """Request cancellation; effective at the next stage boundary or before publication."""

        if self.state == self.PREVIEWING:
            self._preview_epoch += 1  # the cut cannot be interrupted; its result is discarded on arrival
            self._say("Cancelling the preview…")
            return True
        if self.state == self.RUNNING and self._token is not None:
            self._token.cancel()
            self._say("Cancelling…")
            return True
        return False

    def _staged(self, future: Any, token: CancelToken) -> None:
        if future in self._staging:
            self._staging.remove(future)  # delivered: the staged files are handled below
        try:
            staged = future.result()
        except Exception as error:  # noqa: BLE001
            self.state = self.IDLE
            if token.cancelled or getattr(error, "code", "") == "SUBMODEL_CANCELLED":
                self._say("Submodel run cancelled; nothing was published")
            else:
                self._say(f"Submodel run failed — {describe_error(error)}", error=True)
            return
        if token.cancelled:
            discard_staged_run(staged)
            self.state = self.IDLE
            self._say("Submodel run cancelled; staged files were discarded")
            return
        self.state = self.PUBLISHING
        try:
            result = publish_submodel_run(self.app.session, staged)  # GUI thread = snapshot thread
        except Exception as error:  # noqa: BLE001
            try:
                discard_staged_run(staged, self.app.project)
            except SubmodelError:
                pass
            self.state = self.IDLE
            self._say(f"Publication refused — {describe_error(error)}", error=True)
            return
        self.state = self.IDLE
        self.last_run_id = result.run.id
        note = f" (a listener raised: {result.notification_error})" if result.notification_error else ""
        self._say(f"Submodel run {result.run.id[:8]} published{note}")
        self.refresh_history()

    # -- history -------------------------------------------------------
    def refresh_history(self) -> None:
        """Staleness of every stored run (hashes files, so off the GUI thread)."""

        if self._shut:
            return
        self._history_epoch += 1
        epoch = self._history_epoch
        runs = list(self.app.project.submodel_runs.values())
        try:
            store, snapshot = self.store(), self.app.session.snapshot()
        except ValueError as error:
            self.history = [{"run": r, "reasons": (f"STORE_UNAVAILABLE: {error}",)} for r in runs]
            self._notify()
            return
        mesh_ids = {run.id: self.mesh_record_for(run.source.get("job_id")) for run in runs}

        def work() -> List[Dict[str, Any]]:
            return [{"run": run, "reasons": tuple(submodel_run_staleness(snapshot, run, store,
                                                                         mesh_record_id=mesh_ids[run.id]))}
                    for run in runs]

        def done(future: Any) -> None:
            if epoch != self._history_epoch:
                return
            try:
                self.history = future.result()
            except Exception as error:  # noqa: BLE001
                self.history = [{"run": r, "reasons": (f"CHECK_FAILED: {describe_error(error)}",)} for r in runs]
            self._notify()

        self._submit(work, done)

    def inspect(self, run_id: str, field_key: Optional[str] = None) -> None:
        run = self.app.project.submodel_runs.get(run_id)
        if run is None:
            raise ValueError("That run is not in the project")
        self._require_open()
        self._inspect_epoch += 1
        epoch = self._inspect_epoch
        store, artifacts = self.store(), dict(self.app.project.artifacts)

        def done(future: Any) -> None:
            if epoch != self._inspect_epoch:
                return
            try:
                self.inspection = future.result()
            except Exception as error:  # noqa: BLE001
                self.inspection = {"run_id": run_id, "problems": [describe_error(error)], "fields": [],
                                   "mesh": {}, "table": None, "diagnostics": {}, "identity": "", "record": {}}
            self._notify()

        self._submit(inspect_run, done, store, artifacts, run, field_key)

    def open_parent_result(self, run_id: str) -> str:
        """Show the source job's own result through the existing Results workspace."""

        run = self.app.project.submodel_runs.get(run_id)
        if run is None:
            raise ValueError("That run is not in the project")
        job_id = run.source["job_id"]
        job = self.app.project.jobs.get(job_id)
        if job is None:
            raise ValueError("The source job no longer exists")
        self._bind_parent_result(job_id, job)
        self.app.panels["Results"].activate_job(job_id)
        if self.app.active_job_id != job_id:
            raise ValueError(f"The parent result of job {job_id} could not be opened")
        return job_id

    def _bind_parent_result(self, job_id: str, job: Any) -> None:
        """Make sure the job's own persisted result is registered (as normal project open does).

        Only the job's recorded ``result_artifact_id`` is ever used, through the same deferred
        ``ArtifactStore.open_result`` the project open path calls; whatever solution happens to be
        current in the window is never substituted for the historical parent result.
        """

        if job_id in self.app.result_datasets:
            return
        artifact = self.app.project.artifacts.get(getattr(job, "result_artifact_id", None) or "")
        if artifact is None:
            raise ValueError(f"The source job {job_id} retains no registered result artifact "
                             "(its parent result is unavailable)")
        try:
            dataset = self.store().open_result(artifact, defer=True)
        except (OSError, ValueError) as error:
            raise ValueError(f"The parent result artifact of job {job_id} is unavailable: {error}") from error
        self.app.result_datasets = dict(self.app.result_datasets, **{job_id: dataset})

    def show_child_mesh(self, run_id: str) -> None:
        """Draw the run's real child mesh topology in the viewport (display only)."""

        info = self.inspection
        if info is None or info.get("run_id") != run_id or info.get("child_mesh") is None:
            raise ValueError("Inspect the run first")
        run = self.app.project.submodel_runs[run_id]
        if info.get("child_kind") == "solid":
            # The solid child has no project: draw its stored boundary triangles.
            try:
                scene = solid_boundary_scene(info["child_mesh"])
            except Exception as error:  # noqa: BLE001
                raise ValueError(f"The viewport cannot draw this solid mesh ({describe_error(error)}); "
                                 "use the field table") from error
            self.app.viewport.show(scene)
            return
        definition = self.app.project.submodels.get(run.definition_id)
        from ...presentation.scene import build_mesh_scene

        # The stored mesh belongs to the definition the run answered.  When that definition was edited or
        # deleted, its current child (geometry, sections) would not match the mesh: draw the mesh alone.
        if definition is not None and self._definition_matches(definition, run):
            owner = definition.child
        else:
            from ...model.project import Project

            owner = Project(name="submodel result")
        try:
            scene = build_mesh_scene(owner, info["child_mesh"], show_beam_sections=False, show_nodes=False)
        except Exception as error:  # noqa: BLE001
            raise ValueError(f"The viewport cannot draw this child mesh ({describe_error(error)}); "
                             "use the field table") from error
        self.app.viewport.show(scene)

    @staticmethod
    def _definition_matches(definition: Any, run: Any) -> bool:
        """True when ``definition`` is still exactly the one the run recorded (by its definition hash)."""

        from ...model.submodels import definition_hash

        try:
            return definition_hash(definition.to_dict()) == run.definition_hash
        except Exception:  # noqa: BLE001 - an unhashable definition cannot vouch for the stored mesh
            return False

    def restore_view(self) -> None:
        self.app.refresh_views()


# ----------------------------------------------------------------------
# dialog
# ----------------------------------------------------------------------
class SubmodelDialog(QDialog):
    def __init__(self, app: Any, task: Optional[SubmodelTask] = None) -> None:
        super().__init__(app)
        self.app = app
        self.task = task or submodel_task(app)
        self.setWindowTitle("Submodel")
        self.setModal(False)
        self.resize(820, 700)
        layout = QVBoxLayout(self)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs)
        self.status = QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self._build_define()
        self._build_run()
        self._build_history()
        self.task.add_listener(self.sync)
        self.reload()

    # -- define --------------------------------------------------------
    def _build_define(self) -> None:
        page = QWidget()
        outer = QVBoxLayout(page)
        form = QFormLayout()
        outer.addLayout(form)
        self.source = QComboBox()
        form.addRow("Source job", self.source)
        self.case = QLabel("-")
        form.addRow("Load case", self.case)
        self.kind = QComboBox()
        self.kind.addItem("Shell (box cut)", "shell")
        self.kind.addItem("Solid (face footprint)", "solid")
        form.addRow("Model type", self.kind)
        self.name = QLineEdit("submodel")
        form.addRow("Name", self.name)
        self.box_min = QLineEdit("0 0 0")
        self.box_max = QLineEdit("1 1 1")
        form.addRow("Box min (x y z)", self.box_min)
        form.addRow("Box max (x y z)", self.box_max)
        self.face_id = QLineEdit()
        self.face_id.setPlaceholderText("Parent face id carrying the footprint")
        form.addRow("Solid face id", self.face_id)
        self.footprint = QPlainTextEdit()
        self.footprint.setPlaceholderText("Four 3D points on the face, one per line: x y z")
        self.footprint.setMaximumHeight(80)
        form.addRow("Oriented footprint", self.footprint)
        self.target_size = QLineEdit("0.1")
        form.addRow("Target size (m)", self.target_size)
        self.layers = QSpinBox()
        self.layers.setRange(1, 1000)
        self.layers.setValue(1)
        form.addRow("Layers", self.layers)
        self.order = QComboBox()
        self.order.addItem("P1 (linear)", 1)
        self.order.addItem("P2 (quadratic)", 2)
        form.addRow("Element order", self.order)
        self.pressure_skin = QComboBox()
        for value, label in SKINS:
            self.pressure_skin.addItem(label, value)
        self.pressure_skin.setToolTip("The skin that carries the face's uniform pressure. Required when the face is "
                                      "loaded, refused when it is not. Positive pressure acts against that skin's "
                                      "outward normal.")
        form.addRow("Pressure skin", self.pressure_skin)
        self.policy = QComboBox()
        for token, label in POLICIES:
            self.policy.addItem(label, token)
        form.addRow("Shell cut-face policy", self.policy)
        buttons = QHBoxLayout()
        outer.addLayout(buttons)
        self.preview_button = QPushButton("Preview cut")
        self.create_button = QPushButton("Create submodel")
        self.preview_button.clicked.connect(self.app.guarded(self.preview))
        self.create_button.clicked.connect(self.app.guarded(self.create))
        buttons.addWidget(self.preview_button)
        buttons.addWidget(self.create_button)
        self.report = QPlainTextEdit()
        self.report.setReadOnly(True)
        self.report.setPlaceholderText("The cut report (carried, dropped, skipped, refused) appears here.")
        outer.addWidget(self.report)
        self.source.currentIndexChanged.connect(self._source_changed)
        self.kind.currentIndexChanged.connect(self._kind_changed)
        self.tabs.addTab(page, "Define")

    def _kind_changed(self, *_: Any) -> None:
        solid = self.kind.currentData() == "solid"
        for widget in (self.face_id, self.footprint, self.layers, self.order, self.pressure_skin):
            widget.setEnabled(solid)
        for widget in (self.box_min, self.box_max, self.policy):
            widget.setEnabled(not solid)
        if not solid:
            self.order.setCurrentIndex(0)
        self.order.setToolTip("Shell submodels are P1 only in this version" if not solid else "")

    def _source_changed(self, *_: Any) -> None:
        self.case.setText(self.source.currentData(Qt.UserRole + 1) or "-")

    def request(self) -> SubmodelRequest:
        job_id = self.source.currentData()
        if not job_id:
            raise ValueError("Choose a completed source job")
        case = self.source.currentData(Qt.UserRole + 1)
        size = _number(self.target_size.text(), "Target size")
        if size <= 0:
            raise ValueError("Target size must be positive")
        common = dict(job_id=job_id, case=case, target_size=size, name=self.name.text().strip() or "submodel")
        if self.kind.currentData() == "solid":
            try:
                face = int(self.face_id.text().strip())
            except ValueError:
                raise ValueError("Solid face id must be an integer") from None
            return SubmodelRequest(kind="solid", face_id=face, footprint=parse_footprint(self.footprint.toPlainText()),
                                   layers=self.layers.value(), order=int(self.order.currentData()),
                                   pressure_skin=self.pressure_skin.currentData(), **common)
        lower, upper = parse_point(self.box_min.text(), "Box min"), parse_point(self.box_max.text(), "Box max")
        if not all(a < b for a, b in zip(lower, upper)):
            raise ValueError("Box min must be below box max on every axis")
        return SubmodelRequest(kind="shell", policy=self.policy.currentData(), lower=lower, upper=upper, **common)

    def preview(self) -> None:
        self.report.clear()
        self.task.start_preview(self.request())

    def create(self) -> None:
        definition_id = self.task.create()
        self.report.appendPlainText(f"\nCreated submodel {definition_id}")
        self.reload()
        index = self.definition_list.findData(definition_id)
        if index >= 0:
            self.definition_list.setCurrentIndex(index)
        self.tabs.setCurrentIndex(1)

    # -- run -----------------------------------------------------------
    def _build_run(self) -> None:
        page = QWidget()
        layout = QVBoxLayout(page)
        self.definition_list = QComboBox()
        layout.addWidget(QLabel("Created submodels"))
        layout.addWidget(self.definition_list)
        row = QHBoxLayout()
        layout.addLayout(row)
        self.run_button = QPushButton("Run in background")
        self.cancel_button = QPushButton("Cancel run")
        self.run_button.clicked.connect(self.app.guarded(self.run))
        self.cancel_button.clicked.connect(self.app.guarded(self.task.cancel))
        row.addWidget(self.run_button)
        row.addWidget(self.cancel_button)
        self.run_state = QLabel()
        self.run_state.setWordWrap(True)
        layout.addWidget(self.run_state)
        layout.addStretch()
        self.tabs.addTab(page, "Run")

    def run(self) -> None:
        definition_id = self.definition_list.currentData()
        if not definition_id:
            raise ValueError("Create a submodel first")
        self.task.start_run(definition_id)

    # -- history -------------------------------------------------------
    def _build_history(self) -> None:
        page = QWidget()
        layout = QVBoxLayout(page)
        self.runs = QTableWidget(0, 6)
        self.runs.setHorizontalHeaderLabels(["Run", "Submodel", "Type", "Created", "Source job", "Status"])
        self.runs.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.runs.setSelectionMode(QAbstractItemView.SingleSelection)
        self.runs.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.runs.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.runs)
        row = QHBoxLayout()
        layout.addLayout(row)
        self.view_side = QComboBox()
        self.view_side.addItem("Child (submodel)", "child")
        self.view_side.addItem("Parent (source job)", "parent")
        row.addWidget(self.view_side)
        self.field = QComboBox()
        row.addWidget(self.field, 1)
        self.show_mesh_button = QPushButton("Show child mesh")
        self.restore_button = QPushButton("Restore main view")
        self.refresh_button = QPushButton("Refresh status")
        for button in (self.show_mesh_button, self.restore_button, self.refresh_button):
            row.addWidget(button)
        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        self.details.setMaximumHeight(200)
        layout.addWidget(self.details)
        self.table = ResultTable()
        layout.addWidget(self.table)
        self.runs.itemSelectionChanged.connect(self._select_run)
        self.view_side.currentIndexChanged.connect(self._select_run)
        self.field.activated.connect(self._field_chosen)
        self.show_mesh_button.clicked.connect(self.app.guarded(lambda: self.task.show_child_mesh(self.selected_run())))
        self.restore_button.clicked.connect(self.app.guarded(self.task.restore_view))
        self.refresh_button.clicked.connect(self.app.guarded(self.task.refresh_history))
        self.tabs.addTab(page, "History")

    def selected_run(self) -> str:
        rows = self.runs.selectionModel().selectedRows()
        if not rows:
            raise ValueError("Select a run")
        return self.runs.item(rows[0].row(), 0).data(Qt.UserRole)

    def _select_run(self) -> None:
        try:
            run_id = self.selected_run()
        except ValueError:
            return
        if self.view_side.currentData() == "parent":
            try:
                job = self.task.open_parent_result(run_id)
                self.details.setPlainText(f"Parent result of job {job} opened in the Results workspace.")
            except Exception as error:  # noqa: BLE001
                self.details.setPlainText(f"Parent result unavailable: {describe_error(error)}")
            return
        self.task.inspect(run_id)

    def _field_chosen(self, *_: Any) -> None:
        try:
            self.task.inspect(self.selected_run(), self.field.currentText())
        except ValueError:
            pass

    # -- sync ----------------------------------------------------------
    def reload(self) -> None:
        project = self.app.project
        current = self.source.currentData()
        self.source.blockSignals(True)
        self.source.clear()
        for job_id, label, case, refusal in source_choices(project):
            self.source.addItem(label, job_id)
            index = self.source.count() - 1
            self.source.setItemData(index, case, Qt.UserRole + 1)
            if refusal:
                self.source.model().item(index).setEnabled(False)
                self.source.setItemData(index, refusal, Qt.ToolTipRole)
        if current is not None and self.source.findData(current) >= 0:
            self.source.setCurrentIndex(self.source.findData(current))
        else:  # first usable job
            usable = next((i for i in range(self.source.count()) if self.source.model().item(i).isEnabled()), -1)
            self.source.setCurrentIndex(usable)
        self.source.blockSignals(False)
        self._source_changed()
        self._kind_changed()
        chosen = self.definition_list.currentData()
        self.definition_list.clear()
        for definition_id, label in self.task.definitions():
            self.definition_list.addItem(label, definition_id)
        if chosen is not None and self.definition_list.findData(chosen) >= 0:
            self.definition_list.setCurrentIndex(self.definition_list.findData(chosen))
        self.task.refresh_history()
        self.sync()

    def sync(self) -> None:
        task = self.task
        self.status.setText(task.message)
        self.run_state.setText(f"State: {task.state}" + (f" · last run {task.last_run_id}" if task.last_run_id else ""))
        read_only = bool(getattr(self.app.session, "read_only", False))
        self.preview_button.setEnabled(not task.busy)
        self.create_button.setEnabled(task.preview is not None and not task.busy and not read_only)
        self.run_button.setEnabled(not task.busy and self.definition_list.count() > 0 and not read_only)
        self.cancel_button.setEnabled(task.state in (task.RUNNING, task.PREVIEWING))
        self.report.setPlainText(task.preview_text()) if task.preview is not None else None
        self._fill_history()
        self._fill_inspection()

    def _fill_history(self) -> None:
        try:
            keep = self.selected_run()
        except ValueError:
            keep = None
        self.runs.blockSignals(True)
        self.runs.setRowCount(len(self.task.history))
        for row, entry in enumerate(self.task.history):
            run = entry["run"]
            definition = self.app.project.submodels.get(run.definition_id)
            reasons = entry["reasons"]
            values = [run.id[:8], definition.name if definition else "(definition deleted)",
                      str(run.child.get("child_kind", "shell")), run.created_utc,
                      run.source.get("job_id", "?")[:8], "current" if not reasons else "stale: " + ", ".join(reasons)]
            for column, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                if column == 0:
                    item.setData(Qt.UserRole, run.id)
                self.runs.setItem(row, column, item)
            if run.id == keep:
                self.runs.selectRow(row)
        self.runs.blockSignals(False)
        self.runs.resizeColumnsToContents()

    def _fill_inspection(self) -> None:
        info = self.task.inspection
        if info is None or self.view_side.currentData() != "child":
            return
        lines = [f"Run {info['run_id']}", f"Identity: {info.get('identity') or 'unavailable'}",
                 f"Child mesh: {info.get('mesh') or 'unavailable'}"]
        lines += [f"  {key}: {value}" for key, value in (info.get("record") or {}).items()]
        lines += [f"PROBLEM: {p}" for p in info.get("problems", ())]
        lines.append("Diagnostics:\n" + json.dumps(info.get("diagnostics", {}), indent=2, default=str))
        lines.append("Fields:")
        lines += [f"  {f['key']} [{f['location']}, {f['unit']}] components {list(f['components'])} "
                  f"shape {f['shape']} min {f['min']} max {f['max']}" for f in info.get("fields", ())]
        self.details.setPlainText("\n".join(lines))
        keys = [f["key"] for f in info.get("fields", ())]
        if keys != [self.field.itemText(i) for i in range(self.field.count())]:
            self.field.blockSignals(True)
            self.field.clear()
            self.field.addItems(keys)
            self.field.blockSignals(False)
        table = info.get("table")
        if table is not None:
            if table["key"] in keys:
                self.field.setCurrentText(table["key"])
            self.table.show_rows(table["headers"], table["rows"])
            if table["truncated"]:
                self.details.appendPlainText(f"Table shows the first {ROW_LIMIT} of {table['total']} rows.")
        else:
            self.table.show_rows([], [])

    def closeEvent(self, event: Any) -> None:
        self.task.cancel()
        super().closeEvent(event)


def open_submodel_dialog(app: Any) -> SubmodelDialog:
    """The workbench action: one reusable dialog per window."""

    dialog = getattr(app, "_submodel_dialog", None)
    if dialog is None:
        dialog = SubmodelDialog(app)
        app._submodel_dialog = dialog
        app.destroyed.connect(dialog.task.shutdown)
    else:
        dialog.reload()
    dialog.show()
    dialog.raise_()
    dialog.activateWindow()
    return dialog
