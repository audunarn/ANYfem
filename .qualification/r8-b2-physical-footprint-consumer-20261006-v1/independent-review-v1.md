# Independent B2 physical-footprint consumer review V1

2026-10-06. Non-author OpenAI Read review of the complete owned consumer slice in `C:/Github/ANYfem/.worktrees/experimental-ge-panel-consumer`, starting at `94a0b0c56d9494ea2555aab65ed17c086e4d461b`. No numerical imports/jobs, product edits, Git operations, workers or sibling changes were performed. Only this report was written. Root owns integration and acceptance; implementation remains with the assigned author.

## Consequential findings and current disposition

Two actual integration failures were identified and returned to root for the same owner. Both have compatible source repairs visible at this report's last inspection, but terminal evidence of the repaired caller was not yet available to this reviewer. No passing caller claim follows from source inspection.

1. Original `_decode_physical_footprint` and `ExperimentalB2PhysicalRigidFootprintPanel.from_definition` reused the public historical POINT consumer for empty POINT common inputs. That consumer deliberately rejects empty attachments. Source V1 JUnit records all three tests failed with `ValueError: experimental B2 point attachments required`; receipt exit 1, `3.2973269999492913` seconds. The original source SHA was `db959888b5ab3cce405a8420419d5ae60f9f09ccb3ba743d6afcca143ee2cba2`. The repair uses private `_from_definition(..., neutral=True)` to obtain a common record and `_decode(..., neutral=True)` only after the physical outer envelope checks historical static schema and exactly empty points. Historical parser calls retain `neutral=False`, preserving their nonempty POINT guard. This common syntax constructs no POINT owner and permits no POINT/physical coexistence.
2. The V2 physical subclass called `from_dict`/`to_dict` but inherited neither from `ExperimentalB2PointPanel`; those methods elsewhere in the file belonged to the mixed subclass. Source V2 JUnit records three failures with `AttributeError: type object ExperimentalB2PhysicalRigidFootprintPanel has no attribute from_dict`; receipt exit 1, `3.268318900023587` seconds. Frozen V2 source SHA was `83c7c80648e042c345279d43afb128ef4c84a1e1d7c9c4161367697132d8e32f`. The latest physical class explicitly owns both methods; construction revalidates and canonicalizes bytes, and `to_dict` reparses a detached dictionary. The historical POINT test must use its actual constructor rather than assume that absent dictionary API. No historical API addition is required for this repair.

No further concrete cross-law dispatch or lifetime defect was discovered in the inspected connected source. That statement is bounded source review, not a successful end-to-end execution claim.

## Connected contract assessment

The dedicated `anyfem.experimental-b2-physical-rigid-footprint-panel-v1` envelope has an exact field set. It reconstructs only the exact public `B2PhysicalRigidFootprintDefinition`, public physical footprint type and typed physical selection. The nested common record is static and EMPTYPOINT only; mass/material/loaded/modal schemas remain inadmissible. Existing static codec guards refuse shell density, member mass and supplied member geometric metadata.

Physical descriptors require the complete exact public field set, typed five-row and qualification flags, validated director/tangent basis/patch cells, distinct IDs and equality with the public descriptor. Membership/overlap/complete-cell/constraint geometry remains solver-owned. Selected footprint IDs must be declared. Ordinary POINT selections cannot sneak through the common record because the existing parser validates them against its empty attachment set.

`definition` returns the new public physical definition; `create_analysis` constructs a fresh public owner and checks profile, experimental status, production flag and physical-law identity. Static and selected batch methods pass the declared loads, footprint selection and member recovery; batch additionally passes selected DOFs and cancellation/callback. All close in `finally`, including callback exceptions. The methods return actual solver status/failure/prefix results unchanged. `buckling` requires exactly `B2PhysicalFootprintBucklingRequest`, delegates unchanged and closes its owner; modes are explicitly refused. This does not add prestress, mass or accepted-state persistence.

Standalone save/load and existing Project dispatch explicitly admit the physical schema before the old six-row/POINT selectors. Project records retain a detached strict payload; geometry targets/settings/resource/output substitutions remain refused. Finite stepping controls still fail before the B2 batch dispatch. Old six-row footprint and POINT codec branches retain separate meanings.

## Checks, evidence gaps and limits

The new tests cover persistence, static response, selected 1/10/100-case batch counters/chunk bounds, physical buckling status/type/policy, descriptor/selection/schema mutations, detached Project ownership, wrong-law request refusal, unsupported modes/mass, pre-cancel/accepted-prefix cancellation and callback-error propagation. Original V1 and V2 are actual failed attempts, not successful checks of these contracts. Their JUnit/receipts and input snapshots must remain preserved. Effective saved post-runtime records show affinity `[0]`, numeric pools 1, Numba threads 1 and JIT disabled; this reviewer launched no numerical work.

A missing discriminating consumer check remains: the workflow summaries count selected footprint wrenches and assert factor/chunk counters, but do not compare requested member stations, selected displacement values, actual five-row metadata/global row IDs or physical wrench contents against a direct public result. The underlying code forwards these inputs without remapping; this is missing mapping/output evidence, not an observed wrong value. A frozen repaired caller check must at least establish the complete path executes. Further checking requires root-authorized remaining authority and must not silently widen or renew the shared budget.

Installed consumer artifact verification is separate and was not available at the last review. Source success, if later established, does not prove rebuilt wheel/site behavior. Public solver wheel is frozen at `375e9e4525c93aaa4e1d75a26bbbd8fb506494064ae3a698e5ea67603c9b5a9b`; the review adds no solver numerical/physics claim. Latest observed failed V2 receipt leaves `30.35974389997375` seconds of the inherited 600-second shared budget; later root-authorized attempts require their own actual ledger. No numerical authority follows from this report.

Reviewed source: `stiffened_shell_b2.py` common codec, physical consumer and save/load dispatch; `stiffened_shell_project.py` selector and batch dispatch; the new physical-footprint test/example/document; exact raw checkpoint and V1/V2 selected-input, source-hash, JUnit, receipt and runtime records. Candidate files were evolving during repair; frozen attempted identities above are distinguished from the latest inspected source. No protected/body/canary/historical campaign artifacts were traversed.

This is supporting independent developmental review. Scientific, different-family, G4/G6, full programme, installed artifact, aggregate acceptance, release and publication obligations remain separate and open. Root must bind terminal repaired evidence and final candidate bytes before adopting the implemented slice.

## Latest inspected worktree bindings

- src/anyfem/solve/stiffened_shell_b2.py: d010421c16c36ab6fedcd61c91d1ca4157e843799d9db7b74c480cb09672452e
- src/anyfem/solve/stiffened_shell_project.py: 93edbe43c8290972d7c86be372f677aa4c17e6f3a5d93ef182d9b70e46b534b0
- tests/test_stiffened_shell_b2_physical_footprint_consumer.py: 848ec0a0d9032b3fb9bd3fc41ac7a3bbc6861321ba46b37671df7ed6c130c5f6
- examples/stiffened_shell_b2_physical_footprint_consumer.py: fb9585b30cf71f74edd14ed4faf7179deacf7343fba204bc8e44f4aaeb8289c7
