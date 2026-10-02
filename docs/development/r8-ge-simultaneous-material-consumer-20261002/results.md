# Native mixed-material Project workflow — 2 October 2026

The new headless example saves and reopens an experimental native ANYfem Project containing two Q4 J2 shells and two native B3-GE stiffeners with physical-fibre/generalized sections. It solves the staged history, restores a separate accepted-state checkpoint into a fresh owner and continues to an identical final checkpoint. Existing public consumer and solver contracts are reused; no product code, schema, version or default changed.

Run `python examples/stiffened_shell_coupled_plastic_history_consumer.py` for temporary output, or call its `workflow(directory)` to retain the native `.anyfem` input, accepted observations, summary and checkpoints. The installed test executes that same public workflow. The trivial standalone main wrapper was source-reviewed; it was not launched as a second duplicate numerical job.

## Delivered behavior

The native Project preserves exact panel records, original native beam bytes/SHA bindings, material definitions, Q4 algorithmic pullback policy and the model-bound staged endpoints. The persisted Project contains virgin analysis inputs. Accepted material state resides in separate solver checkpoints, never implicitly in the input file.

The initial solve must complete position1 with the overall programme still incomplete. Its checkpoint restores byte-identically into a new owner created from the reopened Project. Direct and restored continuations must both complete position4 and the full programme. They return the same accepted path, callback history and final checkpoint; final bytes differ from the prefix.

Five direct and three resumed accepted observations are retained. Actual shell/fibre updates are simultaneous at positions1,3,4;16 station/fibre identities exhibit reversed signed plastic flow. The unloading stage also contains fibre plasticity. Maximum equilibrium ratio5.8393e−9 and constraint gap1.2691e−16 satisfy the unchanged1e−8/1e−9 criteria. Full low-strength fixture definitions and their limitations are inherited from the public solver example, now committed at solver02e5c19f. This is a lifecycle/consumer demonstration, not a structural design or engineering accuracy reference.

## Verification and review

Consumer source base8af0784; solver product613e43f7/checkpoint02e5c19f. Final example SHA `9d663cceefaf3fa7208c02200db0ddd6a74997790b59c208c1888c169efbc4b7`; test SHA `14016c4e37f36481b339ae1420406513c89fb3ba1ec0ff46155be5025d9f47d9`.

The first installed run passed1/1 in85.593s. Independent source review found that prefix completion/position was not explicitly asserted. Root added prefix and final programme-position/status checks and retained those fields in the summary. This was a coverage defect, not an observed solver nonconvergence. The exact earlier fixtures/results remain in `v1`; only `v2` verifies the added assertions.

Final installed verification passed **1/1**, with zero failures/errors/skips,91.2737s full-route time and181895168B sampled peak process-tree memory. Effective numerical threads1, Numba1, JIT disabled,600s/1GiB supervision. These costs support no speedup or practical-scale claim. The independent review confirmed all277 imports came from the installed site and checked actual saved Project, observations and matching checkpoint hashes.

Unchanged artifact pair, checked against all98 consumer and391 solver source/wheel/site modules:

- ANYfem wheel `02a419916f093ab8711a943cb96f5495b44503a4158717f100c960984db5bab4`.
- ANYsolver wheel `4c14d0942410f97b6e2ab397eb0d4e567cbff4169c05b391c5685c4df40d3b7c`.

Python3.14.2 and full dependency versions are recorded. Product artifacts required no rebuild. The focused installed workflow covers the new example/test; unchanged broad campaigns were not repeated. Final platform/CI/release requirements remain open.

[Raw manifest](raw-v1.json) binds41 files in raw-v1.zip, SHA `7d8e218f08e5d651c7edb7f9aca52b4097683e593236486a5759f090297ce009`. It retains both fixture versions, commands, supervision, package/fixture identities, numerical output, native Projects, accepted observations and checkpoints. Member hashes were rechecked and the three known-current inherited credential values were absent. This does not remediate the separate historical dissemination restriction. Exact unchanged wheels remain in their preceding artifact evidence.

[Independent review](review.md) is clear for this scope. Scientific qualification, general biaxial/large-rotation/S3 simultaneous histories, the full material/attachment envelope, external accuracy, practical scale and complete M4/M5 remain open. No aggregate gate or release status is promoted.
