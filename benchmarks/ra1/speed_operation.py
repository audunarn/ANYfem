"""One supervised paired-speed workload against a frozen RA1 source fixture."""

from __future__ import annotations

from contextlib import ExitStack
from pathlib import Path
from time import perf_counter
from unittest.mock import patch
import json
import importlib
import sys

from anyfem.io.project_file import project_from_dict
from anyfem.quad_first import sg1_quad_options
from anymesher.refinement import Refinement
import anymesher.hybrid as hybrid
import anymesher._cylindrical_public as cylindrical


def _write(path: Path, value) -> None:
    path.write_text(json.dumps(value, sort_keys=True, indent=2, allow_nan=False), encoding="utf-8")


def main(folder: Path) -> None:
    spec = json.loads((folder / "input.json").read_text(encoding="utf-8"))
    fixture = json.loads(Path(spec["fixture"]).read_text(encoding="utf-8"))
    stages = (
        (cylindrical, "prepare_bindings", "qualification"),
        (hybrid, "build_planar_quad_seed", "seeding"),
        (hybrid, "optimize_q4_seed_mcf", "optimization"),
        (hybrid, "run_planar_quad_driver", "front"),
        (hybrid, "optimize_quad_state", "optimization"),
        (hybrid, "_repair_quad_first_quality", "repair"),
        (hybrid, "_promote_quad_first_quadratic", "promotion_including_validation"),
    )
    rows = []
    imported = {
        name: str(importlib.import_module(name).__file__)
        for name in ("anyfem", "anymesher", "anygeometry")
    }
    for iteration in range(4):
        _write(folder / "progress.json", {"stage": f"mesh-{iteration}", "iteration": iteration})
        project = project_from_dict(fixture["project"])
        if spec["refined"]:
            project.refinements.append(Refinement(
                size=float(spec["h"]) / 3.0, radius=0.35,
                center=tuple(fixture["center"]), growth=1.5,
            ))
        stage_seconds: dict[str, float] = {}
        candidate_timings = None
        try:
            from anymesher.quad.timing import collect_quad_stage_timings
        except ImportError:
            collect_quad_stage_timings = None

        def measured(function, label):
            def call(*args, **kwargs):
                started = perf_counter()
                try:
                    return function(*args, **kwargs)
                finally:
                    stage_seconds[label] = stage_seconds.get(label, 0.0) + perf_counter() - started
            return call

        with ExitStack() as patches:
            for module, name, label in stages:
                patches.enter_context(patch.object(module, name, measured(getattr(module, name), label)))
            if collect_quad_stage_timings is not None:
                candidate_timings = patches.enter_context(collect_quad_stage_timings())
            started = perf_counter()
            mesh = project.generate_mesh(
                float(spec["h"]), order="quadratic", strategy="quad_first",
                quad_options=sg1_quad_options(), certification_mode="strict",
            )
            elapsed = perf_counter() - started
        if elapsed > 120.0:
            raise TimeoutError("120 s mesh limit")
        if len(mesh.shells) > 1500 or 6 * len(mesh.nodes) > 15000:
            raise RuntimeError("RA1 shell or DOF cap")
        certified = mesh.hybrid_diagnostics.get("high_order_geometry", {}).get("status")
        if certified != "CERTIFIED_POSITIVE":
            raise RuntimeError(f"strict certificate {certified!r}")
        rows.append({
            "iteration": iteration, "warmup": iteration == 0,
            "mesh_seconds": elapsed, "stage_seconds": stage_seconds,
            "nodes": len(mesh.nodes), "shells": len(mesh.shells),
            "q8": len(mesh.quads), "t6": len(mesh.tris),
            "q4_status": mesh.hybrid_diagnostics.get("q4", {}).get("status"),
            "q4_worker_calls": mesh.hybrid_diagnostics.get("q4", {}).get("worker_calls"),
            "q5_status": mesh.hybrid_diagnostics.get("q5", {}).get("status"),
            "internal_stage_seconds": candidate_timings,
        })
        _write(folder / "partial.json", rows)
    _write(folder / "result.json", {"status": "passed", "runs": rows, "imported": imported,
                                  "python": sys.version})


if __name__ == "__main__":
    import sys
    main(Path(sys.argv[1]))
