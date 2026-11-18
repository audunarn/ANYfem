# D1 Codex review round 4 (commit 0d8a12e, gpt-6.1-sol high, read-only, 2026-11-18)

Codex session 01a1ee7f-a6e4-7f53-aedb-e586c6162625. Focused on git diff b72e9dd 0d8a12e. Final message verbatim below.

1. **P1 — Worker normalisation can corrupt the live project.** [presentation_controls.py:774](C:/ANYworkspaceWork/anyfem-shell-axes-d1-20261118/src/anyfem/ui/qt/presentation_controls.py:774)  
   Pre-normalisation does not make the worker read-only: `build_fe_model → validate → resolve_section_assignments` runs again and clears/rebuilds the shared maps. I reproduced an interleaving where the worker pauses after clearing `face_sections`; a GUI resolver then interprets the empty map as a deletion and removes the canonical assignment. Assignment count remained **0 instead of 1** after completion. Discarding the result cannot undo this mutation.

2. **P2 — The speed route accepts elements the six-field probe refuses.** [mesh_orientation.py:450](C:/ANYworkspaceWork/anyfem-shell-axes-d1-20261118/src/anyfem/mesh_orientation.py:450)  
   Eligibility at line 497 checks exact class, but permits instance overrides. With two elements, an override doubling element 2’s membrane stresses passed zero-displacement admission and escaped sampling: the probe refused it with residual **1.00**, while the report returned **consistent**, zero unchecked. I also reproduced this without overrides using an otherwise ordinary element translated by \(10^{12}\): probe residual **4.88e-4**, yet the fast report accepted it. Sampling therefore does not preserve probe admissibility. A sampled probe exception also leaves `fast_ok=True`, bypassing the all-probe fallback.

3. **P2 — Cache/completion freshness omits mutable mesh content.** [presentation_controls.py:778](C:/ANYworkspaceWork/anyfem-shell-axes-d1-20261118/src/anyfem/ui/qt/presentation_controls.py:778)  
   After checking, clearing, and reversing connectivity in the same mesh object, Show reuses the old arrows and verdict when project identity/document hash remain unchanged. The completion guard has the same identity-only mesh check. With `document_hash=None`, cache reuse is correctly disabled, but an in-place project edit during execution remains undetectable because completion compares `None == None`.

4. **P2 — Close can leave the executor thread alive.** [presentation_controls.py:983](C:/ANYworkspaceWork/anyfem-shell-axes-d1-20261118/src/anyfem/ui/qt/presentation_controls.py:983)  
   Close calls `retire()` without `shutdown()` and stops queue polling. An undrained completion retains its coordinator. I reproduced one live worker after retirement/finalizer completion and garbage collection; explicit shutdown released it.

5. **P3 — The “changes nothing” assertion can repair mutations before comparing.** [test_qt_shell_local_axes.py:126](C:/ANYworkspaceWork/anyfem-shell-axes-d1-20261118/tests/test_qt_shell_local_axes.py:126)  
   `_digest()` calls `project_to_dict()`, which itself normalises assignments. For a canonical assignment, clearing `face_sections` was repaired by serialization, producing an unchanged digest; the subsequent section-state assertion also saw the repaired state. Normalising first is appropriate for Scope A, but this assertion does not establish “no change at all.”

No findings in the mixed-state classification or its normal comparison. For unmodified legacy elements, the helper matches the solver’s material argument, Gauss points and frame functions; subclasses use the probe. No findings in opt-in mesh/solve-path isolation or the settled Scope A decision.

The **186-probe count** is supported by the sampling logic. The documented wall times lack resolvable scripts/log references (`<scratchpad>` placeholders), so I could not substantiate them.

Verification: **38 passed, 1 deselected** with capture disabled. No barge or Qt tests ran; no files changed; D2-design.md was not read.

NOT CLEAN