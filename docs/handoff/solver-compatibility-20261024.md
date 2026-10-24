# Updated local solver compatibility — 2026-10-24

Outcome: adapt ANYfem to the authoritative updated local ANYsolver main,
without changing solver mathematics or unrelated application work.

Risk/acceptance: development compatibility, not solver qualification or release.
Policy: ANY_ECOSYSTEM_RISK_PROPORTIONATE_ENGINEERING_V1, revision 2026-09-24.2,
`C:/Github/ANYopenSoft/governance/ECOSYSTEM_PHILOSOPHY.md`.

Plan and acceptance:
1. Compare current public solver contracts and effective runtime with ANYfem
   model/build/solve/job/result consumers; record concrete incompatibilities.
2. Repair the connected integration and regression coverage, preserving
   historical project formulation selection, failure/cancellation semantics,
   physical assumptions and scientific tolerances.
3. Run focused real-solver workflows, wrapper/refusal/result regressions and
   independent source review; report remaining limits with exact evidence.

Principal question: do updated shell laws require consumer changes, or only
updated provenance/tests/documentation? Compare public declarations, run current
integration tests, then choose repairs from failures and source evidence rather
than tuning tolerances or running a qualification campaign.

Inputs: ANYsolver main `bac246c2c`; ANYfem main `075c4a4` with substantial
pre-existing dirty work. Baseline status and binary diff saved outside sources.
Solver checkout is read-only. No commits, publication, dependency downloads,
qualification campaigns or sibling modifications are authorized here.

Evidence root: `C:/ANYworkspaceWork/anyfem-solver-compat-20261024`.
Effective runtime: Python `C:/Python/Python314/python.exe`, source path explicitly
includes local ANYfem, ANYsolver, ANYgeometry, ANYmesh and ANYloads. Installed
ANYsolver metadata is 0.4.7; source identity, rather than that unchanged version,
identifies the updated law. Functional tests use one numerical thread.

Worker: Mistral allowance exhausted under standing policy; useful subscription
Sonnet 5.5 Read assignment dispatched with API overrides removed and verified
claude.ai/max authentication. Implementation ownership follows after actual
successful tool outcomes. Full diagnostics stay outside source roots.

Worker fallback: subscription auth succeeded, but the useful Read assignment
ended `process_error` after 180.0 seconds with ENOTFOUND service failure and zero
tool calls. Owned process cleanup verified. Cached for this milestone; no API
fallback or further Claude retries. OpenAI worker owns the complete integration
outcome. Parent retains contracts, independent review, verification and acceptance.

Findings so far: production adapters already select the current public Q4/S3
formulations and forward nonlinear controls/recovery. Existing CI pins predate
the new laws. Local solver `origin/main` includes the exact current main SHA,
so updating the three CI pins is authorized within this compatibility slice.

Baseline: 61 passed, 1 failed in 20.281 seconds process wall. Initial failure
was missing ANYpost on source path; corrected only the test harness environment.
With actual local ANYpost, the affected existing recovery test exposes an
incomplete synthetic mesh fixture (`shells`/`beams` absent), now assigned for
repair without weakening recovery assertions. Independent minimal process-job
check passed, 9.75 seconds process wall. An attempted diagnosis selected a
nonexistent test node (exit 4, zero tests); retained as execution evidence.

Broader evidence: `analysis-regressions` 133 passed, 44 failed, 3 setup errors;
`workflows` 184 passed, 17 failed, 8 teardown errors. These are retained failed
attempts, not acceptance. Root causes include sandbox TEMP access, geometry
preparation cache mappingproxy deepcopy, ANYpost history-provider interface,
malformed source-field error normalization, and the owner's deliberate plastic
Q4 warped-facet refusal. This is not evidence of changed numerical tolerances.

With writable TMP/TEMP, `verification-writable-temp`: 25 passed, 1 failed.
The complete 21-case analytical fixture passes. Remaining failure is the
pre-existing headless-import AST scanner flagging function-local Tk imports in
the standalone `post/archive.py` viewer, outside this solver compatibility slice.

