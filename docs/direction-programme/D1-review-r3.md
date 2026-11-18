# D1 Codex review round 3 (commit de84a83, gpt-6.1-sol high, read-only, 2026-11-18)

Codex session 01a1ee5a-9781-7920-b26d-8c32c9a28269. Final message verbatim below.

Four findings; no P1 findings.

1. **P2 — The diagnostic mutates the caller’s project.** [mesh_orientation.py:422](C:/ANYworkspaceWork/anyfem-shell-axes-d1-20261118/src/anyfem/mesh_orientation.py:422) calls the build on the original project. Build validation calls `resolve_section_assignments`, which adopts legacy mappings and creates persistent structural ownership. With a real one-element `Mesh` and a supported direct `face_sections` assignment, running the diagnostic changed counts of **assignments/regions/sheets/parts from `(0,0,0,0)` to `(1,1,1,1)`**, then reported consistent. Mutation also survives a later build refusal. This violates the no-mutation guarantee. The existing digest tests can conceal this because `project_to_dict` itself normalizes assignments before hashing.

2. **P2 — Solve-time imperfections remain outside the reported model.** [mesh_orientation.py:422](C:/ANYworkspaceWork/anyfem-shell-axes-d1-20261118/src/anyfem/mesh_orientation.py:422) includes project imperfections, but nonlinear-static and arc-length accept an additional `imperfection`; capacity applies its selected eigenmode imperfection after the initial build. These routes change reference coordinates before recovery. In an in-memory reproduction, the project report declared a flat Q4 consistent; applying the solver’s `ImperfectionField` with node 3 raised by 0.6 m changed its four recovery normals by **8.07°, 25.52°, 15.03° and 27.88°**. The project report cannot represent those solve configurations. Its guarantee needs an explicit scope restriction or access to the actual analysis model.

3. **P2 — An empty face still reports consistent.** [mesh_orientation.py:221](C:/ANYworkspaceWork/anyfem-shell-axes-d1-20261118/src/anyfem/mesh_orientation.py:221) checks whether faces exist, while `FaceOrientation.consistent` accepts a face with no elements. With `elements_of_face={1: []}`, the result is **“Shell axis check (consistent): 0 elements on 1 faces … 0 unchecked.”** Nothing was compared. The new empty-report regression covers `{}`, but misses this case.

4. **P3 — The stated strongly curved-face limitation is inaccurate.** [mesh_orientation.py:51](C:/ANYworkspaceWork/anyfem-shell-axes-d1-20261118/src/anyfem/mesh_orientation.py:51) promises that strongly doubly curved faces report mixed rather than consistent. There is no corresponding curvature/domain guard. A unit-sphere band spanning **340° longitude and 80° latitude**, discretized into 68 real qualified Q4s with meridional local x, reports **consistent, resultant 1.0, zero unchecked**, using projected global z. That result follows the implemented reference definition, but contradicts the documented exclusion.

No additional findings in planar parameterisation independence or the registered-material/full-mesh recovery admission for the supported elements inspected. Load/support requirement flags only change validation requirements; ordinary load-case/combination construction does not change shell frames. `build.py` is identical to `e4ec0f8`.

The `shell_build` fixture is an acceptable, clearly labelled shell-construction seam. Its frame oracles are independent of the diagnostic, but it cannot establish full-build purity or analysis-specific model equivalence.

Verification: **29 passed, 1 barge test deselected**. Additional reproductions ran in memory. No files modified; `D2-design.md` was not read; no barge or Qt tests ran.

NOT CLEAN