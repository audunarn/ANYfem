# D2-0 capability matrix: shell result recovery with `return_global=True`

Probe: `probe_capability.py` (same folder) -> `capability.json`. One small element per branch, rigidly
rotated (generic orientation), recovered with `return_global=True`. Solver: `C:/Github/ANYfeSolver`
working tree, HEAD `e23aa8c4b` (layout `src/anyfesolver/`; descendant of `7f006b7a3`, the D2 base; differs
there only by 17 comment lines in `e4_pl_s3_v2d_element.py`, so V2D line numbers below are for HEAD).
`S/` = `src/anyfesolver/`. Run: `timeout 300 .venv/Scripts/python.exe probe_capability.py`, 4 threads, about 20 s.

Probe conventions. Pure bending state: curvature k about the local y axis such that the **geometric +N0
side** (N0 = third column of the placement rotation) is in tension. "top on +side" = `sigma_aa(top) - sigma_aa(bot) > 0`
with `a` along local x. "Tensor-basis normal" = null axis of each global family tensor, compared with the
solver's own frames (probe-side use of private `_local_frame_and_derivatives`, `_center_frame`,
`equation7_frame`, `triangle_frame`). A tensor is even in the normal, so **no stored global tensor carries the sign of
its normal**; the sign must come from director metadata. "Varying" = frame differs per station (probe angle given).
Branch ids in `capability.json`: `legacy-{L3,L4flat,L4warp,L6flat,L6curv,L8flat,L8curv}-{h,g}`,
`e4pl-Q4flat-{h,g}`, `e4pl-Q4warp-{h,g}`, `e4pl-Q4{warp,flat}-*-noref` (no `reference_normal`), `s3-{h,g}`, `s3v2d-{h,g}`.
h = homogeneous (material + thickness), g = generalized `GeneralizedShellSection`.

Reachability notes (probe-verified):
- `create_shell_element(..., formulation="e4-pl-s3")` now returns **`NativeParityE4PLS3V2DShellElement`**
  (`S/elements.py:5791-5792`), not `QualifiedE4PLS3ShellElement`. The qualified S3 class is reachable only by direct
  construction or `from_dict`. ANYfem policy allows `s3 in {legacy-s3, e4-pl-s3-v2d}` (`F/model/formulations.py:14`), q4 in
  `{legacy, e4-pl}`, higher order `legacy` only. The `s3-*` rows are therefore off the ANYfem policy.
- No qualified (`e4-pl`) 6- or 8-node variant exists: `create_shell_element(formulation="e4-pl")` with 6/8 nodes raises
  `ValueError: E4-PL is available only for four-node shell topology` (probe `higher_order_qualified_refusals`). 6/8-node = legacy only.
- `e4-pl` Q4 takes the **planar** path or the **warped** path per element by data: `warpage > planar_tolerance (1e-10)`
  (`S/e4_pl_element.py:7026`). The two paths differ in outputs (below), so one formulation needs both adapters.
- S3 V2D `faceted` vs `source` is not a solver mode: the solver gets only `reference_normal`. `faceted` is an ANYfem build choice
  (`F/solve/build.py:287-295`: reference = signed chord-facet normal with `dot(facet, source) > 0`). The solver refuses a
  reference normal off the facet normal (`S/e4_pl_s3_v2d_element.py:688-690`; probe: tilt 0.25 rad gives
  `NativeParityCapabilityError: S3 V2D reference normal is not facet-normal`). With a facet-normal reference the returned
  `frame[:,2]` equals it exactly (probe 1.0). So on a curved sheet `source` mode is only admissible where the source normal is
  already the facet normal; both modes look identical to the recovery output.

## Matrix

Cell format: **probe result** (pointer). Items: (1) global family tensors and surface tensors; (2) station/frame/director metadata;
(3) tensor-basis normal per family; (4) physical director and polarity handling; (5) offsets / moment reference; (6) top side;
(7) n_p. `DOC!` marks a contradiction with D2-design F1-F6 (details in the discrepancy list).

