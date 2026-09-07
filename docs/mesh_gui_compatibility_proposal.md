# Proposal: two-lane mesher compatibility qualification

Status: proposal only, 2026-09-07. Implementation checkpoint remains
`e10f024245b6083e508b0674dab97ed5080fd6e3`, tree
`1e1397c1fdda31d0d3e50626b6100dae1ad66eb6`. This document is the only new
proposal path; no production requirements, CI pins, sources or defaults changed.

## Decision

Retain ANYfem's declared `ANYmesher>=0.4,<0.5` and legacy defaults now. Qualify
two explicit capability lanes, not a moving latest checkout:

1. **Released/legacy:** exact ANYmesher 0.4.0, ANYgeometry[planar] 0.4.2 and
   separately labelled ANYsolver 0.4.2 baseline artifacts. Normal GUI behavior must work without native-v2;
   saved Alpha intent must remain intact with execution clearly blocked.
2. **Alpha/native-v2:** activate only after ANYmesher provides an approved,
   immutable source/tree + geometry dependency pair containing the accepted
   planar/native-v2 and junction/trim fixes required by this consumer slice.
   This does not wait for the cylindrical atlas or full cylinder milestone.
   Planar Frontal Delaunay/spatial sizing stay explicit Alpha opt-ins.
   This lane is currently blocked on candidate artifacts and compatible metadata.

No public extra can override the conflicting `<0.5` requirements in ANYfem and
ANYsolver. Do not use `--no-deps`, editable path precedence, or a moving branch
to claim a resolver-clean installed Alpha environment.

For the actual **candidate comparison**, both legacy-mesher and Alpha-mesher
lanes must use the SAME private ANYsolver candidate wheel/digest, as well as the
same private ANYfem candidate. The released ANYsolver 0.4.2 wheel is retained only
as separately labelled baseline evidence, not silently mixed into that A/B
comparison. This isolates the mesher capability change while validating the
candidate's compatibility with both mesher generations.

Private owner-approved candidate metadata wheels may be built and tested before
publication; this avoids requiring a released range change before its own
qualification can run. Candidate artifact identities and dependency locks must
still be immutable and owner-authorized. After BOTH lanes pass, a separate release
change may propose `ANYmesher>=0.4,<0.6` in ANYfem and an owner-approved matching
ANYsolver dependency change. This is conditional, not an authorized range edit:
the upper-bound change must be backed by the two exact locks, `pip check`, public
contract/round-trip tests, and owner release approval. It does not make Alpha
algorithms defaults or qualify arbitrary future 0.5 patch releases.

## Verified published identities

Read-only PyPI JSON queries returned these identities on 2026-09-07; no packages
were downloaded/installed. Version-specific ANYgeometry 0.4.3 returned HTTP 404.
ANYmesher release metadata listed no 0.5.0 artifacts. A source declaration or
Git commit is not evidence of a published distribution.

