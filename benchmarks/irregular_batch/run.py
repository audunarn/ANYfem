"""Frozen small-barge irregular-wave batch benchmark (supervised check actions).

Actions (``--action``): prepare, baseline10/50/200a/b/c, candidate10/50/200a/b/c,
reference, profile, workflow.  ``baseline*`` and ``candidate*`` run the identical
measurement; the label only records which source tree the parent claims is
installed, and the source SHA-256 values are recorded next to every result.

Scope: benchmark pressures only (32-component seed-0 spectral mapped history on
the 5 m barge section).  No BEM, hydro qualification or full 60 m solve.  Timings
are wall times of the stages named in the result; stages that cannot be separated
without a profiler are reported as unavailable, never estimated.
"""

from __future__ import annotations

import runpy
import sys
from pathlib import Path

ROOT = Path(r"C:/Github/ANYfem")
# Bootstrap the source runtime exactly as the application launcher does.  The
# spawned worker re-imports this module as ``__mp_main__`` and repeats it, which
# gives the child the same sys.path.
runpy.run_path(str(ROOT / "run_gui.py"), run_name="anyfem_benchmark_bootstrap")

import argparse
import cProfile
import hashlib
import io
import json
import os
import pstats
import threading
import time
from contextlib import contextmanager
from unittest import mock

import numpy as np

N_FRAMES_FULL = 200
FRAME_DT = 0.1
COMPONENTS = 32
SEED = 0
MESH_SIZE = 1.25
BARGE = dict(length=5, breadth=5, depth=2.5, bulkheads=(2.5,), stiffener_spacing=1.25,
             target_size=1.25)
WATCHDOG_SECONDS = 80.0
#: Compressed-versus-uncompressed threshold only (``max(10 * 1e-6, 1e-8)``).
REGRESSION_LIMIT = 1.0e-5
#: Candidate versus the unchanged-source baseline: the existing tight
#: implementation-regression tolerance (relative), not the compression threshold.
BASELINE_REGRESSION_LIMIT = 1.0e-10
SOURCES = ("src/anyfem/result_transport.py", "src/anyfem/job_process.py",
           "src/anyfem/solve/run.py", "src/anyfem/solve/build.py",
           "benchmarks/irregular_batch/run.py")

RESULT: dict = {}


# ----------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------
def peak_working_set_bytes():
    """Process-lifetime peak working set (Windows), or None when unavailable."""
    try:
        import ctypes
        from ctypes import wintypes

        class Counters(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD)] + [
                (name, ctypes.c_size_t) for name in (
                    "PeakWorkingSetSize", "WorkingSetSize", "QuotaPeakPagedPoolUsage",
                    "QuotaPagedPoolUsage", "QuotaPeakNonPagedPoolUsage",
                    "QuotaNonPagedPoolUsage", "PagefileUsage", "PeakPagefileUsage")]

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        psapi = ctypes.WinDLL("psapi", use_last_error=True)
        kernel.GetCurrentProcess.restype = wintypes.HANDLE
        psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters),
                                               wintypes.DWORD]
        counters = Counters()
        counters.cb = ctypes.sizeof(counters)
        if not psapi.GetProcessMemoryInfo(kernel.GetCurrentProcess(), ctypes.byref(counters),
                                          counters.cb):
            return None
        return int(counters.PeakWorkingSetSize)
    except Exception:  # noqa: BLE001 - measurement only
        return None


def runtime_identity() -> dict:
    import anyfem, anysolver, anyloads
    info = {
        "python": sys.version, "executable": sys.executable,
        "cpu_count": os.cpu_count(),
        "thread_environment": {key: os.environ.get(key) for key in (
            "OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS",
            "NUMEXPR_NUM_THREADS", "ANYSOLVER_THREADS")},
        "numpy": np.__version__,
        "origins": {"anyfem": anyfem.__file__, "anysolver": anysolver.__file__,
                    "anyloads": anyloads.__file__},
        "source_sha256": {rel: hashlib.sha256((ROOT / rel).read_bytes()).hexdigest()
                          for rel in SOURCES if (ROOT / rel).exists()},
    }
    try:
        import scipy
        info["scipy"] = scipy.__version__
    except Exception:  # noqa: BLE001
        info["scipy"] = None
    try:
        from threadpoolctl import threadpool_info
        info["blas_threadpools"] = [
            {key: item.get(key) for key in ("internal_api", "num_threads", "version")}
            for item in threadpool_info()]
    except Exception as error:  # noqa: BLE001
        info["blas_threadpools"] = f"unavailable: {type(error).__name__}"
    return info


def write_result(evidence: Path | None, name: str = "bench.json") -> None:
    if evidence is None:
        return
    evidence.mkdir(parents=True, exist_ok=True)
    temporary = evidence / (name + ".tmp")
    temporary.write_text(json.dumps(RESULT, indent=2, default=str), encoding="utf-8")
    os.replace(temporary, evidence / name)


def start_watchdog(evidence: Path | None) -> None:
    """Record partial results and exit non-zero shortly before the runner's kill."""

    def fire() -> None:
        RESULT["status"] = "timed_out_partial"
        RESULT["watchdog_seconds"] = WATCHDOG_SECONDS
        write_result(evidence)
        print(json.dumps({"type": "timeout", "partial": True}), flush=True)
        os._exit(2)

    timer = threading.Timer(WATCHDOG_SECONDS, fire)
    timer.daemon = True
    timer.start()


