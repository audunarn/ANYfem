# Direction programme — D2 design: result axes tied to the geometry (2026-11-18)

Status: revision 4 (2026-11-19: O5 changed to B2, see sections 1, 5, 5a, 8, 9), previously DESIGN ONLY, revision 3d, Codex-clean after round 8 (rounds 1-8; and the D1
review facts relayed by the coordinator). No source or test
changes. Review record in section 9.

Goal: directional shell results (membrane, bending and surface xx/yy/xy,
transverse shear xz/yz, section resultants) mean the same physical direction
and the same physical side wherever the element sits, whatever its node order.
Today they are in element-local axes built from node order (D0 table; D1 on the
60 m barge: 880 of 4640 shells have local x in a different 0-30 / 60-90 degree
class than their face's majority; 0 elements have a normal against their face's
majority).

Roots as in D0: S = `ANYfeSolver/src/anyfesolver`, F = `ANYfem/src/anyfem`.
"(checked)" = read directly on 2026-11-18 in the `C:\Github` mains;
"(to verify)" = an acceptance item of a slice below; "(Codex r1)" = pointed out
by the round-1 review and confirmed by me.

## 0. Facts this design rests on

| # | Fact | Pointer |
|---|---|---|
| F1 | Homogeneous shell branches, when asked with `return_global=True`, return the full global **surface stress** tensor per recovery point: `global_{xx,yy,zz,xy,xz,yz}_{top,bot}`, built from the local in-plane surface stresses **and** the local transverse shear (`local_shear`) | legacy S/elements.py:3618-3636 (checked); e4-pl warped homogeneous S/e4_pl_element.py:6945-6951, flat 7227-7232 (checked); key set S/recovery.py:1146 (checked) |
| F2 | Global **resultant** tensors `global_membrane_resultant_tensors`, `global_bending_resultant_tensors`, `global_transverse_shear_resultants` are returned by: legacy generalized section (S/elements.py:3349); e4-pl warped generalized (S/e4_pl_element.py:6692-6800); e4-pl **flat** Q4, homogeneous and generalized (built at 7123, before the homogeneous conditional at 7160); qualified S3 including homogeneous (S/e4_pl_s3_element.py:5328); S3 V2D (S/e4_pl_s3_v2d_element.py:1777). **Not** by legacy homogeneous and not by e4-pl warped homogeneous | (Codex r1 + r2, checked 7123/7160) |
| F3 | Centre-frame quantities: warped generalized Q4 builds the global shear vector with the element **centre** frame (S/e4_pl_element.py:6769-6792, checked); warped homogeneous Q4 keeps its native `shear_*` in the centre frame while its surface tensors carry the projection into each station's tangent plane (S/e4_pl_element.py:6868, 6886, 6949; Codex r2) | |
| F3a | Polarity: V2D's physical director is `director_polarity * reference_normal` (S/e4_pl_s3_v2d_element.py:464); qualified S3 multiplies global M and Q by polarity (S/e4_pl_s3_element.py:5352). Tensor-basis normal and physical section director are different things | (Codex r2) |
| F3b | From the D1 review (relayed by the coordinator, not re-checked here): warped qualified Q4 recovers through `ShellElement.compute_stresses` with frames at each recovery point, not `equation7_frame`; with `s3_reference_surface="faceted"` the solve build uses the signed facet normal, not the source normal (F/solve/build.py:287-295); higher-order frames vary within an element, and a folded Q8 can have normals of mixed sign across its recovery points | coordinator message 2026-11-18 |
| F3c | Surface stresses are formed as rounded `m + b`, `m - b` (S/elements.py:3534; anyfesolver_results formulas.py:126), so recovering m and b from surfaces is lossy in float64 (Codex r2 example: m = 1, b = 2^54 recovers m = 0) | (Codex r2) |
| F4 | Local `membrane_xx`, `bending_xx`, `shear_xz` are **stresses** in Pa: N/t, 6M/t^2, Q/t | S/e4_pl_element.py:7160-7175 (Codex r1, checked) |
| F5 | (qualified in section 10 items 1-2: ordinary homogeneous V2D returns no polarity or offset keys; e4-pl warped returns no `natural_point_coordinates`) Station and frame metadata is not uniform: e4-pl reports `physical_director(s)`, `natural_point_coordinates`; S3 V2D reports `frame`, `hammer_points`, `external_barycentric_coordinates`, `physical_station_coordinates`, offsets; legacy reports none of these | S/e4_pl_element.py:6759-6762, 7091 (checked); S/e4_pl_s3_v2d_element.py:1659-1680 (checked); grep S/elements.py (checked) |
| F6 | (qualified in section 10 item 1: not returned for ordinary homogeneous V2D at polarity +1, offset 0) S3 V2D reports moment reference and physical surface offsets (`reference_surface_offset`, `section_origin_offset_from_reference`, `physical_{bottom,top}_offset_from_reference`) | S/e4_pl_s3_v2d_element.py:1673-1680 (checked) |
| F7 | ANYfem runs recovery only if `requested_stress` (a `stress*` quantity key requested) and adds the global route only if `requested_global_stress` (basis global, not patch); modal and buckling shapes are not traversed by that loop | F/application/workflow.py:1475-1490, 4664-4691 (checked; modal/buckling: Codex r1, to verify) |
| F8 | The artifact persists the global surface stresses as `stress_global_*` (12 keys); the global resultant tensors are not persisted; a centre director table `shell_surface_directors` is written by `_add_requested_surface_history` (linear batch and transient) | F/io/result_artifact.py:43, 238-249, 812-826, 997 (checked; 997 Codex r1) |
| F9 | Local fields are stored with `basis="element_local"`; keys carry no axis text | F/io/result_artifact.py:1709, 1809; F/post/fields.py:41-60 (checked) |
| F10 | Sheet drawing normal: planar Sheet: supporting-plane normal with its largest absolute world component positive; cylindrical face: outward radial. Per-sheet thickness-side overrides persisted as `drawing_thickness_sides`, written only when used, format 21 | F/application/thickness_side.py:1-15; F/application/plate_profiles.py:77-119; F/io/project_file.py:104, 2340-2375 (checked) |
| F11 | Drawing in-plane axes: abs(n_z) > 0.9999: u = projected global X; else v = projected global Z, u = v x n | F/application/plate_profiles.py:111-119 (checked) |
| F12 | Orthotropic materials are registered and reach the solver; with `material_direction` unset (ANYfem never sets it, D0) the material 1-axis is at `material_angle_deg` in the **element-local** frame | F/solve/build.py:125-128; S/elements.py:1776-1785 (checked) |
| F13 | Artifacts carry `created_utc` and are gzip-stored | F/io/artifacts.py:1087, 1091 (Codex r1, to verify) |
| F14 | Elements are assigned to faces in `mesh.elements_of_face` | ANYmesh backends/gmsh.py:324 (checked) |

