# Native BL5 mixed J2 ANYfem consumer handoff

State: verified against assigned development consumer criteria; root final
artifact audit and acceptance remain pending. Historical source/test failures
are preserved; installed scoped impact verification passed.

The owned isolated ANYfem checkout started clean at
`df9d1d78b734de8a1d395a975c34ff5856e8c38f`. No main-checkout or sibling edits,
Git history changes, dependencies, versions or releases were performed.

`ExperimentalGEBL5Panel` adds the strict
`anyfem.experimental-ge-bl5-panel-native-mixed-j2-v6` schema for the public
`BL5_NATIVE_FIBRE_MIXED_SHELL_MATERIAL_V1_DEV` policy. It requires all-S3 or
mixed Q4/S3 topology and the exact public J2 curve type; mixed Q4 uses its
selected objective pullback. Staged/distributed input is rejected. Existing
v1–v5 policy/topology/record guards remain in place. The existing BL5 Project
selector dispatches v6 without a replacement API. The example preserves exact
native-definition bytes through standalone and Project round trips, public
preflight, force-seed and fixed-translation loading/unloading, named material
recovery, checkpoints, fresh restore and continuation.

Public S3, physical-fibre and generalized-member material outputs are retained
under their actual field names in workflow.json. Accumulated physical-fibre
history is filtered by actual recovered fibre element IDs, excluding generalized
members. The axial-only fixture yields fibres with elastic S3; the mixed biaxial
fixture demonstrates simultaneous shell/fibre yield. No changed physics or
scientific tolerance was used to correct the axial test premise.

Changed paths and exact SHA256 identities: `candidate-source-shas.json`.
Runtime source identities for all 99 consumer modules:
`source-binding-v1.json` and `source-binding-v2.json`.

Executed evidence:

- AST parsing passed for the source, example, test and two raw harness files.
- `source-v1/execution.json`: 79.47079709998798 process-wall seconds,
  201519104 sampled peak process-tree RSS; 7 passed, 1 failed. The failed check
  incorrectly expected S3 yielding for an axial-only fixture. Preserve its
  stdout/stderr/JUnit and complete workflow output.
- `source-v1-axial-metadata-replay.json`: corrected and remaining metadata
  assertions applied to the exact completed V1 axial workflow, SHA256
  `d51e8cf20b59960e75ac53e58e7fe2c58a08faf6902bf351a8b808e76127c186`.
  This is reused numerical evidence, not a fresh pytest/numerical pass.
- `source-v2/execution.json`: 4.935120200039819 process-wall seconds;
  only the strengthened strict v6 input/ownership test reran, 1 passed.
- V1's mixed workflow, typed refusal, pre-cancel/accepted-prefix cancellation,
  restore/mismatched-model refusal, continuation and legacy guard checks passed.
- `source-runtime-v1.json`, `source-runtime-v2.json`, `source-loaded-v1.json`
  and `source-loaded-v2.json`: exclusively owned ANYfem source and frozen
  installed solver imports; Python 3.14, NumPy2.5.3/SciPy1.18.1/Numba0.68.0/
  llvmlite0.50.0, affinity[0], effective numeric pools1, JIT disabled and no
  forced OPENBLAS_CORETYPE. Imported solver module hashes match the authoritative
  payload map and exact wheel
  `375e9e4525c93aaa4e1d75a26bbbd8fb506494064ae3a698e5ea67603c9b5a9b`.

Shared ledger: inherited 402.72211770003196 seconds plus the two supervised
attempts gives 487.12803500005975 used /112.87196499994025 remaining of 600.
The tiny stdlib metadata replay has no distinct process-wall receipt; its
combined shell call duration was 5.2350776 seconds, including the V2 child
4.935120200039819 seconds. Preserve this accounting uncertainty and do not
rerun to reconstruct it. Conservatively debiting the entire containing shell
envelope would leave 107.63688739994025 seconds, safely double-counting its V2
child. The replay child's exact duration remains UNKNOWN. Root is resolving
this ledger adjustment; no further numerical jobs are authorized or active.

Scientific acceptance, mandatory independent/different-family obligations,
full programme, capability qualification, release and publication remain OPEN.
Root owns installed artifact acceptance, Git integration and final acceptance.

## Installed consumer outcome