| Branch | (1) global outputs | (2) metadata returned | (3) tensor-basis normal | (4) director / polarity | (5) offsets / moment ref | (6) top on +director side? | (7) n_p |
|---|---|---|---|---|---|---|---|
| **Legacy 3-node, h** | 12 surface keys `global_{xx,yy,zz,xy,xz,yz}_{top,bot}` (+ `local_*`); **no** family tensors (`S/elements.py:3572-3617`, no family code on this path). Matches F1/F2. | none (`metadata {}`) | Surface tensor zz=0 in the centre frame (constant for T3). Frame = `_center_frame` (`S/elements.py:2036`) = per-station `_local_frame_and_derivatives` (`:1926`), identical. Normal = numbered (winding) normal, not returned. | no director. No polarity concept. Normal flips with node order: reversed numbering gives `basis_normal . N0 = -1`. | none | `top = m + b` (`:3534`) is the **+numbered-normal** side. Probe: top on +N0 for CCW order, on -N0 for reversed order. Not a director side. | 3 (`gauss_points`, `:1833-1840`; Hammer 1/6,2/3 points) |
| **Legacy 3-node, g** | 3 family tensors, built per station with station frame `R` (`S/elements.py:3349-3371`; results `:3388-3395`). No surfaces (`physical_stress_available False`). | none | membrane, bending, shear: all in the centre frame (constant). | none. M sign follows numbered normal: reversed numbering gives `M_aa < 0` for the same physical state. | none | no surfaces; M positive = tension on +numbered normal. | 3 |
| **Legacy 4-node flat, h / g** | as 3-node (h: 12 surfaces, no families; g: 3 families) | none | constant frame | as 3-node | none | as 3-node | 4 (2x2; order (-,-),(+,-),(-,+),(+,+)) |
| **Legacy 4-node warped, h** | 12 surfaces, station frames (zz=0 in station frame; 0.139 relative in centre frame). No families. | none | surfaces: **station** frame (`S/elements.py:3572-3579`, `R` per point). Shear inputs `tau_s` come from the centre-frame MITC field (`:3521`, `G_center`) but are placed in the station tensor. | numbered normal, as flat | none | +numbered normal | 4 |
| **Legacy 4-node warped, g** | 3 families | none | membrane **station**, bending **station**, shear **station** (3.7 deg station-vs-centre spread). Shear strain and `As` are the centre-frame values (`:3327-3332`) but are rotated with the station frame `R` (`:3371-3373`), so shear differs from the e4-pl warped-g choice (centre). | none | none | M positive = tension on +numbered normal | 4 |
| **Legacy 6-node, h / g** (flat and curved) | h: 12 surfaces, **no** families. g: 3 families. | none | flat: constant. Curved (z=0.3 y^2): **station** frames, 13.1 deg spread; surfaces zz=0 in station frame (0.28 rel. in centre frame). Shear uses the station frame and section (`:3333-3335`), not the centre one. | none (numbered) | none | +numbered normal (not probed for reversed order) | 7 (`TRI_GAUSS_POINTS_7`) |
| **Legacy 8-node, h / g** (flat and curved) | as 6-node | none | curved: **station** frames, 10.4 deg spread. `native_recovery_frames` (`S/recovery_shell_frames.py:10`) batches the same frames for exact `ShellElement` + exact isotropic `_RecoveryIsotropicMaterial` (returns None otherwise). | none | none | +numbered normal | 9 (3x3), 4 if `reduced_integration` (`:1838-1839`) |
| **e4-pl Q4 planar (flat), with `reference_normal`, h** | 3 families (`S/e4_pl_element.py:7123-7157`) **and** 12 surfaces (`:7227-7260`). Matches F1+F2. | `natural_point_coordinates (4,2)` (`:7091`), `physical_director (3,)` centre vector only (`:7117`), `physical_director_authoritative`, `numbered_frame_director_sign`, `director_polarity_policy_id`, `director_reversal_transform_id`. No per-station director, no positions. | all families and surfaces in the one `equation7_frame` frame mapped to the physical-director frame (`_physical_director_context`, `:4011-4050`; called at `:7062-7065`); constant over the element. Normal = `physical_director` (probe angle 0). | physical director = `polarity * reference_normal` (`:4001-4009`); `_physical_director_context` only flips the numbered frame's y and normal axes (`:4038-4049`), so the recovered frame normal equals the director only when the reference normal is the numbered-frame normal (a reference tilted 0.25 rad gave dot 0.968912; Codex r1). Reversed numbering gives the same result. Polarity -1: curvature maps with diag(p,p,1) and shear with diag(p,1) (`:4019-4022`), so local bending xx/yy and shear xz are negated but twisting xy and shear yz are NOT; global M and Q are negated (probe), membrane tensor unchanged, **top/bot swap** (pure-bending probe). So M, Q are in the director convention and the frame normal *is* the director. | none (no offset concept in Q4) | **Yes**, both orders and both polarities: `top_on_plus_director True`. M positive = tension on +director. | 4; order (-,-),(+,-),(+,+),(-,+) (**differs from warped path**) |
| **e4-pl Q4 planar, g** | 3 families, no surfaces | as h | as h | as h (global M, Q negated under polarity -1) | none | M positive = tension on +director | 4 |
| **e4-pl Q4 planar, no `reference_normal`, h** | as planar h | as h but `physical_director_authoritative False`; `physical_director` = numbered normal | as h | numbered normal; reversed order flips top side and M sign (not a director) | none | +numbered normal | 4 |
| **e4-pl Q4 warped, with `reference_normal`, h** | 12 surfaces only; **no** family tensors (`S/e4_pl_element.py:6808-6985`, raw call `return_global=False` at `:6818`). Matches F1/F2. | `physical_director (3,)` (centre, `:6875`), **`physical_directors (4,3)`** per station (`:6876`), `numbered_frame_director_sign(s)`, `warped_direct`. **No `natural_point_coordinates`** (`DOC!` F5). | surfaces: **station** physical frame (zz=0 to 1e-16; 0.139 rel. in centre frame) (`:6945-6985`); `physical_directors` equals the station normals (probe angle 0). Native local `shear_xz/yz` are the centre-frame values mapped by `center_shear` (`:6887`; probe: identical to legacy centre shear, diff 0.0). Surface shear = centre shear projected into each station's tangent plane. Matches F3. | `polarity * reference_normal`, frames per station via `_physical_director_context` (`:6828` centre, `:6852` stations). Polarity -1: same diag(p,p,1)/diag(p,1) mapping as the flat path (not a uniform negation of local bending and shear), top/bot swapped (pure-bending probe); the recovered normal is the station frame normal, which need not equal `polarity * reference_normal`. | none | **Yes** both orders, both polarities (`top_on_plus_director True`). | 4; order (-,-),(+,-),(-,+),(+,+) |
| **e4-pl Q4 warped, with `reference_normal`, g** | 3 families, no surfaces (`S/e4_pl_element.py:6692-6806`) | same as warped h | membrane **station**, bending **station**, shear **centre** (`:6791-6793`); station vs centre normal spread 3.7 deg. Matches F3. | as warped h; global M, Q **negated** under polarity -1, membrane same | none | M positive = tension on +director | 4 |
| **e4-pl Q4 warped, no `reference_normal`** (h and g) | falls through to `ShellElement.compute_stresses` (`:7034-7041`): h = 12 surfaces, no families; g = 3 families | **none** (legacy output) | = legacy 4-node warped (station frames; g shear **station**) | numbered normal, not a director | none | +numbered normal | 4 |
| **Qualified S3 (direct class), h** | 3 families (`S/e4_pl_s3_element.py:5328-5365`) and 12 surfaces (`:5433-5470`). Matches F2. | **only** offsets and policy ids; **no** frame, director, stations, natural coordinates (probe: keys listed in JSON). Station table is `TRIANGLE_QUADRATURE` (`:405`, 7 points, centroid first). | one frame = `triangle_frame(coords, reference_normal)` (`:1343`) with normal = **owner `reference_normal`** (not polarity-multiplied), constant. Not returned. | director = `polarity * reference_normal` (`:4853-4859`), property only. Local M, Q already in director convention (`:4894`). Global M and Q are multiplied by polarity again (`:5352-5360`) so they are in the **owner-normal convention**; surface tensors keep the director convention (shear x polarity `:5443`). Probe at polarity -1: global M, Q **unchanged** (`same`), local M, Q negated, top/bot swapped. So M tensor positive = tension on +owner normal but `top` = +director side. Reversed (CW) numbering is refused (`ValueError: winding opposes owner normal`). Confirms F3a. | `reference_surface_offset`, `section_origin_offset_from_reference`, `physical_{bottom,top}_offset_from_reference`, `reference_surface_offset_policy_id` (`:5303-5316`); present for h and g, every polarity. Moment reference = nodal reference surface at `reference_surface_offset` along the director. | **Yes** for surfaces at both polarities (`top_on_plus_director True`). M tensor: +owner normal (polarity -1: `M_positive` is on the -director side). | 7 |
| **Qualified S3, g** | 3 families, no surfaces | as S3 h | as S3 h | as S3 h | as S3 h | M as S3 h | 7 |
| **S3 V2D (source mode = reference normal equal to the facet normal), h, polarity +1, offset 0** | 3 families (`S/e4_pl_s3_v2d_element.py:1763-1790`) and 12 surfaces (`:1800-1828`). Matches F2. | `frame (3,3)` (`:1665`), `hammer_points (3,2)` (`:1659`), `external_barycentric_coordinates`, `physical_station_coordinates (3,3)` (`:1661`, equals barycentric x nodes to 0.0), `internal_order`, `physical_weights`, plus private `_recovery_stress_frame`, `_recovery_coordinates`, `_reference_surface_recovery_coordinates` (`:1783-1790`). **No `director_polarity`, no offset keys** in this ordinary case: the V2C shortcut returns V2C's dict (`:1605-1611`, trace shows `e4_pl_s3_v2c_element.py:1675`). **`DOC!` F6.** | one constant `frame` (returned); normal = `reference_normal` (`:698-700`), **not** multiplied by polarity. | director = `polarity * reference_normal` (`:464-469`), property only (not returned). Section transform multiplies bending and shear by polarity (`:808-811`); global tensors are **not** re-multiplied. Probe polarity -1 (pure bending only): global M, Q negated, membrane same, bending-state surface stresses swap top/bot. With nonzero transverse shear the full surface tensors do NOT simply swap (mixed-load check, Codex r1: max abs difference 1.0e7 Pa): in-plane terms swap while the transverse-shear tensor terms change sign (`:1713-1715,1807-1813`). So M tensor positive = tension on +**director** side while the tensor-basis normal is the owner normal (opposite of qualified S3, same as e4-pl in sign but with a different basis normal). Reversed numbering handled internally (`internal_order`), same result. | none in the ordinary case (see (2)) | **Yes** (`top_on_plus_director True`, polarity +1 both orders, polarity -1). `top` = `membrane + bending` (`:1728`, `:1801`). | 3 (Hammer; order mapped to external node order) |
| **S3 V2D, h, polarity -1 or `reference_surface_offset != 0`** | as above | adds `director_polarity`, `reference_surface_offset`, `section_origin_offset_from_reference`, `physical_{bottom,top}_offset_from_reference` (probe `metadata_variants`; `:1672-1680`) | as above | as above | offsets present, e.g. offset 0.02 gives top +0.03, bottom -0.07, origin -0.02 (from reference) | Yes | 3 |
| **S3 V2D, g** | 3 families, no surfaces (`PHYSICAL_GLOBAL_RESULTANTS_ONLY`, `:1795-1797`) | all of the above **and** `director_polarity`, offsets (always) | as V2D h | as V2D h | offsets present (`:1672-1680`) | M positive = tension on +director side | 3 |