Consequence: a tensor the element itself rotated to global axes from its
native local values, once, with the frame it used, can be re-expressed in any
orthonormal frame [a1, a2, nu] with nu = +-(that frame's normal) by one more
rotation: exact up to two roundings. A quantity *reconstructed* from other
rounded quantities (membrane from surfaces, F3c) is not. So each family is
stored as its own global tensor, never reconstructed. No solver numerics change.

## 1. What is stored with results

### Decision S: frame-free global tensors plus a normalized station record

Per shell element and recovery station, when result axes are requested:

| Item | floats | Branches | Status today |
|---|---|---|---|
| native family tensors in global axes: membrane (6), bending (6), transverse shear vector (3), each rotated once by the element from its native local values with the frame that family is in | 15 / case | all | emitted as resultants by the F2 branches; **missing** for legacy homogeneous and e4-pl warped homogeneous (derived by the B2 module, cross-checked; O5 = B2, revision 4) |
| global surface stress top and bot (6 + 6) | 12 / case | homogeneous, only when surface fields are requested | persisted when the global route is requested (F1) |
| station physical position (3) | 3 / artifact | all | derived per branch (normalization contract) |
| tensor-basis normal per station (3) and per centre-frame family per element (3) | 3 / station + 3 / element | all | e4-pl partly; V2D via `frame`; legacy none |
| physical director polarity per element (1) and resultant polarity convention | 1 / element | all | V2D, S3 (F3a) |
| thickness, moment reference and physical surface offsets | about 4 / element | all | partly (F6; face thickness table F/io/result_artifact.py:245) |

Per element, once: face id, group id (section 2), side sign, side source, rung,
minimum sine over every frame it consumes, refusal code.

**Requirement RC (owner decision 2026-11-18, "A now, C in D2").** The D1
check stays a diagnostic of the static solve model (option A). D2 checks frames
**per analysis, at result recovery** (option C): stations, tensor-basis normals
and physical directors are the ones the solver used in *that* analysis, taken
from its recovery output and the solved reference model of that analysis (through
the B2 module of revision 4, which recomputes frames on the model the analysis
solved and cross-checks them), never taken from the static mesh or the initial build. They therefore include analysis-time imperfections:
the nonlinear / arc-length `imperfection` and the capacity eigenmode
imperfection applied after the initial build. The artifact records, per
analysis, the imperfection provenance (kind, mode or field, amplitude) next to
the station records, and the transform refuses stations whose provenance is
missing (`AXES_MISSING_DIRECTORS`) or belongs to another analysis
(`AXES_DIRECTOR_FRAME_MISMATCH`).

RC prerequisite, a pre-existing gap (Codex r5, confirmed): the nonlinear and
arc-length solvers apply the imperfection to a private model copy
(S/nonlinear_static.py:6158, S/arc_length.py:1053), but ANYfem recovers with
the initial `view.built.fe_model` (F/post/results.py:557); the capacity wrapper
also keeps the initial build (F/solve/run.py:1079). So today recovered stresses
of imperfect runs are evaluated on the perfect geometry. New outputs alone do not
fix this. The fix itself is **not designed in D2**: it is being made in a
separate session ("imperfect-runs-stresses", 2026-11-18). D2 depends on it
through D2-6a, which only states what D2 needs from it: recovery evaluated on
the solved reference model (the imperfect copy), and that model's identity
available for the station provenance. The fix **changes existing recovered
values** of imperfect nonlinear, arc-length and capacity runs; sizing that
change and deciding it (O9) belong to that fix. Until D2-6a lands, axes requests on
imperfect analyses are refused (`AXES_ANALYSIS_NOT_QUALIFIED`).

Two geometries are kept apart: the **reference geometry** (perfect nodes plus
the bound, possibly composed imperfection field) and the **current geometry** of
a frame (reference plus displacements). Each adapter states which one each
family's tensor-basis normal is built on (e.g. corotational recovery uses current
coordinates and a rotated stress frame, S/recovery.py:1254, 1275).

All float64. Directors and stations are stored per frame where they can move
(geometrically nonlinear, transient), unless the recovery provenance states the
reference configuration; reuse across frames is refused
(`AXES_DIRECTOR_FRAME_MISMATCH`).

**Normalization contract.** One adapter per formulation branch maps its own
metadata (F5, F3a, F3b) to, separately: station physical position; the
tensor-basis normal of each stored family at each station (per station or
centre); the physical section director and its polarity; which solver surface
is physically "top" for that polarity; resultant polarity (whether the branch
already multiplied M, Q by polarity); physical top and bottom offsets; moment
reference; reference-surface mode (e.g. S3 `faceted` uses the signed facet
normal, F3b). Negative polarity is refused (`AXES_POLARITY_NOT_QUALIFIED`) in
any branch until a test qualifies it. A branch without an adapter is refused
(`AXES_FORMULATION_NOT_QUALIFIED`). The adapter set and capability matrix are
the output of slice D2-0.

