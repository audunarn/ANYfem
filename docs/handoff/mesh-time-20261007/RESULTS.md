# Meshing speedup — implementation and evidence

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
