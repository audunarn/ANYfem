# S6 A5 CCX reference (continuation 2)

**Status: authored, not executed.** No Python, pytest or CalculiX was run by the author in any pass. Nothing below is an
observed result except the quoted evidence from earlier coordinator runs. The coordinator owns every run.

Scope: **A5 full stiffened stage fixture only.** The 3D solid child belongs to the separate solid_reference owner.

## Evidence preserved (not erased)

### Run 1 (development, three test failures; repaired earlier)

| Failure | Cause | Repair |
|---|---|---|
| Preflight `section_orientation_from_offset` | Real section is `Flatbar(flange_width=0.08, flange_thickness=0.01, offset_mode="automatic")`: one rectangle `(y=0, z=tf/2, width_y=0.08, height_z=0.01)`, offset vector `(0, 0, 0.005)`. The old code guessed the offset from the extents. | `spec_from_project` reads extents and offset separately; coincidences with `plate_t/2` and `tf/2` are observations only. |
| Gauge self-test 4.85e-18 | Float subtraction of nearby translations. | Forward error bound from the actual values. |
| Block consistency 50.00000000000001 vs 50 | Product order. | `math.isclose(rel_tol=8*eps)`. |

### Run 2 (development, physical preflight, failed before CCX)

Real ANYfem assembly: force **+10000 z**, moment about origin **(+5000, -5000, -70000)**. The deck assumed pressure toward -z, so it expected
-10000 z and opposite x/y moments. Cause: the sign was assumed ("pressure acts toward -z"), never measured. Repair: the ANYfem sign is preserved
(`PRESSURE_FORCE_SIGN = +1`, `applied_totals` uses `+p*area`), and `test_the_real_assembly_has_the_sign_and_moment_the_deck_applies` re-measures
the real assembly every run. The moment arithmetic is checked in `test_applied_totals_keep_the_anyfem_sign_and_moment`.

### Run 3 (`evidence/ccx-calibration-dev-04`, real CCX; the previous prototype)

* Isolated free-dof probe gauge with `*EQUATION` on shell rotation dofs 4-6: affine probe nodes 9 and 10 both printed uy = -1.635e-3 although rx is
  non-zero. Either the rotation boundary value or the equation term on the rotation dof was not applied; the run cannot tell which.
* Beam cantilever tip uz = **+28810** for a 1 N load: the clamp (`1, 1, 6`) did not hold (a mechanism, wrong sign and magnitude).
* I could not read the `.dat` files in that directory (the file viewer classified them as binary), so the two findings above are taken from the
  coordinator's report, not re-read. Keep the original files.

Consequences, all implemented below: no isolated probe gauge, no cantilever clamp as an instrument, no `*DLOAD` label assumption, and calibrations that
can fail visibly.

## What changed and why

| Item | Before | Now |
|---|---|---|
| Pressure | `*DLOAD P1/P2`, label chosen from a strip, sign assumed toward -z | explicit consistent nodal forces toward **+z** (S8R weights -1/12 corner, +1/3 mid-side), ANYfem sign kept. cal4 records what `P1` does and checks the explicit forces point to +z with the `P1` magnitude. |
| Rotation gauge | probe nodes at z = ±0.01 tied by `*EQUATION` to shell rotations (invalid per dev-04) | **two actual nodes**: plate node and the beam node at the same (x, y), `rx = -(uy_beam - uy_plate)/offset`. Valid only if cal3 accepts the link. |
| Ids | beam base 1 000 000, probes 900 001/900 002 (CCX sizes arrays by max id) | plate ids `1..(2n+1)^2`, beam ids directly after, elements dense; `check_deck_syntax` enforces max id ≤ 4× count and **lines ≤ 132 characters**, and runs on every written deck |
| cal1 | cantilever with rotational clamp | simply supported B31 (no bending-rotation clamp), midspan force in z then y, support reactions must close (≤ 1e-3) and the deflection must be within a factor 2 of the identified closed form |
| cal2 / cal3 | rigid-motion S8R with all rotations prescribed | S8R **Kirchhoff strip** (nu = 0, clamped 1-6, tip load) + soft free B31 stub tied by the *same* six A5 `*EQUATION` blocks. Closed form `theta = P L^2/(2EI)`, expected link offset `-e*theta` along the strip (cal2: about y; cal3: about x, the A5 `rx`). Clamp reaction closure is printed and checked. |
| cal4 | strip with translational "clamp" (a hinge) | clamped 1-6; step 1 explicit loads, step 2 `*DLOAD P1` |
| Whole-child comparator, block decks | `child_deck`, `read_child`, 3D clamped block | **removed** (different BVP; nothing in A5 or the solid assessment could use them) |

