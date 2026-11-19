# O7: orthotropic shell material axes follow element node order

Status: investigation only, nothing fixed (2026-11-19, branch `claude/o7-ortho-axes-20261119`, from origin/main ca9b137).
Paths: F = src/anyfem, S = ANYfeSolver src/anyfesolver (main e23aa8c4b), M = ANYmaterial owner.

## Verdict

Confirmed. ANYfem lets a user author an orthotropic shell material, passes it to the solver
without any material direction, and the solver then puts the material 1-axis at 0 deg in the
**element-local** frame. Local x follows node order (D0). On a flat 1 m plate with
E1 = 40 GPa, E2 = 200 GPa, turning the node order of half the quads by 90 deg changes max
deflection by **+10.6 %** and von Mises by up to **49 %** of the maximum. Isotropic results are
unchanged (rounding level, 3e-15). The solver already has a mechanism that fixes it (section 5).

## 1. Material types and path to the solver

* Non-isotropic: only `orthotropic` (9 constants, optional Hill48 yield). M/spec.py:12-18 accepts
  `isotropic` and `orthotropic` only; M/orthotropic.py:17-30 says direction 1 is the primary
  material axis and "orientation belongs to the element", i.e. the owner expects the element or
  ANYfem to supply it. No anisotropic (full 6x6) type exists. Composite layups are not a material type.
* Authoring: `Material(name, symmetry="orthotropic", constants={elastic_modulus_1, ...})`
  F/model/materials.py:68-100; the accessors raise for orthotropic (F/model/materials.py:108-117).
  The project tree labels it "orthotropic" (F/ui/tree.py:262).
* Build: `fe_model.register_material(specification.build())` F/solve/build.py:123-128 (comment
  says orthotropic is supported). Shells: `create_shell_element(element_id, node_ids, material_name,
  thickness, formulation, **extra)` in `_add_shells` (F/solve/build.py:253-); `extra` only ever holds
  `reference_normal` (S3). No `material_direction`, no `material_angle_deg`.

## 2. Frame the solver uses

`ShellElement._material_angle(local_frame)` S/elements.py:1776-1791: if `material_direction` is None
the angle is `material_angle_deg` (default 0) **in the element-local frame**; otherwise the global
vector is projected onto local x/y of that element and the angle is taken from it (plus
`material_angle_deg`). Used at the stiffness, stress and recovery sites (S/elements.py:728,
2214-2281, 2940, 3479-3503, 5611). The qualified Q4 and S3 V2D constructors accept both arguments
(S/e4_pl_element.py:2844-2845, S/e4_pl_s3_v2d_element.py:420). ANYfem never sets either (D0-inventory.md:25;
re-checked in `_add_shells`). So material 1 = element local x (diagonal bisector for planar Q4,
edge node0-node1 for S3 V2D): stiffness itself, not only display, depends on node order.

## 3. Use in shipped examples, tests, workflows

* No shipped example, drawing workflow or GUI path creates an orthotropic shell material (searched
  src, tests, diagnostics, docs; README says specs are "serializable isotropic and orthotropic").
  Stiffeners are beams, not smeared orthotropic plates.
* Tests: tests/test_workflow.py:293-370 inject a solver `OrthotropicMaterial` into a built plate to
  check wave speed, post-processing and probes work; they check only finite results.
  docs/QUALITY_CONTROL.md:154 says "ANYfem does not author them", which is no longer accurate.
* So the defect is reachable from the public API and from a saved project with an orthotropic spec,
  but no shipped model is known to be affected. Not searched: user project files.

## 4. Measured error

Script `docs/direction-programme/scripts/o7_ortho_axes_probe.py`, test `tests/test_o7_orthotropic_axes.py`.
1 m x 1 m, t = 10 mm, 50 kPa, pinned edges, Q4 e4-pl, orthotropic E1 40, E2 200, E3 100 GPa,
v12 0.05, G 30 GPa. Case A as meshed; case B quads with centroid x > 0.5 get node order rotated by
one (same geometry and normal; 32 of 64 elements at 0.125 m).

| target size | max deflection A [m] | B [m] | B/A - 1 |
|---|---|---|---|
| 0.25 | 0.024873 | 0.027567 | +10.8 % |
| 0.125 | 0.025189 | 0.027849 | +10.6 % |
| 0.0625 | 0.025284 | 0.028681 | +13.4 % |

* Isotropic control: A = B to 3e-15 (0.01107 m at 0.125).
* Frame-invariant von Mises, element-wise max|A-B| / max A: isotropic 0, orthotropic **0.49**.
  Element-local components (bending_xx etc.) also differ for isotropic (expected, D1/D2 display
  issue); only frame-invariant fields are evidence of a material error.
* All elements turned (E1 along global Y): deflection 0.025189, identical to A, as symmetry of the
  square plate requires. The error is the mixing of directions inside one model.
* Test: strict xfail with the reason, plus the passing isotropic control (2 tests, 2.7 s).
* Not measured: S3/V2D triangles, warped Q4, curved or folded surfaces, Hill yield, nonlinear and
  dynamic runs, a real stiffened-plate model. The orientation report cannot be used on this
  unpublished mesh (64 elements "unchecked: unpublished mesh").

## 5. Does the solver have a sound mechanism? Yes, for a global reference vector

`material_direction` (S/elements.py:1776-1791) is D2's "projected" definition evaluated per element
(`D2-design.md` section 2): project a global vector r onto the element plane, take the angle.
Experiment (throwaway process, NOT committed code: `create_shell_element` wrapped to pass
`material_direction=(1,0,0)`): B equals A to 1e-14 (0.025188916957579556 vs 0.025188916957581395),
isotropic unchanged bit for bit, 0.2-0.4 s per run, no visible cost.
Limits: (a) ValueError when r is parallel to the normal (S/elements.py:1788-1793), so ANYfem must
pick r or a fallback per element first; (b) one global vector only, no cylindrical or per-sheet
axis, so ANYfem computes a vector per element (same code as D2 projected plus fallback chain);
(c) on warped or higher-order elements the frame varies and the solver projects with the frame at
each point (S/elements.py:3479-3503): consistent, not one physical direction on a folded element
(not tested). No solver change appears necessary for planar and gently curved shells.

## 6. Fix options (owner decides)

| | Option | Where | Cost | Notes |
|---|---|---|---|---|
| A | Refuse orthotropic shells at build until a material direction is set (typed error) | ANYfem | hours, 1 test | Fail-closed; isotropic bit-identical; blocks the only authoring path. Same as D2 refusal row 5 |
| B | Pass `material_direction` per element from a user-set reference (project, face or sheet) using the D2 projected definition and fallbacks | ANYfem: `_add_shells`, project model field, GUI, persistence/format bump | 1-2 slices after D2 lands; reuses D2 reference code | Pass nothing for isotropic materials, so isotropic is bit-identical by construction. One projection per element at build is negligible next to assembly. Needs a decision on the default reference (none set = refuse, i.e. A) |
| C | Per-face material axes with explicit angle and reference, including cylindrical, plus a solver reference field | ANYfem and solver (coordinate with the ANYsolver CI cleanup session) | large | Only for real composite or cylindrical orthotropy; no model needs it today |

Suggested order (owner decides): A now, then B sharing the D2 axes definition so display and
material axes use one reference. Orthotropic results change under B by design and must be stated
per model.

## 7. Not run

Full ANYfem suite, CI, independent review, any solver change. Solver line numbers are for main
e23aa8c4b at the time of writing.
