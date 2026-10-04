# B2 free-modal consumer — development delivery, 4 October 2026

The existing ANYfem B2 mixed modal consumer now saves/reopens and executes the
explicit public `B2_FREE_FREE_MODAL_ONLY_V1` policy. It uses the distinct
`anyfem.experimental-b2-mixed-free-reference-modal-panel-v1` schema and the
existing consumer class/Project bridge. Supported modal inputs retain their
historical schema and byte representation. This is experimental consumer
delivery, not scientific, GUI, platform or complete-programme acceptance.

Consumer source parent `2ede338ba28932bb9dd3e8eb0f0a4908d37fc765`, worktree
`C:/Github/ANYfem/.worktrees/experimental-ge-panel-consumer`. Solver source
`762d653968c78a003b5403a93ff31a517a384b09` in its separate integration worktree.
No release version/default was changed. Final unreleased wheel pair:

- ANYfem 0.4.1 SHA256
  `66739dbee7f9d6f5171dbb6371a41c71249d5ef52ac496456eac9dbd0ecd737e`.
- ANYsolver 0.4.7 SHA256
  `dea651b918e92871ffc017c6a435dd1f265d22e86f9a307d00d12ebd4abfa27b`.

[Installed binding](installed-binding.json) verifies the two changed consumer
modules match source/wheel/site byte-for-byte. [Runtime](installed-runtime.json)
records all 64 ANYfem and 236 ANYsolver module imports from their intended sites,
two observed BLAS pools and Numba each at one thread, JIT off, Python 3.14.
Observed dependencies are in [dependencies.json](dependencies.json); editable
siblings are not an isolated final qualification graph.

## Delivered behavior and checks

Persistence owns canonical input bytes and creates no solver. Free schema
requires the public policy, identity affine map, zero offset and declared mass;
it cannot silently acquire supported semantics. Actual mechanics admission
remains with ANYsolver. Installed capability guards require the declared free
policy and modes/close operations. Valid rigid/flexible results and numerical
failure/cancellation pass through unchanged; incompatible result type, policy or
qualification metadata fails explicitly. Owners close on rejection and errors.

The complete slice uses source and installed checks of strict input validation,
mutation isolation, standalone/native Project round trips, actual Q4/mixed/S3
rigid/flexible results, old route preservation, capability/result mismatch,
cancellation, status forwarding and programming-error propagation.

- Initial four-file source integration: **179 passed**, retained `source.*`.
- Review identified a concrete missing result guard: an owner advertising the
  free policy could return a historical supported result. Added the free-only
  public contract guard and counterexamples, leaving supported delegation intact.
- Final changed free-consumer file: **51 passed**, `source-final.*`.
- Coherent installed pair, all four affected files: **185 passed**,
  `installed.*`. No skipped/xfail outcomes were reported.
- New installed consumer against preceding installed solver: **57 supported
  checks passed**, `historical.*`. [Historical API check](historical-api.json)
  verifies that solver has no new free export or boundary fields and that the
  relevant installed modules match its wheel. Historical solver wheel SHA256
  `1bf7dabd8771e76ed09825fb66a89e3e48d513dbb0321989c41701096a679632`.
- [Exact payload comparison](compatibility.json): Q4/mixed/S3 supported payloads
  match the parent consumer byte-for-byte; the parent parser rejects new free
  records. The known parent source and disposable comparison are retained.
- [Headless examples](examples.log): supported, mixed-free and all-S3-free
  variants save/reopen native Projects and solve through installed public APIs.
  Both free variants return six checked rigid fields and three flexible pairs,
  one modal factorization each, rigid equation/gap/overlap measures below
  `2.1e-16`. The three exercised Project files are preserved.

Source/package preparation is reused. All numerical jobs were serial, terminal
and bounded at 180 seconds/2 GiB with one numerical thread. Installed tests took
18.596 seconds including startup, sampled peak 212463616 bytes; headless examples
1.957 seconds and 161976320 bytes. These are resource observations, not measured
speedup or practical-scale qualification. Commands/supervisors/logs are retained
in this directory. Original LF file convention is preserved; the normalization
record verifies parsed-AST equality before building, not a mechanics change.

## Review and boundary

[Supporting review](review.md) is by a non-implementing OpenAI worker; root
reviewed the integrated diff, actual run records and artifact/import identities.
It does not replace the required different-family scientific acceptance.
One incorrect canonical-Gram identity assertion was corrected before execution;
canonical Gram is positive and is compared with the direct owner, not identity.

Native save/reopen here is an experimental input path, not an accepted-state
checkpoint, ordinary qualified Project route or complete GUI workflow. Solver
spectral completeness/nullity, repeated-space/physical accuracy, prestress,
external/scale/platform acceptance and the full G0–G6 programme remain open.
M2/F03 connection decisions and protected authorities are unchanged. No push,
main merge, publication, other-task messaging or version change was performed.