Exactly one offline ANYfem wheel build and one installation were executed.
Wheel SHA256:
`0e1ae7b3324410cfd64c4ba1dfd8810e1b6e239de22ba8bfeedcf9bb55cb0315`.
All 99 source/wheel/installed Python module hashes and the known complete package
payload matched. Metadata retained ANYfem 0.4.1 and Python >=3.11; see
`artifact-provenance-v1.json`. Eight immutable test/example/helper input files
are preserved under `installed-input-snapshot-v1/`, with hashes in its manifest.

Build: 4.247954800026491 child-wall seconds. Install: 1.6463941999245435 seconds.
Installed V1 failed before pytest/numerical cases, after 2.155615600058809 seconds:
the harness incorrectly assumed ANYmaterial originated in the numerical CI site.
Its actual readonly source origin and SHA were verified against the frozen solver
evidence by root. Only that unsupported assertion was repaired. The existing
failure stdout/stderr/runtime/receipt remains immutable.

Installed V2: seven explicit impact cases passed, 42.113199800020084 child-wall
seconds, sampled peak process-tree RSS 199073792 bytes. Its mixed biaxial/coarse-P1
Project workflow demonstrated generation-3 material history and generation-4
unloading/continued recovery, actual S3/fibre/generalized material records, exact
native input bytes, force-seed/fresh-owner checkpoint restore and equal continuation
bytes. Strict v6 ownership/refusal, cancellation/restart, and four legacy metadata
checks passed. This is installed seven-case evidence; the all-S3 axial complete
workflow remains the source V1/replayed evidence and was intentionally excluded
from installed execution. It is not an installed eight-case or scientific gate.

Actual installed origins, loaded module hashes, readonly sibling dependency
paths/hashes and effective pre/post-test runtime are preserved in
`installed-loaded-v2.json`, `installed-dependency-bindings-v2.json`,
`installed-runtime-v2.json`, and `installed-post-runtime-v2.json`. Affinity [0],
Numba threads 1, all numerical pools 1, JIT off and unforced OPENBLAS_CORETYPE
were verified. ANYmaterial's root-confirmed __init__.py SHA256 is
`46d9c80639435e40c8533d78d89364c51f1cef65969b1c418eab4f36a7512900`.

Final shared ledger: 542.7474880000897 seconds used /57.252511999910325 remaining.
It separately records the root-adopted conservative replay envelope 5.2350776s
and byte-only artifact-binding envelope 0.221211s, both with exact-child timing
UNKNOWN and the original commands. These are conservative envelopes, not precise
child timings. The prescribed >=20-second root audit reserve is retained.
`final-manifest.json` binds the exact receipts, JUnit, runtime/import maps, input
and artifact identities, successful workflow history scope and remaining gates.
No numerical jobs remain active. The isolated product source is unchanged since
source V1. Root reported an external concurrent main-checkout change; this worker
did not reconcile, edit or deliver that unrelated state.


## Versioned installed all-S3 axial completion

The original V2 handoff, manifest, runner, entry, selector and immutable input
snapshot/artifact bindings remain byte-for-byte unchanged; their identities are
recorded in protected-v2-identities-v3.json. Versioned V3 executes only the existing
corrected all-S3 axial/fibre/P0 Project workflow against the same consumer wheel,
installed site, solver wheel and input snapshot. It passed, consuming
19.295670800027438 precise child-wall seconds, sampled peak tree
RSS 197152768 bytes. S3 alpha remains 0.0 and actual physical
fibres yield; generalized members are absent. Generation-3 loaded histories and
generation-4 unloading/recovery, case and Project persistence, exact native bytes,
force-seed/fresh restore and byte-identical continuation all passed.

Installed evidence now covers eight UNIQUE existing cases by impact: seven V2
plus one V3. No full-eight campaign was rerun and no new physics/material/load
case was introduced. Source V1's test-premise failure/metadata reuse and installed
V1's pre-test harness failure remain preserved and honestly classified.

Before final bookkeeping, the ledger records 562.3846062001171 seconds
used /37.615393799882895 remaining, including the separately adopted
0.3414474-second final-V2 bookkeeping envelope with precise-child timing UNKNOWN.
This final metadata step runs in a supervised child; its precise duration and
updated remaining authority are recorded afterward in bookkeeping-v3/execution.json
and ledger.json. A >=20-second root audit reserve remains protected. Scientific,
different-family, programme, release and final root acceptance obligations stay
OPEN. final-manifest-v2.json binds the versioned evidence without altering V2.