@contextmanager
def timed(stages: dict, key: str):
    started = time.perf_counter()
    try:
        yield
    finally:
        stages[key] = time.perf_counter() - started


class Accumulator:
    def __init__(self):
        self.seconds = 0.0
        self.calls = 0
        self.shapes = []

    def as_dict(self):
        return {"calls": self.calls, "seconds": self.seconds, "result_shapes": self.shapes}


def wrapped(owner, name, accumulator, record_shape=False):
    original = getattr(owner, name)

    def wrapper(*args, **kwargs):
        started = time.perf_counter()
        try:
            result = original(*args, **kwargs)
            if record_shape:
                accumulator.shapes.append(list(np.asarray(result[0]).shape))
            return result
        finally:
            accumulator.seconds += time.perf_counter() - started
            accumulator.calls += 1

    return mock.patch.object(owner, name, wrapper)


# ----------------------------------------------------------------------
# frozen inputs
# ----------------------------------------------------------------------
def frame_times(n: int) -> list[float]:
    return [round(FRAME_DT * index, 10) for index in range(n)]


def barge_project():
    """The deterministic small barge with exterior wet surfaces (no mesh yet)."""
    from anygeometry.entities import EntityRef
    from anyfem import commands
    from anyfem.application import examples

    project = examples.floating_barge_section(**BARGE)
    stack = commands.CommandStack(project)
    geometry = project.geometry
    half = BARGE["breadth"] / 2.0
    wet = {"Bottom": (2, 0.0), "Port side": (1, -half), "Starboard side": (1, half)}
    for name, (axis, coordinate) in wet.items():
        for identifier, face in geometry.faces.items():
            corners = [geometry.vertex_position(geometry.edges[item.edge].start)
                       for item in face.loop]
            if all(corner[axis] == coordinate for corner in corners):
                stack.run(commands.AddWetSurface(
                    EntityRef("face", identifier), name=f"{name} (wet)",
                    fluid_side="away_from_point",
                    interior_point=(BARGE["length"] / 2.0, 0.0, BARGE["depth"] / 2.0)))
    return project


def mesh_hash(mesh) -> str:
    from anymesher.serialize import mesh_to_dict
    from anyfem.document import canonical_hash
    return canonical_hash(mesh_to_dict(mesh))


def spectral_history(mesh, ids, n_frames: int):
    """Seed-0, 32-component spectral mapped history over the given wet elements."""
    from anyfem.application.irregular_wave_input import IrregularWaveInput
    from anyloads.spectral_pressure_history import SpectralMappedPressureHistory

    config = IrregularWaveInput(component_count=COMPONENTS, duration=19.9, dt=FRAME_DT,
                                seed=SEED)
    realization = config.realization()
    omegas = tuple(float(item.omega) for item in realization.components)
    rng = np.random.default_rng(SEED)
    phases = rng.uniform(0.0, 2.0 * np.pi, size=COMPONENTS)
    centroids = np.array([np.mean([mesh.nodes[node] for node in mesh.corners_of(int(e))],
                                  axis=0) for e in ids])
    real = np.empty((COMPONENTS, len(ids)))
    imag = np.empty_like(real)
    for index, omega in enumerate(omegas):
        wavenumber = omega ** 2 / 9.81
        amplitude = 1500.0 / (1.0 + index) ** 0.5
        angle = wavenumber * centroids[:, 0] + phases[index]
        decay = np.exp(-wavenumber * centroids[:, 2])
        real[index] = amplitude * np.cos(angle) * decay
        imag[index] = amplitude * np.sin(angle) * decay
    return SpectralMappedPressureHistory(
        tuple(frame_times(n_frames)), tuple(int(e) for e in ids), omegas, real, imag, 0.0,
        mesh_hash(mesh), "b" * 64, json.dumps(realization.to_dict()))


def attach_series(project, mesh, n_frames: int):
    from anyfem.model import WetPressure
    from anyfem.solve import build as builder

    owner_map, _ = builder._wet_elements(project, mesh, list(project.wet_surfaces))
    ids = sorted(owner_map)
    history = spectral_history(mesh, ids, n_frames)
    series = project.add_load_series("Sea", history.times)
    series.wet_pressures.append(WetPressure(history=history, mapping_key="element",
                                            name="irregular wave"))
    return [case.name for case in project.series_children("Sea")], len(ids)


def frozen_paths(shared: Path) -> dict:
    root = shared / "frozen"
    return {"root": root, "mesh": root / "barge-mesh.json", "meta": root / "frozen-inputs.json"}


