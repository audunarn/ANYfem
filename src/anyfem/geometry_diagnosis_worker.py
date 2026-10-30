"""Snapshot-only diagnosis and atomic report export, independent of Qt."""
from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import multiprocessing
from copy import deepcopy
from dataclasses import dataclass


def _clone_project(project):
    # Geometry supplies its own independently mutable copy. Substituting it
    # during deepcopy keeps all project definitions/attachments detached and
    # avoids copying private read-only kernel stores through Python's codec.
    geometry = project.geometry.clone(include_features=True, preserve_identity=True)
    return deepcopy(project, {id(project.geometry): geometry})


@dataclass(frozen=True)
class GeometryDiagnosisSnapshot:
    """Detached application input; worker packing happens outside the GUI thread."""
    project: object

    def thaw(self):
        return _clone_project(self.project)

    def for_worker(self):
        from anygeometry import GeometryModel
        from anygeometry.model_transport import capture_model_transfer
        geometry = self.project.geometry
        transfer = capture_model_transfer(geometry)
        # Preserve project records without sending a second full GeometryModel
        # through the general job pickler's persisted-schema reducer.
        placeholder = GeometryModel()
        metadata = deepcopy(self.project, {id(geometry): placeholder})
        return GeometryDiagnosisTransport(metadata, transfer)


@dataclass(frozen=True)
class GeometryDiagnosisTransport:
    """Trusted local computation payload, never file or repair authority."""
    project: object
    geometry_transfer: object

    def thaw(self):
        from anygeometry.model_transport import restore_model_transfer
        timings = {}
        geometry = restore_model_transfer(self.geometry_transfer, timings=timings)
        project = deepcopy(self.project, {id(self.project.geometry): geometry})
        project._diagnosis_restore_timings = timings
        return project


def snapshot_project_for_diagnosis(project):
    """Capture without project_to_dict's save-time assignment normalization."""
    return GeometryDiagnosisSnapshot(_clone_project(project))


def _interactive_process_priority():
    """Observe the scheduling policy selected by this computation's runner.

    The general solver runner deliberately lowers worker priority. Full geometry
    diagnosis opts into inherited normal scheduling before worker bootstrap.
    Observing here confirms the effective setting without changing this process
    or a direct headless caller. Other jobs retain their background default.
    """
    if multiprocessing.parent_process() is None:
        return {"requested": "normal", "applied": False, "reason": "not a worker process"}
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        native = ctypes.WinDLL("kernel32", use_last_error=True)
        native.GetCurrentProcess.restype = wintypes.HANDLE
        native.GetPriorityClass.argtypes = (wintypes.HANDLE,)
        native.GetPriorityClass.restype = wintypes.DWORD
        process = native.GetCurrentProcess()
        actual = int(native.GetPriorityClass(process))
        return {"requested": "normal", "applied": actual == 0x20,
                "actual_class": actual, "actual": "normal" if actual == 0x20 else "other"}
    return {"requested": "normal", "applied": False,
            "actual_nice": os.getpriority(os.PRIO_PROCESS, 0) if hasattr(os, "getpriority") else None,
            "reason": "preserved platform worker scheduling"}


def diagnose_snapshot_project(*, project, options=None, progress=None, cancellation_token=None, submitted_at=None):
    """Importable entry point for the established application worker process."""
    from .geometry_cleaner import diagnose_project
    from time import perf_counter
    from dataclasses import replace
    entered = perf_counter()
    priority = _interactive_process_priority()
    report = diagnose_project(
        project, options,
        cancelled=(lambda: cancellation_token.is_cancelled) if cancellation_token is not None else None,
        progress=(lambda phase, fraction: progress({
            "quality": "Checking faces, edges and coordinates",
            "feature_intent": "Checking geometry features",
            "strict_audit": "Checking topology and intersections",
            "near_gaps": "Checking gaps and connections",
            "project_attachments": "Checking project references",
            "finished": "Preparing report",
        }.get(phase, phase.replace('_', ' '))))
                 if progress is not None else None,
    )
    if submitted_at is not None:
        report = replace(report, timings=report.timings + (("process_input_seconds", entered - submitted_at),))
    restored = getattr(project, "_diagnosis_restore_timings", {})
    if restored:
        report = replace(report, timings=report.timings + tuple(("process_restore_" + key, value)
                                                               for key, value in restored.items()))
    report = replace(report, source=dict(report.source or {}, process_priority=priority))
    return report


def write_diagnosis_report(report, path, *, document=None, cancelled=None):
    """Export every finding; publish only a successfully written complete file.

    An incomplete diagnosis is exportable and retains its explicit incomplete
    status. 'Complete file' refers only to atomic file publication.
    """
    target = Path(path)
    if target.suffix.lower() not in (".json", ".md", ".txt"):
        raise ValueError("Choose a .json, .md or .txt report file.")
    def check_cancelled():
        if cancelled is not None and cancelled():
            raise InterruptedError("Report export cancelled; no file was replaced.")
    check_cancelled()
    if target.suffix.lower() == ".json":
        content = report.to_dict()
        if document is not None:
            content = dict(content, document=dict(document))
        text = json.dumps(content, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    else:
        text = report.to_markdown()
        if document is not None:
            details = "\n".join(f"- {key}: {str(value).replace(chr(10), ' ')}" for key, value in document.items())
            text = "# Document snapshot\n\n" + details + "\n\n" + text
    check_cancelled()
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n",
                                         dir=target.parent, prefix="." + target.name + ".",
                                         suffix=".tmp", delete=False) as output:
            temporary = Path(output.name)
            output.write(text)
        check_cancelled()
        os.replace(temporary, target)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return target
