"""Trace one preserved RA1 cell through seed, Q4 and front stages."""

from __future__ import annotations

from contextlib import ExitStack
import json
from pathlib import Path
import sys
from unittest.mock import patch

import numpy as np

from anyfem.io.project_file import project_from_dict
from anyfem.quad_first import sg1_quad_options
from anymesher.refinement import Refinement
import anymesher.hybrid as hybrid


def main(fixture_file: Path, destination: Path) -> None:
    frozen = json.loads(fixture_file.read_text(encoding="utf-8"))
    project = project_from_dict(frozen["project"])
    project.refinements.append(Refinement(size=.25/3, radius=.35,
                                          center=tuple(frozen["center"]), growth=1.5))
    target = np.array((.2601480116065956, .4269929882997314, .25))
    rows = []
    seed_fn, q4_fn, front_fn = (
        hybrid.build_planar_quad_seed, hybrid.optimize_q4_seed_mcf,
        hybrid.run_planar_quad_driver,
    )

    def record(stage, domain, state):
        if domain.face_id != 2:
            return
        positions = {int(node): np.asarray(domain.lift(tuple(chart)), dtype=float)
                     for node, chart in state.nodes.items()}
        nearest = min(positions, key=lambda node: np.linalg.norm(positions[node]-target))
        rows.append({"stage": stage, "nodes": len(positions), "nearest_id": nearest,
                     "nearest_position": positions[nearest].tolist(),
                     "nearest_distance": float(np.linalg.norm(positions[nearest]-target)),
                     "protected": nearest in state.protected_nodes})

    def seeded(*args, **kwargs):
        seed = seed_fn(*args, **kwargs)
        record("seed", seed.domain, seed.state)
        return seed

    def q4(state, *args, **kwargs):
        result = q4_fn(state, *args, **kwargs)
        record("q4", kwargs["domain"], state)
        return result

    def front(seed, *args, **kwargs):
        result = front_fn(seed, *args, **kwargs)
        record("front", seed.domain, result.state)
        return result

    with ExitStack() as patches:
        patches.enter_context(patch.object(hybrid, "build_planar_quad_seed", seeded))
        patches.enter_context(patch.object(hybrid, "optimize_q4_seed_mcf", q4))
        patches.enter_context(patch.object(hybrid, "run_planar_quad_driver", front))
        mesh = project.generate_mesh(.25, order="quadratic", strategy="quad_first",
                                     quad_options=sg1_quad_options(), certification_mode="strict")
    if len(mesh.shells) > 1500 or 6*len(mesh.nodes) > 15000:
        raise RuntimeError("RA1 resource cap")
    destination.write_text(json.dumps(rows, indent=2, sort_keys=True), encoding="utf-8")
    for row in rows:
        print(row)


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]))
