# Supporting consumer review — 2026-10-04

Scope: source review in `C:/Github/ANYfem/.worktrees/experimental-ge-panel-consumer`, under `ANY_ECOSYSTEM_RISK_PROPORTIONATE_ENGINEERING_V1`, revision `2026-09-24.2`. This reviewer inspected the consumer, Project dispatch, regression tests, example and free-modal documentation. No numerical jobs, test runs, Git operations or product edits were performed; the only write is this report.

Outcome: no remaining actionable finding in the reviewed consumer slice. This is OpenAI supporting review, not a different-model-family review or qualification acceptance.

## Closed finding

The initial implementation admitted an owner with the declared free-modal capabilities but returned its result without checking the newly distinct result contract. A concrete counterexample was that owner returning the old supported `B2ReferenceModesResult`, which has no free rigid-field wrapper.

The final free-only guard in `ExperimentalB2MixedReferenceModalPanel.modes()` now checks the exact public `B2FreeReferenceModesResult` type, public free policy, outer unqualified metadata, exact public flexible result type and inner unqualified metadata. It raises `ValueError` for incompatible results inside the existing `try/finally`, so the owner closes. The five mismatch regressions cover the old result, wrong policy, qualified outer/inner result and untyped flexible result. Valid failed/cancelled results still return the same object, with the cancellation token forwarded. Supported result delegation remains unchanged. The guard neither rewrites statuses nor performs physics validation.

## Reviewed boundaries

- Free inputs use a distinct schema and the public boundary policy. The decoder passes the new boundary keyword only for that schema; supported records retain their schema and `supported` interpretation.
- Free input admission requires an identity affine map, zero offset, explicit mass/inertia and strict fields. Schema/policy contamination, supports, MPCs and scaled identity are rejected.
- Persistence and Project reconstruction create definitions only. Standalone and native Project routes dispatch the new schema to the same exact consumer class and preserve detached input ownership.
- Owner admission checks experimental status, unqualified metadata, boundary policy, mass-policy provenance and the free `modes`/`close` operations. Missing free exports affect the free route; supported parsing does not require them.
- The `--free-free` example builds identity coordinates, supplies the public policy, saves/reopens Project inputs and reads flexible frequencies through the public wrapper. Its declared beam inertia follows the stated uniform density and section moments. Documentation exposes modal-only scope and the remaining scientific limitations.
- The rigid kinetic Gram test now checks positive definiteness, without assuming that the reported canonical rigid fields have unit Gram. Public-owner comparisons exercise the consumer contract, not an independent scientific reference.

## Evidence and limitations

`source.log` records 179 passing tests for the earlier binding in `source-binding.json`; its free-test hash differs from the final hash below. The orchestrator reports a subsequent 51-pass run of the final free test file. This reviewer did not rerun tests or inspect a saved log for that subsequent count. Installed-candidate verification was root-owned and ongoing when this review was requested; no installed-pass claim is made here.

Older solver classes lacking the boundary attribute were assessed from the supported omitted-keyword/`getattr(..., None)` paths, rather than a separately installed old wheel. This is a compatibility evidence limitation, not an observed failure.

M2/F03 scientific acceptance, spectral completeness, exactly-six nullity, continuum dynamic accuracy, formal independent review and release gates remain open and unchanged. No performance or savings claim is made.

## Reviewed SHA-256 bindings

Paths are relative to the worktree root above; hashes bind the bytes read for this completed source review.

| File | SHA-256 |
| --- | --- |
| `src/anyfem/solve/stiffened_shell_b2.py` | `454a9235798f4a7c944a03a0e1579193031af4fb45957ebe1ab6dbf6c3c20aa0` |
| `src/anyfem/solve/stiffened_shell_project.py` | `0f65a7f9c368d0e566623a90da338ef527d212fe71faa07f1684bfa65dc39bdf` |
| `tests/test_experimental_b2_free_reference_modal_consumer.py` | `3da4cd05c2189f5849508cca8e19820c140092371c5fa35a73a5559ebbb0e4b4` |
| `tests/test_experimental_b2_mixed_reference_modal_consumer.py` | `18bbb1e789bbbcc484173ad21268660176f525a0834970ab37bdd069e6b425c9` |
| `examples/stiffened_shell_b2_mixed_reference_modes_consumer.py` | `f8410aab1480376094c9c30a172b967d4518a23a97465b24c948697caed41214` |
| `docs/EXPERIMENTAL_STIFFENED_SHELL_CONSUMER.md` | `52120edbab99d70962eed1d6b45d1447f051585b1a1e2ef7c0fefa30bd6e3034` |

## Final evidence applicability — appended 2026-10-04

The reviewer subsequently inspected the saved final logs, runtime/binding records and compatibility/example oracle scripts. All six reviewed hashes above were rechecked and remain unchanged. These were root-executed checks; this reviewer inspected their artifacts and did not launch or repeat any jobs.

- `installed.log` and `installed-runtime.json` record **185 passed, zero failed, exit status 0**. `installed-binding.json` binds the installed consumer wheel SHA-256 `66739dbee7f9d6f5171dbb6371a41c71249d5ef52ac496456eac9dbd0ecd737e`, the two reviewed product-module hashes and the final free-test hash. Runtime paths identify the installed consumer and current solver sites. `dependencies.json` identifies the current solver wheel SHA-256 `dea651b918e92871ffc017c6a435dd1f265d22e86f9a307d00d12ebd4abfa27b`.
- `source-final.log`, `source-final-runtime.json` and `source-final-binding.json` record **51 passed, zero failed, exit status 0**, using the final free-test hash and the reviewed source consumer against that current installed solver. This now supplies the previously uninspected final source-run evidence.
- `historical.log` and `historical-runtime.json` record **57 passed, zero failed, exit status 0**, using the installed candidate consumer and preceding solver site. `dependencies.json` identifies that preceding solver wheel SHA-256 `1bf7dabd8771e76ed09825fb66a89e3e48d513dbb0321989c41701096a679632`. The inspected `historical-api.py` assertions and `historical-api.json` establish absence of the free policy export and boundary fields on both definition and capability classes, plus byte identity between that site's two relevant solver modules and its wheel. The earlier absent-boundary-field compatibility limitation is therefore closed for this recorded solver baseline.
- The inspected `compatibility.py` compares current supported payload bytes against the historical consumer at commit `2ede338ba28932bb9dd3e8eb0f0a4908d37fc765`, reopens them with that parser and checks rejection of the new free schema. `compatibility.json` records exact matches and rejection for Q4, mixed and S3 fixtures. This is a concrete serialization compatibility oracle, not an assertion based only on model agreement.
- `examples.py` asserts installed consumer/current solver import sites before invoking the headless example. `examples.log` contains completed supported, mixed-free and all-S3-free Project routes; both free summaries report six rigid fields and unqualified metadata. The saved output supports these bounded routes, without establishing complete nullity or continuum modal accuracy.

All three recorded test runtimes use one Numba thread with JIT disabled; the installed runtime also reports one thread in each observed BLAS pool. Applicability remains the observed development dependency graph: `dependencies.json` identifies editable sibling imports, an ANYgeometry metadata/runtime version mismatch and an unavailable ANYmesher import probe. This is not an isolated final ecosystem qualification. The source/result-contract finding remains closed; no new actionable finding arose from these records. Supporting OpenAI review still does not constitute different-family qualification, and the scientific/formal F03 criteria and release gates remain unchanged and open.
