# S0 spike results — submodelling transfer on planar shells

Date 2026-10-09. Level A development evidence for [PLAN.md](../PLAN.md) slice S0. Nothing in
any owner repository was changed. These results informed development and are regression
benchmarks, not blind validation or acceptance.

## Environment

| Item | Value |
|---|---|
| Interpreter | `C:/Python/Python314/python.exe` (3.14.2), numpy 2.4.6, scipy 1.17.1 |
| Stack | `git archive` snapshots of HEAD, loaded first on `sys.path` by `s0_env.py`, which refuses to run if any module resolves elsewhere |
| ANYfem | `f74812d9b2757f40d586f3cbded0d638ffb35909` (0.4.1) |
| ANYgeometry | `a534b0d3782af9007b71edb30b923fc96232be67` (0.4.5) |
| ANYmesh | `d7e74147658e097871de1f4f45d0fe0774987927` (0.5.1), native extension copied from the main checkout, SHA-256 `814b9471...922f` |
| ANYsolver | `da1ba2e96b035d329f046ae33c99acbfd70aa637` (0.4.7) |
| ANYmaterial, ANYfileIO, ANYloads | `d8a233e`, `db73950`, `28a15c0` |
| Elements | Q4 builds as `QualifiedE4PLShellElement`, S3 as `NativeParityE4PLS3V2DShellElement` |

Every JSON file in `results/` repeats the full environment record. To rerun, recreate the
snapshots with `git archive HEAD src pyproject.toml` per repository, copy the native
extension, set `S0_SNAP` if the location differs, then run each `e*.py` with the interpreter
above.

Pitfalls found while setting this up: site-packages `.pth` files put the live `C:\Github\*\src`
trees on the path, so snapshots must come first. ANYsolver's main checkout has uncommitted
edits in the shell element files, which is why snapshots were used.

## What was run

| Script | Plan case | Content |
|---|---|---|
| `e1_a2_exact.py` | A2-exact | Coarse global `[0,1]²` carrying the analytical nodal field; fine submodel `[0.3,0.7]²` not aligned with the global grid; fields (a) tension, (b) affine membrane with rotation, (c) rigid motion; policies P2 (drive six) and P1 (release normal rotation); Q4 and S3 for source and target |
| `e2_a1_restriction.py` | A1 | Element-aligned 3×3-face plate under pressure, edge tension and edge shear; inside model, complementary outside model, both driven with global values |
| `e3_a2_bend.py` | A2-bend | `w = κx²/2`, `θy = −κx` on four global sizes; P2, P1, and translations only |
| `e4_hole.py` | A3/A4/A8 lite | Strip with a hole, d/W = 0.2, global *with* the hole; reference = in-place refinement sequence; submodels from three coarse globals, two box sizes |
| `repro_hole_routes.py` | none | Minimal reproducers for two owner hole routes that fail on HEAD |

## Results

### A2-exact: passes for every pair

24 runs: three fields, two policies, four source/target pairs (Q4/S3). All within allowance.

| Quantity | Value |
|---|---|
| Boundary transfer error / interpolation allowance, worst | 0.11 |
| Interior error / solve allowance with diagonally scaled κ, worst | 0.20 |
| Interior error / solve allowance with unscaled κ, worst | 0.0004 |
| κ₁ unscaled / diagonally scaled | 1.4e6 to 3.4e6 / 1.2e3 to 6.8e3 |
| Backward error η | 4.6e-17 to 1.6e-16 |
| Interface force in the rigid-motion case, applied load zero | up to 3.0e-9 N |
| Ambiguous shared-node hits | 0 (up to 2 hits per node) |

P1 also reproduces `θz = ω` exactly in field (b): the drilling DOF follows the physical
rotation when released.

### A1: exact consistency, and an independent Newton check

| Check | Result |
|---|---|
| Inside mesh equals the global elements of the central face | yes (16 elements) |
| BVH transfer vs direct nodal copy | 1.6e-18 |
| Inside interior vs global, P2 | 4.7e-17 m on a 2.1e-3 m scale; 5.9e-16 rad on a 1.9e-3 rad scale |
| Outside interior vs global | 3.6e-17 m; 5.6e-16 rad |
| Newton across the cut: max of inside plus outside nodal cut force | 1.3e-9 N and 8.4e-11 N·m, on cut forces up to 1.2e4 N |
| Inside free body: cut-force resultant against pressure on the box | −1600.000 N against 1600 N; other components below 2e-10 |
| Inside interior vs global, P1 (rz released) | 8.1e-9 m (4e-6 relative); 4.9e-7 rad (2.5e-4 relative) |
| Rotation sign: correlation of `θy` with `−∂w/∂x` | 1.0000 over 49 nodes |

The inside solve's own allowance (3.7e-16) is smaller than the observed rotation error
(5.9e-16). The global solve's share (κ₁ scaled 1.9e5, about 3.8e-14) covers it. The
allowance must include both solves, as the plan states.

### A2-bend: second order, with a boundary layer; rotations must be driven

| Global h | Boundary `w` error (bound `κh²/8`) | Interior `w` error | Interior `θy` error | Core `θy` error (≥ 0.05 m from the cut) | Translations only: interior `w` error |
|---|---|---|---|---|---|
| 0.25 | 7.81e-6 (7.81e-6) | 7.38e-6 | 1.08e-4 | 4.88e-5 | 1.28e-5 |
| 0.125 | 1.88e-6 (1.95e-6) | 1.86e-6 | 4.04e-5 | 7.53e-6 | 9.19e-6 |
| 0.0625 | 4.69e-7 (4.88e-7) | 3.52e-7 | 6.12e-6 | 2.30e-6 | 8.11e-6 |
| 0.03125 | 1.17e-7 (1.22e-7) | 1.16e-7 | 2.52e-6 | 4.71e-7 | 7.88e-6 |
| Observed order | 2.02 | 2.04 | 1.90 | 2.18 | 0.23 |

