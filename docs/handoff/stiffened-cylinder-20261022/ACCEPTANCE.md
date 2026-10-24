# Stiffened cylinder quad-first acceptance — 2026-10-22

The requested **linear** radius 1 m, height 5 m cylinder with 12 requested circumferential segments, longitudinal spacing 0.5 m, target size 0.25 m and the application's default automatic stiffener offsets meshes successfully. Its returned neutral mesh contains **1092 nodes, 767 quads, 52 triangles, 260 B2 beams and 273 offset couplings**. The actual separate-worker-process mesh job returns `ready` / solver-admitted data, preserves the source project, survives the public codec and builds a solver model successfully. This is development verification, not a structural solve, mesh-convergence study or release qualification.

## Change and ownership

- ANYmesh reuses existing canonical source-edge/vertex station identities and inherited shell division counts. The added linear validation checks complete segment coverage, unique source-edge beam ownership, exact beam station-chain connectivity and actual incidence on every adjoining shell face boundary. Separate offset chains and exact eccentric couplings remain intact. No coordinate welding, proximity search or second shell generation was introduced. New validation polls cancellation per face/edge and every 256 elements.
- ANYfem admits exact Cylinder faces with effective linear order and no custom parameterization. Existing planar behavior and unsupported higher-order/noncylindrical refusals remain.
- ANYgeometry retains both exact parent endpoint occurrences of a closed source member at a contact. The deterministic source member and target choices, structural validator, residual checks and scientific tolerances stay unchanged. Junction-based coupling is consumed once; the additional attachment is occurrence evidence, not a second physical constraint.

Existing dirty application/generator/sketch work was preserved. The task-only project.py delta is saved in `C:/ANYworkspaceWork/stiffened-cylinder-20261022/project-task.diff`; source hashes are in `source-manifest.json` there. No commits, branch changes, publication or native rebuild were performed.

## Verification

**117 distinct focused tests passed** across the following final source-relevant checks (the four focused geometry tests are included in the 80-test independent geometry run):

| Check | Result | Exact invocation |
|---|---:|---|
| Mesher cylinder / canonical merge | 8 passed | `C:/Python/Python314/python.exe C:/ANYworkspaceWork/stiffened-cylinder-20261022/run_checks.py mesh` |
| Public generated cylinder / recoverable job | 9 passed | same harness, `fem` |
| Independent geometry impact checks | 80 passed | `C:/Python/Python314/python.exe C:/ANYworkspaceWork/stiffened-cylinder-20261022/parent_check.py geometry-regression tests/test_closed_member_endpoint_junctions.py tests/test_batch_intersections.py tests/test_local_structural_validation.py tests/test_prepared_member_sheet_network.py -q` |
| Final planar / alias / actual process integration | 20 passed | `C:/Python/Python314/python.exe C:/ANYworkspaceWork/stiffened-cylinder-20261022/parent_check.py fem-final tests/test_quad_planar_boundary_bars.py tests/test_beam_boundary_aliases.py C:/ANYworkspaceWork/stiffened-cylinder-20261022/test_process_acceptance.py -q` |

Coverage includes complete source-chain ownership, periodic seam and reversed traversal, scalar/vector/automatic offsets, unrelated coincident topology not welded, malformed chain and missing shell segment refusal, persistence, job execution, cancellation and source nonmutation/nonpublication. The older station-merge fixture was corrected to two shell cells that actually consume its existing boundary midpoint; original identity checks were retained.

The broader curved check returned **20 passed / 8 failed**. The saved pre-change package snapshots reproduce the same eight topology-count failures in CH4, CH7, CH9 and CH13. They were neither modified nor waived. Exact commands and complete diagnostics are in the `parent-*-mesh-curved` and `parent-*-baseline-mesh` evidence directories. This work does not claim a globally green or release-ready repository.

Independent author review inspected the production paths and connected geometry change, found and resolved a missing cancellation hook and nonconforming old test fixture, and reported no remaining blocker. Reviewed production source hashes match `source-manifest.json`. A different-family cloud reviewer was unavailable because automatic approval review rejected the proposed Anthropic worker's data-egress/effective permission scope. The work continued through an OpenAI implementation worker and independent OpenAI author review; no external assignment ran and no different-family acceptance is claimed.

## Measured performance

Separate batch timings were noisy, including the unaffected beam-free control. The discriminating final comparison used two source-isolated long-lived processes, serial alternating AB/BA order, the same single allowed CPU, BLAS/OpenMP threads 1, two warmups and six measured samples per existing control. The target used one warmup and three measured samples. Mesh generation excludes imports/project creation. Before/after connectivity hashes and counts match on both controls.

| Model | Before median | After median | Observed change |
|---|---:|---:|---:|
| Beam-free cylinder | 3.132 s | 3.245 s | +3.60% wall; identical 3.078 s median CPU time |
| Planar stiffened panel | 0.1771 s | 0.1682 s | -5.04% wall |
| Requested stiffened cylinder | previously rejected | 2.714 s | no valid previous full-project runtime comparison |

The new boundary conformity check takes **1.534 ms median** on the target and 0.479 ms on the planar control. Both paired control changes remain within the prospective 10% material-regression threshold. This supports maintained speed for these measured cases; the negative planar change is not claimed as an optimization or general speedup.

Exact inputs, runtime module paths, per-sample wall/CPU times, ordered trials, source identity and connectivity hashes: `C:/ANYworkspaceWork/stiffened-cylinder-20261022/paired-192734672230/{results.json,summary.json}`. The owning timed harness command is `C:/Python/Python314/python.exe C:/ANYworkspaceWork/stiffened-cylinder-20261022/parent_check.py paired-perf`; full process-tree wall time was 72.3440948 s. Earlier noisy timing attempts and failed evidence remain intact.

## Limits

- Cylindrical source-boundary beams at quadratic order remain typed unsupported.
- Smaller generated rings and reversed member traversal pass. The extra height 5 m / ring spacing 1 m case creates 65 faces and retains its existing typed cylinder-atlas interval-operation qualification-budget refusal. This is now an explicit refusal/nonmutation regression. No atlas resource budget, certificate or scientific tolerance was changed.
- No full suite, installed-wheel/release gate, convergence study or physical solve was claimed. Eight pre-existing broader failures remain open.

Full worker handoff: [WORKER.md](WORKER.md). One living task record and all exact commands, exit/timeout states, precise process-wall budgets, stdout/stderr and preserved failures are under `C:/ANYworkspaceWork/stiffened-cylinder-20261022/`.

Final accounting: parent verification/performance process-wall 300.854872300/900 s; implementation-worker check process-wall 140.928835900/900 s. Final source-manifest comparison passed.
