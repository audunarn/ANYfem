"""Bounded, serial, evidence-preserving RA1 application attempt."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import time

import psutil


ROOT = Path(__file__).resolve().parents[2]
ECOSYSTEM = ROOT.parent
REPORTS = ROOT / "reports" / "ra1" / "attempts"
MEMORY_CAP = 2 * 1024**3
STAGE_CAP = 120.0
ATTEMPT_CAP = 30 * 60.0


def _save(path: Path, value: object) -> None:
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, sort_keys=True, indent=2, allow_nan=False)


def _identity(environment):
    names = ("ANYfem", "ANYstructure", "ANYmesh", "ANYgeometry", "ANYsolver", "ANYmaterial")
    revisions = {}
    for name in names:
        repository = ECOSYSTEM / name
        git = ["git", "-c", f"safe.directory={repository.as_posix()}"]
        head = subprocess.run(
            [*git, "rev-parse", "HEAD"], cwd=repository,
            text=True, capture_output=True, check=True,
        ).stdout.strip()
        dirty = subprocess.run(
            [*git, "status", "--porcelain"], cwd=repository,
            text=True, capture_output=True, check=True,
        ).stdout.splitlines()
        diff = subprocess.run(
            [*git, "diff", "--binary", "HEAD"], cwd=repository,
            capture_output=True, check=True,
        ).stdout
        revisions[name] = {
            "head": head, "dirty_entries": len(dirty),
            "tracked_diff_sha256": hashlib.sha256(diff).hexdigest(),
        }
    code = (
        "import json,importlib,importlib.metadata as md; out={}; "
        "pairs=[('anyfem','ANYfem'),('anymesher','ANYmesher'),"
        "('anygeometry','ANYgeometry'),('anysolver','ANYsolver'),"
        "('anymaterial','ANYmaterial'),('anystruct','ANYstructure')]; "
        "[(out.__setitem__(n,{'path':str(importlib.import_module(n).__file__),"
        "'version':next((d.version for d in md.distributions() "
        "if d.metadata['Name'].lower()==p.lower()),None)})) for n,p in pairs]; "
        "print(json.dumps(out))"
    )
    imported = subprocess.run(
        [sys.executable, "-c", code], env=environment,
        text=True, capture_output=True,
    )
    source_files = {
        "ANYfem": (
            "src/anyfem/quad_first.py", "src/anyfem/model/project.py",
            "src/anyfem/mesh_jobs.py", "src/anyfem/native_meshing_backend.py",
            "src/anyfem/ui/app.py", "src/anyfem/ui/panels.py",
            "benchmarks/ra1/operation.py", "benchmarks/ra1/runner.py",
        ),
        "ANYstructure": (
            "anystruct/quad_first_integration.py", "anystruct/fem_integration.py",
            "setup.py", "pyproject.toml",
        ),
    }
    source_hashes = {
        f"{repository}/{relative}": hashlib.sha256(
            (ECOSYSTEM / repository / relative).read_bytes()
        ).hexdigest()
        for repository, names in source_files.items()
        for relative in names
    }
    # HEAD/diff hashes do not identify untracked owner runtime modules.
    for repository in ("ANYmesh", "ANYgeometry", "ANYsolver", "ANYmaterial"):
        root = ECOSYSTEM / repository
        for source in sorted((root / "src").rglob("*.py")):
            source_hashes[f"{repository}/{source.relative_to(root).as_posix()}"] = hashlib.sha256(
                source.read_bytes()
            ).hexdigest()
    for relative in (
        "third_party/quad/worker/quad_mcf_worker.cc",
        "third_party/quad/worker/quad_tinyad_optimizer.cc",
    ):
        source = ECOSYSTEM / "ANYmesh" / relative
        source_hashes[f"ANYmesh/{relative}"] = hashlib.sha256(source.read_bytes()).hexdigest()
    return {
        "utc": datetime.now(timezone.utc).isoformat(),
        "platform": platform.platform(), "python": sys.version,
        "processor": platform.processor(), "logical_cpus": os.cpu_count(),
        "revisions": revisions,
        "source_sha256": source_hashes,
        "imported": json.loads(imported.stdout) if imported.returncode == 0 else {
            "error": imported.stderr.strip(),
        },
        "resource_limits": {
            "operation_stage_seconds": STAGE_CAP,
            "attempt_seconds": ATTEMPT_CAP,
            "process_peak_bytes": MEMORY_CAP,
            "shells": 1500, "dofs": 15000,
            "threads": 1,
        },
    }


def _inventory():
    sizes = {
        "fem_deck": (0.5, 0.25),
        "structure_panel": (2.0, 1.0),
        "structure_cylinder": (1.0, 0.5),
    }
    rows = []
    for case, (coarse, fine) in sizes.items():
        for h in (coarse, fine):
            for order in ("linear", "quadratic"):
                rows.append({
                    "case": case, "h": h, "order": order, "refined": False,
                    "solve": order == "quadratic" and h == fine,
                    "direct_parity": order == "quadratic" and h == fine,
                })
        rows.append({
            "case": case, "h": fine, "order": "quadratic", "refined": True,
            "solve": True, "direct_parity": False,
        })
    return rows


def _operation(folder: Path, spec, environment, deadline):
    folder.mkdir(parents=True, exist_ok=False)
    _save(folder / "input.json", spec)
    started = time.monotonic()
    stage, stage_started = None, started
    peak = 0
    reason = None
    with (folder / "stdout.log").open("x", encoding="utf-8") as stdout, (
        folder / "stderr.log"
    ).open("x", encoding="utf-8") as stderr:
        process = subprocess.Popen(
            [sys.executable, "-m", "benchmarks.ra1.operation", str(folder)],
            cwd=ROOT, env=environment, stdout=stdout, stderr=stderr,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        child = psutil.Process(process.pid)
        while process.poll() is None:
            now = time.monotonic()
            progress = folder / "progress.json"
            if progress.exists():
                try:
                    reported = json.loads(progress.read_text(encoding="utf-8"))["stage"]
                    if reported != stage:
                        stage, stage_started = reported, now
                except (OSError, ValueError, KeyError):
                    pass
            try:
                memory = child.memory_info().rss + sum(
                    item.memory_info().rss for item in child.children(recursive=True)
                )
                peak = max(peak, memory)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
            if peak > MEMORY_CAP:
                reason = "2 GiB process-memory limit"
            elif now - stage_started > STAGE_CAP:
                reason = f"120 s limit in {stage or 'startup'}"
            elif now > deadline:
                reason = "30 minute attempt limit"
            if reason is not None:
                for owned in child.children(recursive=True):
                    try:
                        owned.terminate()
                    except psutil.NoSuchProcess:
                        pass
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill(); process.wait()
                break
            time.sleep(0.1)
    result_file = folder / "result.json"
    result = json.loads(result_file.read_text(encoding="utf-8")) if result_file.exists() else {
        "status": "incomplete", "reason": reason or "worker did not publish result"
    }
    observation = {
        "exit_code": process.returncode, "supervisor_reason": reason,
        "peak_rss_bytes": peak, "elapsed_seconds": time.monotonic() - started,
        "last_stage": stage,
    }
    _save(folder / "supervisor.json", observation)
    if reason is not None:
        result = {**result, "status": "incomplete", "reason": reason}
    elif process.returncode != 0 and result.get("status") == "passed":
        result = {**result, "status": "failed", "reason": f"worker exited {process.returncode}"}
    return result, observation


def _plot(folder: Path, destination: Path):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import numpy as np
        from anymesher.serialize import mesh_from_dict

        mesh = mesh_from_dict(json.loads((folder / "mesh.json").read_text(encoding="utf-8")))
        metrics = json.loads((folder / "mesh-metrics.json").read_text(encoding="utf-8"))
        error = {int(key): float(value) for key, value in metrics["geometry_by_cell"].items()}
        fig = plt.figure(figsize=(7, 5))
        axis = fig.add_subplot(111, projection="3d")
        values = []
        for element_id, body in mesh.shells.items():
            corners = body[:4] if len(body) in (4, 8) else body[:3]
            points = np.asarray([mesh.nodes[node] for node in corners])
            path = np.vstack((points, points[0]))
            axis.plot(path[:, 0], path[:, 1], path[:, 2], linewidth=0.25, color="#34495e")
            values.append((points.mean(axis=0), error.get(int(element_id), 0.0)))
        positions = np.asarray([item[0] for item in values]); scalars = np.asarray([item[1] for item in values])
        dots = axis.scatter(positions[:, 0], positions[:, 1], positions[:, 2], c=scalars, cmap="inferno", s=8)
        fig.colorbar(dots, ax=axis, label="sampled owner distance [m]")
        axis.set_xlabel("x [m]"); axis.set_ylabel("y [m]"); axis.set_zlabel("z [m]")
        fig.tight_layout(); fig.savefig(destination, dpi=150); plt.close(fig)
    except BaseException as error:
        destination.with_suffix(".error.txt").write_text(str(error), encoding="utf-8")


def _crosschecks(attempt: Path, inventory):
    checks = []
    for case in ("fem_deck", "structure_panel", "structure_cylinder"):
        rows = [row for row in inventory if row["case"] == case]
        data = {}
        for row in rows:
            label = f"{case}-h{row['h']}-{row['order']}-r{int(row['refined'])}"
            folder = attempt / label
            result_file, metrics_file = folder / "result.json", folder / "mesh-metrics.json"
            if result_file.exists() and metrics_file.exists():
                data[(row["h"], row["order"], row["refined"])] = (
                    json.loads(result_file.read_text(encoding="utf-8")),
                    json.loads(metrics_file.read_text(encoding="utf-8")),
                )
        hashes = {item[0].get("source_hash") for item in data.values()}
        checks.append({
            "case": case, "name": "identical-source-model",
            "status": "passed" if len(data) == 5 and len(hashes) == 1 else "failed",
            "source_hashes": sorted(str(value) for value in hashes),
        })
        coarse, fine = {"fem_deck": (.5, .25), "structure_panel": (2., 1.), "structure_cylinder": (1., .5)}[case]
        for order in ("linear", "quadratic"):
            key_a, key_b = (coarse, order, False), (fine, order, False)
            if key_a not in data or key_b not in data:
                checks.append({"case": case, "name": f"{order}-resolution-count", "status": "blocked"})
                continue
            a, b = data[key_a][1], data[key_b][1]
            checks.append({
                "case": case, "name": f"{order}-resolution-count",
                "status": "passed" if b["counts"]["equivalent"] > a["counts"]["equivalent"] else "failed",
                "coarse": a["counts"]["equivalent"], "fine": b["counts"]["equivalent"],
            })
        key_a, key_b, key_r = (coarse, "quadratic", False), (fine, "quadratic", False), (fine, "quadratic", True)
        if all(key in data for key in (key_a, key_b, key_r)):
            a, b, r = (data[key][1] for key in (key_a, key_b, key_r))
            checks.append({
                "case": case, "name": "geometry-response",
                "status": "passed" if b["max_geometry_error"] < a["max_geometry_error"] or a["max_geometry_error"] <= 1e-12 else "failed",
                "coarse": a["max_geometry_error"], "fine": b["max_geometry_error"],
            })
            inside = r["core_corner_count"] > b["core_corner_count"]
            core = r["core_median"] is not None and b["core_median"] is not None and r["core_median"] < b["core_median"]
            remote = r["remote_median"] is not None and b["remote_median"] is not None and abs(r["remote_median"] / b["remote_median"] - 1) <= .2
            checks.append({
                "case": case, "name": "refinement-causality",
                "status": "passed" if inside and core and remote else "failed",
                "uniform_core_nodes": b["core_corner_count"], "refined_core_nodes": r["core_corner_count"],
                "uniform_core_median": b["core_median"], "refined_core_median": r["core_median"],
                "uniform_remote_median": b["remote_median"], "refined_remote_median": r["remote_median"],
            })
        else:
            checks.append({"case": case, "name": "geometry-and-refinement-response", "status": "blocked"})
    return checks


def main():
    environment = os.environ.copy()
    environment["PYTHONPATH"] = os.pathsep.join((
        str(ROOT / "src"), str(ECOSYSTEM / "ANYstructure"),
        str(ECOSYSTEM / "ANYmesh"), environment.get("PYTHONPATH", ""),
    ))
    for name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMBA_NUM_THREADS", "VECLIB_MAXIMUM_THREADS", "NUMEXPR_NUM_THREADS"):
        environment[name] = "1"
    identity = _identity(environment)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    short = hashlib.sha256(json.dumps(identity["revisions"], sort_keys=True).encode()).hexdigest()[:8]
    attempt = REPORTS / f"{timestamp}-formal-{short}"
    attempt.mkdir(parents=True, exist_ok=False)
    _save(attempt / "environment.json", identity)
    fixtures = attempt / "fixtures"
    fixtures.mkdir()
    for case in ("fem_deck", "structure_panel", "structure_cylinder"):
        output = fixtures / f"{case}.json"
        frozen = subprocess.run(
            [sys.executable, "-m", "benchmarks.ra1.operation", "--freeze", case, str(output)],
            cwd=ROOT, env=environment, text=True, capture_output=True, timeout=STAGE_CAP,
        )
        if frozen.returncode != 0 or not output.exists():
            _save(fixtures / f"{case}-failure.json", {
                "returncode": frozen.returncode,
                "stdout": frozen.stdout, "stderr": frozen.stderr,
            })
            raise RuntimeError(f"could not freeze {case} source project")
    inventory = [
        {**row, "fixture": str(fixtures / f"{row['case']}.json")}
        for row in _inventory()
    ]
    _save(attempt / "inventory.json", inventory)
    deadline = time.monotonic() + ATTEMPT_CAP
    results = {}
    for spec in inventory:
        label = f"{spec['case']}-h{spec['h']}-{spec['order']}-r{int(spec['refined'])}"
        if time.monotonic() >= deadline:
            results[label] = {"status": "incomplete", "reason": "30 minute attempt limit before start"}
            continue
        result, observation = _operation(attempt / label, spec, environment, deadline)
        results[label] = {
            "status": result["status"],
            "reason": result.get("reason") or result.get("message"),
            "failed_checks": sum(item["status"] == "failed" for item in result.get("checks", ())),
            "peak_rss_bytes": observation["peak_rss_bytes"],
            "elapsed_seconds": observation["elapsed_seconds"],
        }
        print(f"{label}: {result['status']}", flush=True)
        if spec["order"] == "quadratic" and spec["h"] == {"fem_deck": .25, "structure_panel": 1., "structure_cylinder": .5}[spec["case"]] and not spec["refined"]:
            if (attempt / label / "mesh.json").exists():
                _plot(attempt / label, attempt / f"{spec['case']}-mesh-error.png")
    crosschecks = _crosschecks(attempt, inventory)
    _save(attempt / "crosschecks.json", crosschecks)
    _save(attempt / "case-dispositions.json", results)
    manifest = {}
    for file in sorted(attempt.rglob("*")):
        if file.is_file() and file.name != "hashes.json":
            manifest[str(file.relative_to(attempt))] = hashlib.sha256(file.read_bytes()).hexdigest()
    _save(attempt / "hashes.json", manifest)
    counts = {status: sum(row["status"] == status for row in results.values()) for status in ("passed", "failed", "blocked", "incomplete")}
    print(json.dumps({"attempt": str(attempt), "counts": counts}, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
