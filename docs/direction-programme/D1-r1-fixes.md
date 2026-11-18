# D1 review fixes, rounds 1 and 2 (2026-11-18)

Review: `D1-review-r1.md` (Codex gpt-6.1-sol high on e4ec0f8, nine findings).

## Root cause and the class fix

The first check re-implemented the solver's frames at the element centre. It
now uses what the solver's stress recovery reports, at its recovery points, for
the element the solve builds:

* `anyfem.solve.build._shell_element` and `_admitted_s3_owner_normals` were
  factored out of `_add_shells` without changing it (same calls, same order,
  same refusals); the check calls both.
* `anyfem.mesh_orientation.element_recovery_frames` calls the element's own
  `compute_stresses` with six uniform strain fields (probe material E = 1,
  nu = 0) and reads the output frame at every recovery point from
  `membrane_ab = e_a . E . e_b`; a response that is not a rotated orthonormal
  frame (residual > 1e-6) is a refusal, not a guess.
* Classification: doubled-angle dominant direction per face, 45 +- 10 deg band
  ambiguous, points of one element in different classes or on both normal
  sides = inconsistent, no dominant direction (resultant < 0.2) = unchecked.

## Findings, regression tests, result on e4ec0f8

Tests in `tests/test_mesh_orientation.py` and `tests/test_qt_shell_local_axes.py`.
On e4ec0f8 the new tests fail (19 of 21 headless tests, many on the changed
API); to show the old *behaviour*, each counterexample was also run through the
e4ec0f8 API (`git archive e4ec0f8 src` into a scratch folder first on the path):

| # | Test | e4ec0f8 result |
|---|---|---|
| 1 | `test_r1_f1_warped_q4_reports_the_shell_element_gauss_frames`, `..._higher_order_frames_match_the_legacy_gauss_frames` | centre Equation-7 frame: x 4.95 deg, normal 10.58 deg from the Gauss frames |
| 2 | `test_r1_f2_folded_q8_is_inconsistent_within_the_element` | consistent=True, nothing reported |
| 3 | `test_r1_f3_faceted_s3_reports_the_facet_normal_the_solver_uses` | normal shown (0, .707, .707), solver uses (0, 0, 1) |
| 4 | `test_r1_f4_s3_reference_normal_the_solver_rejects_is_unchecked` | consistent=True, 0 unchecked |
| 5 | `test_r1_f5_s3_without_owner_normals_is_unchecked` | consistent=True, 0 unchecked |
| 6 | `test_r1_f6_turned_elements_found_whatever_the_u_direction[45.0]` (+ ambiguous band, even split) | u at 45 deg: consistent=True, turned=() |
| 7 | `test_r1_f7_face_reference_to_a_missing_element_is_unchecked` | consistent=True, element_count 12 (999 dropped) |
| 8 | `test_r1_f8_overlay_follows_scene_replacement_and_mesh_change` | fails: "arrows must be redrawn after the scene was replaced" |
| 9 | `test_r1_f9_normal_flip_with_x_kept_changes_bending_xx_not_bending_xy` | passes on e4ec0f8 too: it pins the solver fact the corrected D0 text states (the defect was the text) |

Finding 8 also: the Qt no-mutation digest now covers the full project document
(`project_to_dict`, geometry included), the mesh's structural preparation and
hybrid diagnostics.

## Barge numbers (drawing_barge_60m, 0.5 m quadratic quad_first adaptive)

Before: 880 of 4640 turned, 0 reversed, 0 unchecked; deck 144 in 3 blocks.
After: 402 turned, 0 reversed, 0 ambiguous, 0 inconsistent, 968 unchecked;
deck unchanged (169 turned, 144 between the end houses in the same 3 blocks,
0 unchecked); bottom 171, sides 30 and 32 unchanged. The difference is the
eleven faces (3 bulkheads, 8 end-house walls) whose elements split almost
evenly (resultant 0.03-0.12): no dominant direction, so unchecked instead of
"turned against u". Check time 16.2 s (all legacy ShellElement).

## Round 2 (review `D1-review-r2.md` on 23ed254, seven findings)

Class: the probe was not the finished solver model (findings 2-4), and the
face reference still followed the parameterisation locally (finding 1).

* The check now builds the model with `build_fe_model(project, mesh,
  load_case=None, require_loads=False, require_supports=False)` and probes the
  shell elements of `fe_model.mesh` (imperfect coordinates, full model scope);
  each element first runs an ordinary recovery with its own registered
  material. A build refusal makes every element unchecked with the reason.
  The `build.py` refactor of round 1 is no longer needed and was reverted
  (`build.py` is identical to e4ec0f8).
* The in-plane reference is the global axis least aligned with the face's
  normals, projected onto the tangent plane at each centroid.
* `faces` is materialised once; an empty report is not consistent.

| # | Test | 23ed254 result |
|---|---|---|
| 1 | `test_r2_f1_spatially_varying_parameterisation_does_not_hide_turns` | sheared parameterisation: consistent=True, turned=() |
| 2 | `test_r2_f2_frames_include_the_imperfections_the_solve_applies` | 0.1 m plate mode: consistent=True, all normals exactly +z |
| 3 | `test_r2_f3_mixed_shell_scope_the_solver_refuses_is_unchecked` | consistent=True, 0 unchecked |
| 4 | `test_r2_f4_material_the_solver_refuses_is_unchecked` | assigned material never used (probe material only) |
| 5 | `test_r2_f5_generator_faces_argument` | "consistent: 0 elements on 0 faces" |
| 6 | `test_r2_f6_unsupported_connectivity_is_unchecked_not_raised` | raised ValueError (no report) |
| 7 | `test_r1_f9_normal_flip_..._bending_xx_yy_not_bending_xy` | test fix: bending_yy now nonzero (w gains 2 y^2) and asserted |

Barge after round 2: identical to round 1 (402 turned, 968 unchecked, deck
169 / 144 in 3 blocks, 0 reversed); check time 18.7 s.
Not covered by a test: replacing the canvas object itself (the Qt test covers
canvas.clear and Geometry/Mesh view switches; `maintain_shell_axes` also
compares the group's viewer with the current canvas).
