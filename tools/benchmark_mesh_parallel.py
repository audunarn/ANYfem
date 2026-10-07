"""Bounded one-row calibration through the public ANYfem caller.

Run each row under the task's process-tree/time-budget harness. This is a
development measurement, not a release or scientific qualification campaign.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))
import anymesher
import anymesher._native as native
from anyfem.mesh_execution import MeshExecutionPolicy
from parallel_mesh_contract import mesh_signature, separated_plates
from threadpoolctl import threadpool_info


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--components", type=int, choices=(4, 16, 32), required=True)
    parser.add_argument("--size", type=float, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--warm-repeats", type=int, choices=(1, 2), default=1)
    args = parser.parse_args()
    project = separated_plates(args.components)
    workers = min(args.components, 16)
    report = {
        "components": args.components, "target_size": args.size, "workers": workers,
        "python": sys.version, "platform": sys.platform,
        "native_path": native.__file__,
        "native_sha256": hashlib.sha256(Path(native.__file__).read_bytes()).hexdigest(),
        "versions": {name: importlib.metadata.version(name)
                     for name in ("ANYgeometry", "ANYmesher", "ANYmaterial", "numpy", "scipy")},
        "threadpools": threadpool_info(), "runs": [],
    }

    def save():
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")

    expected = None

    def run(label, policy, lease=None):
        nonlocal expected
        started = time.perf_counter()
        mesh = project.generate_mesh(args.size, strategy="native",
                                     execution_policy=policy, pool_lease=lease)
        duration = time.perf_counter() - started
        signature = mesh_signature(mesh)
        if expected is None:
            expected = signature
        elif signature != expected:
            raise AssertionError(f"{label}: serial/parallel mesh semantics differ")
        info = mesh.hybrid_diagnostics.get("parallel", {})
        if label != "serial" and not info.get("used"):
            raise AssertionError(f"{label}: parallel did not activate: {info}")
        report["runs"].append({"label": label, "wall_seconds": duration,
                               "elements": mesh.num_elements,
                               "elements_per_component": mesh.num_elements / args.components,
                               "execution": project._last_mesh_execution,
                               "parallel": info, "parity": True})
        save()
        print(json.dumps({"label": label, "wall_seconds": duration,
                          "elements": mesh.num_elements}), flush=True)

    save()
    run("serial", MeshExecutionPolicy.off())
    run("cold", MeshExecutionPolicy.explicit(workers))
    started = time.perf_counter()
    with anymesher.create_parallel_pool(workers) as lease:
        report["warm_pool_create_seconds"] = time.perf_counter() - started
        run("warm_first", MeshExecutionPolicy.explicit(workers), lease)
        if args.warm_repeats == 2:
            run("warm_reuse", MeshExecutionPolicy.explicit(workers), lease)
    report["warm_pool_closed"] = True
    save()


if __name__ == "__main__":
    main()
