# D1 Codex review round 7 (commit dcd6bd4, gpt-6.1-sol high, read-only, 2026-11-18)

Codex session 01a1eefb-7e67-7303-9399-814809670895. Focused on git diff 4aea087 dcd6bd4. Final message verbatim below.

No P1 or P2 found. Two P3 findings remain.

1. **P3 — “No cancellation” overstates near-singular accuracy.** [mesh_orientation.py:617](/C:/ANYworkspaceWork/anyfem-shell-axes-d1-20261118/src/anyfem/mesh_orientation.py:617)

   The identity `sigma_min = |t_xi × t_eta| / sigma_max` is correct, but cross-product components can cancel. For `a=(.267,.534,.801)` and `b=a+(2e-16,0,1e-16)`, the implementation gives `1.31785e-16`; an 80-digit calculation on those same floating-point inputs gives `1.40746e-16`, a **6.37% error**. These extreme cases are safely rejected by the gate. Correct the comment and add near-singular coverage: the new SVD test exercises moderately conditioned geometries, not this case.

2. **P3 — Timing attribution exceeds the supplied evidence.** [D1-r1-fixes.md:374](/C:/ANYworkspaceWork/anyfem-shell-axes-d1-20261118/docs/direction-programme/D1-r1-fixes.md:374)

   `time_check.py` measures complete checks; it does not isolate the gate’s claimed **0.63–0.66 s** cost. The cited admission timing also does not establish that unchanged components slowed “by the same factor,” or that machine load caused the variation. Retain the isolated measurement command/results and qualify the causal explanation as an inference.

Other conclusions:

- **Criterion and recovery points:** No correctness defect found. The gate evaluates the element’s own derivatives at exactly `gauss_points`, matching default stress recovery and both frame-reading paths. Batched multiplication can round differently from the solver’s scalar products, but I found no admission failure resulting from that difference.
- **Fail closed:** Zero, singular, NaN and infinite Jacobian cases tested returned infinity. Extremely ill-conditioned cases were rejected despite diminished relative accuracy.
- **Counterexample search:** None found under C1/C2. Of 360 generated geometries, 356 passed both gates and passed the actual six-field probe. These included slivers, curved Q8s, reduced integration and warped geometries. Among 237 boundary cases near conditioning `9.9e6`, the maximum measured residual was **`9.39e-10`**.
- **Limit:** `8·eps·1e7 ≈ 1.78e-8`, providing substantial first-order margin below `1e-6`. This supports the conservative choice; the stated angular bound alone is not a complete derivation of the probe’s tensor-residual error.
- **Tests:** **48 passed, 1 barge test deselected.** The curved-Q8 regression passes on HEAD and fails against the `4aea087` module loaded in memory. The four SVD cases pass; on the baseline they lack the newly introduced function.
- **Accepted under contract C1/C2:** Published-mesh mutation, runtime monkeypatching and mid-request solver-surface changes remain excluded. Neither exclusion was needed to dismiss a counterexample found here.
- No files were modified. D2-design.md was not read. Barge and Qt tests were not run.

NOT CLEAN