def prepare(shared: Path) -> dict:
    from anymesher.serialize import mesh_from_dict, mesh_to_dict
    from anyfem.solve.build import build_fe_model

    paths = frozen_paths(shared)
    paths["root"].mkdir(parents=True, exist_ok=True)
    stages: dict = {}
    with timed(stages, "project_seconds"):
        project = barge_project()
    with timed(stages, "mesh_generation_seconds"):
        mesh = project.generate_mesh(MESH_SIZE)
    payload = json.dumps(mesh_to_dict(mesh), sort_keys=True, separators=(",", ":"),
                         default=lambda item: item.tolist() if hasattr(item, "tolist") else item)
    paths["mesh"].write_text(payload, encoding="utf-8")
    original_hash = mesh_hash(mesh)
    reloaded = mesh_from_dict(json.loads(paths["mesh"].read_text(encoding="utf-8")))
    reloaded_hash = mesh_hash(reloaded)
    admitted = {"identical_hash": original_hash == reloaded_hash}
    # Admission: a full-length history must attach and the model must assemble.
    names, wet = attach_series(project, reloaded, N_FRAMES_FULL)
    built = build_fe_model(project, reloaded, load_case=None, require_loads=False)
    meta = {
        "mesh_sha256_of_file": hashlib.sha256(paths["mesh"].read_bytes()).hexdigest(),
        "mesh_content_hash": reloaded_hash, "round_trip": admitted,
        "nodes": len(reloaded.nodes), "quads": len(reloaded.quads), "tris": len(reloaded.tris),
        "wet_elements": wet, "total_dofs": built.fe_model.mesh.dof_manager.total_dofs,
        "frames": N_FRAMES_FULL, "dt": FRAME_DT, "components": COMPONENTS, "seed": SEED,
        "barge": {k: list(v) if isinstance(v, tuple) else v for k, v in BARGE.items()},
        "series_cases": len(names), "stage_seconds": stages,
    }
    paths["meta"].write_text(json.dumps(meta, indent=2), encoding="utf-8")
    if not admitted["identical_hash"]:
        raise RuntimeError("frozen mesh did not round-trip to the same content hash")
    return meta


def load_inputs(shared: Path, n_frames: int, stages: dict):
    """Fresh project + the persisted mesh; returns (project, mesh, case names)."""
    from anymesher.serialize import mesh_from_dict

    paths = frozen_paths(shared)
    meta = json.loads(paths["meta"].read_text(encoding="utf-8"))
    with timed(stages, "mesh_reload_seconds"):
        raw = paths["mesh"].read_bytes()
        if hashlib.sha256(raw).hexdigest() != meta["mesh_sha256_of_file"]:
            raise RuntimeError("frozen mesh file changed since prepare")
        mesh = mesh_from_dict(json.loads(raw.decode("utf-8")))
        if mesh_hash(mesh) != meta["mesh_content_hash"]:
            raise RuntimeError("frozen mesh content hash differs from prepare")
    with timed(stages, "project_and_history_seconds"):
        project = barge_project()
        names, _ = attach_series(project, mesh, n_frames)
    return project, mesh, names, meta


# ----------------------------------------------------------------------
# measured solve
# ----------------------------------------------------------------------
def measured_solve(shared: Path, n_frames: int, *, load_tolerance=1.0e-6,
                   project_mesh=None) -> tuple[dict, np.ndarray, object]:
    from anyfem.solve import build as builder, run
    import anysolver

    stages: dict = {}
    if project_mesh is None:
        project, mesh, names, meta = load_inputs(shared, n_frames, stages)
    else:
        project, mesh, names, meta = project_mesh
    with timed(stages, "fe_model_build_seconds"):
        built = builder.build_fe_model(project, mesh, load_case=None, require_loads=False)
    dofs = built.fe_model.mesh.dof_manager.total_dofs
    preflight_record: dict = {}
    started = time.perf_counter()
    try:
        report = run.preflight(built, analysis_type="linear_static")
        preflight_record["error_count"] = len(report.errors)
        preflight_record["warning_count"] = len(report.warnings)
        if report.errors:
            raise RuntimeError("; ".join(f"{issue.code}: {issue.message}" for issue in report.errors))
        preflight_record["ok"] = True
        preflight_record["report_type"] = type(report).__name__
    except Exception as error:  # noqa: BLE001 - recorded honestly
        preflight_record["ok"] = False
        preflight_record["error"] = f"{type(error).__name__}: {error}"
        raise RuntimeError(f"benchmark preflight refused the model: {error}") from error
    stages["preflight_seconds"] = time.perf_counter() - started

    load_prep, owner, compressed, proxy, modes = (Accumulator() for _ in range(5))
    messages: list[str] = []
    with wrapped(builder, "_build_load_case", load_prep), \
            wrapped(anysolver, "solve_linear_many", owner, record_shape=True), \
            wrapped(run, "_solve_compressed", compressed), \
            wrapped(run, "_nodal_force_proxy", proxy), \
            wrapped(run, "_modes", modes):
        started = time.perf_counter()
        batch = run.solve_linear_static_many(
            built=built, load_cases=names, load_tolerance=load_tolerance,
            progress=lambda message: messages.append(str(message)))
        stages["solve_linear_static_many_total_seconds"] = time.perf_counter() - started
    total = stages["solve_linear_static_many_total_seconds"]
    non_load = total - load_prep.seconds
    stages["load_preparation_seconds"] = load_prep.seconds
    stages["load_compression_nonsolver_seconds"] = (
        max(compressed.seconds - owner.seconds, 0.0) if compressed.calls else 0.0)
    stages["owner_solve_seconds_assembly_factorization_backsubstitution"] = owner.seconds
    stages["result_reconstruction_and_other_seconds"] = max(
        non_load - max(compressed.seconds, owner.seconds), 0.0)
    matrix = np.column_stack([shape.displacements for shape in batch.shapes])
    info = batch.info if isinstance(batch.info, dict) else {}
    record = {
        "frames": n_frames, "total_dofs": int(dofs), "matrix_shape": list(matrix.shape),
        "stage_seconds": stages, "preflight": preflight_record,
        "accumulators": {"build_load_case": load_prep.as_dict(), "solve_linear_many": owner.as_dict(),
                         "solve_compressed": compressed.as_dict(),
                         "nodal_force_proxy": proxy.as_dict(), "modes_svd": modes.as_dict()},
        "load_compression": info.get("load_compression"), "status": batch.status,
        "backend_diagnostics": info.get("backend", {}),
        "effective_thread_policy": info.get("thread_policy", {}),
        "progress_messages": messages[:12],
        "unavailable_stages": {
            "owner_assembly_factorization_backsubstitution_split": "see the profile action",
            "worker_spawn_transfer": "not part of the in-process stage run; see workflow"},
        "frozen_inputs": {key: meta[key] for key in ("mesh_content_hash", "nodes", "quads",
                                                       "tris", "wet_elements")},
    }
    return record, matrix, batch


