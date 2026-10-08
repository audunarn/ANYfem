# Meshing speedup — implementation and evidence

## Single-process milestone — completed 2026-10-07

Implemented and accepted for the requested G8/G1 and M1–M6 speedup on the
recorded frozen workspace inputs. All 54 alternating performance runs passed;
all six barge pairs were faster, and all seven representative medians improved.
Every compared mesh and authored-geometry digest matches exactly, without
coordinate rounding. The complete authored barge project also matches across
all 12 runs. Loads, materials, tolerances, quality/admission requirements,
candidate search and execution-policy defaults are unchanged.

The measured source was frozen before delivery on `codex/single-process-speedup` in
`C:/ANYworkspaceWork/meshing-speedup-next-20261007/worktrees/{ANYgeometry,ANYmesh,ANYfem}`.
Mistral authored the geometry and mesher slices; Astra supplied contracts,
independent review, ANYfem integration and acceptance of the recorded campaign.
Git delivery was subsequently authorized; its current status is recorded below.
No version bump or package release is included. Earlier delivery below is
historical. Unrelated source edits in the primary checkouts were preserved.

### Delivered changes

- ANYgeometry: invocation-local conservative planar boundary/curve boxes prune
  attachment clipping only after cancellation and whole-curve support checks.
  Expanded bounds round outward; unsupported/curved/ambiguous cases retain
  exact clipping. Scalar LinePath and private cross products preserve arithmetic,
  production np.float64 inputs and exceptional numeric behavior.
- ANYmesh: public BVH construction remains eager and preserves snapshots/errors;
  the connectivity-only route validates and snapshots shell inputs immediately
  but builds records/tree only on first lookup. Invocation-local edge screening
  confirms winners/tolerances with scalar norms and clears/restores caches.
  Chart queries batch selected faces per unchanged preparation stage, forwarding
  cancellation/revision controls with older-owner fallback. Native orientation,
  point-in-polygon and successful linear validity use vectorized checks while
  retaining scalar/exact fallbacks, original error precedence and Python sum order.
- ANYfem: outer geometry counters join existing runtime execution diagnostics
  and survive process jobs. Runtime measurements do not enter solver-affecting
  hashes. Public APIs and dependency ranges are unchanged.

### Measured performance

Each row is three alternating baseline/candidate pairs (AB, BA, AB), all retained.
Numbers measure the public meshing call; exact process-wall accounting and stage
timings are in the attempt records. Improvements are for the combined candidate.

| Workload | Baseline median (range), s | Candidate median (range), s | Reduction |
|---|---:|---:|---:|
| Large barge, off | 54.340 (53.836–75.592) | 32.909 (32.418–34.140) | 39.4% |
| Large barge, auto | 59.958 (59.762–60.083) | 39.200 (38.785–39.273) | 34.6% |
| Mapped face | 1.254 (1.249–1.275) | 0.170 (0.169–0.174) | 86.4% |
| Stiffened panel | 1.277 (1.256–1.307) | 0.694 (0.684–0.694) | 45.7% |
| Crossing grid | 7.786 (7.722–7.807) | 7.162 (7.139–7.187) | 8.0% |
| Native plates | 3.477 (3.474–3.504) | 2.423 (2.395–2.433) | 30.3% |
| Plate with hole | 10.368 (10.230–11.628) | 6.312 (6.150–6.535) | 39.1% |
| Independent structures | 14.653 (14.635–14.661) | 10.705 (10.699–10.914) | 26.9% |
| Curved cylinder | 0.142 (0.142–0.148) | 0.116 (0.114–0.123) | 18.3% |

The 0.25 m barge retains 12,100 nodes, 8,916 shells, 3,112 beams and 3,500 couplings,
with four candidates and exactly one final connectivity application in every run.
Attachment clipping falls from 31,620 to 2,008 calls: 29,612 conservative rejections
(93.65%). Off-policy median structural preparation is 30.264 → 9.765 s; its nested
remapping stage is 23.817 → 3.677 s. Connectivity is 7.871 → 7.840 s. These nested
stage times must not be added together or interpreted as isolated patch effects.