Decision: preserve current shell defaults and all owner refusals. No silent
legacy fallback. The orchestration-only capacity fixture may explicitly select
legacy Q4 (owner-supported) while a dedicated current-Q4 refusal/nonmutation
regression records the unsupported warped plastic route. This is an application
compatibility decision, not adoption of different physics for user projects.

## Final implementation and review

Implemented against solver `bac246c2c55e82101efc7b99d0f3cf6fd3fea5a5`:

- Three CI solver pins and documented installation revision now select this
  exact source; package and GUI preflight minimums agree on 0.4.7. The unchanged
  solver release number alone does not identify the updated laws.
- Prepared static snapshots exclude meshing execution caches and seed the
  original project identity in deepcopy memo so bound load callbacks refer to
  the detached snapshot. Source cache identity and load ownership are tested.
- The history provider supplies the current ANYpost mandatory field-refusal
  interface. Malformed archived submodel fields retain the application
  `SOURCE_FIELD` error contract rather than leaking owner reader errors.
- New public integration coverage exercises real Q4 elastic/plastic and S3
  von Karman build/solve/recovery, accepted snapshots, membrane stiffening,
  before-yield single-elastic-law displacement agreement, cancellation/reuse,
  missing mesh authority and warped-Q4 refusal without mutation/fallback.
- Existing synthetic recovery tests carry complete neutral mesh topology.
  Capacity orchestration tests explicitly select legacy Q4 as described above.

Parent inspected all product/test/config diffs and actual check evidence;
no outstanding actionable finding in this scoped change. Different-family
review was unavailable because the subscription service failed before any Read
tool use; the author and parent acceptance reviewer were separate OpenAI agents.
This is development compatibility evidence, not scientific requalification,
installed-wheel acceptance, hosted CI execution or release approval.

## Final checks and evidence

All exact commands, effective environment, JUnit results, full stdout/stderr,
process-wall timing and owned-process cleanup are retained per attempt under
the evidence root (`command.json`, `junit.xml`, `result.json`).

| Attempt | Result | Process wall seconds |
| --- | --- | --- |
| final-public-contracts | 75 passed | 32.015 |
| results-repaired | 109 passed | 81.188 |
| prepared-repaired-final | 51 passed | 22.6834878 |
| connected-repairs-02 | 109 passed, 39 earlier prepared-copy failures | 271.072557 |
| verification-writable-temp | 25 passed, 1 unrelated failure | 40.656 |

The 39 earlier prepared failures are all covered and passing in
`prepared-repaired-final` after the final memo repair. The connected run's
capacity, submodel, launcher and other workflow checks passed. The complete
21-case analytical verification fixture passes unchanged.

`acceptance-manifest.json` records exact final source hashes and deduplicated
test identities: **353 distinct focused tests passed**. The remaining failed
test is `test_the_headless_layers_do_not_import_tk`, which flags function-local
Tk imports in the existing standalone archive viewer. No tests were weakened or
excluded to claim an all-green suite. Full application/UI matrix and installed
artifacts were not run for this source compatibility slice.

Preservation check (`preservation.json`): all 36 originally dirty tracked file
diffs remain exactly unchanged; original untracked paths still exist. ANYsolver
remains clean. No sibling edits, branches, commits, pushes or publications.

## Limits

Qualified plastic Q4 with warped reference facets/eigenmode imperfections
remains an explicit owner refusal; legacy Q4 must be chosen deliberately for
that capacity route. Historical solver plastic-law restarts remain subject to
the solver's documented law-version limitation; no migration was invented here.
An exploratory before-yield comparison found ~7.2e-5 relative VM difference
between elastic reconstruction and committed-history recovery, despite the
matching displacement law. The unsupported cross-recovery equality assertion
was removed from the new test; no tolerance was relaxed. Each recovery mode
continues to be checked against its own source contract.

Status: implemented and verified against the scoped source compatibility
criteria, with the explicit solver capability and unrelated test limits above.
