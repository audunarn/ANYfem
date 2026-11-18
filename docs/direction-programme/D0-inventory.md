# Direction programme — D0 inventory and D1 findings (2026-11-18)

Question: which results depend on a direction, how is that direction defined,
and can it change silently between neighbouring elements?

Trigger: on the 60 m drawing barge, "membrane xx" showed grey squares on the
deck where von Mises showed none. Cause: shell local axes follow element node
order, and some elements have local x turned 90 degrees.

Roots: S = ANYfeSolver/src/anyfesolver, F = ANYfem/src/anyfem,
M = ANYmesh/src/anymesher. Pointers are from a read-only survey; rows marked
(checked) were confirmed directly; the others are survey results not yet
re-verified line by line.

## How the axes are defined

| Formulation (ANYfem policy token) | Local x | Local z (normal) | Source |
|---|---|---|---|
| Higher order 6/8-node, legacy 3/4 (`legacy`) | xi tangent, projected, **at each Gauss point** (varies within a curved or folded element) | xi x eta at each Gauss point (node winding) | S/elements.py:1926 `_local_frame_and_derivatives`, used per point by `compute_stresses` S/elements.py:3492 (checked) |
| Qualified Q4 (`e4-pl`, default), planar (warpage <= `planar_tolerance`) | bisector of the diagonals, one frame for the element | from diagonals (winding) | S/e4_pl_element.py:2125 `equation7_frame` (checked) |
| Qualified Q4, warped (no reference normal, the ANYfem build passes none for Q4) | as legacy: xi tangent at each Gauss point | as legacy, per Gauss point | `compute_stresses` delegates to `ShellElement.compute_stresses`, S/e4_pl_element.py:7026-7041 (checked; corners (0,0,0),(2,0,0),(2,1,.6),(0,1,0): x differs 4.8-5.0 deg and the normal 9.3-10.6 deg from Equation 7) |
| S3 V2D (`e4-pl-s3-v2d`, default) | edge node0 to the next node in positive order about the reference normal | the reference normal the build passes: the mesher owner normal (`s3_reference_surface="source"`), or the signed facet normal on the owner side (`"faceted"`, F/solve/build.py `_shell_element`); the element refuses a reference that is not facet-normal | S/e4_pl_s3_v2d_element.py:~690 (checked) |
| Beams | edge direction | section web_direction, else first attached face normal, else least-parallel global axis; `attachment_side`, `rotation_deg` | F/model/project.py:1441 `beam_section_frame` |

The solver's `material_direction` / `material_angle_deg` turn only the material
axes, not the output axes, and ANYfem never sets them (checked).
No ANYfem code reorients elements to a face or sheet; M/boundary_orientation.py
`orient_displacement_boundary` exists but has no ANYfem caller (checked).

## Quantities

| Quantity | Basis | Basis stored with result | Changes with node order? |
|---|---|---|---|
| membrane/bending xx, yy, xy; shear xz, yz; section resultants | element local | `basis="element_local"` (F/io/result_artifact.py:1709, checked); label has no axis text (F/post/fields.py:43) | Yes: 90 deg turn swaps xx/yy. A normal flip with x kept flips e_y: membrane_xy, bending_xx and bending_yy change sign; membrane_xx/yy and bending_xy do not (checked on a legacy Q4, tests/test_mesh_orientation.py `test_r1_f9_...`); by the same rule shear_xz changes sign and shear_yz does not (not tested) |
| top/bottom xx, yy, xy | element local; top = +normal side | as above | Yes: also top/bottom swap with normal |
| global_*_top/bot (`stress_global_*`) | global tensor | `basis="global"` | Tensor no; which surface is "top" yes |
| global resultant tensors | global | not traced | Bending sign via normal |
| patch-recovered stresses | global, side by normal | `basis="global"`, `recovery="patch"` | Side yes; patch fit groups normals within 15 deg (S/recovery.py:393), sign handling not checked |
| displacements, rotations | global | yes | No |
| beam stresses | section local y/z | `basis="material"` (S/quantities.py:920) | With edge direction or face order, not mesh order |
| pressure / wave loads | face normal, per-element sign | per-element sign | Compensated (F/application/wave_surface_pressure.py:133) |
| fatigue shell side | polygon winding of the display cell | no | Yes (F/application/fatigue.py:191) |
| principal-axis glyphs | requires global tensors | passed through | No when fed global tensors |
| envelopes | per component | not traced | Inherits the basis of the component |

Not checked: the solver's ordinary pressure-load sign convention; UI label text
beyond fields.py; whether results record each element's normal.

## D1 measurement: drawing_barge_60m (quad_first, adaptive, 0.5 m, quadratic)

`anyfem.mesh_orientation.project_orientation_report` after the review fixes
(rounds 1 and 2). The solver model is built by `build_fe_model` itself (no
loads or support requirement; imperfections, model scope and materials as in a
solve); each shell element's output frames are read from its own
`compute_stresses` at every recovery point; local x is compared, as a doubled
angle, with the face's dominant direction measured from a fixed global axis
projected onto the face (independent of the face parameterisation), the normal
with the face's majority side. All 4640 elements are legacy `ShellElement`
(4596 Q8, 44 Q6). Check time 18.7 s.

* 402 of 4640 shell elements have local x turned against their face's
  dominant direction; 0 reversed normals; 0 ambiguous; 0 inconsistent within
  an element.
* Main deck (face 1): 169 of 992 turned, including 144 between the end
  houses in three blocks, x 4.98-7.80, 27.20-32.80, 52.20-55.02 m (element
  centres 5.23-7.55, 27.45-32.55, 52.45-54.77 m; the grey squares).
* Bottom 171/992, sides 30/496 and 32/496.
* 968 unchecked: the three transverse bulkheads (56 each, resultant 0.12) and
  eight end-house walls (100 each, resultant 0.03) split almost evenly between
  two layouts, so no dominant direction exists and the check refuses to call
  either half "turned" (`MIN_DOMINANT_RESULTANT = 0.2`).

Change from the first D1 measurement (880 turned, 0 unchecked): the same
deck, bottom and side counts; the 478 elements previously counted as turned on
the eleven evenly split faces (against the face u direction) are now, with
their faces' other elements, unchecked because no majority exists.

So the problem is general on this mesher route, not a deck special case.
Every "xx"/"yy" plot on element-local components is affected wherever such
elements occur.