def _spooled_round_trip(batch, route: str) -> dict:
    """One in-process spooled serialize/deserialize of ``batch`` (benchmark-only).

    ``route`` is "direct" (the production first attempt) or "codec_v4" (the
    plain-data fallback).  Sizes come from the spool, never from a whole-message
    ``bytes`` copy; the read-back unpickles straight from the spool file.
    """
    import pickle
    from anyfem import result_transport as rt
    from anyfem.job_process import dumps_spooled

    record: dict = {"route": route, "complete": False}
    started = time.perf_counter()
    if route == "direct":
        spool = dumps_spooled(("result", batch))
    else:
        spool = rt.encode_result_stream(
            batch, lambda envelope: dumps_spooled(("encoded_result", envelope)))
    record["serialize_seconds"] = time.perf_counter() - started
    try:
        record["serialized_bytes"] = spool.size
        record["rolled_to_disk"] = spool.rolled_to_disk
        spool.rewind()
        started = time.perf_counter()
        value = pickle.load(spool.file())
        record["unpickle_seconds"] = time.perf_counter() - started
        if route == "direct":
            restored = value[1]
        else:
            started = time.perf_counter()
            restored = rt.decode_result(value[1])
            record["decode_seconds"] = time.perf_counter() - started
    finally:
        spool.close()
    shapes = list(zip(batch.shapes, restored.shapes))
    record["shared_fe_model"] = all(s.built.fe_model is restored.built.fe_model
                                    for s in restored.shapes)
    record["shared_project"] = all(s.built.project is restored.built.project
                                   for s in restored.shapes)
    record["shared_mesh"] = all(s.built.mesh is restored.built.mesh for s in restored.shapes)
    record["displacements_bitwise_identical"] = all(
        a.displacements.tobytes() == b.displacements.tobytes() for a, b in shapes)
    record["complete"] = bool(record["displacements_bitwise_identical"])
    return record


def codec_measurement(batch) -> dict:
    """Measure both transport routes to completion; any failure fails the action.

    Not bounded by a helper thread: the process supervisor is the only time
    bound, so an unfinished measurement is an unfinished (failed) action.
    """
    from anyfem import result_transport as rt
    from anyfem.job_process import TransportSpoolError

    out: dict = {"codec_version": rt.CODEC_VERSION,
                 "label": "benchmark-only in-process measurement via bounded spools"}
    try:
        out["direct"] = _spooled_round_trip(batch, "direct")
    except TransportSpoolError:
        raise
    except Exception as error:  # noqa: BLE001 - the supported fallback follows
        out["direct"] = {"route": "direct", "complete": False,
                         "refused": f"{type(error).__name__}: {error}"}
    out["codec_v4"] = _spooled_round_trip(batch, "codec_v4")
    out["complete"] = bool(out["codec_v4"]["complete"]
                           and (out["direct"]["complete"] or "refused" in out["direct"]))
    return out


# ----------------------------------------------------------------------
# actions
# ----------------------------------------------------------------------
def action_frames(action: str) -> int:
    digits = "".join(ch for ch in action if ch.isdigit())
    return int(digits)


def run_numeric(action: str, shared: Path, evidence: Path) -> None:
    n = action_frames(action)
    RESULT["frames"] = n
    before = peak_working_set_bytes()
    record, matrix, batch = measured_solve(shared, n)
    RESULT.update(record)
    results = shared / "results"
    results.mkdir(exist_ok=True)
    np.savez_compressed(results / f"{action}.npz", displacements=matrix)
    RESULT["displacement_checksum_sha256"] = hashlib.sha256(
        np.ascontiguousarray(matrix).tobytes()).hexdigest()
    RESULT["peak_working_set_bytes_before"] = before
    RESULT["peak_working_set_bytes_after_solve"] = peak_working_set_bytes()
    write_result(evidence)
    problems: list[str] = []
    label = "baseline" if action.startswith("baseline") else "candidate"
    if label == "candidate":
        # Same-letter baseline of the unchanged source; absence is a failure.
        reference = results / f"baseline{action.removeprefix(label)}.npz"
        if not reference.exists():
            problems.append(f"no baseline result {reference.name} to compare against")
        else:
            old = np.load(reference)["displacements"]
            if old.shape != matrix.shape:
                problems.append(f"shape {matrix.shape} differs from baseline {old.shape}")
            else:
                scale = max(float(np.max(np.linalg.norm(old, axis=0))), 1e-300)
                difference = float(np.max(np.linalg.norm(matrix - old, axis=0)) / scale)
                RESULT["baseline_regression"] = {
                    "reference": reference.name, "max_column_difference_relative": difference,
                    "limit": BASELINE_REGRESSION_LIMIT,
                    "within_limit": difference <= BASELINE_REGRESSION_LIMIT}
                if difference > BASELINE_REGRESSION_LIMIT:
                    problems.append(
                        f"candidate differs from unchanged baseline by {difference:.3e}")
    write_result(evidence)
    RESULT["transport"] = codec_measurement(batch)
    RESULT["peak_working_set_bytes_final"] = peak_working_set_bytes()
    if not RESULT["transport"]["complete"]:
        problems.append("transport measurement incomplete")
    RESULT["problems"] = problems
    RESULT["status"] = "failed" if problems else "complete"


