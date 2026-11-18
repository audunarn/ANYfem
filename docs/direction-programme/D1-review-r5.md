# D1 Codex review round 5 (commit 1eb6889, gpt-6.1-sol high, read-only, 2026-11-18)

Codex session 01a1eea5-167e-7640-8366-4174abe5e24a. Focused on git diff d1292b8 1eb6889. Final message verbatim below.

Five **P2 findings** remain:

1. **The worker still reads a mutable live mesh.** [presentation_controls.py:789](C:/ANYworkspaceWork/anyfem-shell-axes-d1-20261118/src/anyfem/ui/qt/presentation_controls.py:789) passes `mesh` directly to the worker. Completion-time digests cannot detect changes restored before completion. I reproduced this after publication validation: temporarily sloping the mesh while FE nodes were copied, then restoring it, left the completion key unchanged. The report contained eight checked elements with centroid z up to **0.175**, while every live node had z=0. The mesh needs a detached worker snapshot.

2. **The digest omits inputs that affect solve admission.** [mesh_orientation.py:331](C:/ANYworkspaceWork/anyfem-shell-axes-d1-20261118/src/anyfem/mesh_orientation.py:331) hashes shell connectivity and selected metadata, but omits `hybrid_diagnostics`, among other build inputs. On a published plate, changing its automation diagnostics to `inspection_only` preserved this digest, while a fresh check changed from eight checked elements to eight unchecked elements because publication validation refused the mesh. Cache reuse and completion can therefore accept obsolete results.

3. **Drawn overlays still use mesh identity rather than mesh content.** [presentation_controls.py:854](C:/ANYworkspaceWork/anyfem-shell-axes-d1-20261118/src/anyfem/ui/qt/presentation_controls.py:854) checks only the revision portion of `_shell_axes_drawn_key`; the preceding mesh check uses `id()`. An in-place connectivity or coordinate edit with an unchanged document revision leaves the previous arrows and statistics displayed. The stored mesh digest is never checked during maintenance.

4. **Eligibility does not protect the module-level native recovery callable.** [mesh_orientation.py:576](C:/ANYworkspaceWork/anyfem-shell-axes-d1-20261118/src/anyfem/mesh_orientation.py:576) checks helpers in `elements`, but not `recovery_shell_frames.native_recovery_frames`. I patched that callable to double derivatives only for unsampled element 2. Eligibility remained true, both elements used `solver-frame`, and nothing was unchecked; directly probing element 2 refused it with residual **1.0 > 1e-6**. Zero-displacement admission cannot detect this. The claimed construction guarantee remains incomplete.

5. **The scale gate does not bound error in narrow element directions.** [mesh_orientation.py:544](C:/ANYworkspaceWork/anyfem-shell-axes-d1-20261118/src/anyfem/mesh_orientation.py:544) divides by the largest corner separation. A rotated Q8 measuring 1 × 1e-4, translated by `(1e6, -1e6, 1e6)`, had a ratio of approximately **1e6**, so passed the gate. Its normal drifted **0.000661°** from the untranslated frame; its probe refused with residual **3.13e-6**, yet as an unsampled element it remained checked through `solver-frame`. The documented single-shape investigation does not justify the limit across supported element shapes.

**Class A is not closed** because of findings 1–3. I found no additional defect in project/geometry/session detachment or snapshot preservation of mesh records and imperfections. A published imperfect-plate comparison produced identical live/thawed FE coordinates.

**Class B is not closed** because of findings 4–5.

I found **no separate close/join lifetime defect**: retirement cancels work, shutdown joins it, and repeated close skips the cleared executor. The new close test does not deterministically establish that a check is running, or exercise repeated close.

Verification: **41 passed, 1 deselected**, with `-s` added because default pytest capture required unavailable temporary files. The three new headless regressions also failed by assertion against the old implementation loaded in memory. Qt tests were inspected only; the raw/cache test’s unmodified-old failure encounters the newly introduced helper import before testing cache behavior.

No files were modified. D2-design.md was not read.

NOT CLEAN