## Doc claims F1-F6 versus probe

| Claim | Holds? | Observation |
|---|---|---|
| F1 homogeneous branches return 12 global surface keys | Holds for all 13 homogeneous probe branches | legacy `S/elements.py:3572-3617` (doc: 3618-3636, shifted), e4-pl warped `S/e4_pl_element.py:6945-6985`, flat `:7227-7260`, key set `S/recovery.py:1145-1148` (doc: 1146). |
| F2 families: not legacy-h, not e4-pl warped-h, all others | Holds | exact match on all 25 branches. |
| F3 warped generalized shear centre, warped homogeneous native shear centre and surfaces station | Holds | g: membrane/bending station, shear centre; h: surfaces station, native `shear_*` identical to the legacy centre-frame values. Extra: **legacy** warped g (and e4-pl warped with no `reference_normal`) puts the centre-frame shear in a **station-frame** tensor (shear tensor classifies `station`). |
| F3a polarity | Holds, with a sharper reading | S3 global M, Q are in the owner-normal convention (unchanged under polarity), V2D and e4-pl in the director convention (negated). S3 surfaces and V2D surfaces are director convention. So S3 global M disagrees in sign with S3's own surfaces at polarity -1. |
| F3b warped Q4 recovers through `ShellElement.compute_stresses` with per-point frames | **Partly**: holds for homogeneous (and the no-`reference_normal` fallback); for **generalized** the path is `ShellElement._compute_generalized_section_results` (`S/elements.py:3253`), not `compute_stresses` | trace: homogeneous enters `elements.py:3398` and `_local_frame_and_derivatives` 10x (centre + 4 stations, both Q4 and base overrides); generalized enters `elements.py:3253`. `equation7_frame` is only the warpage test (`S/e4_pl_element.py:7025`, and the planar frame) on the warped path. |
| F5 e4-pl reports `physical_director(s)` and `natural_point_coordinates` | **Wrong for warped**: `natural_point_coordinates` only on the planar path (`:7091`); warped reports `physical_director` + `physical_directors (4,3)` and no coordinates. Without `reference_normal` the warped path reports nothing. | |
| F5 V2D reports `frame`, `hammer_points`, barycentric and physical station coordinates | Holds (h and g) | |
| F5 legacy reports none | Holds | |
| F6 V2D reports moment reference and surface offsets | **Wrong for ordinary homogeneous** (polarity +1, offset 0): none of `director_polarity`, `reference_surface_offset`, `section_origin_offset_from_reference`, `physical_{bottom,top}_offset_from_reference` is returned (V2C shortcut `S/e4_pl_s3_v2d_element.py:1605-1611`). Present for generalized, for polarity -1 and for nonzero offset. Qualified S3 returns them always. | |

