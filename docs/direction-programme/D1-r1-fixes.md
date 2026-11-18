# D1 review fixes, rounds 1-4 (2026-11-18)

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

## Round 3 (review `D1-review-r3.md` on de84a83, four findings) - stop report

Fixed: finding 3 (a face with no elements now makes the report NOT
consistent; `test_r3_f3_face_without_elements_is_not_consistent`, fails on
de84a83) and finding 4 (module docstring: no curvature guard exists, so the
curved-face text now says what "consistent" means there instead of promising
a "mixed" verdict).

Not fixed - stopped under the three-round rule. Rounds 1-3 each found more of
one kind: the diagnostic's model is not exactly the model of the solve the
owner cares about. Round 3's findings 1 and 2 are that kind:

1. The build normalises the caller's project (legacy section maps adopted
   into assignment/region/sheet/part records) - a solve does the same. The
   project cannot be deep-copied (mappingproxy) and `project_to_dict` itself
   normalises, so isolating the build is not a local fix.
2. Analysis-time imperfections (nonlinear/arc-length `imperfection`,
   capacity eigenmode imperfection) change the reference geometry after the
   build; the project-level check cannot see them.

Contract question for the owner (decide one):

* A. Scope the check to "the model a static solve of the project as it stands
  would build", documented (now in the module docstring "Scope"), and accept
  the same section-assignment normalisation a solve performs; or
* B. Make it strictly read-only: refuse (all unchecked) when the build would
  normalise the project, which needs a read-only "would adopt" predicate in
  `Project` (owner-reserved model code); and/or
* C. Check frames per analysis: take the actual analysis model (after any
  solve-time imperfection) from the analysis entry points, i.e. move the check
  next to result recovery (D2 territory) rather than before the solve.

Recommendation: A for D1 (it is a mesh-order diagnostic; the barge result does
not depend on these cases), C as part of D2 where results carry their frames.

## Round 4 (owner decisions after the stop report)

Owner: "A now, C in D2" and "As always ensure speed and perfomance is
maintained."

* Scope A made explicit: module docstring Scope, `SCOPE_NOTE` in the first
  summary line (the overlay caption), overlay module docstring. The Qt test
  applies `Project.resolve_section_assignments(strict=False)` first (what a
  static solve build does) and then asserts the overlay changes nothing
  (full digest and the section stores); the headless twin test asserts the
  check's normalisation equals a `build_fe_model` run on an identical project.
* `mixed`: faces with no dominant direction are a counted, non-consistent
  state; their normals are still compared. `unchecked` is only for what could
  not be compared.
* Overlay off the GUI thread: its own serial `ResultRequests` worker,
  completions drained on the GUI thread in `poll()`; a mesh change cancels
  (status "cancelled"), a project or document change discards, clear/close
  cancel and retire. The last result is cached per (mesh object, project,
  document hash), so Show after Clear is instant.
* Speed: for exact legacy `ShellElement`s the frames come from the two solver
  functions `compute_stresses` uses to pick them, instead of six recoveries;
  every 25th such element is also probed and one disagreement switches every
  element to the probe (`frame_source` records the route).
* Opt-in: nothing new runs on the mesh or solve path (`git diff e4ec0f8 --
  src/anyfem/solve/build.py src/anyfem/mesh_jobs.py src/anyfem/mesh_session.py`
  is empty; `mesh_orientation` is imported only by the overlay; `poll()` adds
  only a queue drain and identity checks).

| Test | Result on b72e9dd |
|---|---|
| `test_r4_even_split_is_mixed_not_unchecked` | AttributeError: no `mixed` |
| `test_r4_normals_are_still_compared_on_a_mixed_face` | 0 + 0 normals compared, expected 4 |
| `test_r4_check_normalises_exactly_like_a_static_solve_build` | normalisation equal (scope A held already); fails on the missing scope text |
| `test_r4_cancel_stops_the_check` | ImportError: no `OrientationCancelled` |
| `test_r4_background_check_is_cancelled_by_a_mesh_change` (Qt) | check ran on the GUI thread with no cancel event |
| `test_r4_solver_frame_route_equals_the_probe`, `..._switches_everything_to_the_probe` | new route (no old counterpart) |

### Timings (drawing_barge_60m, 4640 legacy Q8/Q6, same machine)

Command (from the worktree, PYTHONPATH as in the brief):
`python docs/direction-programme/scripts/time_check.py` - meshes the barge (0.5 m quadratic
quad_first adaptive) and times `project_orientation_report` three times.
Breakdown from `docs/direction-programme/scripts/profile_check.py`.

| Version | Check time (3 runs) |
|---|---|
| e4ec0f8 (centre frames, no build) | about 1.6 s (earlier measurement, not repeated) |
| b72e9dd | 28.20-30.37 s (single run earlier the same day: 18.2-18.7 s; machine load varies) |
| this round | 6.92-7.48 s |

Breakdown before (18.45 s run): model build 0.31 s, admission recovery 2.04 s,
six-field probe 15.32 s, classification 0.69 s. After: the probe runs on 186
of 4640 elements (verification sample); the rest is the admission recovery
(one `compute_stresses` per element with its own material - kept, it is what
catches material and per-element refusals), the solver-frame reads and the
classification. In the GUI the check runs in the background either way.

Barge after round 4: 402 turned, 0 reversed, 0 ambiguous, 0 inconsistent,
968 mixed on 11 faces, 0 unchecked; deck 169 turned, 144 between the end
houses in the same 3 blocks. Tests: 75 passed (test_mesh_orientation incl.
barge, test_qt_shell_local_axes, test_presentation_workspace,
test_qt_sketch_overlay_lifetime).

