# C0 shared contracts — status and evidence

Date 2026-10-09. Slice C0 of [PLAN.md](../PLAN.md). Contracts are proposed and await owner
review; nothing is implemented. C0 is governing-contract work, a decision reserved for the
lead, so it was written directly rather than dispatched to a Mistral worker.

## Contracts

| Owner | Document | Covers |
|---|---|---|
| ANYsolver | `C:/Github/ANYsolver/docs/SUBMODEL_TRANSFER_CONTRACT.md` | Admission, interpolation, `DrivenTrace`, drilling policy and junction rule, `InterfaceForce`, `ErrorAllowance`, many cases, test specification |
| ANYmesher | `C:/Github/ANYmesh/docs/SUBMODEL_CORRESPONDENCE_CONTRACT.md` | `TransferHit`, lineage-restricted locate, separated tolerances, topological conformity, refusals |
| ANYgeometry | `C:/Github/ANYgeometry/docs/SUBMODEL_VOLUME_CUT_CONTRACT.md` | `BoxVolume`, plan/apply volume cut, member partition, bounded extraction, identity map |
| ANYfem | `C:/Github/ANYfem/docs/SUBMODEL_WORKFLOW_CONTRACT.md` | Definition, flow, source admission, attribute carry-over, identity composition, staleness, format 9, reporting |

All four are new, untracked files. Nothing was committed, and no existing file in those
repositories was changed.

## Closure of the round-3 review obligations

1. **Redundant versus inconsistent traces.** Closed in ANYsolver contract section 3: reduce
   the trace to independent coordinates, drop redundant rows that are consistent, refuse
   inconsistent ones, emit the rest with deterministic pivots.
2. **Allowance scaling and zero-load semantics.** Closed in ANYsolver contract section 6:
   backward error and condition number on the same equilibrated operator, units carried by
   the solver's scaling, and a matrix-vector roundoff floor that is nonzero at zero load.

## Evidence (`verify_c0.py`, `results/verify_c0.json`, same S0 environment)

| Check | Result |
|---|---|
| MPC example: slave on the cut, one master on the cut, one interior | `u_s = g_s`, `u_m2 = 2g_s − g_m1` with errors 0 and 1.4e-20 m; multiplier forces balance 2e5 N of load to 9.6e-10 N |
| Coupled stiffener node on the cut, prescribed directly | Refused: `CONSTRAINT002`, multiple dependent definitions |
| Same, driving only the shell master | Beam node equals `u + θ × e` with error 0 |
| Model-level slave prescribed, equal to its master's value | Redundant (`CONSTRAINT007`), solves |
| Model-level slave prescribed, different value | Inconsistent (`CONSTRAINT005`), refused |
| Allowance on the A1 restriction, interior | Errors at 1.7e-3 (translation) and 7.8e-4 (rotation) of the allowance |
| Allowance on A1, Newton across the cut | 1.4e-3 of the allowance |
| Allowance on a zero-load rigid motion, interface forces | 2.3e-9 N at 7.3e-4 of the allowance |

## Changes these findings make to PLAN.md

- D5: the refusal of a prescribed slave applies to element-provided couplings, such as the
  eccentric beam coupling. Model-level equations are re-pivoted by the audit. The trace is
  always handed to the solver as reduced rows, never as direct slave prescriptions.
- Section 5.4: supports touching the cut are carried. The trace treats a consistent support
  row as redundant and refuses an inconsistent one. This replaces the blanket refusal.
- Section 7 tolerance rules: replaced by the ANYsolver contract's `ErrorAllowance`.
- `InterfaceForce`: corner nodes are separate records, not split between cut faces.
- `TransferHit`: conformity is decided topologically by ANYmesher; ANYsolver's own shape
  functions are the weight authority, with ANYmesher's weights as a cross-check.

## Exit criterion

The plan's C0 exit evidence is owner review of each contract. That review is pending.