## Doc facts that turned out wrong or stale (list)

1. F6 (above): homogeneous V2D, ordinary case, has no offset or polarity keys.
2. F5 (above): e4-pl warped has no `natural_point_coordinates`; unqualified warped has no metadata at all.
3. F3b: generalized warped Q4 does not enter `ShellElement.compute_stresses`.
4. Line pointers: F1 legacy `3618-3636` is now `3572-3617`; V2C-line `1777` for V2D global resultants is `1763-1790`; `S/recovery.py:1146` is `1145-1148`. Others (6692/6808 warped, 7123/7160 flat, 5352 S3 polarity, V2D 464) are still right in this checkout.
5. `formulation="e4-pl-s3"` no longer selects the qualified S3 element (V2D now), so any plan that exercises "qualified S3" through `create_shell_element` tests V2D.
6. Station order is not stable across Q4 paths: planar `natural_point_coordinates` are `(-,-),(+,-),(+,+),(-,+)` but warped and legacy Q4 stations are `(-,-),(+,-),(-,+),(+,+)` (probe `q4_station_order.flat_equals_warped False`). Same station index is a different physical point depending on the element's warpage. Direct consumers of station index must use per-path tables or returned/derived positions.
7. Station counts n_p are not 4/3 everywhere: S3 qualified 7, legacy T6 7, legacy Q8 9 (4 reduced), Q4 4, T3/V2D 3.