## Mesher version of the barge numbers

Rounds 1-4 above were measured with `C:/Github/ANYmesh` at ANYmesher
`0d85b68` (that checkout has uncommitted files). After the rebase onto ANYfem
main `05b13c9` (which pins ANYmesher `432b4ed`), the barge was re-measured with
the pinned, clean checkout `C:/ANYworkspaceWork/anyfem-runtime-main-20261111/owners/ANYmesh`
(`432b4ed696bbb0fbf52bd62e72e4ba98cc008e00`):

| ANYmesher | Turned | Reversed | Ambiguous | Inconsistent | Mixed (faces) | Unchecked | Deck turned / between end houses / blocks |
|---|---|---|---|---|---|---|---|
| 0d85b68 (dirty) | 402 | 0 | 0 | 0 | 968 (11) | 0 | 169 / 144 / 3 |
| 432b4ed (pinned) | 402 | 0 | 0 | 0 | 968 (11) | 0 | 169 / 144 / 3 |

Identical, including every per-face count. Check time with 432b4ed: 7.9 s
(single run). The first D1 measurement (880 turned) and the round-1 to round-3
numbers were all taken with 0d85b68.

## Round 5 (coordinator decision on Codex round 4: two classes)

Branch rebased onto ANYfem main `05b13c9` before this round (round-4 commit
`0d8a12e` is now `77a496f`; `d1292b8` = round 4 plus the mesher record).
All numbers below: ANYmesher `432b4ed` (pinned, clean runtime checkout).

**Class A - the worker never touches the live project** (r4 findings 1, 3, 4, 5).
`show_shell_axes` takes `session.snapshot()` on the GUI thread; the worker
thaws it and runs the check (Scope-A normalisation and `build_fe_model`) on
that detached copy only. Completion and the cache are keyed on (revision id,
document hash, `mesh_content_digest(mesh)`); no document hash means no cache;
a drawn overlay is cleared when the revision changes. `close()` retires the
worker and joins it (`executor.shutdown(wait=True)`). The Qt test compares the
live project and mesh raw (assignment and region records, section maps, mesh
content digest, revision) - no `project_to_dict`, no prior normalisation.
Note: `session.snapshot()` serialises with `project_to_dict`, which folds
direct legacy-map edits on the GUI thread exactly as every commit, save and
existing worker snapshot does; on a committed GUI project that changes nothing
(the raw comparison holds).

**Class B - the fast route only where correct by construction** (r4 finding 2).
`_solver_frame_eligible` now requires the exact class, no generalised section,
and the guard the solver's own `native_recovery_frames` uses: unchanged
`ShellElement`/`Element` namespaces and frame helpers (as captured at solver
import) and no instance attribute shadowing them. Everything else is probed.
The 1-in-25 sample is only an extra cross-check; a disagreement or a probe
failure switches every element to the probe. Scale gate on both routes:
non-finite coordinates, or coordinates more than 1e7 element sizes from the
origin, are unchecked.

1e12 investigation (legacy Q8, `docs/direction-programme/scripts/scale_check.py`,
frames compared with the unshifted element):

| Shift | Solver frame error x / normal | Six-field probe |
|---|---|---|
| 0 | 0 / 1.2e-6 deg | accepted |
| 1e3 | 8.5e-7 / 1.2e-6 deg | accepted |
| 1e6 | 0 / 1.2e-6 deg | accepted |
| 1e9 | 2.2e-5 / 2.1e-5 deg | accepted |
| 1e12 | 3.8e-2 / 3.2e-2 deg | refused (residual 1.0e-3) |

So it is precision, not a frame defect: at 1e12 m the solver's own frame (and
its stresses) lose about 4 digits, and the probe's refusal is right. Outcome:
the scale gate (1e7 element sizes) refuses such elements on either route.

| Test | Result on d1292b8 (= r4 code) |
|---|---|
| `test_r5_worker_never_touches_the_live_project` (Qt; Codex's clearing interleaving) | worker resolved the live project (`touched == [True]`) |
| `test_r5_close_joins_the_worker_thread` (Qt) | worker thread alive after close |
| `test_shell_axes_overlay_counts_caption_clear_and_no_mutation` (raw state, in-place mesh edit defeats the cache) | fails (new helper; old cache keyed on identity) |
| `test_r5_instance_override_is_probed_not_fast_tracked` | override accepted, report consistent |
| `test_r5_coordinates_far_beyond_the_element_scale_are_unchecked` | 1e12-shifted element accepted |
| `test_r5_a_failing_sample_probe_switches_everything_to_the_probe` | stayed on the solver-frame route |

Timings (barge, `docs/direction-programme/scripts/time_check.py`, 3 runs):
6.99 / 7.09 / 7.06 s (round 4: 6.92-7.48 s). `profile_check.py` breakdown
(one run, 7.52 s): build 0.46 s, admission recovery 3.29 s, classification
0.78 s; the rest is the solver-frame reads and the 186 sample probes. Probing
every element instead would take 23.91 s. GUI thread: snapshot 0.145 s, mesh
digest 0.018 s; worker: thaw 0.21 s.

Barge: 402 turned, 0 reversed, 0 ambiguous, 0 inconsistent, 968 mixed on 11
faces, 0 unchecked; deck 169 turned / 144 between the end houses / 3 blocks;
all 4640 elements on the solver-frame route. Tests: 80 passed
(test_mesh_orientation incl. barge, test_qt_shell_local_axes,
test_presentation_workspace, test_qt_sketch_overlay_lifetime).