Values are for a Q4 target. The S3 target gives the same orders (2.02, 2.04, 1.87, 2.21, and
0.23 for translations only). `θy` scale is 2e-4 rad.
The transfer agrees with an independent piecewise-linear interpolation of `w` to 2.8e-20.
P1 and P2 are identical here because the field has no drilling component.

### Hole: the submodel recovers most of the coarse error; the global still matters

Reference: the global model refined in place converges at observed order 3.0 to
**Kt = 3.1691 ± 0.0013** (Richardson over n = 16, 32, 64). Heywood's fit gives 3.140,
0.9 % lower; the exact Howland value and the finite-length effect are not pinned yet.

| Source | Global Kt (error) | Submodel Kt, extrapolated (error) | Submodel at n = 32 (error) |
|---|---|---|---|
| Global n = 2, 48 elements | 1.608 (−49.3 %) | 2.997 (−5.4 %) | 2.992 (−5.6 %) |
| Global n = 4, 128 elements | 2.286 (−27.9 %) | 3.058 (−3.5 %) | 3.052 (−3.7 %) |
| Global n = 8, 448 elements | 2.825 (−10.9 %) | 3.126 (−1.4 %) | 3.120 (−1.6 %) |
| Global n = 4, smaller box B (1.25d) | 2.286 (−27.9 %) | 3.035 (−4.2 %) | 3.035 (−4.2 %) |

Box A is 2d by 1.75d half-size. The submodel's own discretisation error at n = 32 is about
0.2 %, so the remaining error is coarse-transfer error. It falls as the global hole is
resolved better, and it grows when the box moves closer to the hole.

The in-place refinement reference at n = 32 (7168 elements, 82 s) reaches −0.3 %. A submodel
from the n = 8 global (6 s) at n = 32 (35 s) reaches −1.6 %. On a model this small, in-place
refinement is the better tool. Submodelling pays when the global model is large.

### Owner hole routes on HEAD

| Route | Result |
|---|---|
| Legacy butterfly decomposition, three-face strip | ANYfem structural preparation refuses: "face 3 has no qualified analytic or bilinear support" |
| Neutral trimmed hole, one plain strip, sizes 0.125, 0.05, 0.02 | `S3RepairError`: bounded qualified-S3 repair did not satisfy the admission contract |
| Same O-grid built by hand on an explicit `Plane`, arcs and spokes pinned | Meshes, all quads |

## Findings for the plan

1. **The transfer itself is sound on planar shells.** Interpolation with the source
   element's weights is exact for representable fields, consistent at conforming shared
   nodes, and exactly consistent in the element-aligned restriction. A2-exact and A1 pass.
2. **Rotations must be driven.** Translations-only transfer does not converge for bending
   (order 0.2). This rules out a translations-only policy for shells.
3. **Driving `w` and `θ` from a coarse field creates a shear boundary layer.** Interior
   rotation error near the cut is 2.2 to 5.4 times the core error for Q4 targets and 2.0 to
   9.5 times for S3 targets, and it converges at second order.
   The transfer/equilibrium diagnostic should report error away from the cut separately.
4. **D3 evidence.** P1 and P2 are both exact on representable fields. P2 reproduces A1
   exactly. P1 perturbs a coupled field by 2.5e-4 relative in rotation. A1 consistency
   favours P2. The decision stays with S1 and the owner, as the plan says.
5. **Allowance design (C0 item 2) is confirmed with numbers.** The unscaled κ₁ is about
   1000 times the diagonally scaled one and makes the check nearly vacuous; use the
   equilibrated operator. Include both global and submodel solves. Interface forces in a
   zero-load rigid motion reach 3e-9 N, so force floors cannot scale with the applied load.
6. **The complementary outside model is a strong independent check for A1.** Newton across
   the cut held to 1e-13 relative without using the submodel's own force operator. Add it
   to A1.
7. **Interface-force definition needs a corner rule.** Per-edge resultants are ambiguous at
   corner nodes, which belong to two cut faces. C0's `InterfaceForce` must say how corner
   forces are attributed.
8. **The coarse-transfer error dominates in the hole case.** It was −5.4 %, −3.5 % and
   −1.4 % for three global refinements, against a submodel discretisation error near 0.2 %.
   Under A3's proposed 3 % limit, the global hole must be resolved to about n = 8 here, with
   the global itself at −11 %. Admission condition 1 (the global is adequate at the cut) is
   binding. A practical diagnostic is to drive the submodel from two global refinements and
   report the change.
9. **Box placement matters.** Moving the box from 2d to 1.25d raised the error from −3.5 %
   to −4.2 % for the same global. A4 should keep the placement study.
10. **Two owner hole routes fail on HEAD, independent of submodelling.** Real cut geometry
    with holes will hit them in S2a/S4a. Hand them to the ANYmesh and ANYgeometry owners
    with `repro_hole_routes.py`. The butterfly case looks like missing surface support on
    the patches; the hand-built O-grid passes with an explicit `Plane`.
11. **One BoundaryCondition per driven node works mechanically** at a few hundred nodes.
    G2a is still needed for audit, persistence and the compression guard, not for the
    solve.
12. **Cost.** Build, solve and nodal recovery took about 10 ms per element in this
    environment (28,672 elements in 279 s). Not a gate; relevant for S4 usability.

Not exercised by S0, and still open: the volume cut and bounded closure (G1), member
partitioning, MPC slaves on the cut (D5), junction rotation rank (D6), cylinders and curved
correspondence, per-case `u0` reuse, and multiplier-based interface forces.