def run_reference(shared: Path, evidence: Path) -> None:
    """Candidate vs an uncompressed all-case solve of every frame, plus S3 fixture scaling."""
    record, matrix, _ = measured_solve(shared, N_FRAMES_FULL)
    RESULT["compressed"] = {k: record[k] for k in ("stage_seconds", "load_compression")}
    full_record, full, _ = measured_solve(shared, N_FRAMES_FULL, load_tolerance=None)
    scale = max(float(np.max(np.linalg.norm(full, axis=0))), 1e-300)
    per_frame = np.linalg.norm(matrix - full, axis=0) / scale
    RESULT["uncompressed"] = {k: full_record[k] for k in ("stage_seconds", "matrix_shape")}
    RESULT["reference_comparison"] = {
        "frames": int(full.shape[1]), "max_relative_to_largest_frame": float(per_frame.max()),
        "worst_frame": int(per_frame.argmax()), "limit": REGRESSION_LIMIT,
        "within_limit": bool(per_frame.max() <= REGRESSION_LIMIT)}
    write_result(evidence)
    baseline = shared / "results" / "baseline200a.npz"
    if baseline.exists():
        old = np.load(baseline)["displacements"]
        RESULT["uncompressed_vs_baseline200a_relative"] = float(
            np.max(np.linalg.norm(full - old, axis=0)) / scale) if old.shape == full.shape else None
    RESULT["status"] = "complete" if RESULT["reference_comparison"]["within_limit"] else "failed"
    s3 = {}
    sys.path.insert(0, str(ROOT / "tests"))
    try:
        s3 = s3_fixture_scaling()
    except Exception as error:  # noqa: BLE001
        s3 = {"error": f"{type(error).__name__}: {error}"}
    RESULT["s3_fixture_scaling"] = s3
    if "error" in s3:
        RESULT["status"] = "failed"
    else:
        rows = {(r["cases"], r["marker_bytes"]): r for r in s3["rows"]}
        delta = rows[(200, 65536)]["wire_bytes"] - rows[(200, 0)]["wire_bytes"]
        s3["marker_delta_bytes"] = delta
        s3["marker_delta_within_70kib"] = delta <= 70 * 1024
        if not (s3["marker_delta_within_70kib"]
                and all(r["shared_fe_model"] and r["case_identity_in_model"]
                        for r in s3["rows"])):
            RESULT["status"] = "failed"


def s3_fixture_scaling() -> dict:
    """Real qualified S3 V2D model through the codec at 1/10/50/200 cases."""
    from dataclasses import replace
    import result_transport_helpers as helpers
    from anyfem import result_transport as rt
    from anyfem.job_process import dumps, loads
    from anyfem.post.results import LinearBatchSolution, LinearSolution
    from anysolver import LoadCase

    rows = []
    for n in (1, 10, 50, 200):
        for marker in (0, 65536):
            if marker and n != 200:
                continue
            built = helpers._build_built("s3 scale")
            built.project.presentation["diagnostic_marker"] = b"x" * marker
            shapes = []
            for index in range(n):
                case = LoadCase(name=f"case-{index}")
                case.nodal_loads[2] = np.array([float(index), 0, 0, 0, 0, 0])
                built.fe_model.add_load_case(case)
                shapes.append(LinearSolution(
                    displacements=helpers.DISPLACEMENTS * (index + 1),
                    built=replace(built, load_case=case), info={"case": index},
                    label=case.name, value=float(index)))
            batch = LinearBatchSolution(built=built, shapes=shapes,
                                        case_names=tuple(s.label for s in shapes))
            started = time.perf_counter()
            wire = dumps(("encoded_result", rt.encode_result(batch)))
            encode = time.perf_counter() - started
            started = time.perf_counter()
            restored = rt.decode_result(loads(wire)[1])
            decode = time.perf_counter() - started
            rows.append({"cases": n, "marker_bytes": marker, "wire_bytes": len(wire),
                         "encode_seconds": encode, "decode_seconds": decode,
                         "shared_fe_model": all(s.built.fe_model is restored.built.fe_model
                                                for s in restored.shapes),
                         "case_identity_in_model": restored.shapes[0].built.load_case is
                         next(c for c in restored.shapes[0].built.fe_model.load_cases
                              if c.name == "case-0")})
    return {"codec_version": rt.CODEC_VERSION, "rows": rows}