The mapped case has 37,249 nodes and 36,864 shells: unused BVH builds fall from
one to zero, with zero lookups in both versions. Chart qualification changes from
33 single queries to one batch for the crossing grid, four to one for native
plates, and 192 to one for the independent-structure preparation. Selected-face
qualification counts remain unchanged. No representative exceeded the allowed
regression threshold; all representative pairs were faster as well.

### Correctness and installed verification

All 72,600 displacement components, 19,032 reaction components, 11,920 element
stress peaks and 38,344 von Mises samples match exactly under unchanged loads.
Strain energy is exactly 145467.31573723748 J in both runs; its relative change
and all recorded maximum response differences are zero. Mesh correspondence is
verified before comparison. No load calibration, geometry change or tolerance
adjustment was used.

632 selected installed-artifact tests pass: geometry 104; mesher speedup 273;
mesher lifecycle/coupling/native contracts 142; ANYfem consumer integration 97;
analytical/convergence 8; public parallel integration 8. The existing 4/8/16-worker
routes retain mesh signatures and hashes. The public process-based large-barge
job matches the same exact mesh digest and four/one lifecycle counts.
Compiled and genuinely binary-free fallback wheel smokes both pass, including
the protected curved-cylinder case (408 nodes, 416 elements).

One opt-in experimental prepared-CURRENT-receipt module was skipped; its separate
execution gate was not bypassed. Broader legacy Python/quadratic schema6 and
quality-optimization sweeps remain timeout-limited: corresponding bounded parent
checks also time out. These are preserved limitations, not passing full-suite or
release-qualification claims. Focused affected contracts and public workloads
above supply this milestone's acceptance evidence.

### Preserved failures and review repairs

Development fixtures, earlier timeout attempts and worker checkpoints remain on
disk. Review repaired public BVH eager behavior, cache cleanup, cancellation and
mutation controls, exact error ordering and extreme-number behavior. The first
installed multiprocessing test attempt exposed a missing entry-point guard in
the new verification launcher; the launcher was fixed and the full parallel and
consumer suites then passed. A serial-adapter mock failed on the parent because
default auto routing bypassed its interception. The test now explicitly selects
off and uses a mutable mesh stand-in for runtime diagnostics; it passes on both
parent and candidate. This post-freeze repair changes only a test, not measured
runtime source or wheels. Its exact content/hash is retained in final-review.

An early baseline timeout termination was denied by Windows and was completed
by terminating only the verified owned tree; its full 200.119382 s duration is
charged. Later jobs use reliable process-tree permissions. pip's owner-only ACLs
were repaired only within this task's isolated artifact directories, with no byte
changes. Failed instrumentation preflights are preserved. The automatic approval
review initially misattributed a separate chat's pause to the parallel check;
the identical authorized action was approved after clarifying task ownership.

### Reproduction and identity

Evidence root: `C:/ANYworkspaceWork/meshing-speedup-next-20261007`.
Baseline heads: ANYgeometry `d7a920e107b270377656390772202867c77f4fc8`, ANYmesh
`2d0afaf34179faf9eedfcc00090ef7ddfb4b740e`, ANYfem
`2ca462a62d7f754a3c76af5db65201b8dca40b92`. The baseline freezes the existing
dirty ANYfem/dependency overlays; candidate sources apply only the owned deltas.
Acceptance is scoped to those explicit snapshots, not an evolving checkout.

- `baseline-manifest.json`, `candidate-manifest.json` and the two
  `artifacts/*/manifest.json` files retain full source/wheel/install hashes.
  Both native extensions were independently rebuilt. Baseline native SHA256:
  `429a6dab690c7d175719d90f0c3ba7bfc0b6780ec2e479b3aa3ecdcb08108c70`;
  candidate: `b0102a2ab6b1daa56da7a2a11290f114b8cda5fe5e50c377168c3fb8f5adacbc`.
- `accepted-barge-runs.json`, `accepted-representatives-runs.json` and
  `performance-summary.json` identify every accepted run, ranges, counts, stages
  and exact hashes. `measurement-tools/manifest.json` freezes executable probes;
  `representative-inputs/manifest.json` freezes authored representative models.