**Missing outputs (O5: DECIDED 2026-11-18 by the owner: (a), "the solver adds
them as extra outputs").** Legacy
homogeneous and e4-pl warped homogeneous emit no global family tensors, and
legacy emits no station or director (F2, F5). Either (a) additive solver outputs
(global membrane, bending, shear tensors; station positions; tensor-basis
normals), emitted only with `return_global=True`, existing keys and values
unchanged; or (b) ANYfem rotates the stored native local families with a frame
it recomputes from the mesh, proven by a test that the recomputed frame
reproduces the solver's own `global_*` surface values to 1e-13 relative.
Decided (a) on 2026-11-18; **REVISED 2026-11-19 (owner: "B2 with cross-check")
to (b), implemented in an unpinned solver module.** Reason: ANYfeSolver PR #74
implemented (a) and was stopped because the qualified source files are pinned by
a successor record that binds each carrier byte-for-byte to its upstream blob
(`docs/reference_cases/e4_pl_source_binding_20261111.json`, schema v2; 12 tests
fail on any edit to `e4_pl_element.py`, `e4_pl_s3_element.py`, `elements.py`),
by a frozen contract (`ge_beam3_g3c_contract_v1.json`) and by
`release_043_runtime.json`. A schema v3 successor would be a change to the
qualification process, which the owner declined. Revised rule (B2):
* A new, unpinned module in ANYfeSolver (`src/anyfesolver/shell_axes_outputs.py`)
  derives, from the dict that `compute_stresses(return_global=True)` already
  returns plus the mesh, displacements and material, the station positions, the
  tensor-basis normals, the director polarity metadata and, where the branch
  emits none (legacy homogeneous, e4-pl warped homogeneous), the three global
  family tensors, by rotating the native local N, M, Q forms with frames it
  recomputes through the existing read-only helpers. Every pinned file stays
  byte-identical; nothing is called unless a caller invokes the function (the
  entry hook is in ANYfem's post/recovery path).
* **Cross-check on every call (acceptance, not an option).** The module's
  recomputed frames are compared with a quantity the solver itself emitted that
  depends on the same frame, so the check is never an identity of the recomputed
  frame with itself:
  - homogeneous branches: the solver's `global_{xx,yy,zz,xy,xz,yz}_{top,bot}`
    (full tensors) against the same tensors rebuilt from the solver's local
    surface values (`local_*`, including `local_shear` and, for warped Q4, the
    centre-frame shear projected into each station's tangent plane as the
    solver documents it) rotated with the module's recomputed station frame;
  - generalized branches: the solver-emitted `global_membrane_resultant_tensors`,
    `global_bending_resultant_tensors`, `global_transverse_shear_resultants`
    against the solver's local N, M, Q components rotated with the recomputed
    frame of that family (station frame for membrane and bending; centre frame
    for shear where the branch uses it, F3), with the branch's polarity
    convention (F3a) applied exactly as the adapter records;
  - the stored tensor-basis normals have unit length (checked), and each derived
    membrane and bending tensor T (in-plane by construction) satisfies
    |nu . T| <= 1e-13 s_T; the shear vector is not required tangent (warped Q4
    centre-frame shear), its normal component nu . Q is recorded (section 4).
  If no solver-emitted frame-dependent reference exists for a family, the
  family is refused (`AXES_FRAME_RULE_MISMATCH` with reason `no_reference`).
  The check is **per family, at the resolution the reference allows** (Codex
  r2-r4; no orientation-sensitivity machinery): every family f (membrane,
  bending, shear) of an element is compared separately.
  (1) Family reference. Generalized, flat Q4, S3 and V2D emit each family's
  global tensor, so the reference is that tensor and the allowance is
  a_f = 1e-13 s_f. Homogeneous branches that emit only the surface tensors
  (legacy, warped Q4) resolve the families after rotating the solver's global
  surface tensors into the module's recomputed station frame (G^T S G):
  membrane = in-plane components (xx, yy, xy) of (top + bot)/2, bending =
  in-plane components of (top - bot)/2 (both surfaces carry the same transverse
  shear, which survives the sum and cancels in the difference), shear = (xz, yz)
  of (top + bot)/2; for warped Q4 the native shear is the centre-frame vector, so
  the derived shear is compared after the same projection into the station's
  tangent plane that the solver documents (F3); the allowance is a_f = 1e-13 s_f + 64 eps s_surf with eps = 2^-52
  and s_surf = max abs of the element's surface tensors (the roundoff of the
  emitted m +- b, F3c). Here the derived family is compared with the reference
  resolved from the solver's own surfaces, never reconstructed for storage.
  (2) Residual r_f = max over the element's stations and components of
  |derived_f - reference_f|, s_f = max over the same of |reference_f|; all values
  must be finite; a missing reference refuses the family (`no_reference`).
  (3) Certification: a family is certified iff r_f <= a_f **and**
  a_f <= 1e-9 s_f (the check can detect any error of the family above 1e-9 of its
  own magnitude, including a reversed sign or a rotation by pi). A family that
  fails the second inequality (for surface-resolved families, roughly
  s_f < 1.4e-5 s_surf) is refused with reason `unresolvable_family`, even when
  its numbers happen to agree; a family with s_f = 0 is certified only if its
  derived tensor is exactly zero.
  (4) Each distinct frame is thereby checked through every family that uses it;
  the centre frame of warped Q4 shear is checked through the shear family only.
  Regression tests (D2-1): (a) isotropic bending of magnitude 1 and membrane
  1e-8 with a wrong frame (1e-6 rad): the membrane is refused
  (`unresolvable_family`, because 1e-8 < 1.4e-5), the bending certified or
  refused on its own agreement; the same with the right frame also refuses the
  membrane (it cannot be certified, by design); (b) anisotropic bending (xx 1, yy -1) and anisotropic membrane (xx 1e-3,
  yy -1e-3): right frame certified for both, a 1e-6 rad frame error refused with a
  residual failure; (c) anisotropic bending 1 with shear (xz 1e-14) and a frame reversed by pi:
  shear refused (`unresolvable_family`); (d) generalized branch with anisotropic N, M and a Q vector: a wrong frame
  of 1e-12 rad is detected at 1e-13 s_f.
  On failure (`r_f > a_f`, nonfinite values, `no_reference`, or
  `unresolvable_family`): typed refusal `AXES_FRAME_RULE_MISMATCH` for that family
  of that element (nothing is stored for it; the reason is recorded), so a
  frame-rule drift fails closed. This per-family rule (1)-(4) is the only
  acceptance rule of the cross-check; it supersedes every earlier "1e-13 relative
  to the family max" wording.
* Consequence for D2-6 (nonlinear, corotational): the module must reproduce the
  recovery's rotated current-geometry stress frame (S/recovery.py:1254, 1275)
  per frame; if that cannot be cross-checked to the bound, the analysis is
  refused (`AXES_ANALYSIS_NOT_QUALIFIED`) until a solver hook is agreed.

**Solver changes are coordinated first (owner instruction 2026-11-18: "For
solver related issues coordinate with the task 'ANYsolver CI cleanup'").** The
B2 module (revision 4; formerly the O5(a) outputs) and any other ANYfeSolver change in a D2 slice are agreed with the
"ANYsolver CI cleanup" session (local_13c40095-3e8f-4330-8826-a8dc2198b6e9)
before implementation starts. That session confirmed (2026-11-18, relayed by
the coordinator) that the then-planned O5(a) change did not conflict with its work, with one
precondition: no D2 solver change starts until ANYfeSolver main has been
fast-forwarded to `claude/ci-green-kernel-20261117` (moves the package from
`src/anysolver/` to `src/anyfesolver/`; expected within hours of that date).
The S/ pointers in this document were read in the `C:\Github\ANYfeSolver`
checkout under `src/anyfesolver/`; their line numbers are re-checked after the
fast-forward, as part of D2-0.

### Derived stress and resultant families

Result-axes keys are separate by physical meaning and unit:

| Family | Keys | Unit | Homogeneous source | Generalized source |
|---|---|---|---|---|
| surface stress | `axes_top_{xx,yy,zz,xy,xz,yz}`, `axes_bottom_*` | Pa | G^T S G of the chosen physical surface (as the solver formed it) | refused unless the branch emits surface tensors |
| membrane / bending / shear stress | `axes_membrane_*`, `axes_bending_*`, `axes_shear_{xz,yz}` | Pa | rotated native family tensors (stress form, or N/t, 6M/t^2, Q/t exactly as the element divides, F4) | refused (`AXES_SECTION_NOT_QUALIFIED`) |
| section resultants | `axes_N_*` (N/m), `axes_M_*` (N m/m), `axes_Q_*` (N/m) | as stated | rotated native resultant tensors where emitted; otherwise refused | rotated N, M, Q about the branch's own moment reference (recorded) |

No family is reconstructed from another (F3c): membrane and bending are never
taken from (top +- bot)/2. Warped homogeneous Q4 shear stays the native
centre-frame vector with its centre normal (F3); its station-projected
counterpart inside the surface tensors appears only as surface xz/yz, labelled
as such. Re-expression never moves the moment reference; translating moments
to another origin (an N-dependent term) is out of scope and refused.

### Size (raw payload; artifacts are gzip-stored, F13)

n_p = stations per element (4 or 9 for 8-node quads; 3 or 7 for triangles,
S/elements.py:1647-1857); the barge's n_p is measured in D2-1.

Per station and case: families 15 floats (120 B); surfaces, only if surface
fields are requested, 12 floats (96 B). Per station once: 6 floats (48 B). Per
element once: centre normal 3 floats + polarity, thickness, offsets, record
(about 64 B in all).

| Model | n_p | per load case: families only / with surfaces | once per artifact |
|---|---|---|---|
| 60 m barge, 4 640 shells | 4 | 2.23 MB / 4.01 MB | 0.89 MB stations + 0.30 MB elements |
| same | 9 | 5.01 MB / 9.02 MB | 2.00 MB + 0.30 MB |
| 1 000 000 shells | 4 | 0.48 GB / 0.86 GB | 0.19 GB + 0.06 GB |

Today's global route already stores the 12 surface floats when requested, so
for such runs the increment is the 15 family floats plus the static records.

D2-1 measures serialized (gzip) size, peak memory and recovery wall time on the
barge, and compares A1 below over 1, 10 and 100 load cases.

### Rejected alternatives

* **A1: existing local components plus per-station element frames.** Cheaper
  per case when local components are already stored (6 static floats per station
  instead of 15 per case). Rejected as the first route because every branch would
  have to export exactly the frame each local family is in (warped Q4: per
  station for some families, centre for others, F3) and legacy exports none. The
  global route's frame use is already inside each element. Kept as the fallback
  if D2-1 shows A2 too large; the decision is made on the measured comparison.
* **Centroid-only frames and values.** Loses the tangent-plane variation on
  curved elements (axis error up to half the element's subtended angle).
* **Re-solve to change axes.** Re-expression must work from the stored artifact.
* **Old artifacts** without global tensors: refused
  `AXES_MISSING_GLOBAL_TENSORS`, never a silent fall back to element-local.

## 2. How the user defines result axes

### Decision U: projected reference direction, uniform per continuity group, explicit fallback chain

```
result_axes:
  default:
    kind: projected
    reference: [1, 0, 0]               # global X
    fallbacks: [[0, 1, 0], [0, 0, 1]]
    band_deg: 10                       # admissible (0, 45]
  overrides:                           # precedence: face > sheet > default
    - target: {sheet: "Transverse BHD 3"}   # id or unique name
      kind: projected
      reference: [0, 0, 1]
      fallbacks: []
    - target: {face: 41}
      kind: cylindrical
      axis_point: [0, 0, 0]
      axis_direction: [1, 0, 0]
      x: hoop                          # or axial
```

Validation when set (typed `AXES_DEFINITION_INVALID`): references and axis
directions finite with norm above the length tolerance (normalized on store);
band in (0, 45]; fallbacks distinct from the reference; target exists and is
unique; no two overrides on one target.

Kinds:

* `projected`: a1 = unit(r - (r . nu) nu), a2 = nu x a1.
* `cylindrical`: axial = projected axis direction, hoop = nu x axial (or
  swapped by `x`). Refused on the axis (`AXES_CYLINDER_ON_AXIS`) and where the
  axis is within the band of nu (`AXES_REFERENCE_NEAR_NORMAL`).
* `drawing` (planar sheets only): F11 u axis used as the reference direction r
  and then projected and normalized against each consumed normal exactly like
  `projected` (a warped station on a planar sheet is not exactly in the sheet
  plane), with the same band check; `AXES_DRAWING_NOT_PLANAR` otherwise.

For every kind G = [a1, a2, nu] is built by projection and normalization at
each consumed normal, so it is orthonormal by construction (checked:
abs(G^T G - I) <= 1e-14, else `AXES_FRAME_NOT_ORTHONORMAL`).

**Continuity group** = maximal set of elements with the same effective
definition, the same side source and sign rule, connected across shared edges
where the adjacent **geometry-oriented** normals nu = s d (section 4, not the
signed winding directors) differ by less than the fold limit, so a reversed
winding never splits a group. Group construction runs only on elements that
passed checks 0-8 of section 3; an element refused there is excluded and does
not split or join groups; a group with a refused member keeps its rung only if
the member's exclusion does not disconnect it (connectivity is recomputed
without it). A face
override therefore splits its sheet into groups; a crease inside a sheet splits
it too. Every element of a group uses the same rung. Nodal averaging is done
only within a group (section 5).

Expected rungs on the barge (to verify in D2-5): deck, bottom, sides,
longitudinal walls rung 0; transverse bulkheads rung 1 (projected Y); oblique
bulkheads rung 0 unless their normal is within 10 degrees of X.

### Persistence

Project key `result_axes`, written only when set, gated at the next free
project format number (allocated at implementation; other branches hold
uncommitted format work, coordinate first). Pattern of `drawing_thickness_sides`
(F10), so documents that do not use it keep their bytes and hashes. An override
that no longer binds (geometry edit) is kept and makes every result-axes
evaluation refuse `AXES_OVERRIDE_UNBOUND` until it is rebound or deleted; it is
never silently replaced by the default.

The artifact embeds the canonical JSON and SHA-256 of the definition used, the
geometry revision hash and the mesh hash. A different definition is evaluated
from the stored tensors without re-solving; a geometry or mesh hash mismatch is
refused `AXES_DEFINITION_STALE`.

### Rejected alternatives

* Fixed global X with no fallback: refuses every transverse bulkhead. Available
  as an override, not the default.
* Per-element fallback: neighbours on one plate in X and Y, the squares again.
* Face parametric u: depends on how the surface was built; diagnostic only.
* Drawing axes as default: consistent with drawings, no singularity on planar
  sheets, but a rule switch at abs(n_z) = 0.9999 and undefined on curved
  sheets. Owner option O3.

## 3. Degenerate cases, fail closed

Checks run in this order; the first failure is the element's (or group's)
recorded refusal. Refused elements carry no value, shown as "refused: reason".

| # | Case | Rule | Refusal |
|---|---|---|---|
| 0 | Definition invalid; override unbound; geometry or mesh hash differs from the recorded one | checked **first**, before any geometry is touched | `AXES_DEFINITION_INVALID`, `AXES_OVERRIDE_UNBOUND`, `AXES_DEFINITION_STALE` (whole evaluation) |
| 1 | Analysis type not yet qualified (nonlinear, transient, modal, buckling until their slice); imported results; patch-recovered fields | | `AXES_ANALYSIS_NOT_QUALIFIED`, `AXES_IMPORT_NOT_QUALIFIED`, `AXES_PATCH_NOT_QUALIFIED` |
| 2 | Formulation branch without an adapter; negative polarity not qualified; result lacks family tensors, stations or normals | | `AXES_FORMULATION_NOT_QUALIFIED`, `AXES_POLARITY_NOT_QUALIFIED`, `AXES_MISSING_GLOBAL_TENSORS`, `AXES_MISSING_DIRECTORS` |
| 3 | Shell assigned to an orthotropic material with element-frame material axes (F12) | the analysis itself depends on node order | `AXES_MATERIAL_ORIENTATION_ELEMENT_DEPENDENT` (until a fixed physical material direction is qualified) |
| 4 | Element on no geometry face, or on more than one | | `AXES_NO_GEOMETRY_FACE`, `AXES_FACE_AMBIGUOUS` |
| 5 | Face association on the **perfect** reference: perfect centroid or any perfect station further than 0.25 h from its face (as D1), or outside the face trim (tangential offsets judged on perfect stations only). Then, for imperfect analyses, the stored reference stations minus the perfect stations must equal the bound imperfection field interpolated at those stations with the element's shape functions to 1e-12 h (no global allowance A; a node maximum does not bound stations of higher-order elements) | catches a distant parallel face independently of the imperfection, and any mismatch between stations and the recorded field | `AXES_OFF_FACE`, `AXES_IMPERFECTION_MISMATCH` |
| 6 | Any consumed normal (station or centre) differs from the face normal at its projection by more than the fold limit (30 deg), sign ignored | folds, knuckles, wrong assignment | `AXES_DIRECTOR_DISAGREES` |
| 7 | Per consumed normal: abs(d . n_side) below cos(fold limit) | side cannot be decided | `AXES_SIDE_UNDECIDED` |
| 8 | Mixed orientation inside one element: the signs s_p = sign(d_p . n_side(p)) of its consumed normals (stations and centre) are not all equal. Catches an inverted or folded mapping (e.g. planar Q8 x = xi, y = eta(1 - 2 xi^2): 3 positive and 6 negative Jacobians at 3x3 stations, all with abs agreement 1, which checks 6 and 7 cannot see; the solver frame routine does not reject it, S/elements.py:1935, Codex r3). A uniformly reversed winding (all s_p = -1) is admissible | | `AXES_ELEMENT_ORIENTATION_MIXED` |
| 9 | Group construction (section 2) over the survivors of 0-8 | | none by itself |
| 10 | Rung selection: for each group, the first rung whose minimum abs(sin(r, nu)) over **every normal the group consumes** (per station and per centre-frame family, F3) is at least sin(band). No frame is built before this | | group refused `AXES_REFERENCE_NEAR_NORMAL` with the minimum sine per rung |
| 11 | Frame construction with the selected rung, then orthonormality check (section 2) | | `AXES_FRAME_NOT_ORTHONORMAL` |

Thresholds (owner decision O4): band 10 deg (direction error amplification at
most 1/sin 10 deg = 5.8); fold limit 30 deg (far above a sound curved element's
tangent-plane variation, far below a real fold). The band can change values,
not only availability: a group near the boundary moves to another rung and its
"xx" changes direction. That change is recorded per element and stated in the
label.

Triangles need no special rule (global tensors and the element's own director;
V2D basis normal = mesher reference normal, D0; its physical director is `polarity * reference_normal`, F3a).

## 4. Top/bottom and signs from geometry (D3 coupling)

Side normal n_side per element at its centroid, recorded as `side_source`:
1. per-sheet flip override (D3; absent in D2);
2. sheet drawing normal (F10);
3. face normal du x dv at the centroid's projection.

The sign is evaluated **per consumed normal**: s_p = sign(d_p . n_side(p)) at
each station (and s_c for a centre-frame family), because frames vary inside
higher-order elements and a folded Q8 can have normals of mixed sign across its
stations (F3b). Check 8 then requires all of an element's signs to be equal, so
each admitted element has one stored side sign s (= every s_p); a mixed element
is refused, never normalized station by station. nu_p = s d_p;
G_p = [a1, a2, nu_p]. Here d is the **tensor-basis normal** of the stored
family; the physical director and polarity (F3a) decide only which solver
surface is "top" and whether the branch already signed M and Q, through the
adapter.

| Family | Result-axes value |
|---|---|
| surface stress | choose the physical surface first: "top" = the +n_side surface = solver `top` if the adapter's physical-director sign q = sign(physical director . n_side) is +1 else `bot` (for legacy branches, which have no physical director, solver `top` follows the numbered normal and q = s; otherwise q comes from the physical director and polarity, not from s, which is built on the tensor-basis normal; they differ e.g. for V2D at polarity -1, where the basis normal is the owner reference normal but solver `top` is on the +director side, D2-0 round 2); then the **full** tensor G^T S G, all six components; no further sign change |
| membrane (stress or N) | a_i . N a_j (invariant to s) |
| bending (stress or M) | s (a_i . M a_j), M as stored after the adapter has removed any polarity factor the branch applied (F3a) |
| shear (stress or Q) | s (a_i . Q), with the frame of the normal Q was built with (centre for warped Q4, F3); residual nu . Q recorded |

Component check (agrees with the D1 review, F3b): for an x-preserving flip of
the element frame the *local* components bending_xx/yy and membrane_xy change
sign and bending_xy does not; in global form this is simply M' = -M, Q' = -Q,
N' = N, which the s factor above undoes.

Shear sign check for Q: with f3' = -f3, f1' = f1, f2' = -f2, sigma'_13 =
-sigma_13 and sigma'_23 = sigma_23, so Q' = -Q; the same for M. For surface
tensors no rule is needed: S is a physical tensor at a physical point.

What D1 does and does not say: D1 compares each element's normal with its
face's **majority**, so "0 normal minorities" means every face is internally
consistent, not that winding agrees with du x dv or with the sheet drawing
normal; and its 60-90 deg "across" class is not an exact 90 deg swap. D2-1
therefore measures, per barge face, the signed agreement of element normals
with du x dv and with the sheet drawing normal, and the actual angle of each
element's local x to the projected reference. On faces where the drawing normal
opposes the element winding, result-axes "top" is the opposite surface from
today's element-local "top"; that is a new field, and its label names the side
("top (+n sheet)").

Out of D2: `fatigue.py:191` chooses the shell side by display winding (D0); D3
moves it to this side table.

## 5. Where the transform lives; what stays bit-identical

**Decision L.** Pure ANYfem post module `anyfem/post/result_axes.py` (numpy,
no Qt): inputs are artifact arrays, the station records and the definition;
outputs are the result-axes families, the per-element record and refusals. The
post access layer calls it for the new keys only (basis `"result_axes"`,
descriptor with the definition hash, the side source and the rung table).

Solve side (ANYfem): a request for any `axes_*` quantity sets **both**
`requested_stress` and `requested_global_stress` (F7), and persists the station
records and, for generalized branches, N, M and Q. Each analysis route claimed
in a slice gets a save, reopen and evaluate test that requests only `axes_*`
fields. Solver: numerics untouched and every pinned file byte-identical; the B2 module is new and unpinned (revision 4).

Rejected locations: solver recovery (needs sheets and geometry the solver must
not know, and would change solver outputs); anypost (no geometry or sheet
concept; a generic tensor-rotation helper could move there later).

**Bit-identical guarantee for ordinary use.** With no `axes_*` request, every
existing artifact array is bit-identical and every key, descriptor and label is
unchanged, including runs that already request the global or patch route.
Checked by comparing arrays bit-for-bit and keys/descriptors/labels exactly
(not file hashes: `created_utc`, F13), on the barge and two small fixtures, plus
the existing artifact and project-file regression tests unchanged. Project
files without `result_axes` keep exact bytes.

### Every changed default or label (owner decisions; none takes effect before D2-7; O1-O6, O8 decided 2026-11-18/19, O7 is a separate investigation, O9 belongs to the imperfect-runs fix)

| # | Change | Size (measured in D2-1/D2-5 before decision) | Reason |
|---|---|---|---|
| O1 | **DECIDED 2026-11-19: yes (default, set in D2-7).** GUI "Membrane xx" etc. show result axes instead of element local | per element: the field change from rotating by the measured angle between local x and the projected reference; on elements near 90 deg xx and yy swap (difference about abs(sigma_xx - sigma_yy)); barge counts and max differences reported per face | removes squares |
| O2 | **DECIDED 2026-11-19: yes for ordinary models; opt-in above a payload threshold (about 256 MB raw estimated per case as the starting point, number fixed and justified from measurements in D2-7).** Request the global route by default whenever shells are solved | barge 2.2-9.0 MB raw per case + 1.2-2.3 MB once; 1 M shells 0.48-0.86 GB per case; recovery time measured | re-expression without re-solve |
| O3 | **DECIDED 2026-11-19: projected global X, falling back to Y then Z.** Default definition: projected X -> Y -> Z, band 10 deg (proposed) vs drawing axes | changes the axes (hence values) on groups where the two differ; counted per group | meaning of "xx" |
| O4 | **DECIDED 2026-11-19: band 10 deg, fold limit 30 deg.** Band 10 deg, fold limit 30 deg | availability and rung, hence values on boundary groups | fail-closed thresholds |
| O5 | Missing outputs: (a) additive solver outputs or (b) recompute outside the pinned files | **DECIDED 2026-11-18: (a); REVISED 2026-11-19 to (b) as B2 with cross-check** (new unpinned solver module, every pinned file byte-identical, `AXES_FRAME_RULE_MISMATCH` on a cross-check failure) | (a) needs a schema-v3 successor record for the pinned qualified sources, declined by the owner |
| O6 | **DECIDED 2026-11-19: labels name the basis, "(element axes)" / "(result axes)"; keys unchanged.** Existing labels gain axis text: "Membrane xx (element axes)" | text only, keys unchanged | basis visible |
| O7 | **NOT part of D2 (owner 2026-11-19): separate investigation.** Orthotropic shells: today their material axes follow node order (F12) | not measured; a modelling defect independent of D2 | flagged for a separate decision |
| O8 | Performance budget with an axes request (P-b): solve-path added time <= 10 % of the solve stage, load <= 10 %, first evaluation <= 1 s (barge) / <= 10 % of solve (larger), field change <= 0.2 s, memory <= 2 x raw payload | **DECIDED 2026-11-18: approved** ("O8 budget approved"); pass/fail per slice | owner: "ensure speed and performance is maintained" |
| O9 | Recovery of imperfect nonlinear/arc-length/capacity runs on the solved imperfect model instead of the initial build (separate fix "imperfect-runs-stresses"; D2-6a is the dependency) | changes existing recovered values of those runs; sized in that fix | today's values are evaluated on the perfect geometry (pre-existing gap, Codex r5) |

## 5a. Performance acceptance (owner: "ensure speed and performance is maintained")

Measurement protocol (all perf criteria): same machine, same thread pinning,
each run in a fresh guarded process (hard timeout, stop only its own PID);
5 runs per variant, old and new interleaved; report the median and the median
absolute deviation (MAD) of wall time per stage (model build, solve, stress
recovery, artifact write) from the existing progress stages, and the process
peak working set (psutil `peak_wset`) per run. Models: the 60 m barge and one
larger model of at least 5x its shell count, chosen and recorded (element count,
mesh settings) in D2-1; candidate: the barge meshed at a 0.25 m target.

* **P-a, no `axes_*` request: unchanged.** Per stage and for peak memory,
  relative change d = (median_new - median_old) / median_old, and measurement
  uncertainty u = 2 x max(MAD_old, MAD_new) / median_old (both dimensionless).
  Pass: d <= 3 % and u <= 3 %. If u > 3 %, repeat with up to 15 runs per
  variant; still u > 3 %: the result is **inconclusive** and reported, never a
  pass. Both models, for a plain run and for a run that already requests the
  global route. Carried by every slice that touches the solve, recovery or artifact
  path: D2-1, D2-3, D2-4, D2-6.
* **P-b, with an `axes_*` request: measured against a budget.** Matched
  controls: the same analysis with and without the axes request (and, as a
  second control, with only today's global route), same protocol and
  uncertainty rule as P-a. Every added stage is measured: solve-path recovery and
  artifact write; artifact load; first `axes_*` evaluation (checks, grouping,
  rung selection, frames); a subsequent field change. Proposed budget (owner
  decision O8, **APPROVED 2026-11-18**): solve-path added time <= 10 % of the same run's **solve stage**
  median (the solver call alone, excluding recovery and write); added artifact
  load <= 10 % of today's load time; first evaluation <= 1 s on the barge and
  <= 10 % of the solve stage on the larger model; subsequent field change
  <= 0.2 s on the barge (frames and groups cached per definition); added peak
  memory <= 2 x the raw payload of section 1. These are **pass/fail
  criteria** of each slice that carries P-b.

  Controls and normalization per stage (Codex r6). Stages that exist without
  the axes request (solver call, recovery, artifact write, artifact load, peak
  memory) use the no-axes run as control. The new stages use an existing
  operation as control on the same artifact: first evaluation = first
  evaluation of the element-local field `membrane_xx`; field change = switching
  between two element-local fields. Added cost = median(with) - median(control),
  timed with `time.perf_counter_ns` in the same process layout. Uncertainty is
  normalized by the stage's **budget** B (a positive absolute value: 10 % of the
  solve-stage median, 1 s, 0.2 s, or 2 x payload bytes), never by a control
  median, so a control near zero is harmless. Two kinds of budget (Codex r7):
  * **incremental** budgets (solve-path added time, added artifact load, added
    peak memory): pass if added + 2 x max(MAD_with, MAD_control) <= B; a
    negative added cost counts as zero;
  * **absolute latency** budgets (first `axes_*` evaluation <= 1 s on the barge
    and <= 10 % of the solve-stage median on the larger model; field change
    <= 0.2 s on the barge): pass if median(with) + 2 x MAD(with) <= B. The
    control is measured and reported for context only and is never subtracted.
  Inconclusive for either kind (repeat up to 15 runs, then report, never a
  pass): 2 x the relevant MAD > 0.5 B. A slice that exceeds the budget fails,
  stops and reports (A1 of section 1 is the prepared alternative).
  Carried by D2-1 (solver outputs and persistence), D2-2 (transform core,
  timed on stored barge arrays), D2-4 (evaluation and display on a field
  change), D2-6 (per-frame directors).
* **P-c, no second build; per-element Python loop only in the opt-in B2 module.**
  The per-analysis frame check (RC) runs on the arrays the recovery already
  returns: no second FE model build, no geometry re-mesh. **Amended 2026-11-19
  (owner, with O5 = B2):** the B2 derivation module (section 1) may loop over
  elements in Python, on the axes-request path only; the ordinary path
  (no `axes_*` request) gains no loop and stays bit-identical (P-a), and the O8
  time budget (P-b) stays pass/fail with the module's cost measured on the barge
  and on the larger model. The checks and grouping of the transform are still
  vectorized
  numpy (face projection with the geometry's `*_many` batch calls as D1 does;
  groups by sparse connected components). Verified by (i) a test that counts
  model builds per analysis (exactly one, as today) with and without an axes
  request, (ii) a review check of the diff for new per-element loops on the
  solve/recovery path, (iii) P-b showing the transform's time scales linearly
  between the two models (ratio of time per station within 1.5x). Carried by
  D2-1, D2-2, D2-6.

## 6. Slices and acceptance criteria

Each slice: a regression test that fails on old code first; Codex review; owner
merges.

**D2-0 Capability matrix (read-only).** For every formulation and branch
(legacy 3/4/6/8 homogeneous and generalized, e4-pl flat/warped homogeneous and
generalized, including whether warped Q4 recovers through
`ShellElement.compute_stresses` (F3b), e4-pl-s3, S3 V2D in source and
`faceted` reference-surface modes): which global family tensors, stations,
tensor-basis normals per family, physical director and polarity handling,
offsets, top sign convention. Accept: a table with file:line
per cell, every cell checked by a small probe; O5 decided by the owner ((a) 2026-11-18, revised to B2 2026-11-19).

**D2-1 Data.** Adapters, persistence, request gates. Precondition (first
slice touching the solver): ANYfeSolver main fast-forwarded to
`claude/ci-green-kernel-20261117`, and the O5 change (now B2, revision 4) agreed with the
"ANYsolver CI cleanup" session (section 1). **Met 2026-11-18** (coordinator):
ANYfeSolver origin/main fast-forwarded to `7f006b7a3`, layout
`src/anyfesolver/`; that SHA is the base for the first solver slice (D2-1). The
coordination with "ANYsolver CI cleanup" before implementation still applies.
Accept:
(i) per branch and per qualified polarity, pure bending: which solver surface is
on the +physical-director side;
(ii) per branch, the stored global family tensors re-expressed in the element's
own frame reproduce its native local membrane, bending and shear values to
1e-13 relative to a fixed nonzero scale per family (the family's max abs over the
fixture), including a mixed-magnitude fixture (membrane 1e-8 of bending and the
reverse) that would fail any surface reconstruction. Revision 4: this storage
test applies to every branch (the derived tensors are rotations of the native
local values, never reconstructed). The **runtime cross-check** of section 1
additionally certifies a family only if it is resolvable: on direct-reference
branches (generalized, flat Q4, S3, V2D) the 1e-8 fixture is stored and
certified; on surface-only branches (legacy and warped Q4 homogeneous) the
1e-8 family is an expected refusal `unresolvable_family`, tested as such; (iii) ordinary-use bit-identical check (section 5);
(iv) barge measurements: n_p, raw and gzip size per case, peak memory, recovery
seconds, signed normal agreement per face (du x dv and drawing normal), angle of
local x to the projected reference per element, A1 vs A2 at 1/10/100 cases.

**D2-2 Transform core.** Accept: independent physical oracles (hand-built
tensors in known frames) to 1e-14; director reversal with the element's own
top/bot swap and M, Q sign gives identical output to 1e-15 relative; one
reproducer test per refusal code; rung selection deterministic, including the
warped-saddle counterexample where the reference is parallel to the centre
director but not to station directors (must refuse or pick the next rung for the
whole group).

**D2-3 Definition and persistence.** Accept: round trip exact; projects without
the key keep exact bytes; invalid definitions refused at set; unbound override
refuses evaluation.

**D2-4 Post and GUI.** New keys, labels naming axes, rung and side, overlay of
a1 (reuse D1 overlay), nodal averaging only within groups, refusals visible per
element. Accept: GUI tests through a persisted artifact and the actual display
reduction (to verify first: how today's contour reduces element values to
nodes); no NaN or zero shown where a refusal applies.

**D2-5 Physical and invariance acceptance.**
* Physical oracles: flat plate with elements in random rotations under uniform
  tension along X (axes_membrane_xx = sigma_0, yy = xy = 0 to 1e-10 relative),
  pure in-plane shear, pure bending (top/bottom sign from the load side), and a
  closed cylinder under internal pressure (hoop membrane against a converged
  fine-mesh reference within a tolerance fixed before the run). Per formulation.
* Permutation: same plates plus a warped saddle and a cylindrical panel; for
  each formulation, every valid node relabelling: quads 4 cyclic x 2 reversal
  with explicit corner and midside maps for 8-node; triangles 3 x 2 with midside
  maps for 6-node; applied uniformly and randomly per element, including
  independent winding reversals (group membership and rungs must not change).
  Stations matched by physical position (1e-12 h). Criterion: abs difference <=
  1e-12 x a fixed nonzero scale per family (max abs of that family's
  `axes_*` components over the fixture, fixed from the unpermuted run; a family
  whose scale is zero is not used as evidence, and a positive fixture must have
  at least one family with a nonzero scale or it fails). Refusal fixtures include
  the folded planar Q8 of check 8. Coverage: every positive fixture
  must produce finite values on 100 % of its elements; refusals are tested only
  in separate expected-refusal fixtures. The same test on element-local fields
  must fail. If a formulation itself is not invariant to that level it is a
  reported finding, not a relaxed tolerance.
* Barge, fixed before assessment: (a) re-solve with every shell's node order
  cyclically shifted by one: all `axes_*` fields equal to 1e-12 relative to the
  field max, element-local membrane xx differs (old-code check); (b) squares
  metric on deck face 1: for each element in the three blocks (x 4.98-7.80,
  27.20-32.80, 52.20-55.02 m), r = abs(v - median of edge neighbours) / D,
  D = max(median over face 1 of the same quantity, 1e-6 x max abs of the field
  on face 1); if that max abs is zero (D = 0) the fixture is invalid and the
  slice fails, it does not pass; pass if no block element has r > 3 for
  `axes_membrane_xx`, and the
  metric must flag element-local membrane xx on the same blocks (if it does not,
  the metric is inadequate and the slice stops); coverage 100 % finite on face 1;
  (c) rung table equal to an independent oracle (a separate script computing
  each group's sheet normal from the geometry alone and applying the rung rule);
  (d) screenshot.

**D2-6a Dependency: recovery bound to the solved model (O9).** Not a D2
implementation slice: D2-6 starts only after the separate
"imperfect-runs-stresses" fix is merged. D2 needs from it, and checks at the
start of D2-6: (1) recovery of nonlinear, arc-length and capacity runs is
evaluated on the solved imperfect reference model; (2) that model's identity and
imperfection field are reachable from the recovered result so the station
provenance of RC can name them; (3) its own regression test (fails on old code)
and its measured change of existing values are in its report. If (2) is missing,
D2-6 adds only the provenance plumbing, not a second recovery path.

**D2-6 Nonlinear, transient, modal, buckling.** Directors per frame, shape
traversal and persistence; refused until qualified. Acceptance for RC:
(i) a nonlinear (arc-length) plate or panel run with a nonzero `imperfection`:
the stored **reference** stations equal those computed independently from the
imperfect reference geometry to 1e-12 h and differ from the perfect mesh by the
imposed field (proves they are not taken from the static build); for a
reference-configuration recovery and for at least two converged frames with
nonzero displacement (corotational), each stored tensor-basis normal equals the
normal the formulation's recovery uses in that frame (reference or current, per
adapter) to 1e-12 rad, and the stored tensors re-expressed in the stored frame
reproduce the native local values of that frame (D2-1 ii criterion);
(ii) a capacity case with an eigenmode imperfection applied after the initial
build: the same checks; (iii) permutation invariance (D2-5 criteria) with the
**same physical imperfection**: the nodal imperfection field of the unpermuted
run is frozen and remapped to the permuted node ids, so mode sign or degenerate
mode selection cannot change the analysis; separately, a regenerated eigenmode
after permutation is compared up to sign (and, for degenerate modes, by the
subspace projection residual <= 1e-10), reported, not used for (iii);
(iv) refusals: provenance naming a different analysis, a wrong frame of the
same analysis, missing imperfection record, stations not matching the field
(`AXES_IMPERFECTION_MISMATCH`). D2-6a (recovery bound to the solved model,
O9) precedes these items.

**D2-7 Owner decisions O1-O7** with the measured sizes.

## 7. Out of scope

Beams (D4); fatigue side (D3); moment-reference translation; renumbering mesh
nodes to align element x (changes the solve input, does not cover curved
faces, triangles or imported meshes; complementary at most).

## 8. Owner decisions summary

O1 DECIDED 2026-11-19: GUI default to result axes; O2 DECIDED 2026-11-19: global route by
default for shell solves on ordinary models, opt-in above a payload threshold (about 256 MB raw per case as the
starting point, fixed in D2-7 from measurements); O3 DECIDED 2026-11-19: default definition projected X -> Y -> Z;
O4 DECIDED 2026-11-19: band 10 deg, fold limit 30 deg; O5 DECIDED 2026-11-18: (a), REVISED 2026-11-19 to B2 with cross-check (new unpinned solver module, `AXES_FRAME_RULE_MISMATCH`; reason: pinned qualified sources, section 10); O6 DECIDED 2026-11-19: labels name the basis,
keys unchanged; O7 orthotropic material axes follow node order today (separate defect; NOT part of this work, separate investigation, owner 2026-11-19);
O8 DECIDED 2026-11-18: performance budget approved, pass/fail (section 5a);
solver changes (the B2 module and any other) coordinated with "ANYsolver CI cleanup"
before implementation; O9 imperfect-run recovery
on the solved model (D2-6a, changes existing values of imperfect runs).

## 9. Review record (Codex, GPT 6.1 Sol, high effort)

### Round 1 (session 01a1ee1f-a7b4-71f1-a2d0-86e1913d9038): NOT CLEAN, 15 findings

| # | Sev. | Finding | Resolution in revision 2 |
|---|---|---|---|
| 1 | blocker | F1 false: homogeneous legacy and Q4 return global surface stresses, not N/M/Q; O5 must precede D2-1 | Confirmed (S/elements.py:3618). F1/F2 rewritten; storage uses surface tensors for homogeneous, N/M/Q for generalized; D2-0 capability matrix; O5 before D2-1 |
| 2 | blocker | Table returned resultants under stress names (local keys are N/t, 6M/t^2, Q/t) | Confirmed (S/e4_pl_element.py:7164). Separate stress and resultant families with units; identities qualified against local values |
| 3 | major | Metadata not uniform (V2D frame/barycentric/stations; legacy none); F5 missed transient | Normalization contract + adapters; F5/F8 corrected |
| 4 | major | Rung selection ignored centre-frame shear: singular counterexample | Rung check over every consumed frame; saddle test |
| 5 | major | Surface zz/xz/yz had no contract | Choose physical surface, full G^T S G, all six |
| 6 | major | Moment reference and offsets omitted | Offsets/reference in station record; re-expression keeps the reference; translation refused |
| 7 | major | Orthotropic shells already reach the solver with element-frame material axes | Confirmed (F12). Refusal row 5; O7 flagged |
| 8 | major | No parameter validation; distant parallel face undetected | Definition validation; off-face and trim checks; unique assignment |
| 9 | major | Face overrides and creases inside a sheet broke group/averaging | Continuity group redefined (definition, side rule, smooth connectivity) |
| 10 | major | Unbound overrides still produced default-based values; imports implicitly admitted | Typed refusals `AXES_OVERRIDE_UNBOUND`, `AXES_IMPORT_NOT_QUALIFIED` |
| 11 | major | Setting only the global flag does not trigger recovery; modal/buckling not traversed | Both gates; modal/buckling moved to D2-6; save/reopen/evaluate tests per route |
| 12 | major | Invariance not a physical oracle; barge metric unthresholded | Physical oracles; fixed squares metric with self-check; warped cases; explicit high-order maps; coverage |
| 13 | major | D1 compares with face majority, not du x dv; across band is 60-90 deg; O3/O4 change values | Section 4 and O-table corrected; signed agreement and angles measured in D2-1 |
| 14 | minor | Hashes not comparable (`created_utc`) | Arrays bit-for-bit plus keys/descriptors/labels; existing global requests included |
| 15 | minor | Size arithmetic and A1/A2 baseline; gzip | Sizes recomputed as raw payload for the new basis; gzip, peak memory and A1/A2 over 1/10/100 cases measured in D2-1 |

### Round 2 (resumed session): NOT CLEAN; r1 #2-8, 10, 11, 13, 14 resolved; r1 #1, 9, 12, 15 not; 9 new

| # | Sev. | Finding | Resolution in revision 3 |
|---|---|---|---|
| 16 | major | Flat homogeneous Q4 and homogeneous S3 do emit global N/M/Q | F2 corrected (7123 before the 7160 conditional, checked); missing set is legacy homogeneous and warped homogeneous Q4 |
| 17 | blocker | membrane/bending from (top +- bot)/2 is lossy in float64 | Accepted (F3c). Each native family is stored as its own global tensor; no reconstruction; mixed-magnitude fixture in D2-1 |
| 18 | major | Warped homogeneous Q4 native shear is centre-frame; surfaces carry the station projection | F3 extended; native centre-frame shear kept; surface xz/yz labelled as station-projected |
| 19 | major | Signed-director grouping depends on winding | Groups built from nu = s d; winding-reversal test of membership and rungs |
| 20 | major | Refusal order consumed stale/invalid definitions late | Check 0 first; order fixed; refused members excluded before grouping |
| 21 | major | Tensor-basis normal vs physical director and polarity conflated | Adapter normalizes them separately; negative polarity refused until qualified |
| 22 | major | Drawing u not tangent at warped stations; G not orthonormal | All kinds project and normalize per consumed normal; orthonormality check |
| 23 | major | Acceptance passed with all-refusal; zero scales; zero median | Coverage 100 % on positive fixtures; separate refusal fixtures; fixed nonzero scales; D floored; rung oracle |
| 24 | minor | Generalized storage double-counted; element records omitted at 1 M | Size table rebuilt (families / surfaces / stations / elements) |

Also folded in from the D1 review via the coordinator (F3b): per-station frames
and side signs, the `faceted` S3 normal, the warped-Q4 recovery path in D2-0,
and the bending_xy sign statement.

### Round 3 (resumed session): NOT CLEAN; 16-22, 24 and r1 #1, 9, 15 resolved; #23 / r1 #12 still open; 2 new majors

| # | Sev. | Finding | Resolution in revision 3a (reviewed in round 4) |
|---|---|---|---|
| 23 / r1 12 | major | Barge denominator D zero for an all-zero field | D = 0 makes the fixture invalid and the slice fail; positive fixtures need a nonzero family scale |
| 25 | major | Frame G built and checked (old check 8) before the rung was chosen: undefined projection on transverse bulkheads before fallback Y | Order now: eligibility 0-8, groups, rung selection, then frame construction and orthonormality (check 11) |
| 26 | major | Mixed-sign folded Q8 stations pass checks 6-7 and per-station signs hide the inversion (planar Q8 x = xi, y = eta(1 - 2 xi^2)) | New check 8 `AXES_ELEMENT_ORIENTATION_MIXED`: all of an element's signs must agree; one stored sign per admitted element; the folded Q8 added as a refusal fixture |

Outcome after round 3: three rounds used (the stop rule); the owner approved a
focused round 4 on items 23, 25, 26 only.

### Round 4 (resumed session, owner-approved, focused on 23, 25, 26 and the text they touched): CLEAN

| # | Verdict |
|---|---|
| 23 / r1 12 | RESOLVED: an all-zero barge field invalidates the fixture and fails the slice; positive fixtures need a nonzero family scale |
| 25 | RESOLVED: eligibility and grouping precede rung selection; frame construction and orthonormality follow it |
| 26 | RESOLVED: check 8 refuses mixed station/centre signs before normalization; uniform reversal admissible; folded-Q8 fixture required |

No new blocker or major defects in the touched text. Final state: revision 3a,
Codex-clean (scope of round 4: the round-3 resolutions; rounds 1-3 covered the
whole document). Nothing remains open from review; owner decisions O1-O7
(section 5) remain.

### Owner-directed change after round 4 (2026-11-18): requirement RC, not Codex-reviewed

Owner decision "A now, C in D2": D1 stays a static-model diagnostic; D2 checks
frames per analysis at result recovery, including nonlinear/arc-length and
capacity eigenmode imperfections. Added: requirement RC (section 1), the
imperfection allowance in check 5 (section 3), and acceptance items (i)-(iv) in
D2-6 (section 6). Also recorded: O5 = (a) decided 2026-11-18. The check-5
allowance is a new fail-closed rule, so this text is not covered by the round-4
"clean" verdict.

### Round 5 (resumed session, focused on RC, check 5, D2-6 (i)-(iv), section 5a): NOT CLEAN, 6 majors

| # | Finding | Resolution in revision 3b (**not reviewed**; no sixth round without the coordinator) |
|---|---|---|
| 27 | Recovery is not bound to the imperfect solved model (S/nonlinear_static.py:6158, S/arc_length.py:1053 vs F/post/results.py:557; F/solve/run.py:1079) | Confirmed. RC prerequisite paragraph; new slice D2-6a; O9 (changes existing values of imperfect runs); axes on imperfect runs refused until then |
| 28 | D2-6 oracle assumed the wrong configuration (corotational current frames) | Reference vs current geometry kept apart per adapter; reference-configuration and two nonzero corotational frames tested; wrong-frame refusal |
| 29 | 0.25 h + A admits a wrong nearby face; node maximum does not bound stations | Check 5 split: face association on the perfect reference; station displacement must equal the interpolated bound field (`AXES_IMPERFECTION_MISMATCH`); no global A |
| 30 | Capacity permutations may pick another mode sign or degenerate combination | Frozen, remapped nodal imperfection field for (iii); regenerated mode compared separately up to sign/subspace |
| 31 | P-a allowance grows with noise (old 100 x5 vs new 100..500 passes) | Dimensionless d and u; pass needs d <= 3 % and u <= 3 %; otherwise repeat, then inconclusive, never pass |
| 32 | P-b budget omitted load, evaluation and field-change cost; denominator undefined | Matched controls; every added stage budgeted; denominator = solve stage alone; reported only until O8 approved |

Open after round 5: the revision-3b text above is unreviewed. O1-O4, O6-O9 open;
O5 decided.

### Round 6 (resumed session, owner-approved, focused on 27-32 and the text they touched): NOT CLEAN, 1 major

27-32 all RESOLVED (27: the D2-6a dependency statement judged sufficient for D2;
the fix itself is in the separate "imperfect-runs-stresses" session).

| # | Finding | Resolution in revision 3c (**not reviewed**; no seventh round) |
|---|---|---|
| 33 | P-b inherited P-a's uncertainty denominator (median_old), undefined for new stages with no without-axes counterpart | Per-stage controls defined (no-axes run; for new stages the element-local `membrane_xx` first evaluation and a local field switch); uncertainty normalized by the positive stage budget; pass = added + 2 MAD <= B; inconclusive if 2 MAD > 0.5 B |

Codex's round-6 remark that O8 was still report-only predates the owner's
approval recorded the same day (O8 now pass/fail). Text changes made during or
after round 6 and not reviewed: O8 decision, the solver-coordination note and
the fast-forward precondition (owner/coordinator instructions, text only), and
the finding-33 resolution. Open: O1-O4, O6, O7, O9; O5 and O8 decided.

### Round 7 (resumed session, owner-approved, focused on 33 and the text it touched): NOT CLEAN, 1 major

33 RESOLVED.

| # | Finding | Resolution in revision 3d (reviewed in round 8) |
|---|---|---|
| 34 | The incremental test (with minus control) replaced O8's absolute latency limits: a 0.15 s control and 0.30 s field change would pass against 0.2 s | Budgets split: incremental (solve path, load, memory) use added + 2 MAD; absolute latency (first evaluation, field change) use median(with) + 2 MAD(with) <= B, control reported only |

Open: finding-34 resolution unreviewed; owner decisions O1-O4, O6, O7, O9 (O5,
O8 decided).

### Round 8 (resumed session, owner-approved, focused on 34 and the text it touched): CLEAN

34 RESOLVED: absolute-latency gates compare the with-axes median plus
uncertainty directly with the O8 limits; controls are not subtracted. No new
blocker or major defects.

Final state: revision 3d, Codex-clean (round 8 covered the finding-34 text;
rounds 1-7 covered the rest). Not covered by any round: the text-only owner and
coordinator records (O5 and O8 decisions, solver-coordination note, fast-forward
precondition). Open owner decisions: O1-O4, O6, O7, O9.

## 10. D2-0 result: capability matrix (2026-11-19)

Matrix and probes: `evidence/d2-0/matrix.md`, `probe_capability.py`, `capability.json`
(25 branches; cells are probed except those the matrix marks source-derived; run time about 20 s, 4 threads). Solver checkout
ANYfeSolver `e23aa8c4b` (D2 base `7f006b7a3` plus 17 comment lines in the V2D file).
Corrections to section 0 and new facts that the later slices rely on:

1. F6: ordinary homogeneous V2D (polarity +1, offset 0) returns no `director_polarity`
   or offset keys (V2C shortcut, `e4_pl_s3_v2d_element.py:1605-1611`); they appear for
   generalized sections, polarity -1 or nonzero offset. Qualified S3 always returns the
   offset keys but no `director_polarity` key (`e4_pl_s3_element.py:5284-5314`). under B2 the
   module supplies polarity and offsets for both (the pinned sources are not changed).
2. F5: e4-pl warped returns no `natural_point_coordinates` (planar path only,
   `e4_pl_element.py:7091`); it returns `physical_director` and per-station
   `physical_directors`. Warped without `reference_normal` returns no metadata at all.
3. F3b: holds for warped homogeneous only; generalized goes through
   `ShellElement._compute_generalized_section_results` (`elements.py:3253`).
4. Pointers moved: legacy F1 `elements.py:3572-3617`; V2D global resultants 1763-1790;
   key set `recovery.py:1145-1148`.
5. `create_shell_element(formulation="e4-pl-s3")` builds V2D; qualified S3 is reachable
   only by direct construction, and ANYfem allows only `legacy-s3` / `e4-pl-s3-v2d`.
   There are no qualified 6/8-node e4-pl variants (refused by the solver).
6. Station order is data dependent: planar Q4 `natural_point_coordinates` are
   (-,-),(+,-),(+,+),(-,+); warped and legacy Q4 are (-,-),(+,-),(-,+),(+,+). The same
   index is a different physical point depending on whether warpage exceeds 1e-10.
   Adapters must match stations by position, never by index.
7. n_p: legacy T3 3, T6 7, Q4 4, Q8 9 (4 if reduced); e4-pl Q4 4; qualified S3 7; V2D 3.
8. Polarity convention differs per branch (polarity -1): e4-pl global M, Q negate and the
   frame normal is the director only when the reference normal is collinear with the numbered-frame normal (see the caveat at the end of this item); V2D global M, Q negate but the basis normal is the owner
   `reference_normal`; qualified S3 global M, Q are multiplied back to the owner convention
   while its surfaces stay in director convention (so S3's global M disagrees in sign with
   its own surfaces). Negative polarity stays refused until each adapter is qualified. These statements rest
   on pure-bending probes: they support the top-side rule and global M, Q negation only.
   Local components do not negate uniformly (e4-pl maps curvature diag(p,p,1) and shear
   diag(p,1), `e4_pl_element.py:4019-4022`); V2D surface tensors with transverse shear do
   not simply swap top/bottom (mixed-load difference 1e7 Pa); and the recovered e4-pl
   frame normal equals `polarity * reference_normal` only when the reference normal is the
   numbered-frame normal, in either direction (`:4038-4053`); the returned `physical_director` is the tensor-frame normal and `physical_reference_director` is `polarity * reference_normal`. D2-1 adapters keep the director and the tensor-basis
   normal as separate vectors and probe polarity with mixed loads.
9. Top side: `top = m + b` is on the +director side for e4-pl, S3 and V2D at both
   polarities; e4-pl is unaffected by reversed numbering. Legacy has no director: top is
   the +numbered-normal side and flips with node order.
10. Global tensors carry no normal sign; the sign must come from director metadata.
11. Legacy warped Q4 generalized and e4-pl warped without `reference_normal`: the shear
    is computed in the centre frame but rotated with the station frame (a mixed-frame
    case for the adapter, to be handled or refused).
12. `faceted` is an ANYfem build choice (`build.py:287-295`), not a solver mode; the
    solver refuses a reference normal off the facet normal.

The missing outputs per branch (now derived by the B2 module) are listed in `matrix.md`. In short: legacy homogeneous
and e4-pl warped homogeneous lack the three family tensors; every branch except V2D lacks
station positions; tensor-basis normals per family are missing except V2D `frame`; qualified
S3 returns no frame or director.

Not probed (carried into D2-1/D2-5): legacy 6/8-node reversed numbering; orthotropic and
Hill materials, custom `natural_points`, nonzero initial stress; nonlinear and corotational
recovery frames; ANYfem's recovery wrapper (`_RecoveryIsotropicMaterial`); strict-flat
V2/V2B/V2C candidates; the barge's planar/warped split.

### Revision 4 (owner decision 2026-11-19, "B2 with cross-check")

Triggered by D2-1 implementation: ANYfeSolver#74 (additive solver outputs) was
correct and Codex-clean but cannot merge without a v3 successor record, because
`e4_pl_source_binding_20261111.json` binds the qualified Q4/S3/legacy sources to their
upstream blobs and symbol-closure hashes (12 pin tests), and `ge_beam3_g3c_contract_v1.json`
and `release_043_runtime.json` pin the same files. Changed text: section 1 (O5 paragraph),
section 5 (O5 row), section 5a (P-c), section 6 (D2-1 precondition), section 8. The cross-check
bound (1e-13 relative to the family max abs) is the one section 1 had already required for
option (b). Review of the changed text: see the round record below.