## Missing outputs O5(a) must add, per branch

All additions only with `return_global=True`, existing keys and values unchanged. Needed set: global family tensors (membrane, bending, shear vector), station physical positions (3 per station), the tensor-basis normal per station per family (3) with the sign convention, and the physical-director convention flag for M and Q.

| Branch | Global family tensors | Station positions | Tensor-basis normals (per family) | Other |
|---|---|---|---|---|
| Legacy 3/4/6/8 homogeneous | **add all 3** (stresses are in `sigma_m`, `moments`, `tau_s`; global form is `R tensor R.T` per point, `S/elements.py:3484-3570`) | **add** (none returned; computable from `compute_shape_functions` at each point) | **add** per station: `R[:,2]`; shear in the centre frame for 3/4-node (mixed with station frame; see note) | director convention flag = "numbered normal, no director" |
| Legacy 3/4/6/8 generalized | have all 3 | **add** | **add** per station; for T3/Q4 state that shear values are centre-frame values placed in the station frame (warped Q4) | same |
| e4-pl planar h and g | have all 3 | **add** (only natural coordinates today) | **add** the one `equation7` normal (= `physical_director`, already returned as centre vector) per family so consumers need not infer | flag M, Q = director convention (frame normal = director) |
| e4-pl warped h | **add all 3** (computed in `_recover_warped_homogeneous_section` from `membrane`, `bending`, `shear`; station frames `physical_frames[index]` are available at `:6845-6860`) | **add** natural coordinates and physical positions | **add**: membrane and bending station (= `physical_directors`), shear centre (= `physical_director`) | |
| e4-pl warped g | have all 3 | **add** | **add** (membrane, bending station; shear centre) | |
| e4-pl warped without `reference_normal` | h: add 3; g: have 3 | add | add (station; legacy shear mixing note) | unauthoritative director |
| Qualified S3 h and g | have all 3 (+surfaces for h) | **add** (no positions, no `frame`; 7 stations) | **add** the owner frame normal (not returned today; one `triangle_frame`) | **add polarity and director**: global M, Q are owner-convention but surfaces are director-convention; state per family |
| S3 V2D h and g | have all 3 | have (`physical_station_coordinates`) | have `frame` (one constant frame, normal = reference normal) | **add director** (= `polarity*reference_normal`, derivable but not returned) and always add `director_polarity` + offsets for homogeneous polarity 1 offset 0 (V2C shortcut returns none) |

