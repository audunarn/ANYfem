# D1 Codex review round 6 (commit 681da44, gpt-6.1-sol high, read-only, 2026-11-18)

Codex session 01a1eedd-c114-71f2-9f91-580e6f4ae9ee. Focused on git diff a6a9ab1 681da44, under contracts C1 (published immutable mesh) and C2 (no monkeypatching). Final message verbatim below.

Found **one P2 and one P3; no P1**.

1. **P2 — The corner measure does not bound the smallest dimension of curved Q8 elements.** [mesh_orientation.py:557](C:/ANYworkspaceWork/anyfem-shell-axes-d1-20261118/src/anyfem/mesh_orientation.py:557)

   A valid Q8 mapping is \(x=\xi,\ y=\eta(10^{-5}+\xi^2),\ z=0\). Its Jacobian remains positive, but its width near \(\xi=0\) is tiny while its corners remain well separated. After rotating it by 0.3 radians and translating by `(1e6, -1e6, 1e6)`, `smallest_dimension` returns approximately **2**, so the gate passes.

   With an ordinary first element occupying the verification sample, this Q8 remained checked through `solver-frame`, with no unchecked elements. Its direct probe refused with **residual 1.73e-5 > 1e-6**, and its normal drifted **0.000667°** from the untranslated frame. No monkeypatching or mid-request mutation was involved.

   Thus the corner-only gate does not resolve the general higher-order case. The documented single-shape measurements also do not justify `1e7` across supported shapes.

2. **P3 — The script does not establish the hash-only timing attribution.** [D1-r1-fixes.md:329](C:/ANYworkspaceWork/anyfem-shell-axes-d1-20261118/docs/direction-programme/D1-r1-fixes.md:329)

   [profile_check.py:14](C:/ANYworkspaceWork/anyfem-shell-axes-d1-20261118/docs/direction-programme/scripts/profile_check.py:14) times the entire `build_fe_model` call. It does not isolate the publication hash check, so it supports “build 1.21 s,” but not the attribution of approximately 1.2 s specifically to hashing. Likewise, 20.07 s measures the probe loop alone, not a complete project check using probes everywhere.

Outside those findings:

- **Publication and keys:** No additional defect found in project checking, overlay admission, cache reuse, completion or maintenance. The helper checks the tag, nonzero generation, active record ID and record hash. The solve build additionally checks changed mesh sources and recomputes the semantic hash. Remaining identity checks add rejection conditions; they do not authorize stale reuse.
- **Writers:** Searching `src` supported the narrower claim that the listed solver-affecting mesh fields are written during generation/import/staging. I found no post-publication writer to those fields.
- **Tests:** **43 passed, 1 deselected**. The sliver regression catches the stated rectangular counterexample; against `1eb6889`, both elements were checked through `solver-frame`. It does not establish the general geometric bound. The unpublished regression also fails by assertion against that version; the active-publication test fails first on the missing API import. The `shell_build` seam is acceptable and clearly labelled.
- **Timing:** The scripts support the three total-check measurements and separate build/admission/classification/probe measurements. I did not rerun barge timings or Qt tests.

Accepted under contract C1/C2:

- **C1:** In-place edits of published meshes, including edit-and-undo during checking.
- **C2:** Runtime monkeypatches outside guard coverage and mid-request solver-surface changes.

No files were modified. D2-design.md was not read.

NOT CLEAN