def run_profile(shared: Path, evidence: Path) -> None:
    from anyfem.solve import build as builder, run

    stages: dict = {}
    project, mesh, names, _ = load_inputs(shared, N_FRAMES_FULL, stages)
    built = builder.build_fe_model(project, mesh, load_case=None, require_loads=False)
    profiler = cProfile.Profile()
    profiler.enable()
    batch = run.solve_linear_static_many(built=built, load_cases=names)
    profiler.disable()
    stream = io.StringIO()
    stats = pstats.Stats(profiler, stream=stream).sort_stats("cumulative")
    stats.print_stats(45)
    (evidence / "profile.txt").write_text(stream.getvalue(), encoding="utf-8")
    profiler.dump_stats(str(evidence / "profile.pstats"))
    rows = []
    for (filename, line, func), (cc, nc, tt, ct, _) in stats.stats.items():
        rows.append({"function": f"{Path(filename).name}:{line}:{func}", "calls": nc,
                     "total_seconds": tt, "cumulative_seconds": ct})
    rows.sort(key=lambda row: row["cumulative_seconds"], reverse=True)
    RESULT["top_cumulative"] = rows[:40]
    # Retain all function counters, including fast factorization/back-solve
    # calls that do not appear among the top cumulative functions.
    (evidence / "profile-functions.json").write_text(json.dumps(rows, indent=2), encoding="utf-8")
    RESULT["owner_phase_profile"] = [row for row in rows if (
        row["function"].startswith("linalg.py:")
        and row["function"].rsplit(":", 1)[-1] in {"factorize", "factorize_cached", "solve_many"}
    ) or (row["function"].startswith("matrix_assembly.py:")
          and row["function"].rsplit(":", 1)[-1] in {"assemble_system", "assemble_load_matrix"})]
    RESULT["backend_diagnostics"] = batch.info.get("backend", {})
    RESULT["frames"] = len(batch.shapes)
    RESULT["status"] = "complete"


def worker_job(*, project, progress, cancellation_token=None, shared_root, n_frames, stats_path):
    """Production-worker job: frozen mesh + the supplied project, batch solve."""
    from anyfem.solve import build as builder, run
    from anymesher.serialize import mesh_from_dict

    started = time.perf_counter()
    paths = frozen_paths(Path(shared_root))
    mesh = mesh_from_dict(json.loads(paths["mesh"].read_text(encoding="utf-8")))
    names = [case.name for case in project.series_children("Sea")]
    built = builder.build_fe_model(project, mesh, load_case=None, require_loads=False)
    batch = run.solve_linear_static_many(built=built, load_cases=names, progress=progress,
                                         cancellation_token=cancellation_token)
    Path(stats_path).write_text(json.dumps({
        "worker_solve_seconds": time.perf_counter() - started,
        "worker_peak_working_set_bytes": peak_working_set_bytes()}), encoding="utf-8")
    return batch


