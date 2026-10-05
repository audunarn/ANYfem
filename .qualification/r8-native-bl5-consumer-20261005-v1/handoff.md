# Native elastic BL5 consumer development handoff

State: verified against assigned development criteria. Root owns acceptance and Git.
Adopted `ANY_ECOSYSTEM_RISK_PROPORTIONATE_ENGINEERING_V1` revision
`2026-09-24.2`; no frozen campaign, physical law, tolerance or release gate
changes. OpenAI fallback used because root established local/Mistral unavailable;
this does not satisfy a different-family acceptance requirement.

Outcome: existing ExperimentalGEBL5Panel and native Project route retain exact
BeamDefinition bytes and distinguish the two explicit solver elastic-native
policies in input schema `anyfem.experimental-ge-bl5-panel-native-elastic-v5`.
Pure Q4 policy refuses S3; mixed-shell policy requires actual S3. Solver owns
finite response, physical recovery, checkpoint integrity/model identity and
typed elastic-domain refusal. Defaults and older schemas remain unchanged.

Changed files: src/anyfem/solve/stiffened_shell.py,
src/anyfem/solve/stiffened_shell_project.py,
tests/test_stiffened_shell_native_fibre_bl5_consumer.py,
examples/stiffened_shell_native_fibre_bl5_consumer.py,
docs/experimental_stiffened_shell_native_fibre_bl5.md.

Question for bounded source run: does strict v5 forwarding/persistence preserve
actual native inputs and complete Q4/S3/mixed fresh-owner replay while refusing
schema/policy/topology mutations and preserving accepted state on refusal or
cancellation? Competing failure modes are consumer serialization/admission
errors versus solver-domain refusals. Public lifecycle and byte equality checks
discriminate them. The initial fixed-load cancellation assertion incorrectly
queried staged `load_position`; final repair uses fixed-load `load_factor`. Original
test/entry/product files are saved in raw evidence before that assertion repair.
The intermediate `amplitude` guess also failed (the public fixed-load result
exposes `load_factor`); both failed attempts remain intact. The final assertion
uses `load_factor == .5` and passes with byte-exact continued accepted state.

Resources: sole numerical slot released by root; sequential existing supervisor,
600 seconds/check, 2 GiB tree, one affinity CPU, actual pools one, JIT off,
unforced CPU dispatch. Sampler's numerical-or-unresolved count is not a job count.
The inherited execution record's `thread_scope` mentions OpenSees, although
these runs load ANYfem/ANYsolver and do not load OpenSees. Original records are
preserved without rewriting; actual ANYfem/solver pool settings are independently
verified in runtime records. Exact inherited supervisor (7424 bytes, SHA256
ad9947c9268774a980041112004364d4daba6ce884fe339fab9f0d90b265d3ec)
is retained as inherited-supervisor-v1.py. Final five owned source files are
retained under final-source-v1; failed test versions are separately retained.
Exact solver wheel reused: b3d5f572797f3c2954d1bf9dc1c6b4293da14c5812e543dbde7f314a60207291.
Source runtime uses installed-s3-v1 solver with frozen CI4 ci-site-network-v2.
Runtime/binding/loaded/JUnit/full stdout/stderr paths are versioned in this root.

Executed checks and evidence:

- source-v1: 26 passed, 1 failed in 314.82 s. Sole failure was the new test's
  fixed-load `load_position` assertion. All three real topology workflows,
  strict ownership/mutation guards and typed-yield/refusal/checkpoint checks
  passed, as did all older generalized/coupled finite-history and Project
  regressions in that batch. Raw stdout, stderr, JUnit and execution record retained.
- source-v2: affected-only recheck failed on nonexistent `amplitude` attribute,
  3.41 s. Test snapshot/raw diagnostics retained.
- source-v3: affected-only recheck passed, 5.70 s; supervised 8.32 s.
- build-v1/install-v1: fresh ANYfem 0.4.1 wheel, offline `--no-index --no-deps
  --no-build-isolation`, installed with `--no-index --no-deps` to fresh site-v1.
- installed-v1: 23 passed in 64.81 s; supervised 67.54 s, peak tree 206856192
  bytes, numerical-or-unresolved max 1. Includes all six new checks and older
  native BL5 Project round-trip/finite lifecycle, v3/v4 strict schema and
  distributed input controls. No skips. The unchanged long older material
  history campaigns are reused from source-v1 by impact, not claimed rerun.
- All 99 ANYfem Python package modules match source, new wheel and fresh site.
  All 424 solver package modules match the exact previously tested wheel and
  existing installed-s3-v1 target. All 310 loaded consumer/solver modules came
  from those selected paths. Frozen dependency versions/paths are checked:
  NumPy 2.5.3, SciPy 1.18.1, Numba 0.68.0, llvmlite 0.50.0.
- Runtime verifies one-core affinity, actual pools one, JIT disabled and no
  OPENBLAS_CORETYPE forcing. Every numerical check used the sole shared slot.
- git diff --check passed; only five owned code/test/example/doc files changed
  or added. Raw evidence is ignored under this fresh permitted root.

ANYfem wheel SHA256:
`a8a255eb26696543e31f27d7a49a1df15af8f16c7a1ef341953f02028df545bc`.
Binding, runtime, package-module, loaded-module and JUnit records are
source-bindings-v1/source-bindings-v2/source-bindings-v3.json,
installed-bindings-v1.json, source-runtime-v*.json, installed-runtime-v1.json,
source-loaded-v*.json, installed-loaded-v1.json and corresponding JUnit XML.
Reproduce commands: `C:/Python/Python314/python.exe run-v1.py source v3`,
`... run-v1.py build v1`, `... run-v1.py install v1`,
`... run-v1.py installed v1`, from this evidence directory. Existing version directories
are immutable; choose fresh versions/targets for any authorized future rerun.

Remaining: root review/integration and any separately required independent-family,
scale/scientific acceptance or release gates. No solver rebuild/retest, old
archive, frozen campaign, scientific acceptance, publication or Git action.

## Default Project helper seam — root-requested bounded installed smoke

The single `default-helper-v1` job exercises `solve_bl5_panel(record)` with
`targets=None` on Q4/S3/mixed native inputs. All three return completed
reference-linear `LinearResult` values with finite shell displacement. Q4
equilibrium/gap: 1.00695722633398e-14 / 1.6790814936756083e-21; S3:
2.938634682902606e-15 / 1.1193876624504055e-21; mixed:
7.006612872050237e-15 / 8.470329472543003e-22. These meet unchanged 1e-8/1e-9
criteria. The helper's reference-linear default remains unchanged.

Supervised time 2.884 s, peak tree 168771584 bytes, numerical-or-unresolved max
one. Exact existing installed graph, 99/424 package module manifests and five
source bindings were verified unchanged; all 307 loaded consumer/solver modules
come from the selected targets. Actual pool threads one, affinity one, JIT off,
frozen CI4 dependency versions/paths and unforced dispatch checked again.
No product/source change, wheel rebuild or repeated 23-test batch was needed.

Raw evidence: default-helper-v1/{execution.json,stdout.log,stderr.log,
thread-samples.json}, default-helper-{runtime,results,loaded,binding}-v1.json,
default-helper-entry-v1.py and default-helper-run-v1.py. Run command:
`C:/Python/Python314/python.exe default-helper-run-v1.py` from this evidence
directory. No jobs remain; root acceptance remains reserved.