Sources: [ANYmesher registry metadata](https://pypi.org/pypi/ANYmesher/json),
[ANYgeometry metadata](https://pypi.org/pypi/ANYgeometry/json),
[ANYsolver metadata](https://pypi.org/pypi/ANYsolver/json).

| Distribution artifact | SHA-256 |
| --- | --- |
| anymesher-0.4.0-cp314-cp314-win_amd64.whl | 1812161c8f04ac7c5584cb2226c8605fed875004f2a561ae4261032503bf44e5 |
| anymesher-0.4.0-cp314-cp314-manylinux_2_24_x86_64.manylinux_2_28_x86_64.whl | 74370ee0e147dbfba498e616310662d238fb5bb1a90dcace7c2d86e11929e42e |
| anymesher-0.4.0.tar.gz | 6939bd548621a636c1e2696d60482b8aec2cb9c75caa7a068e0190f430ae3624 |
| anygeometry-0.4.2-py3-none-any.whl | d7a0b8d9cd3d5b0c8e673162b3747ad12ff0726e30ac8cae0e7ac16cd9a41b92 |
| anygeometry-0.4.2.tar.gz | c7c4437276fa5b11648632035e2167378bac50d2db9edff8e8253869aaefb36a |
| anysolver-0.4.2-py3-none-any.whl | 2f07253f9c58af93001dbb8a86ee0a1e340fdf114bb38aaddaef525448532889 |
| anysolver-0.4.2.tar.gz | 0a9661c47bc3d5c3f39b9e21c775c3bc2be8df1e010af9f1b0676c3ecdafd255 |

The current published ANYsolver 0.4.2 metadata requires
`ANYmesher<0.5,>=0.4`. Published ANYgeometry latest was 0.4.2, not the locally
declared 0.4.3. These are proposal pins, not a claim this complete ANYfem commit
has already passed against those distributions. Lock the remaining existing
dependencies and their artifact hashes before an installed-environment run.

## Source identities and availability

| Role | Exact commit | Exact tree | Interpretation |
| --- | --- | --- | --- |
| Existing CI ANYmesh | 27e428188a891705288fef82bab0b166e330aff2 | 47e3bab7254b7bf197ee629e026a3f2fae8e5060 | Declares 0.4.0; retained legacy reference |
| Existing CI ANYgeometry | dd954f088a4cb95e267280cc4777b09e16232bd9 | efa4125bc549dbf2957ac21b6af4ce33835cd73c | Existing frozen source pin |
| Existing CI ANYsolver | 7322bf6f5b25092908ed41f26554970da4f5643f | 257f9b55b940f718998cb2e17ac454417a4cac8f | Existing frozen source pin |
| Retained ANYmesh HEAD | 246658c17096cd6e07075c4a4596c28e10c318a6 | e923c6126fa06cb57b956ab7dd658d4ffa7474c8 | Declares 0.5.0; predates subsequent uncommitted fixes, NOT a new delivery pin |
| Current reviewed owner-trim ANYgeometry | 12683ae5d6dbb2620f5020b67c0a7673f2601766 | 7bbcea8893eb9cbe9aae313379fea2427340a058 | Required local owner implementation; remote/artifact availability unproven |

The original corrected-baseline record referenced geometry
`a4f798ad8e52b12f91396bec71a84428b5e17201`, tree
`1578b4a95e5f245d51e3fbf4921f186a4c26bddf`; this is not a substitute for the
subsequent owner-trim implementation. Git source identities are not asserted to
be byte-equivalent to PyPI artifacts.

ANYmesher worker confirmed no deliverable immutable 0.5 commit/tree, qualified
wheel/sdist or published identity is available yet. Its retained HEAD excludes
accepted uncommitted junction/trim/metric corrections. The next boundary is
scoped source integration and focused compatibility qualification, not package
publication. Owner checklist:
`C:/Github/ANYmesh/docs/CYLINDRICAL_0_5_REMAINING_GATES_20260907.md`.

Subsequent owner preservation handoff: local accepted junction/trim snapshot
`b8ea86b485257cf8eea8ef07e6d604062255a31b`, tree
`e9ec158c516e47fd65e7b5579e380ad222d26a29`, parent `246658c`. This five-file
slice excludes cylindrical/native metric foundation work and packaging changes.
It supplies a concrete local source candidate paired with geometry `12683ae`,
NOT remote availability, artifact delivery, or full 0.5 release qualification.
The earlier retained-HEAD row remains historical context, not the proposed pin.

ANYsolver's metadata-only private candidate is to start from stable
`09351645ba17a0a5b130a1c7a48007d36dd08ada`, tree
`cfb0cf9a19f6519abf335694253efa264cd2e695`, never GE-B3. Its owner is preparing
the exact private version/manifest. Unresolved candidate fields are explicit in
`mesher_compatibility_pin_inventory.json`; they are not invented lock entries.

Final source-pair handoff supersedes the raw snapshot: ANYmesher normalization
child `76b9e7af534fb6c44a1259afc9e3d923bf453606`, tree
`21adb3db5074a56bb2b8f75c9ac9537c9e0cce07`, with the same geometry `12683ae`
pair. The raw `b8ea86b` snapshot remains preserved. The owner/boss verified this
is the accepted five-path junction/trim slice plus CRLF-to-LF normalization.
The current ANYmesh working tree also contains excluded cylinder/metric edits;
candidate builds must use exact Git objects/isolated checkouts, never a copy of
the whole working tree.

Solver owner proposed private `0.4.3.dev1`, candidate-only
`ANYmesher>=0.4,<0.6`, pending review/collision/freeze; no candidate commit or
wheel yet. ANYfem proposes private `0.4.1.dev1` from the accepted GUI checkpoint
plus this frozen test preparation, changing only candidate metadata to accept
mesher `<0.6`. The existing solver range `>=0.4.2,<0.5` can admit an explicitly
locked `0.4.3.dev1`; the Alpha lock must name it explicitly rather than depend on
implicit prerelease selection. No public declaration is changed. Both private
versions remain proposals until collision and exact candidate-tree checks finish.

## Concrete CI migration proposal

Do not retarget the existing eight-platform/Python broad workflow now. First
separate the consumer test corpus by required public capability, then add a
bounded manually dispatched qualification workflow with these two exact lanes.
Only after acceptance should the reviewed pins/test selection enter push CI.

**Shared tests:** legacy defaults; method radio/dropdown synchronization;
recombine/audit persistence; immutable job/control hashes; invalid input before
mutation; document-scoped drafts; same-ID reopen; serialization; unsupported
combination refusal. No Tk root, mesh generation, native numerical workload or
performance test in this control-level workflow.

**Real legacy installation:** assert the public native-v2 API is absent rather
than mocking it. Create a saved-Alpha fixture directly through the existing
NativeMeshSettings serialization schema (not by constructing unavailable native
options). Verify preserved intent and visible/disabled refusal, legacy operation
still available for legacy projects, and old artifacts round-trip. Keep the
existing simulated-capability recovery test as an additional unit check, not a
replacement for this installed legacy test.

**Real Alpha installation:** assert the approved API and native exports/origins
are present; fail, rather than skip, if the expected capability is missing.
Run the actual NativeMeshingOptions default/range/combination checks, Alpha
save/reopen, worker/public-API propagation and mapped exclusion tests. Classify
only genuinely Alpha-dependent test nodes with an explicit marker. The legacy
lane may skip those identified nodes; it must not skip shared/legacy-recovery
tests or turn a broken import into an automatic capability skip.

Proposed exact focused command after the marker/fixture change is frozen:

```
python -m pytest tests/test_mesh_gui_controls.py \
  tests/test_mesh_method_selection.py::test_panel_routes_mapped_selection_and_hides_irrelevant_triangulator \
  tests/test_mesh_method_selection.py::test_existing_native_settings_schema_persists_the_method_without_tk \
  tests/test_mesh_method_selection.py::test_mesh_settings_strategy_is_canonical_and_hash_affecting -q
```

Each lane additionally runs installed-module origin/version/capability checks
outside source trees and `python -m pip check`. Expected lane capability is an
explicit assertion in the proposed tests; it is not inferred from an import
failure. Use immutable artifact URLs and SHA-256 locks for all dependencies.
No editable sibling source roots or source-launcher preloading in installed lanes.

## Gates and resource request

1. ANYmesher/ANYgeometry owners deliver one reviewed immutable source pair and
   available artifacts, including source/tree/version/artifact digest manifests.
2. ANYsolver owner supplies an approved dependency-compatible delivery for the
   Alpha lane; ANYfem proposes the matching conditional metadata change.
3. Freeze the consumer marker/fixture diff, full dependency locks, interpreter,
   command, timeout and expected results for each lane. Register a resource
   request for installation/build or any heavy qualification before running it;
   obtain ledger approval and the global lock. No request is fabricated while
   the actual dependency artifacts and immutable launch manifest are missing.
4. Run bounded control qualification first. Broad pytest/verification/parity,
   wheels/platform/native numerical tests and any rendered GUI review remain
   separate explicit requests. Push stays held until its triggered workload and
   dependency migration are accepted.

No unchanged 33-test rerun was performed for this proposal. Current control
implementation and its accepted evidence remain preserved independently.

## Authorized preparation paths (following design acceptance)

Test/lock preparation only: `tests/test_mesh_gui_controls.py`, test marker/lane
support in `tests/conftest.py`, this proposal, and
`docs/mesher_compatibility_pin_inventory.json`. The inventory is NOT an
installable lockfile: transitives/candidate artifacts are unresolved and its
explicit readiness flag must remain false. No workflow, dependency requirement,
production source or defaults change is included in preparation.

Preparation implemented: a `native_v2` marker identifies genuinely dependent
cases; shared hydration/persistence/hash/submission tests have both legacy and
Alpha parameter sets. The saved-Alpha fixture is built directly through existing
NativeMeshSettings parameters, without needing native-v2 constructors. The real
capability-branch test runs in both lanes and preserves the same wire intent.
`ANYFEM_EXPECT_NATIVE_V2=0` asserts real absence; `=1` asserts real presence.
A present module that fails import or lacks its callable options type is an
error, never a legacy skip. Only marked dependent nodes may skip in the legacy
lane. Normal developer runs may omit the expectation; qualification lanes may not.
An autouse session gate enforces an explicitly requested capability even when
all Alpha nodes or the entire GUI-controls module are deselected.

Changed-test local smoke: explicit expectation `1`, CPython 3.14, the proposal's
focused command via current source launcher paths: **46 passed in 4.99 seconds**.
This is current-local-source test preparation evidence, NOT an immutable
candidate-wheel lane or a real legacy installation. No installation/build or
unchanged original 33-test rerun was performed; the expanded corpus changed.

## Resource needs to freeze next (not execution requests)

- **ANYmesher/ANYgeometry owner artifact delivery:** build the exact normalized
  source pair in isolated paths with the owners' approved supervisors/toolchains.
  Capture source/tree, compiler/Python/platform, wheel/sdist/native ABI hashes.
  ANYfem does not own or launch those builds.
- **ANYsolver metadata candidate:** owner freezes its private metadata diff,
  candidate commit/tree, version and boundary-test nodes. No GE-B3 sources.
- **ANYfem candidate preparation:** freeze the approved candidate-only metadata
  diff and test-preparation commit/tree. Proposed build is one CPython 3.14
  `python -m build --wheel` invocation against that exact isolated candidate;
  capture resulting wheel filename/SHA and Requires-Dist. No test suite is
  implicitly bundled with the build.
- **Consumer installed qualification:** one legacy then one Alpha environment,
  serialized. Resolve all dependency locks before freezing the requests; install
  with `python -m pip install --require-hashes -r <frozen-platform-lane-lock>`,
  followed by `python -m pip check`, installed origins/expected capability checks
  outside source trees, then the exact focused pytest command above. Do not run
  from the source checkout or use launcher path preloading. Copy only the frozen
  test corpus/config into a unique isolated test directory so tests import the
  installed ANYfem wheel. Bound numerical threads to one; propose a 120-second
  outer timeout for the headless control-test process, with install/build limits
  separately set by the resource administrator. No full suite, GUI, solver
  benchmark or meshing workload in the consumer request.

Actual request IDs remain absent intentionally: candidate metadata trees,
artifact digests, complete transitive locks and resulting command paths are not
yet available. Once frozen, each owner registers the exact launch manifest with
the resource manager, obtains the APPROVED ledger entry and lock, and executes
only that request. This is a concrete needs handoff, not permission to execute
placeholder commands or to bypass the resource process.

## Private ANYfem metadata candidate scope — submitted, not applied

Proposed version: `0.4.1.dev1`. Read-only PyPI metadata on 2026-09-07 reported
latest ANYfem `0.4.0` and no artifacts for `0.4.1.dev1`. Local Git had no matching
0.4.1 tag or candidate/compatibility branch; local dist contained only historical
0.3.0 artifacts. These observations do not reserve the version or prove absence
in another task's private artifact store. Recheck owner collision/freeze approval
immediately before creating the candidate.

Proposed isolated path:
`C:/Github/ANYfem/.compatibility-candidates/native-v2-0.4.1.dev1`.
Do not create it, edit metadata, or build until separately authorized. Materialize
the exact approved preparation commit from Git objects (the scoped preservation
commit returned for these four paths), never copy the live working tree.

Candidate-only file allowlist:

- `pyproject.toml`: version `0.4.1.dev1`; change only the ANYmesher requirement
  from `>=0.4,<0.5` to `>=0.4,<0.6`. All other requirements unchanged.
- `src/anyfem/__init__.py`: align `__version__` with that private metadata version.

Freeze the exact metadata diff, candidate commit/tree, build input hashes and
package origin/version checks before a build request. Public main declarations,
source launcher, application behavior/defaults, GitHub workflows and release
configuration remain unchanged. The same resulting ANYfem wheel and the same
owner-approved private ANYsolver wheel must be used in both candidate comparison
lanes. The existing published wheels are separate released-baseline evidence.

Additional session-gate check after preparation: selecting only the existing
mapped-routing test outside the controls module with expected capability `1`
passed (1 test, 0.92 seconds). An expected-legacy `0` run of that same selection
against the current native-v2 source refused at setup with the exact expected
capability mismatch, despite selecting no Alpha-marked tests. This is a negative
guard check, not installed-legacy qualification. Two initial shell/Python wrapper
quoting attempts failed before tests ran; the corrected bounded harness verified
the expected refusal. No production code or dependency metadata changed.