def run_workflow(shared: Path, evidence: Path) -> None:
    """Production worker, artifact write/reopen and first/last-case data access."""
    from anyfem.jobs import _cancellation_token
    from anyfem.job_process import run_in_worker_process
    from anyfem.io.artifacts import ArtifactStore
    from anyfem.io.result_artifact import write_solution_artifact

    stages: dict = {}
    project, mesh, names, meta = load_inputs(shared, N_FRAMES_FULL, stages)
    stats_path = evidence / "worker-stats.json"
    messages: list[str] = []
    started = time.perf_counter()
    from anyfem.document import DocumentSession
    from anyfem.job_process import WorkerProcessError

    # Production route: the immutable project snapshot, not the raw override.
    snapshot = DocumentSession(project).snapshot()
    # Observe the actual framed result receive without changing production
    # transport. This separates parent I/O/framing from unpickling; worker
    # encoding and graph decoding also have isolated in-process measurements.
    from anyfem import job_process as transport
    receive_times: dict = {}
    receive_state = {"inside_result": False}
    original_accept = transport._IncomingMessage.accept
    original_load = transport.pickle.load

    def observed_load(*args, **kwargs):
        started_load = time.perf_counter()
        try:
            return original_load(*args, **kwargs)
        finally:
            if receive_state["inside_result"]:
                receive_times["unpickle_seconds"] = (
                    receive_times.get("unpickle_seconds", 0.0) + time.perf_counter() - started_load)

    def observed_accept(incoming, data):
        arrival = time.perf_counter()
        receive_state["inside_result"] = incoming.kind in ("result", "encoded_result")
        try:
            value = original_accept(incoming, data)
        finally:
            receive_state["inside_result"] = False
        if data[:1] == b"H" and incoming.kind in ("result", "encoded_result"):
            receive_times["header_received_at"] = arrival
            receive_times["serialized_bytes"] = incoming.size
            receive_times["kind"] = incoming.kind
        if (isinstance(value, tuple) and value and value[0] in ("result", "encoded_result")
                and "header_received_at" in receive_times):
            elapsed = time.perf_counter() - receive_times.pop("header_received_at")
            receive_times["framed_receive_including_unpickle_seconds"] = elapsed
            receive_times["framing_and_io_seconds"] = max(
                elapsed - receive_times.get("unpickle_seconds", 0.0), 0.0)
        return value

    try:
        with mock.patch.object(transport._IncomingMessage, "accept", observed_accept), \
                mock.patch.object(transport.pickle, "load", observed_load):
            batch = run_in_worker_process(
                function=worker_job,
                kwargs={"shared_root": str(shared), "n_frames": N_FRAMES_FULL,
                        "stats_path": str(stats_path)},
                snapshot=snapshot, project_override=None,
                progress=lambda value: messages.append(str(value)), token=_cancellation_token())
    except WorkerProcessError as error:
        RESULT["worker_failure"] = {
            "remote_type": error.remote_type, "message": str(error),
            "remote_traceback": error.remote_traceback,
            "remote_diagnostic": repr(error.remote_diagnostic),
            "progress_tail": messages[-6:]}
        raise
    RESULT["worker_wall_seconds_including_spawn_transfer_decode"] = time.perf_counter() - started
    RESULT["parent_result_receive"] = receive_times
    RESULT["worker_stats"] = json.loads(stats_path.read_text(encoding="utf-8"))
    RESULT["progress_tail"] = messages[-6:]
    RESULT["result_type"] = type(batch).__name__
    RESULT["cases"] = len(batch.shapes)
    matrix = np.column_stack([shape.displacements for shape in batch.shapes])
    reference = shared / "results" / "candidate200a.npz"
    if reference.exists():
        old = np.load(reference)["displacements"]
        scale = max(float(np.max(np.linalg.norm(old, axis=0))), 1e-300)
        RESULT["worker_vs_candidate200a_relative"] = float(
            np.max(np.linalg.norm(matrix - old, axis=0)) / scale)
        if RESULT["worker_vs_candidate200a_relative"] > BASELINE_REGRESSION_LIMIT:
            raise RuntimeError("worker result differs from the in-process candidate result")
    else:
        RESULT["worker_vs_candidate200a_relative"] = "unavailable: run candidate200a first"
    write_result(evidence)
    store = ArtifactStore(evidence / "project.anyfem")
    store.ensure()
    started = time.perf_counter()
    reference_ref = write_solution_artifact(
        store, batch, job_id="bench-job", document_id="bench-doc", mesh_id="bench-mesh",
        model_hash="m" * 64, mesh_hash=meta["mesh_content_hash"], analysis_hash="a" * 64)
    RESULT["artifact_write_seconds"] = time.perf_counter() - started
    started = time.perf_counter()
    dataset = ArtifactStore(evidence / "project.anyfem").open_result(reference_ref)
    keys = list(dataset.field_keys)
    RESULT["artifact_field_keys"] = keys[:12]
    RESULT["artifact_frames"] = int(len(dataset.frames))
    problems: list[str] = []
    RESULT["first_last_case_read"] = artifact_values_check(dataset, batch, problems)
    RESULT["artifact_reopen_seconds"] = time.perf_counter() - started
    write_result(evidence)
    try:
        RESULT["qt_display"] = qt_first_last_display(batch, batch.built.mesh, dataset, evidence, problems)
    except BaseException as error:  # noqa: BLE001 - a missing dependency is a failed check
        import traceback
        RESULT["qt_display"] = {"status": "failed", "error": f"{type(error).__name__}: {error}",
                                "traceback": traceback.format_exc()}
        problems.append("headless Qt first/last display failed")
    try:
        dataset.close()
    except Exception:  # noqa: BLE001 - the Qt window may already have retired it
        pass
    RESULT["problems"] = problems
    RESULT["status"] = "failed" if problems else "complete"


def artifact_values_check(dataset, batch, problems: list) -> dict:
    """First/last frame of the reopened artifact equal the batch displacement data.

    Expected values come from the same public adapter that wrote the artifact
    (``result_artifact_payload``), so the descriptor's component/DOF layout is
    respected; the comparison is exact (``array_equal``) per frame.
    """
    from anyfem.io.result_artifact import result_artifact_payload

    payload = result_artifact_payload(batch)
    keys = list(dataset.field_keys)
    key = "displacement" if "displacement" in keys and "displacement" in payload.fields else None
    if key is None:
        problems.append("no displacement field common to the artifact and the batch payload")
        return {"status": "failed", "artifact_keys": keys[:12],
                "payload_keys": sorted(payload.fields)[:12]}
    expected = np.asarray(payload.fields[key][1])
    frames = int(len(dataset.frames))
    field = dataset.field(key)
    out: dict = {"field": key, "frames": frames, "expected_shape": list(expected.shape)}
    for label, frame in (("first", 0), ("last", frames - 1)):
        got = np.asarray(field.read(frame))
        want = expected[frame]
        out[label] = {"frame": frame, "shape": list(got.shape),
                      "equal_to_batch_payload": bool(np.array_equal(got, want.reshape(got.shape))
                                                     if got.size == want.size else False)}
        if not out[label]["equal_to_batch_payload"]:
            problems.append(f"artifact {label} frame differs from the batch displacement data")
    out["status"] = "passed" if all(out[k]["equal_to_batch_payload"] for k in ("first", "last")) else "failed"
    return out


