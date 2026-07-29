# Quality control

How to check ANYfem, and what the evidence does and does not claim.

## Commands

```bash
python -m pytest tests -q
```

```bash
python -m anyfem.verification
```

```bash
python -m anyfem.parity
```

The last two write dated evidence under `reports/`. Both are also installed as
`anyfem-verify` and `anyfem-parity`.

## The evidence hierarchy

**1. The test suite** is the working check. It covers geometry, meshing,
attribution, analyses, postprocessing, interop and the GUI, and it runs in
seconds. GUI tests skip rather than fail when no display is available.

**2. The verification suite** is the auditable claim. Each case states what it
is checked against and how close it has to be:

| case | checked against |
| --- | --- |
| GEOM-01 | a revolved cylinder is exactly circular |
| MESH-01 | shared-edge nodes are the same node objects |
| STAT-01 | `PL³/3EI` plus the Timoshenko shear term |
| STAT-02 | Timoshenko `0.00406 q a⁴ / D` |
| STAT-03 | `6M/t²` with `M = 0.0479 q a²` |
| STAT-04 | `P/A` |
| LOAD-01 | `ρgV` |
| LOAD-02 | `1.2 dead + 1.5 live` exactly |
| MODE-01 | `1.875² / 2πL² · √(EI/ρA)` |
| BUCK-01 | Euler `π²EI/L²` |
| DYN-01 | undamped step response peak = 2 × static |
| IMPA-01 | sphere momentum balance closes |
| IMPA-02 | sphere kinetic energy = ½mv² |

**3. The parity ledger** is the migration gate, not a verification. It records
what ANYstructure's `fem_integration.py` exposes and what ANYfem covers, and
computes whether the ledger is clear. It is deliberately unflattering: a
capability counts as covered only when ANYfem can do the same job.

## What the evidence does not claim

- **Not a claim of general correctness.** These are closed-form comparisons on
  simple cases. They say nothing about geometry or load regimes outside them.
- **Not a restatement of ANYsolver's qualification.** Element validity,
  plasticity, recovery policy and external CalculiX comparison live in
  ANYsolver, which has its own evidence and its own scope statements. ANYfem
  does not repeat, summarise or extend those claims.
- **Not an engineering judgement.** A generated report states what was asked
  for and what the solver returned. Whether an answer is acceptable is the
  engineer's call, and a generated document should not appear to make it.
- **A generated CalculiX deck is not evidence.** Until it has been run and its
  results compared, it is a reproducibility handoff and nothing more.

## Regenerating evidence after a change

Evidence is dated and environment-stamped. Regenerate it after any change that
could move a number, and do not cite a stale report. A report that fails is
still evidence — of a regression.

## Known open items

The parity ledger lists them; run `anyfem-parity` for the current state rather
than trusting a copy here. The largest are 8-node shell elements, graded mesh
refinement (including around an impact zone), symmetry modelling, and result
import from CalculiX FRD and SESAM SIF — all supported by ANYsolver but not yet
exposed by ANYfem.

One limit is the solver's rather than ANYfem's, and worth stating because it
would otherwise look like a wrapper gap: plasticity is the layered-shell path,
so beam elements stay elastic in a nonlinear solve. A stiffener that carries
most of the moment will not yield, whatever hardening curve its material
holds.