The stub in cal2/cal3 has section 1e-3 × 1e-3 (EI ≈ 1.75e-4 vs the strip's ≈ 1.75e3): it only carries the rotation dofs of the beam node. No artificial
stiffness exists anywhere; a soft carrier is the opposite of one.

## Gates (no bypass)

`CalibrationRecord.require_usable` refuses unless **all four** hold: beam frame confirmed (cal1), link transmits rotation about y (cal2) and about x (cal3),
explicit loads verified (cal4). Refusal text contains **INCONCLUSIVE**: if the link fails, A5 is reported inconclusive. No fallback is implemented.

The numeric screens are gross-error screens (measured / closed form within a factor 2; reaction closure 1e-3 = 100× the 6-digit print precision). They say
"the mechanism works", never "the result is accurate". They were chosen from the failure observed in dev-04 and from the print precision, not tuned on results.

### Fallback not built (gap)

If cal2/cal3 show CCX cannot carry shell rotations through `*EQUATION`, the next candidate is a solid layered reference (C3D20R plate in layers, bar as solid,
translation-only ties; rotation read from the top/bottom node pair). It was **not** built: the 0.08-wide bar does not conform to the h = 0.1 lattice, the
project's shell+beam model double counts the overlap of bar and plate by construction, and a solid model of it would be a different model family whose
comparison could not be claimed. Decide after the calibration outcome.

## Original A5 plan and sensors (frozen `s4b/test_a5_budget.py`)

`POINTS = {w_centre: ((0.5, 0.5), 2), w_stiffener: ((0.5, 0.3), 2), rx_stiffener: ((0.5, 0.3), 3)}`, read as `node_displacement(node)[component]` of the
full model at h = 0.1, 0.05, 0.025, 0.0125. Component 3 is the **shell rotation dof rx of the plate node on the stiffener line**. The CCX gauge is the rotation of
that same plate node as seen through the link (`uy_beam - uy_plate = -e*rx`), and cal3 calibrates exactly that relation against a closed form. The reference uses
the same points and h as S8R n = 10..80 (`A5_ORIGINAL_QUANTITIES`, `compare_with_original_a5_run`); the difference is a comparison between element families, not an error.
`test_original_run_sensors_are_the_frozen_a5_points` pins the mapping.

## Stages (coordinator runs every subprocess)

```
python docs/handoff/submodelling-20261009/s6/benchmark.py stage-a5 --out <new dir A>
#   A/report/a5_input_report.json, A/spec.json, A/calibration/cal1..cal4 .inp ; exit 2 and no decks if the preflight fails (report still written)
ccx -i cal1_beam_frame ; ccx -i cal2_shell_beam_link_ry ; ccx -i cal3_shell_beam_link_rx ; ccx -i cal4_pressure_sign    # each separately, keep stdout/stderr
python docs/handoff/submodelling-20261009/s6/benchmark.py read-calibration --run-dir <run dir> --spec A/spec.json --out <new dir C>
#   C/calibration.json {beam_frame, rotation_link_ry, rotation_link_rx, pressure_sign, record, usable, refusal, errors}
python docs/handoff/submodelling-20261009/s6/benchmark.py a5-decks --spec A/spec.json --calibration C/calibration.json --out <new dir D>
ccx -i a5_n10 ... a5_n80
python docs/handoff/submodelling-20261009/s6/benchmark.py read-a5 --run-dir <run dir> --spec A/spec.json --out <new dir E>
```

Tests: `python docs/handoff/submodelling-20261009/s4a/run_tests.py docs/handoff/submodelling-20261009/s6/test_reference_ccx.py --basetemp=<dir>`;
`.../s6/test_closed_cylinder.py` (A6, untouched, independent of CCX).

## Physical-offset fallback ("A5-F") — authored, NOT RUN

**Recorded state (preserved, unchanged):** real CCX 2.22 cal1/cal4 pass; cal2/cal3 (`*EQUATION` shell–beam link) fail with zero measured
rotation-arm extension. Evidence: `evidence/ccx-a5-cal-dev`, `evidence/a5-cal-verdict-dev`. Sections 1–11 of `reference_ccx.py`, the four
calibration decks and `CalibrationRecord.require_usable` are untouched and still refuse production A5 decks (INCONCLUSIVE). Nothing below
relaxes them; A5 stays open unless the gates below pass on real CCX output.

**Idea.** Beam elements share the plate node ids (coincident midsurface node, no `*EQUATION`) and carry a physical `OFFSET2` on the B31 section
(assumed normalized by the section dimension along n2; phys_a measures this). The claim to prove is kinematic only:
`u_arm = u_plate + theta x (0,0,e)`, the ANYfem coupling. Proof is by imposed motion, never by model agreement. Rotation is read from the
ACTUAL expanded shell translations at ±t/2 (`*NODE FILE, OUTPUT=3D` → `.frd`): `slope = -(u_axis(top) - u_axis(bot))/t`
(`u_axis` = ux for a strip along x, uy along y). No fitted constant: signs are measured, not tuned; tolerances are `PRINT_REL = 1e-3`
(100 × the 5-digit `.frd` print), `REACTION_SCREEN` and the existing factor-2 `GROSS_SCREEN` for closed-form bending.

| Stage / deck | What it imposes | What it measures | Gate |
|---|---|---|---|
| `phys_a_offset_law` | axial tension P on three pinned B31 beams: x-beam, y-beam (`OFFSET2 = +offset/bar_height`), control (no offset) | midspan uz → signed `e_eff = -uz·8EI/(P L²)` | control straight (≤ PRINT_REL × expected); `|e_eff|/offset` in factor 2; reactions close. Signs `sign_x`, `sign_y` recorded (n2 of the y-beam is −z). If every uz ≈ 0 the offset is **not** an elastic eccentricity ⇒ INCONCLUSIVE with that evidence. `implied_unit_over_bar_height/width` documents the actual normalization. |
| `phys_j_translation_{x,y}` | rigid uz = 1e-3 on every strip node, rotations free | skin slope, stub-corner arm, uz of skin/corners | slope = 0, arm = 0, uz rigid (PRINT_REL) |
| `phys_j_rotation_{x,y}` | rigid rotation of the midsurface (uz = 0.01·s, ux = uy = 0), rotations free | skin slope from expanded translations; arm = mean(stub root corner u_axis) − midsurface u_axis | slope = imposed; **arm = −e·slope**; arm nonzero; uz rigid (all PRINT_REL) |
| `phys_j_bending_{x,y}` | clamped Kirchhoff strip (nu = 0), tip load (cal2/cal3 geometry) | same gauges | slope within factor 2 of `P L²/(2EI)` (not an accuracy claim); arm = −e·(measured slope) (PRINT_REL); clamp closure |

The soft stub (1e-3 section) only carries the rotation of the shared tip node; its offset is `sign·e/1e-3 = ±5` normalized units, so it also checks
the unit rule on a second dimension. Root corner nodes are looked up by coordinates and must be unique (`frd_node_at`); a missing node is an error kept as evidence.

`PhysicalRecord.require_usable(calibration)` needs: the ORIGINAL record's cal1 (beam frame) and cal4 (explicit loads) — **not** the failed cal2/cal3 flags — plus
phys_a effective and all six joint gates. Otherwise `ValueError("... A5-F is INCONCLUSIVE ...")` and no native deck is written.

**What a pass would and would not mean.** It would show that CCX's shared-node + section-offset joint transmits translation and the plate rotation to the offset arm as the
ANYfem rigid eccentric coupling does, at the tested configurations. It would not show element-family equivalence, nor the N/M/work match: **no beam N/M or work is compared (gap)**.

### Comparative native-connectivity sequence (separately marked)

`a5_native_deck`: the A5 plate mesh, supports and explicit loads of `a5_deck` (duplicated on purpose) with B31 on plate ids and the calibrated offsets. Default levels
`NATIVE_LEVELS = (10, 20, 40)` (h = 0.1, 0.05, 0.025; `DEFAULT_LEVELS = (10, 20, 40, 80)` of the assessment freeze is a separate count and unchanged). Graded: the
translational QoIs `w_center`, `w_stiffener` (convergence via `reference_convergence`, one Richardson triple). `rx` from the skin nodes is reported as `rx_skin_observation_not_graded`
and is neither converged nor compared to ANYfem. It measures the CCX native-joint model's own mesh convergence; it is not an ANYfem equivalence test.

### Commands (coordinator; `A` = existing stage-a5 output with `spec.json`, `C` = existing `calibration.json`; every `<…>` is a new empty directory outside `handoff`)

```
python docs/handoff/submodelling-20261009/s6/benchmark.py stage-physical --spec A/spec.json --out <P1>
(cd <P1> && ccx -i phys_a_offset_law)                                  # keep .dat/.stdout/.stderr whatever happens
python docs/handoff/submodelling-20261009/s6/benchmark.py physical-joint-decks --spec A/spec.json --law-run-dir <P1> --out <P2>   # exit 2 = INCONCLUSIVE, prints the law evidence
(cd <P2> && for j in phys_j_{translation,rotation,bending}_{x,y}; do ccx -i $j; done)
python docs/handoff/submodelling-20261009/s6/benchmark.py read-physical --spec A/spec.json --law-run-dir <P1> --joint-run-dir <P2> --out <P3>   # exit 2 = any gate failed
python docs/handoff/submodelling-20261009/s6/benchmark.py native-decks --spec A/spec.json --calibration C/calibration.json --physical <P3>/physical.json --out <P4>   # refuses unless every gate passed
(cd <P4> && for n in 10 20 40; do ccx -i a5_native_n$n; done)
python docs/handoff/submodelling-20261009/s6/benchmark.py read-native --run-dir <P4> --spec A/spec.json --out <P5>
python docs/handoff/submodelling-20261009/s4a/run_tests.py docs/handoff/submodelling-20261009/s6/test_reference_ccx.py --basetemp=<dir>
```

(The `for … {a,b}` loops are bash; in PowerShell run the six `ccx -i phys_j_<mode>_<axis>` jobs one by one. Each `ccx` job must run in the deck's directory.)

Offline tests of this section use hand-built `.frd`/`.dat` fixtures (not CCX output) and include a joint with zero rotation arm (the recorded cal2/cal3 failure mode) that the checker must reject.

### New unverified assumptions (each can fail on the first run; keep the failing output)

10. `*BEAM SECTION ... OFFSET1=, OFFSET2=` is accepted by 2.22 and moves the solved beam (phys_a decides), normalized by dim 2.
11. B31 sharing a node with S8R keeps dofs 4–6 consistent (no extra link); the stub root face follows `u_node + theta_node × r_arm`.
12. `*NODE FILE, OUTPUT=3D` writes the expanded nodes with 5-digit coordinates at (x, y, ±t/2) for S8R and at the four rectangle corners of the B31 root section; `.frd` rows are ` -1`, I10, 3 × E12.5.
13. Prescribed nonzero `*BOUNDARY` inside the step on shell dofs 1–3 with free rotations gives the rigid-rotation solution (zero-energy mode) without drilling trouble.

## Unverified assumptions (each can fail on the first CCX run; keep the failing output)

1. `*BOUNDARY` 1-6 clamps S8R rotations and `*EQUATION` terms on S8R/B31 rotation dofs 4-6 are applied (cal2/cal3 decide; dev-04 suggests not).
2. `SECTION=RECT` takes dimension 1 along n1 (cal1).
3. `*BOUNDARY` dof 4 at one pinned B31 node removes torsion without disturbing bending (cal1).
4. `*CLOAD, OP=NEW` with a zero entry resets loads and `*DLOAD, ...` follows in the same step (cal4).
5. `.dat` layout as the parser expects (fixtures are hand-written); the `RF` header is `forces (fx,fy,fz)`.
6. A nodeless-of-elements free stub end (stub end node has no load or support) does not trouble the solver.
7. Repository attributes read: `mesh.couplings[*].beam_node/plate_nodes/weights/eccentricity`, `project.beam_section_frame/beam_offset_vector`, `built.node_dofs`.
8. `anysolver` `Iy`/`Iz` naming is unproven (recorded as an observation, not used).
9. `tests/test_submodel_cut.stiffened_project` is importable (the scripts add `tests/` to `sys.path`).