def qt_first_last_display(batch, mesh, dataset, evidence: Path, problems: list) -> dict:
    """Drive the production Qt workflow (offscreen, software viewport, never shown).

    The window adopts the worker batch through its own ``_on_solved`` (first
    result display), then browses to the last case, then shows first and last
    frames of the reopened persisted artifact.  Each wait is bounded to 10 s.
    """
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    from datetime import datetime, timezone
    from PySide6.QtWidgets import QApplication
    from anyfem.io import recovery
    from anyfem.document import canonical_hash
    from anyfem.model.records import JobRecord, JobStatus, MeshRecord
    from anyfem.ui.qt.app import QtFemWindow
    from anymesher.serialize import mesh_to_dict

    app = QApplication.instance() or QApplication([])
    record: dict = {"status": "running", "backend": "software", "shown_on_desktop": False}
    scratch = evidence / "qt-display"
    scratch.mkdir(parents=True, exist_ok=True)

    def settle(window, label):
        started = time.perf_counter()
        deadline = started + 10.0
        while time.perf_counter() < deadline:
            app.processEvents()
            loader = getattr(window, "_result_loader", None)
            if ((loader is None or not loader._records)
                    and not window.panels["Results"]._display_pending
                    and not window._refresh_pending):
                return time.perf_counter() - started
            time.sleep(0.01)
        raise TimeoutError(f"{label}: Qt result display did not settle within 10 s")

    with mock.patch.object(recovery, "default_recovery_root", lambda: scratch / "recovery"):
        window = QtFemWindow(viewer_backend="software")
        try:
            app.processEvents()
            now = datetime.now(timezone.utc).isoformat()
            mesh_hash_value = canonical_hash(mesh_to_dict(mesh))
            mesh_record = MeshRecord(
                name="Benchmark mesh", kind="generated",
                source_model_hash=window.session.revision.model_hash, mesh_input_hash="",
                mesh_hash=mesh_hash_value,
                summary={"nodes": mesh.num_nodes, "elements": mesh.num_elements})
            job = JobRecord(
                analysis_id="", name="Benchmark batch",
                model_hash=window.session.revision.model_hash, mesh_hash=mesh_hash_value,
                analysis_hash="a" * 64, input_hash="i" * 64, status=JobStatus.COMPLETED,
                created_utc=now, started_utc=now, finished_utc=now, summary={})
            with window.session.transaction("attach benchmark result", solver_affecting=False):
                window.project.mesh_records[mesh_record.id] = mesh_record
                window.project.jobs[job.id] = job
            window.mesh = mesh
            window.mesh_record_id = mesh_record.id
            window._meshes[mesh_record.id] = mesh
            record["jobs_registered"] = True

            started = time.perf_counter()
            window._on_solved(batch, job.id)
            record["first_live_display_settle_seconds"] = settle(window, "first live")
            record["first_live_display_total_seconds"] = time.perf_counter() - started
            record["first_live_shape_index"] = int(window.shape_index)
            if window.shape_index != 0 or window.active_job_id != job.id:
                problems.append("first live display selected the wrong case/job")
            png = window.viewport.capture_png(scratch / "first.png")
            record["first_live_png_bytes"] = Path(png).stat().st_size

            last = len(batch.shapes) - 1
            started = time.perf_counter()
            window.shape_index = last
            window.show_results()
            record["last_live_display_settle_seconds"] = settle(window, "last live")
            record["last_live_display_total_seconds"] = time.perf_counter() - started
            record["last_live_shape_index"] = int(window.shape_index)
            if window.shape_index != last:
                problems.append("last live display did not select the last case")
            record["last_live_png_bytes"] = Path(
                window.viewport.capture_png(scratch / "last.png")).stat().st_size

            window.result_datasets[job.id] = dataset
            for label, frame in (("first", 0), ("last", last)):
                ready: list[bool] = []
                started = time.perf_counter()
                window.show_persisted_result("displacement", frame=frame,
                                             on_ready=lambda: ready.append(True))
                record[f"{label}_persisted_display_settle_seconds"] = settle(window, label)
                record[f"{label}_persisted_display_total_seconds"] = time.perf_counter() - started
                record[f"{label}_persisted_on_ready"] = bool(ready)
                if not ready:
                    problems.append(f"persisted {label} frame display never reported ready")
            record["status"] = "passed" if not problems else "failed"
        finally:
            window.session.mark_saved()
            window.close()
            app.processEvents()
    return record


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--action", required=True)
    parser.add_argument("--evidence", required=True)
    parser.add_argument("--shared-root", required=True)
    args = parser.parse_args(argv)
    evidence, shared = Path(args.evidence), Path(args.shared_root)
    action = args.action
    RESULT.update({"action": action, "status": "running", "runtime": runtime_identity()})
    start_watchdog(evidence)
    started = time.perf_counter()
    try:
        if action == "prepare":
            RESULT["frozen"] = prepare(shared)
            RESULT["status"] = "complete"
        elif action == "reference":
            run_reference(shared, evidence)
        elif action == "profile":
            run_profile(shared, evidence)
        elif action == "workflow":
            run_workflow(shared, evidence)
        elif action.startswith(("baseline", "candidate")):
            run_numeric(action, shared, evidence)
        else:
            raise SystemExit(f"unknown action {action!r}")
    except BaseException as error:  # noqa: BLE001 - fail honestly with the traceback
        import traceback
        RESULT["status"] = "failed"
        RESULT["error"] = f"{type(error).__name__}: {error}"
        RESULT["traceback"] = traceback.format_exc()
        RESULT["wall_seconds"] = time.perf_counter() - started
        write_result(evidence)
        print(json.dumps({"type": "failed", "error": RESULT["error"]}), flush=True)
        return 1
    RESULT["wall_seconds"] = time.perf_counter() - started
    RESULT["peak_working_set_bytes_process"] = peak_working_set_bytes()
    write_result(evidence)
    print(json.dumps({"type": "result", "action": action, "status": RESULT["status"],
                      "wall_seconds": RESULT["wall_seconds"]}), flush=True)
    return 0 if RESULT["status"] == "complete" else 1


if __name__ == "__main__":
    sys.exit(main())