- `response-comparison.json`, both `engineering-*/data` directories and their
  NPZ files retain the physical response comparison. Installed tests record
  actual import/native paths; pytest's source-path override is disabled.
- Python 3.13.9, NumPy 2.4.3 and SciPy 1.16.3 were used. Every timed run records
  actual BLAS library identity and one numerical thread. `dependency-audit.json`
  separately records the post-run distribution/hash audit. No shared dependency
  install or native binary was overwritten.
- `final-artifact-audit.json` verifies that all recorded baseline/candidate
  artifact bytes remain unchanged and every candidate installed Python file
  exactly matches its frozen source. External investigations remain unchanged
  under `external/` as historical evidence.

Owned timed jobs were sequential and the sustained matrix was coordinated with
the separate static-batch task. No pending job reservations remain. Exact
cumulative accounting, including failed and timeout attempts:

- geometry-checks: 421.880434 / 1800 process seconds, 14 preserved attempts.
- mesher-checks: 1636.447523 / 2400 process seconds, 24 preserved attempts.
- fem-checks: 457.941720 / 3600 process seconds, 30 preserved attempts.
- build-checks: 37.375220 / 1800 process seconds, 8 preserved attempts.
- performance: 945.002466 / 3600 process seconds, 54 preserved attempts.

## Delivery and verification follow-through

The measured changes are committed and pushed as draft pull requests:

