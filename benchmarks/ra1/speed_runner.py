"""Bounded baseline/candidate complete-call benchmark for the frozen RA1 fixtures."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import statistics
import subprocess
import sys
import time

import psutil


ROOT = Path(__file__).resolve().parents[2]
ECOSYSTEM = ROOT.parent
REPORTS = ROOT / "reports" / "ra1"
BASELINE = REPORTS / "curved-development" / "baseline-20260924"
FIXTURES = REPORTS / "attempts" / "20260924T175354Z-formal-4a6079d4" / "fixtures"
DESTINATION = REPORTS / "curved-development"
WORKERS = {
    "ANYMESH_QUAD_MCF_WORKER": ECOSYSTEM / "ANYmesh" / "third_party" / "quad" / "worker" / "out" / "lemon" / "quad_mcf_worker.exe",
    "ANYMESH_QUAD_TINYAD_WORKER": ECOSYSTEM / "ANYmesh" / "third_party" / "quad" / "worker" / "out" / "tinyad" / "quad_tinyad_optimizer.exe",
}
MEMORY_CAP = 2 * 1024**3
MESH_CAP = 120.0
ATTEMPT_CAP = 30 * 60.0
WORKLOADS = (
    ("fem_deck", .25, False),
    ("fem_deck", .25, True),
    ("structure_cylinder", 1., False),
    ("structure_cylinder", .5, False),
    ("structure_cylinder", .5, True),
    ("structure_panel", 1., False),
)


def _write(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, sort_keys=True, indent=2, allow_nan=False), encoding="utf-8")


def _hashes(root: Path) -> dict[str, str]:
    return {
        str(file.relative_to(root)).replace("\\", "/"): hashlib.sha256(file.read_bytes()).hexdigest()
        for file in sorted(root.rglob("*.py"))
    }


def _environment(revision: str) -> dict[str, str]:
    environment = os.environ.copy()
    if revision == "baseline":
        mesh = BASELINE / "ANYmesh" / "src"
        geometry = BASELINE / "ANYgeometry" / "src"
    else:
        mesh = ECOSYSTEM / "ANYmesh" / "src"
        geometry = ECOSYSTEM / "ANYgeometry" / "src"
    environment["PYTHONPATH"] = os.pathsep.join((
        str(ROOT / "src"), str(ECOSYSTEM / "ANYstructure"),
        str(mesh), str(geometry), environment.get("PYTHONPATH", ""),
    ))
    for name in (
        "OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
        "NUMBA_NUM_THREADS", "VECLIB_MAXIMUM_THREADS", "NUMEXPR_NUM_THREADS",
    ):
        environment[name] = "1"
    for name, path in WORKERS.items():
        environment[name] = str(path)
    return environment


def _stop_owned(process: subprocess.Popen, child: psutil.Process) -> None:
    for owned in child.children(recursive=True):
        try:
            owned.terminate()
        except psutil.NoSuchProcess:
            pass
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


def _run(folder: Path, spec: dict, revision: str, deadline: float) -> dict:
    folder.mkdir(parents=True, exist_ok=False)
    _write(folder / "input.json", spec)
    started = time.monotonic()
    last_stage = None
    stage_started = started
    peak_bytes = 0
    peak_by_iteration: dict[int, int] = {}
    reason = None
    with (folder / "stdout.log").open("x", encoding="utf-8") as stdout, (
        folder / "stderr.log"
    ).open("x", encoding="utf-8") as stderr:
        process = subprocess.Popen(
            [sys.executable, "-m", "benchmarks.ra1.speed_operation", str(folder)],
            cwd=ROOT, env=_environment(revision), stdout=stdout, stderr=stderr,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        child = psutil.Process(process.pid)
        while process.poll() is None:
            now = time.monotonic()
            progress = folder / "progress.json"
            if progress.exists():
                try:
                    stage = json.loads(progress.read_text(encoding="utf-8"))["stage"]
                    if stage != last_stage:
                        last_stage, stage_started = stage, now
                except (OSError, ValueError, KeyError):
                    pass
            try:
                memory = child.memory_info().rss + sum(
                    item.memory_info().rss for item in child.children(recursive=True)
                )
                peak_bytes = max(peak_bytes, memory)
                if last_stage and last_stage.startswith("mesh-"):
                    iteration = int(last_stage.removeprefix("mesh-"))
                    peak_by_iteration[iteration] = max(
                        peak_by_iteration.get(iteration, 0), memory,
                    )
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
            if peak_bytes > MEMORY_CAP:
                reason = "2 GiB process-memory limit"
            elif now - stage_started > MESH_CAP:
                reason = f"120 s limit in {last_stage or 'startup'}"
            elif now > deadline:
                reason = "30 minute attempt limit"
            if reason:
                _stop_owned(process, child)
                break
            time.sleep(.1)
    result_file = folder / "result.json"
    if result_file.exists():
        result = json.loads(result_file.read_text(encoding="utf-8"))
    else:
        result = {"status": "incomplete" if reason else "failed",
                  "reason": reason or f"worker exited {process.returncode}"}
    result["peak_rss_bytes"] = peak_bytes
    if "runs" in result:
        for row in result["runs"]:
            row["peak_rss_bytes"] = peak_by_iteration.get(row["iteration"], peak_bytes)
    result["elapsed_seconds"] = time.monotonic() - started
    result["last_stage"] = last_stage
    result["exit_code"] = process.returncode
    _write(folder / "supervisor.json", {
        key: result[key] for key in (
            "peak_rss_bytes", "elapsed_seconds", "last_stage", "exit_code",
        )
    } | {"limit_reason": reason})
    return result


def _summarize(results: dict[str, dict]) -> dict:
    out = {}
    for label, row in results.items():
        if row["status"] != "passed":
            out[label] = {"status": row["status"], "reason": row.get("reason")}
            continue
        runs = row["runs"][1:]
        times = [item["mesh_seconds"] for item in runs]
        stages = sorted(set().union(*(item["stage_seconds"] for item in runs)))
        out[label] = {
            "status": "passed", "median_seconds": statistics.median(times),
            "range_seconds": [min(times), max(times)],
            "stage_median_seconds": {
                name: statistics.median(item["stage_seconds"].get(name, 0.) for item in runs)
                for name in stages
            },
            "peak_rss_bytes": row["peak_rss_bytes"],
            "measured_rss_median_bytes": statistics.median(
                item["peak_rss_bytes"] for item in runs),
            "measured_rss_range_bytes": [
                min(item["peak_rss_bytes"] for item in runs),
                max(item["peak_rss_bytes"] for item in runs),
            ],
            "nodes": runs[0]["nodes"], "shells": runs[0]["shells"],
            "q8": runs[0]["q8"], "t6": runs[0]["t6"],
            "q4_statuses": [item["q4_status"] for item in runs],
            "q5_statuses": [item["q5_status"] for item in runs],
            "imported": row["imported"],
        }
    comparisons = {}
    for case, h, refined in WORKLOADS:
        key = f"{case}-h{h}-r{int(refined)}"
        baseline = out.get(f"{key}-baseline", {})
        candidate = out.get(f"{key}-candidate", {})
        if baseline.get("status") == candidate.get("status") == "passed":
            comparisons[key] = {
                "baseline_median_seconds": baseline["median_seconds"],
                "candidate_median_seconds": candidate["median_seconds"],
                "candidate_over_baseline": candidate["median_seconds"] / baseline["median_seconds"],
                "nonoverlap_faster": candidate["range_seconds"][1] < baseline["range_seconds"][0],
            }
        else:
            comparisons[key] = {"status": "incomplete comparison"}
    return {"workloads": out, "comparisons": comparisons}


def main() -> None:
    if not (BASELINE / "source-manifest.json").exists():
        raise FileNotFoundError("preserved source baseline is unavailable")
    for case, _, _ in WORKLOADS:
        if not (FIXTURES / f"{case}.json").exists():
            raise FileNotFoundError(f"sealed fixture {case} is unavailable")
    for worker in WORKERS.values():
        if not worker.is_file():
            raise FileNotFoundError(f"pinned worker {worker} is unavailable")
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    attempt = DESTINATION / f"speed-{timestamp}"
    attempt.mkdir(parents=True, exist_ok=False)
    identity = {
        "utc": datetime.now(timezone.utc).isoformat(),
        "platform": platform.platform(), "python": sys.version,
        "logical_cpus": os.cpu_count(),
        "baseline_manifest_sha256": hashlib.sha256(
            (BASELINE / "source-manifest.json").read_bytes()).hexdigest(),
        "candidate_mesh_source_sha256": _hashes(ECOSYSTEM / "ANYmesh" / "src"),
        "candidate_geometry_source_sha256": _hashes(ECOSYSTEM / "ANYgeometry" / "src"),
        "frozen_fixture_sha256": {
            case: hashlib.sha256((FIXTURES / f"{case}.json").read_bytes()).hexdigest()
            for case, _, _ in WORKLOADS
        },
        "workers": {
            name: {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
            for name, path in WORKERS.items()
        },
        "limits": {"mesh_seconds": MESH_CAP, "attempt_seconds": ATTEMPT_CAP,
                   "rss_bytes": MEMORY_CAP, "shells": 1500, "dofs": 15000,
                   "numerical_threads": 1},
    }
    _write(attempt / "identity.json", identity)
    deadline = time.monotonic() + ATTEMPT_CAP
    results = {}
    for case, h, refined in WORKLOADS:
        key = f"{case}-h{h}-r{int(refined)}"
        spec = {"case": case, "h": h, "refined": refined,
                "fixture": str(FIXTURES / f"{case}.json")}
        for revision in ("baseline", "candidate"):
            label = f"{key}-{revision}"
            if time.monotonic() >= deadline:
                results[label] = {"status": "incomplete", "reason": "30 minute attempt limit before start"}
            else:
                results[label] = _run(attempt / label, spec, revision, deadline)
            print(f"{label}: {results[label]['status']}", flush=True)
    summary = _summarize(results)
    _write(attempt / "summary.json", summary)
    manifest = {
        str(file.relative_to(attempt)).replace("\\", "/"): hashlib.sha256(file.read_bytes()).hexdigest()
        for file in sorted(attempt.rglob("*"))
        if file.is_file() and file.name != "hashes.json"
    }
    _write(attempt / "hashes.json", manifest)
    print(json.dumps({"attempt": str(attempt), "comparisons": summary["comparisons"]}, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