## What was not probed

- Legacy 6/8-node reversed numbering (D4 reflection is defined for 3/4 nodes in the probe only); top-side facts for them follow the 3/4-node source (`top = m + b` in the numbered frame).
- Real mixed-material (orthotropic) recovery, Hill yield, `natural_points=` custom stations, nonzero initial stress, nonlinear/corotational recovery frames (current vs reference geometry); those are D2-6 items. The probe uses small linear displacement fields on the reference geometry.
- ANYfem's own recovery wrapper (`_RecoveryIsotropicMaterial`, `S/recovery.py` batching and the `corotational` rotated stress frame at `S/recovery.py:1254,1275`) was not run; only direct element `compute_stresses`.
- V2, V2B, V2C strict-flat 3-node candidates (`e4_pl_s3_v2{,b,c}_element.py`, each with its own `compute_stresses`): not selected by ANYfem policy, not probed (V2D delegates to V2C for the ordinary elastic case, which the probe exercises).
- A Q4 with `warpage` just below/above 1e-10 on real meshes (data-dependent branch choice) was not measured on the barge.

## Review corrections (Codex round 1, 2026-11-19)

* Qualified S3 returns the offset keys always but **no** `director_polarity` key (`e4_pl_s3_element.py:5284-5314`).
* All polarity-reversal statements above come from pure-bending probes. They support the
  top-side rule and global M, Q negation, not general sign rules for local components, surface
  tensors with shear, or the recovered normal versus `polarity * reference_normal`.
* Source-derived, not exercised by a probe: reduced-integration Q8 (n_p 4); negative polarity
  combined with reversed numbering for the variants listed in `probe_capability.py:152,554-558`.
