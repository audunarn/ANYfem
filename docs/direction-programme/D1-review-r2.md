# D1 Codex review round 2 (commit 23ed254, gpt-6.1-sol high, read-only, 2026-11-18)

Codex session 01a1ee47-0fa4-7861-9cdb-2c53364a5631. Final message verbatim below.

Found seven issues in `23ed254`. No files were modified; D2-design.md was not read.

1. **P1 — A spatially varying face parameterisation can hide 90° turns.** [mesh_orientation.py:455](C:/ANYworkspaceWork/anyfem-shell-axes-d1-20261118/src/anyfem/mesh_orientation.py:455) measures each element against its own centroid’s u direction before averaging. Concrete reproduction: a planar 4×3 Q8 grid with elements 10–12 turned 90° correctly reports three turned elements under Cartesian parameterisation. Reparameterising the same plane as `r(u,v)=(u,v+g(u),0)`, with `g′=0` at the first three columns and `g′=tan(80°)` at the last, changes the report to **consistent**, with nothing turned or unchecked. The map is nonsingular throughout. The independence claims in the module docstring and D0:54 are therefore incorrect. The parameterisation tests exercise only uniform rotations.

2. **P1 — The probe ignores geometric imperfections applied by the solve build.** [mesh_orientation.py:375](C:/ANYworkspaceWork/anyfem-shell-axes-d1-20261118/src/anyfem/mesh_orientation.py:375) copies coordinates directly from the original mesh. The actual build subsequently applies `_apply_imperfections` at build.py:144–145. On a planar 2×2 Q4 grid spanning 2×2 m, with a 0.1 m plate-mode imperfection, the diagnostic reports **consistent** using planar frames. The solver’s imperfect reference geometry instead produces recovery normals differing by **1.71–6.36°** on the first element. Sharing `_shell_element` does not reproduce the finished solver model.

3. **P2 — Isolating each element suppresses S3 model-scope refusals.** [mesh_orientation.py:375](C:/ANYworkspaceWork/anyfem-shell-axes-d1-20261118/src/anyfem/mesh_orientation.py:375) creates a fresh `FEMesh` containing only the element being probed. A geometrically admitted V2D triangle plus a legacy Q4, using the permitted `q4="legacy", s3="e4-pl-s3-v2d"` policy, reports **consistent: two elements, zero unchecked**. Recovery in the actual combined mesh raises `NativeParityCapabilityError`: “S3 V2D mixed shell scope admits only exact V2D, accepted V2C and qualified Q4.” The relevant guard is solver e4_pl_s3_v2d_element.py:650–669.

4. **P2 — The substitute material masks actual material refusals.** [mesh_orientation.py:344](C:/ANYworkspaceWork/anyfem-shell-axes-d1-20261118/src/anyfem/mesh_orientation.py:344) never checks the element with its assigned material. An admitted planar V2D triangle assigned a valid `OrthotropicMaterial` reports **consistent**, but actual recovery raises “S3 V2D layered material currently requires exact isotropic Material” at solver e4_pl_s3_v2d_element.py:738–742. Material-independent frame geometry does not establish material admissibility.

5. **P2 — A generator-valued `faces` argument silently produces an empty, consistent report.** [mesh_orientation.py:544](C:/ANYworkspaceWork/anyfem-shell-axes-d1-20261118/src/anyfem/mesh_orientation.py:544) passes the same iterable through both stages. Frame extraction consumes a generator; classification then sees no faces. Calling `project_orientation_report(project, mesh, faces=(f for f in [1]))` on the grid containing three turned elements returns **“consistent: 0 elements on 0 faces.”** This violates the declared `Iterable[int]` contract and fail-closed requirement.

6. **P2 — Unsupported connectivity aborts the report instead of becoming unchecked.** [mesh_orientation.py:368](C:/ANYworkspaceWork/anyfem-shell-axes-d1-20261118/src/anyfem/mesh_orientation.py:368) selects the formulation outside the per-element exception handler. A face referencing a five-node entry in `mesh.quads` raises `ValueError: unsupported shell topology with 5 nodes`; no report is returned. The solve refusal should be recorded against that element, preserving the rest of the diagnostic.

7. **P3 — The bending-yy sign regression is vacuous.** [test_mesh_orientation.py:334](C:/ANYworkspaceWork/anyfem-shell-axes-d1-20261118/tests/test_mesh_orientation.py:334) checks that xx, xy and membrane shear are nonzero, but omits yy. With `w=x²+xy` and `nu=0`, `bending_yy` is numerically zero. Its sign assertion at line 336 would pass if yy incorrectly remained unchanged. A nonzero y² term would exercise the claimed rule.

No additional findings in these areas:

- **Probe mathematics:** for supported combinations on the coordinates actually supplied, the six symmetric fields soundly reconstruct the sign-free in-plane tensor frame and its normal. I found no separate wrong-frame acceptance caused by the `1e-6` residual tolerance. Membrane, bending and top/bottom share that frame on the inspected recovery paths.
- **Build refactor:** no ordinary-input or refusal behavior change found in the extraction itself.
- **Remaining classification/accounting:** no further defect found in the ambiguous band, no-dominant handling, normal majority/tie, missing IDs, duplicate membership or caught geometry refusals.
- **Overlay:** no concrete implementation defect found through static inspection of clear, scene replacement, canvas replacement and new-mesh handling. Qt tests were not run; the added test does not actually replace the canvas object.
- **D0 corrections:** the normal-flip sign rule, warped-Q4 fallback and faceted-S3 normal descriptions are correct. The module’s broader guarantees remain overstated by findings 1–4.

Verification: **21 headless tests passed, one barge test deselected**. Additional reproductions ran entirely in memory. The solver-frame test oracles are independent of the diagnostic, but the suite misses the counterexamples above.

NOT CLEAN