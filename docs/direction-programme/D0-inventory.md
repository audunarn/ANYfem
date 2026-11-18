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
| Higher order 6/8-node, legacy 3/4 (`legacy`) | xi tangent, projected | xi x eta (node winding) | S/elements.py:1926 (checked) |
| Qualified Q4 (`e4-pl`, default) | bisector of the diagonals | from diagonals (winding) | S/e4_pl_element.py:2125 `equation7_frame` (checked) |
| S3 V2D (`e4-pl-s3-v2d`, default) | edge node0 to the next node in positive order | mesher reference (owner) normal | S/e4_pl_s3_v2d_element.py:~690 (checked) |
| Beams | edge direction | section web_direction, else first attached face normal, else least-parallel global axis; `attachment_side`, `rotation_deg` | F/model/project.py:1441 `beam_section_frame` |

The solver's `material_direction` / `material_angle_deg` turn only the material
axes, not the output axes, and ANYfem never sets them (checked).
No ANYfem code reorients elements to a face or sheet; M/boundary_orientation.py
`orient_displacement_boundary` exists but has no ANYfem caller (checked).

## Quantities

| Quantity | Basis | Basis stored with result | Changes with node order? |
|---|---|---|---|
| membrane/bending xx, yy, xy; shear xz, yz; section resultants | element local | `basis="element_local"` (F/io/result_artifact.py:1709, checked); label has no axis text (F/post/fields.py:43) | Yes: 90 deg turn swaps xx/yy; normal flip swaps sign of bending and xy |
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

`anyfem.mesh_orientation.project_orientation_report`, local x compared with
the face u direction (folded to 0-90 deg), normal with du x dv:

* 880 of 4640 shell elements have local x turned against their face's majority.
* 0 elements have a reversed normal; 0 unchecked.
* Main deck (face 1): 169 of 992 turned, including 144 between the end
  houses in three blocks, x 4.98-7.80, 27.20-32.80, 52.20-55.02 m (the grey
  squares).
* Bottom 171/992, sides 30/496 and 32/496, transverse bulkheads 26/56 each
  (two oblique), end-house walls about 50/100 each (mixed layouts).

So the problem is general on this mesher route, not a deck special case.
Every "xx"/"yy" plot on element-local components is affected wherever such
elements occur.
