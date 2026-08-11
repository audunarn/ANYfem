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

```bash
python -m anyfem.migration
```

The verification and parity commands write dated evidence under `reports/`.
The three checks are also installed as `anyfem-verify`, `anyfem-parity` and
`anyfem-gate`.

Normal pytest runs limit native numerical libraries to one thread. Override
that safe default with `ANYFEM_TEST_THREADS` only when intentional. Large-scale
selection/result qualification requires `ANYFEM_RUN_SCALE_GATES=1`; hardware
timing gates additionally require `ANYFEM_RUN_HARDWARE_GATES=1`.
Tests that create real Tk windows additionally require
`ANYFEM_RUN_GUI_TESTS=1`; ordinary runs remain headless.

## The evidence hierarchy

**1. The test suite** is the working check. It covers geometry, meshing,
attribution, analyses, postprocessing, interop and the GUI. The complete suite
includes several real nonlinear and impact solves, so allow a few minutes on a
typical development machine. GUI tests skip rather than fail when no display is
available.

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
| ECC-01 | transformed-section neutral-axis position |
| ECC-02 | transformed-section stiffness ratio |
| MATL-01 | DNV-RP-C208 proportional limit |
| MESH-02 | requested size at a mesh-refinement zone |
| ELEM-01 | converged Q8 plate result |
| IMPA-03 | four contact elements per sphere radius |
| SYMM-01 | quarter model agrees with the full model |
| INTR-01 | a CalculiX FRD round trip preserves displacement |

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

The parity ledger has none left: every capability is covered or named in
`OUT_OF_SCOPE` with a reason. Run `anyfem-parity` for the current state rather
than trusting a copy here.

**The migration gate is a separate question, and it is still closed.** Run
`anyfem-gate`. Three of five criteria are met — ledger clear, saved ANYstructure
FE state importable, headless API builds every model type. Two are not, and
both need the same thing: a fixed set of models run through ANYstructure with
the results and timings recorded, so ANYfem's have something to be compared
against. That is not work ANYfem can do from this side, and the gate reports it
unmet with the reason rather than passing it by default. A gate that opened
because nobody supplied the evidence would be worse than no gate.

**An imported result is not a solved one**, and the difference is enforced. A
CalculiX FRD has three displacement components and no rotations, so asking an
imported result for a rotation raises rather than returning zero, and the raw
array holds NaN where a component is absent. Imported stresses stay node-valued
and are never recovered over: `evaluate_field` finds them before it would
recompute, so the file's answer is what gets reported.

**ANYfem models isotropic steel.** The solver has orthotropic materials with a
Hill yield criterion; ANYfem does not author them, but nothing in this layer may
break a model that has them, since results, recovery, fields, probes and the
impact time step all handle solver material objects. Two rules follow, and both
are tested: never read an isotropic-only attribute without a fallback, and never
filter stress components against a list held here — recovery grows components,
and a frozen whitelist discards new ones while everything still appears to run.

Some limits are the solver's rather than ANYfem's, and are worth stating because
they would otherwise look like wrapper gaps:

- **Plasticity is the layered-shell path.** Beam elements stay elastic in a
  nonlinear solve, so a stiffener carrying most of the moment will not yield,
  whatever hardening curve its material holds.
- **The 3-node beam is not more accurate than the 2-node one.** ANYsolver's
  2-node Timoshenko beam is exact for a tip-loaded cantilever; B3 gives
  `PL³/4EI` against `PL³/3EI` on one element. Both are exact in pure bending, so
  B3 is correct — it exists in ANYfem for compatibility with Q8 shell edges, not
  for accuracy. B3 is also straight-sided, so a quadratic beam on a curved line
  is refused.
- **A diverged contact iteration is refused, not reported.** A run ending in
  `contact_iteration_failed` has a peak force and an absorbed energy that are
  whatever the integration reached before giving up — plausible magnitudes and
  no meaning. `solve_impact` raises rather than returning them; pass
  `strict=False` to inspect a partial result deliberately. If it happens, the
  cause is nearly always too coarse a time step: raise `steps_per_contact`.
  Lowering the penalty stiffness will also make it converge and is the wrong
  fix — it changes the contact instead of resolving it, and on the verification
  plate it put absorbed energy at 0.06 kJ against the correct 0.45 kJ.

- **Resource controls are phase-specific.** `ResourceConfig` reaches every
  solver family ANYfem drives. Linear, modal and arc-length calls receive it as
  a solver option; buckling applies it to both the prerequisite static solve and
  eigensolve; transient and impact carry it in `TransientConfig`; nonlinear and
  capacity workflows use the `resources` argument. `solver_threads`, parallel
  assembly workers, recovery workers and memory limits still govern only their
  corresponding phases. Omitting a policy preserves the backend defaults.
- **Symmetry planes must be normal to a global axis.** Boundary conditions are
  applied in global axes with no nodal transformation, so a tilted plane is
  refused rather than approximated.

And one limit is inherent to mapped meshing, not to any implementation choice:
a Coons patch interior is the transfinite blend of its boundary, so a refinement
zone inside a plate refines nothing until the plate is decomposed. This is why
`RefineForImpact` is a geometry command.