- [ANYgeometry #30](https://github.com/audunarn/ANYgeometry/pull/30):
  `3b98599473d1e795ddbebf94dca539042e0d3628`.
- [ANYmesh #11](https://github.com/audunarn/ANYmesh/pull/11):
  `e764c751c471e03aa7005bca8bb47b3e84f33c5a`.
- [ANYfem #12](https://github.com/audunarn/ANYfem/pull/12):
  implementation `81090570d351d89dbdd08ef077d85386960eee88`, followed by
  integration merge `3aa540ccb03d3187dfcfa695795df4ffbd9627ec` of main
  `3e4eb7f206042931d496b98f39af5e2a40b5951f`. Ninety consumer tests passed
  against that combined source in 17.222334 process seconds. This integration
  check does not replace or relabel the frozen performance measurements.

Delivery CI exposed existing main failures. ANYmesh development smoke has the
same four failing node IDs on main run 37663276646 and candidate run 37690580036:
finite-hull legality nonconvergence in `test_auto_mixed_faces_share_registry_identity`,
and three recovery-hint failures against the old pinned geometry. ANYfem's clean
installed build cannot import `Envelope` from the CI-pinned ANYloads revision, identically on
main run 37685840570 and candidate run 37690586051. Raw logs are preserved in
the evidence root as `delivery-*-ci*.log`. No failed gate is waived; draft PR
delivery is distinct from main integration or release approval.

The user authorized Git delivery followed by investigation of the two preserved
verification timeouts. The measured implementation is being delivered first as
three reviewable commits; disposable worker notes and diagnostics stay outside
the staged change. ANYfem main advanced by three independently delivered commits
since the frozen campaign. Integration checks will distinguish the newly merged
source state from the immutable sources that produced the measurements above.

The follow-up question is whether the Python/quadratic schema6 and mesh-quality
checks are slow but terminating, stuck in fallback refinement, or affected by the
new fast paths. Inspect the existing output and test/source paths first, then use
one bounded verbose/stack-reporting run per affected path to identify the active
stage. Compare baseline only where the result discriminates a cause. Preserve
all prior failures and cumulative accounting; do not change scientific tolerances,
mesh-search budgets or example geometry to make these checks pass. No new speedup
scope is authorized by this follow-through.

### Verification follow-up outcome

The broad mesh-quality timeout is resolved by a separate correctness commit,
`c1b2115`, after the measured speedup commit. Nonintrusive instrumentation of
the original rotated-plate test captured a repeated finite-hull state at
iterations 11 and 13. The four-station regression uses exact binary inputs and
an independent Fraction determinant: the old in-circle filter returned wrong
signs in 6 of 24 point orderings. Its error bound used cross products after
cancellation. Bounding their constituent products instead retains the existing
Decimal fallback and stops the demonstrated flip cycle. Scientific tolerances,
candidate search and iteration limits are unchanged.

Mistral's independent read review supports the minimal predicate correction.
Astra implemented the repair after two bounded worker investigation runs ended
without a reliable witness or final handoff; their useful checkpoint and failed
probes remain preserved. The successful witness observes the original function
without copying it. The earlier worker claim of duplicate automatic recovery
routes was incorrect: with quad options the routes are quad_first, auto, native.

- Before repair: exact-oracle check **6 failed, 18 passed**.
- Source repair: **50 passed**, including the complete mesh-quality optimization
  file and the mixed-face test that also failed in main CI; 40.396385 process s.
- Fresh independently built repair wheels: **266 passed** across the predicate,
  mesh-quality, hybrid, recovery and native-validation tests; 43.393396 process s.
  Artifacts live in `artifacts/verification-repair`, with separate hashes and
  build inputs. The original baseline/candidate measurement artifacts remain
  unchanged. No new performance campaign or revised savings claim is implied.
- One correctness-only barge run with the fresh repair wheels exactly matches
  the measured candidate mesh/material hash and authored geometry hash, with
  four candidates, one connectivity application and unchanged operation counts.
  Evidence: `fem-checks/delivery-repair-barge-equivalence` (35.124834 process s).
  This was an equivalence check, not a new before/after timing pair.
- ANYgeometry CI ran **2,883 passing tests and one failing fixture** on both
  Linux and Windows. NumPy changed its exception wording. Test-only commit
  `2879048bbe5f779992220ad44c2cd2f4e3edd3cb` compares the wrapper's exception with
  the installed NumPy oracle; **19 focused tests passed**. Production geometry
  is unchanged; the CI rerun is pending at this checkpoint.
- ANYmesh development smoke now pins that verified geometry owner in commit
  `0f555088b41093cf6b4c2c8532a526fbc525da14`; historical qualification inputs
  and public dependency ranges remain unchanged. The previous owner pin lacked
  the contracts required by three existing recovery tests. Both push and PR
  development smoke checks are now green.

**At the preceding checkpoint (superseded by closure below):** the unchanged schema6 inspection-only test now reaches its own
120-second recovery budget and raises `MeshRecoveryIncomplete` at triangle
recombination start (125.737436 process s). A bounded probe observed 1,280
distinct finite-hull states for 796 points and 1,485 triangles in 33.47 s, without
a repeated-state witness. This is remaining Python-path work, not proof of a
second cycle. A local cache experiment matched exact connectivity and callback
sequences on six reference inputs but did not close the original budget gate
(125.176489 process s). The cache was removed from delivered source; its patch,
review and evidence are preserved. No test budget or assertion was relaxed.

The draft PRs therefore deliver the measured improvement and the separately
verified predicate repair, but do not establish complete verification or main
merge readiness. The schema6 gate and unrelated ANYfem pinned-dependency CI
failure remain explicit limitations. No main branch or package release was
modified by this delivery. Primary local main branches matched their fetched
GitHub main refs at the delivery inspection.

Evidence: `finite-hull-witness.json`, `mesher-checks/gap-*`,
`verification-gap-worker-01.log`, `verification-gap-worker-02.log`,
`verification-gap-review.log`, `verification-gap-cache-review.log`,
`legality-cache-comparison.json`, and
`predicate-and-legality-cache-investigation.diff` under the evidence root.

### Authorized integration closure — 2026-10-08

**Delivered:** ANYgeometry PR #30 merged as
`a534b0d3782af9007b71edb30b923fc96232be67` after all eight CI checks passed.
ANYmesh PR #11 merged as `2273c61fe1e888302a02b5f376c66fe5ae8edadb` after
both new-head development smoke checks passed; twelve qualification/release jobs
were skipped by their existing conditions. Local main checkouts match those
merges. No version bump or package release occurred.

**The unchanged schema6 gate is closed.** The remaining 849-point candidate
cycled between states 190 and 192. The captured four binary coordinates form an
exactly cocircular rectangle, but the old 80-digit Decimal in-circle fallback
returned nonzero residues in all 24 orders. The before-repair regression recorded
24 failures and 24 other passes. Ambiguous in-circle evaluations now use exact
Fraction arithmetic, preserving the filter, tolerances, orientation convention,
first-two-coordinate behavior and signed-infinity conversion for extreme values.
The finite-hull loop maintains incidence and a sorted pending queue while
preserving the first admissible flip, convergence limit and candidate budgets.

Evidence under `C:/ANYworkspaceWork/meshing-speedup-next-20261007`:

- `schema6-cycle-witness.json` / `schema6-cycle-points.npy`: original binary input
  and repeated-state witness; failed pre-repair runs remain in `closure-checks`.
- `rational-predicate-review.log`: independent Mistral review confirmed the exact
  formula and raised the float-overflow compatibility issue, which was repaired.
- `independent-hull-review-01`: 30 exact differential cases and all five
  cancellation phases passed against frozen before/after loop implementations.
- `schema6-rational-gate-01`: unchanged schema6 test plus 53 predicate/hull checks
  passed, 25.727445 process seconds. This is verification timing, not a new barge
  speedup measurement.
- `final-installed-mesher-01`: **323 passed**, 46.404926 process seconds, including
  all schema6 consumer cases, recovery, hybrid meshing, quality optimization,
  predicate witnesses, hull differential tests and cancellation.
- `final-installed-fallback-02`: **73 passed, 5 compiled-only skips**, 2.892482
  process seconds, in a separate installation with actual extension absence.
  The first harness attempt incorrectly treated the build-only disable variable
  as a runtime switch; its failed availability assertion is preserved as
  `final-installed-fallback-01`. No product failure was hidden.
- `final-barge-equivalence-01` and `final-process-barge-equivalence-01`: public
  synchronous/off and process-job/auto routes both completed with exact matching
  unrounded mesh and authored-geometry hashes. Each retains 12,100 nodes, 8,916
  shells, 3,112 beams, 3,500 couplings, **four candidates and one connectivity
  application**. Auto correctly selected serial work for the single component.
  `closure-barge-comparison.json` records the comparison. The existing fixed-load
  response evidence remains applicable to this unchanged mesh; no new solver
  response or performance campaign is claimed.

Fresh final wheels and identities are in `artifacts/verification-closure-final`.
Original baseline, measured candidate and preceding repair artifacts were not
overwritten. The source repair is separately reviewable in mesher commit
`7b16c1e74cefc9bf269f2a28a5689b1ce906b009`.

**Remaining ANYfem integration blocker:** Envelope is absent from committed
ANYloads main `8fdb08de69c8e2ccfe7be9a746e6b4cd10c68d0b`; it exists only in five
pre-existing local dirty files. No ANYloads source or Git state was changed.
The existing owner suite passed **64 tests** (2.082165 process seconds).
`loads-envelope-prerequisite.diff` and `loads-envelope-prerequisite-review.md`
make the exact proposed prerequisite delivery reviewable. The user explicitly approved publishing this fourth-repository prerequisite
on 2026-10-08. Delivery and dependency integration are now in progress.

ANYfem CI edits are prepared locally for merged geometry/mesher and the committed
solver prerequisite `d109511ea953e5e5946d72ea6b163cc347b89417`. The newer solver
`b246a985` was not adopted because its Linux/Python 3.14 shard has two unresolved
qualification-record blob assertions. The selected predecessor's Q4 source blob
matches the expected `bd6c0dc2cb3661eb73942fc0fea56e527aa8d0b4`; its lack of GitHub
checks is not called a green CI result. **120 ANYfem load-series/static-batch
consumer tests passed** against that exact frozen source and the reviewed local
Envelope code (`fem-owner-pins-01`, 34.994408 process seconds). The loads prerequisite is now published in ANYloads PR #1 at
`16b1179de9623fc065bf034b0995b02dd5e47f86`; the four exact CI owner pins
are being delivered together in ANYfem PR #12 for CI verification. ANYfem PR #12 is not
merged or claimed ready; primary main remains synchronized at `3e4eb7f`, with
unrelated dirty edits preserved.

Execution accounting: the user authorized this follow-through after the earlier
mesher ledger consumed 2,123.838313 of 2,400 seconds. A separate prospective
closure cap of 1,200 process seconds did not reset earlier authority. The first
Mistral allocation was 300/900 worker/parent, later 500/700 within the same total.
Two useful but incomplete worker runs exhausted their turn limits before final
handoff. After inspecting their source, tests and failed outputs, Astra took over
because repeated turn exhaustion prevented reliable delivery; unspent worker
allocation returned to the same total reserve. Failed diagnostics and harness
attempts are included. The current ledger records 23 completed attempts,
**536.471040 seconds consumed / 663.528960 remaining**; there are no active
owned numerical jobs. The original schema6 120-second budget, assertions,
scientific tolerances, fallback order and quality gates were never changed.

Next action after the pending scope decision: deliver the reviewed Envelope
prerequisite in its own PR if authorized, complete the exact loads CI pin,
resolve ANYfem checks and merge #12 only when its applicable gates pass. No new
optimization milestone is opened.

## Earlier milestone and main delivery — historical

## Outcome and scope

Accepted for the authorized performance milestone on 2026-10-07. Implementation,
focused package/consumer checks, public process jobs, fixed-load response
comparisons and isolated installed-wheel smoke checks are complete. No version,
publication or Git-history changes were made.

The user-approved replacement plan supersedes PLAN.md. Different meshes were
permitted, while authored geometry, loads, materials, tolerances, quality limits,
solver admission and freshness requirements remained fixed. This implementation
retains four candidates on the large-barge fallback chain and applies connectivity
once, after selection and shell repair. Regional remeshing and new process-pool
architecture remain outside scope.

Existing ANYfem edits were preserved. Three concurrent viewport/test edits were
also preserved in the frozen source; the timed headless workflow does not exercise
viewport behavior. All owned live source files match the verified final snapshot.

## Changes

- ANYgeometry: invocation-local dependency bounds cache and incremental propagation
  of consumer/dependency pairs through complete ownership closure; descendant-face
  material-domain/effective-tolerance caches in attachment remapping. Refusals,
  deterministic evidence, mutation/cancellation guards and clipping arithmetic
  remain unchanged. No persistent geometry cache was introduced.
- ANYmesh: invocation-local face/edge filters, shell-node membership, tolerances
  and first-record coupling lookup. Public BVH iterable coercion is preserved;
  only a private normalized filter bypasses repeated normalization.
- ANYmesh: private retained candidate context owns its working geometry, pipeline,
  maps, mesh and reports through the structured/native/refined/conservative chain.
  Selected shell repairs and S3 qualification precede connectivity. Mesh replacement
  invalidates prior S3 admission. Final quality, admission, beam/shell coupling
  references, source remapping and publication freshness are checked before return.
  Standalone native and quad-first behavior remains compatible.
- ANYfem: copies owner runtime diagnostics into Project/job execution diagnostics
  and excludes the exact hybrid_diagnostics.runtime block from semantic hashes.
  Public meshing signatures and execution-policy defaults are unchanged.
- benchmark.py records runtime/source identity, stage timings, candidate/connectivity
  counts, planner bounds calls, public process jobs and fixed-load response arrays.

Mistral authored the coherent geometry and mesher slices. Astra reviewed them,
owned ANYfem integration, and completed final repairs/checks after repeated mesher
continuations exhausted their tool limits without a handoff. Details and failed
attempts remain in the development history and worker logs; completed work was
preserved rather than restarted.

## Measured performance

Large full barge, 0.25 m, compiled runtime, three alternating baseline/candidate
pairs for each policy. Times below are the complete Project.generate_mesh call;
precise outer process-wall times are separately recorded for every attempt.

| Policy | Baseline median (range), s | Candidate median (range), s | Reduction |
|---|---:|---:|---:|
| off | 142.295 (142.019–142.946) | 95.605 (95.243–95.907) | 32.8% |
| auto | 185.944 (185.496–186.056) | 104.387 (104.250–105.479) | 43.9% |

All 12 final measurements produced the same material mesh: 12,100 nodes,
8,916 shells, 3,112 beams and 3,500 coupling records. Four top-level candidates
were retained; connectivity applications fell from four to one. Auto correctly
remained serial for one connected component. Planner bounds calls fell from
951,172 to 1,186 (802-fold fewer).

Auto stage medians: planning 43.166 -> 9.045 s; connectivity 59.300 -> 14.022 s;
structural preparation 54.885 -> 53.856 s. Preparation remains the largest
remaining cost. Nested substage timings must not be added to their parent timings.

The real process-based mesh-job route completed in 189.421 s for the baseline and
107.445 s for the candidate, with identical material mesh data and one candidate
connectivity application. These are individual public-route checks, not additional
three-pair statistics.

Representative workloads:
- Basic 5 m example, three alternating pairs: median 17.445 -> 17.505 s (+0.34%,
  0.060 s); ranges 17.397–17.445 and 17.363–17.786 s overlap. The initial +2.2%
  sample did not persist as a consistent end-to-end slowdown. Mesh data is exact.
- Large curved-opening example without bulkhead stiffeners, one comparison:
  84.856 -> 83.068 s; identical 11,682-node material meshes. No statistical
  speedup claim is made from that single pair.

The earlier 115.6 s investigative run is not before/after evidence. The failed
preparation-interception probe supplies no conclusion and remains preserved.

## Engineering comparison

The baseline and candidate large-barge meshes were solved from captured project
and mesh files, with unchanged loads/materials/supports and no recalibration.
Node/element locations and associations match, allowing direct field comparison.

| Quantity | Observed difference |
|---|---:|
| 72,600 displacement DOF values | relative L2 1.42e-12; max absolute 2.89e-13 |
| 19,032 reaction components | relative L2 1.58e-12 |
| 38,344 von Mises samples | relative L2 7.57e-13; max absolute 0.00198 Pa |
| Strain energy | relative difference 4.11e-13 |

Strain energy is approximately 145,467.315737 J, maximum translation 0.088421039 m,
and peak von Mises stress 200 MPa in element 14231 for both runs. Differences are
at numerical precision; no material response difference required refinement.
Full arrays, sample offsets, IDs and metrics are preserved in response.npz/json.

## Verification

- Final ANYmesh slice: **82 passed**, covering candidate routes, selected working
  ownership, S3/junction repair, zero connectivity on complete quality rejection,
  one final connectivity application, cancellation, stale publication, final
  coupling references, public BVH coercion and face/edge/sheet/quadratic coupling.
- ANYgeometry cache/remapping/partition and affected intersection suites passed;
  per-attempt counts and environment-gate repairs are in its owner HANDOFF.md.
  Astra independently compared **64 complete old/new component-plan records**
  (membership, bounds, refusals and deterministic evidence), all exactly equal.
- ANYfem integration: **107 passed**. Its opt-in parallel module was then enabled
  explicitly and passed **8 tests**, including real worker-count/hash compatibility.
- The existing analytical verification suite passed **21/21 cases** at unchanged
  tolerances. No scientific tests, loads or acceptance thresholds were relaxed.
- Isolated wheels built/installed: ANYfem 0.4.1, ANYgeometry 0.4.5 and compiled
  ANYmesher 0.5.1. Owner mapped/native/cylindrical/structured smoke, installed
  Project.generate_mesh and installed large-barge process-job checks passed.
  Imports resolved to the isolated installed directory and all four compiled
  capability flags were true. Installed large-barge material data matches the
  frozen source result; it reports four candidates and one connectivity application.
- Final diff whitespace checks passed. No owned source differs from the final
  candidate manifest. Existing dirty/concurrent work was not reset or discarded.

## Preserved limitations and failed evidence

The barge's existing ordered connectivity diagnostics are **exactly unchanged**:
6,971 first-record coupling conflicts, 36 unresolved attachments, 90 unsupported
attachment targets and 184 junction residuals. This milestone preserves that
public behavior; it does not claim those legacy diagnostics are resolved or
requalify their physical meaning. Materialized couplings now must reference the
final repaired beam/shell mesh before structured/fallback publication.

A provisional blanket refusal of every connectivity issue was too broad for the
existing contract and failed the barge check. Its snapshot and failure output are
preserved as candidate-report-gate and performance/final-auto-01-candidate. The
final implementation preserves conflict/refusal reporting and validates actual
coupling references instead. Baseline/candidate diagnostic equality is recorded
in connectivity-diagnostic-comparison.json, with complete baseline reports saved.

Existing Python finite-hull failures reproduced against the frozen baseline;
they were not concealed or changed. The historical source-bound fixture still
requires its pinned old geometry wheels; its pins were not weakened. Broad
quality-optimization sweeps and one geometry alphabetical sweep timed out or were
interrupted and are not claimed as passing. Final acceptance uses the affected
suites and public workflows above; this is not a full-suite or release qualification.

## Reproduction and evidence

Evidence root: **C:/ANYworkspaceWork/meshing-speedup-20261007**.

- baseline-manifest.json and candidate-manifest.json: exact source hashes,
  compiled extensions and heads (ANYfem dbee56d, ANYmesh b981487, ANYgeometry
  660430a). Baseline includes the original dirty ANYfem source.
- performance-final-runs.json identifies the 12 accepted measurements explicitly;
  performance-summary.json and acceptance-timings.json contain ranges, medians,
  stage timings, operation counts and representative checks.
- response-comparison.json plus fem-checks/large-response-{baseline,candidate}/data.
- installed-manifest.json records wheel hashes and installed Python source hashes;
  final-wheels contains all three verification wheels.
- Every timed attempt has command.json, result.json, full stdout/stderr and runtime
  identity where applicable. Python 3.13.9 was used, with effective BLAS/OpenMP
  thread counts of one. Owned timed numerical jobs ran sequentially.
- ANYgeometry evidence: C:/Github/ANYgeometry/.local-evidence/mesh-time-20261007.
- ANYmesh evidence: C:/Github/ANYmesh/reports/mesh-time-20261007.
- RESULTS-development-history.md and worker logs preserve the implementation
  transitions, partial work, failed fixtures and review decisions.

The preliminary successful off matrix is retained but excluded from final medians;
its snapshot is candidate-initial. The unchanged auto baseline already in flight
when review tightened publication was retained as the first baseline in the final
alternating matrix. Failed candidate attempts were preserved and charged, not
included in successful timing statistics.

Resource accounting kept every completed attempt and outstanding reservation.
A prospective transfer of 600 unused seconds from the completed mesher-check
allocation to performance verification left their combined 5,400-second allocation
unchanged (mesher 2,400; performance 3,000). Performance used 2,436.965895 process
seconds including preliminary/failed attempts. Earlier missing worker result files
retain conservative timeout reservations and explicit accounting uncertainty; no
precise duration or test-pass claim was reconstructed by rerunning them. No
consumed execution authority was reset.

## Main delivery, 2026-10-07

The user subsequently authorized updating local and GitHub main for all three
repositories. Delivery includes the speedup implementation, its focused tests,
this evidence note and benchmark.py. Earlier ANYfem application, connection,
load-series and viewport edits remain outside these commits and are preserved
locally. No package version, tag or release is changed.

Before committing, the ANYfem delta was applied to an archive of its clean main
source, independently of those earlier edits. The runtime-diagnostics, mesh-job
snapshot and parallel-lifecycle suites passed there: 14 tests, 7.937001 process
seconds. Evidence: fem-checks/main-delivery-clean-commit under the evidence root.
The large-barge timing and engineering measurements above remain scoped to the
recorded frozen dirty-source identities; they are not new clean-main measurements.
The delivery snapshot preserves all outstanding files and hashes under
main-delivery/preserved and main-delivery/before.json.
