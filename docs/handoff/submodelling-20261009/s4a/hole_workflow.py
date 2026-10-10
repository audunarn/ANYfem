"""S0's hole case (A3-lite) through the S4a workflow API instead of hand-wired calls.

Expected: the same Kt as S0 e4_hole.py for the same levels (global n=8, box A, sub n=16:
S0 gave 3.09401), now with the owner locator, transfer, driven trace and diagnostic.
Run with run_tests.py's environment: python hole_workflow.py (from this folder).
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "s0"))
import s0_env  # noqa: E402,F401  (snapshots first)

for path in (r"C:\ANYworkspaceWork\sm4\src", r"C:\ANYworkspaceWork\sm3\src", r"C:\ANYworkspaceWork\sm1\src"):
    sys.path.insert(0, path)

import numpy as np  # noqa: E402

import anyfem  # noqa: E402
import anymesher  # noqa: E402
import anysolver  # noqa: E402
import e4_hole as H  # noqa: E402
import s0_lib as L  # noqa: E402
from anyfem.submodel import SubmodelLineage, SubmodelSettings, bind_source, solve_submodel  # noqa: E402


def main():
    out = {"anyfem": anyfem.__file__, "anysolver": anysolver.__file__, "anymesher": anymesher.__file__}
    gp, gm = H.global_project(8)
    gsol = L.build(gp, gm)
    from anyfem.post.results import LinearSolution
    from anysolver import solve_linear
    u, info = solve_linear(gsol.fe_model, gsol.load_case)
    source = LinearSolution(displacements=u, built=gsol, info=info, label="global")
    central = [f for f in gp.geometry.faces
               if all(abs(gm.nodes[n][0]) <= 0.25 + 1e-12 for n in gm.nodes_on(gp.face(f)))]
    assert len(central) == 4, central
    sp, sm, _driven = H.sub_project(2 * H.D, 1.75 * H.D, 16)
    g = sp.geometry
    cut = tuple(e for e in g.edges if all(
        abs(abs(np.asarray(g.vertex_position(v))[0]) - 2 * H.D) < 1e-9 or abs(abs(np.asarray(g.vertex_position(v))[1]) - 1.75 * H.D) < 1e-9
        for v in (g.edges[e].start, g.edges[e].end)) and g.faces_using_edge(e) and
        abs(np.linalg.norm(np.asarray(g.vertex_position(g.edges[e].start))[:2]) - H.R) > 1e-9)
    lineage = SubmodelLineage({f: tuple(central) for f in g.faces}, {f"side{k}": (e,) for k, e in enumerate(cut)})
    binding = bind_source(source)
    for policy in ("drive_all", "release_normal_rotation"):
        result = solve_submodel(source, binding, sp, sm, lineage, SubmodelSettings(policy=policy))
        peak = H.peak(result.solution.built, result.solution.displacements, sm)
        d = result.diagnostics
        out[policy] = {"kt": peak["kt"], "free_body_force_ratio": d["free_body"]["force_ratio"], "free_body_moment_ratio": d["free_body"]["moment_ratio"],
                       "driven_nodes": d["driven_nodes"], "corners": len(d["corner_records"]),
                       "max_transfer_allowance": d["max_transfer_allowance"],
                       "equilibrium_ratio": d["equilibrium_ratio"]}
        print(policy, out[policy], flush=True)
    out["s0_reference_kt_global8_boxA_sub16"] = 3.09401
    (HERE / "hole_workflow.json").write_text(json.dumps(out, indent=2, default=float))
    print("saved")


if __name__ == "__main__":
    main()
