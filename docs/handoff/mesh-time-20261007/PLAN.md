# Handoff plan: why the 15 m barge example takes about two minutes to mesh

Date 2026-10-07. Written for a model that did not see the investigation. Status: investigation
finished, **no source change made for it** (only the example work described in section 2 exists,
uncommitted). Read sections 1-4 before touching anything; section 5 is the work, section 6 the rules.

## 1. The question and the answer in one paragraph

The user asked why parallel meshing "seems inefficient" on the large barge example. Answer: the
parallel route cannot apply to a connected hull and still costs time, the mesher repeats its whole
pipeline four times on this model, and a single pass is dominated by two Python loops that no
parallel route touches. All numbers: `Project.generate_mesh(0.25, order="linear")` on
`floating_barge_large()`, compiled mesher, main of every repo, one shared Windows machine.

| Fact | Evidence |
|---|---|
| Default policy `auto` is **slower** than `off`: ~142 s vs ~115 s in one in-process pair; 126-133 s vs 90-111 s over four paired mesh jobs | `stage_times.py`, mesh-job timing |
| Reason: model is **1 component** -> serial fallback *after* `plan_independent_components` ran for **33 s** (23%) | `stage_times.py auto`; cProfile: `claim` recursion, 951k `bounds`/`entity_bounds_many` calls |
| The mesher runs **4 full passes** (`generate_hybrid_mesh_result` calls itself 3 times): structured -> native fallback -> refined junction transition -> conservative (alignment off) | `mesher_passes.py`; `hybrid.py` ~5308/5381/5423 |
| Trigger = **bulkhead stiffeners**, not the rounded corners: 1 pass without them (43-49 s), 4 passes with (82-90 s). Rejection: `quality_v2 element_growth`, 76 pairs, ratio up to 2.1 (limit 1.5), on the X = 5 m bulkhead near z ~ 0.4 and ~ 2.0 beside the openings (0.09 m elements next to 0.3 m ones) | `growth_violations.py` |
| One pass is ~70% two Python loops: ANYfem `prepare_structural_connectivity` 43 s (37%; ANYgeometry `apply_intersections` 36 s, `remap_face_attachments` 34 s) and ANYmesh `StructuralMeshingPipeline._add_attachment_coupling` 40 s (34%, 42,384 calls = 4 passes x ~10.6k) | `stage_times.py off` |
| Element generation itself is a small share | cProfile top-45 shows none |

Pass timeline of the full model (wall, seconds, `off`): preparation ~43, four hybrid passes ~72 in total,
nothing else (0.2 s unaccounted). Before the stiffeners: preparation ~35 + one pass ~14.

## 1b. Checked idea: mesh regions of the connected model individually, connect afterwards

The user's proposal: identify regions of the (connected) model that can be meshed independently, mesh them
separately and connect them afterwards. Checked against one single-pass profile (`one_pass_breakdown.py`,
15 m model with rounded openings, no bulkhead stiffeners so no retry cascade; cProfile, 89 s wall, shares
indicative):

| Stage inside `Project.generate_mesh` | Time | Share | Region-separable? |
|---|---|---|---|
| ANYfem `prepare_structural_connectivity` (ANYgeometry intersections, attachment remap) | 50.2 s | 56% | partly, see below |
| `generate_hybrid_mesh` total | 38.5 s | 43% | |
| - `apply_connectivity` (the "connect afterwards" step) | 27.7 s | 31% | yes, by target face |
| - `prepare_qualified_s3_mesh` | 5.9 s | 7% | not examined |
| - `mapped.generate_mesh` (the faces and beams are meshed here) | 1.4 s | 1.6% | yes, already per face |
| - `_mesh_native_face`, structured layout, seeding, quality, regularity | ~1.8 s | 2% | yes |

Conclusions:

1. **Meshing the regions is not where the time is** (about 3%). Parallel regional *meshing* of this model can gain
   at most a few seconds. The pipeline already has the shape the idea describes: components (faces, members) are
   generated, then `apply_connectivity` couples them with declared attachments/junctions afterwards. That
   "connect afterwards" step plus preparation are ~87% of a pass.
2. **The idea pays where the work is**:
   - *Connect step*: `_add_attachment_coupling` is read-only on the finished mesh; its inputs are (attachment,
     station, target-face mesh). Grouping attachments by target face gives regions whose payload is one face's
     elements/nodes plus the beam stations (small). The only shared state is the running record-id counter
     (`next_record`), which is assigned in sorted attachment order, so results can be computed per region and
     merged sequentially without changing numbering. `apply_connectivity(mesh, components=...)` already accepts a
     component subset; `hybrid.py` never uses it (always `apply_connectivity(mesh)`, lines ~4617 and ~5225).
   - *Retries*: the quality gate is evaluated on the whole mesh and a rejection re-meshes **and reconnects the whole
     model** (the 4-pass cascade, about 40 s on the stiffened barge). With region-level retry only the failing face
     (the X = 5 m bulkhead, ~0.5 s to mesh) would be re-meshed and only the attachments targeting it re-coupled.
     This is the strongest argument for the decomposition and needs no process pool.
   - *Preparation*: `remap_face_attachments` (ANYgeometry) loops attachment x descendant face x path and computes
     candidates purely until the model write at the end of each attachment; `MaterialDomain.from_model` and the
     tolerance (depend only on `face_id`) are recomputed inside the loop for every attachment. Region/attachment
     decomposition is possible here too, but this is ANYgeometry (hand over only).
3. **Order matters (Amdahl).** Threads cannot help (GIL-bound Python loops); processes need mesh transport (a
   `Mesh` does not pickle; the codec dict or per-face payloads do) and a spawn cost of 1-3 s per cold pool. The
   serial hot spots are 10-100x slower than necessary (per-call face-point rebuild, per-point Python BVH inverse
   map, per-call `lstsq`), so cache/vectorise first, measure, and only then consider regional process
   parallelism for what is left.
4. Not established: how large the saving of region-level retry is exactly (estimate from pass timings:
   3 extra passes x (connect ~10-12 s + s3 ~3 s + gate ~2 s) ~ 40 s), whether `prepare_qualified_s3_mesh` also
   separates by region, and whether a face-local fallback is acceptable to the ANYmesh quality contract (the gate
   currently speaks about the whole mesh). These are questions for package C below.

## 2. Current state of the checkout (preserve it)

`C:\Github\ANYfem` is on `main` at `4097f97` with **uncommitted work** from several sessions. The
parts relevant here, all mine, all uncommitted:

- `src/anyfem/application/examples.py` - `floating_barge_section(...)` generalised
  (`length, bulkheads, girders, openings, opening_bar, bulkhead_stiffener, stress_scale, target_size, name`),
  `floating_barge_large()`, `BulkheadOpening(y, z, width, height, radius)`, constants `BARGE_LARGE_*`.
- `src/anyfem/ui/qt/app.py` - Examples menu entry + `open_barge_example("large")`.
- `tests/test_barge_example.py` (9 tests), `tests/test_qt_examples.py`, `CHANGELOG.md`, `README.md`.
- The 5 m example is unchanged by all of it (`floating_barge_section()` defaults).

Do not revert, reformat or commit other people's uncommitted files (`git status` lists ~23 modified files).
The sibling repos are on `main` and clean except unrelated edits (ANYsolver `docs/QUALITY_CONTROL.md`,
ANYloads, ANYsection, ANYmaterial). ANYmesh main is b981487, ANYgeometry main 660430a.

Relevant calibration fact: the example's end moment is calibrated so the peak von Mises is 200.000 MPa
**on this exact mesh** (`BARGE_LARGE_STRESS_SCALE`, peak 186.869 MPa at the 5 m scale, a side girder at
X = 12 m). Any change that alters the mesh (task C) requires recalibration with `solve_and_calibrate.py`.

## 3. Environment and measurement rules (these cost time to learn)

1. **Python paths.** Use the ANYsolver venv, `C:/Github/ANYsolver/.venv/Scripts/python.exe` (3.13), and a
   *Windows-style* `PYTHONPATH`, entries separated by `;`:
   `C:/Github/ANYfem/src;C:/Github/ANYgeometry/src;C:/Github/ANYmesh/src;C:/Github/ANYsolver/src;C:/Github/ANYloads/src;C:/Github/ANYsection/src;C:/Github/ANYmaterial/src;C:/Github/ANYfileIO/src`.
   Bash-style `/c/...` entries are **silently ignored** and the venv falls back to its installed copies
   (mesher 0.5.1 with no compiled extension, 4-5x slower, different code).
2. **Prove the setup first:** run `native_check.py`. It must print `anymesher` and `anygeometry` from
   `C:\Github\...\src` and `native True v2 True quality True tri True`.
3. **Qt tests** use `C:/Python/Python314/python.exe` with `ANYFEM_RUN_QT_TESTS=1 QT_QPA_PLATFORM=offscreen`
   and `C:/Github/ANYfem/tests` added to `PYTHONPATH`. Non-Qt tests: venv python, `-m pytest -p no:cacheprovider`.
4. **The machine is shared and timings drift ~20%** (115 s vs 90 s for the same `off` run on different
   minutes). Compare only paired runs in alternating order, >= 3 repeats, report the median and the spread.
   Never time while your own other heavy jobs run. Do not use cProfile for shares of call-heavy code (it
   inflated this run by 1.5-1.8x and over-weights tiny calls); use the wall-clock wrappers in
   `stage_times.py` and cProfile only to find candidates.
5. Run `mesher_passes.py` variants to count passes: `basic5`, `large_plain`, `large_rect`, `large_arcs`,
   `large_stiff`, `large_full`. A fix for task C must bring `large_stiff`/`large_full` to 1 pass or prove why not.
6. Resource policy (`C:\Github\AGENTS.md`): Normal-token mode is active; run your own tests/builds, keep processes
   bounded, no overlapping writes, clean up.

## 4. How the code is laid out (verified by reading source)

- `Project.generate_mesh` (`ANYfem/src/anyfem/model/project.py:1927`) -> ANYfem `prepare_structural_connectivity`
  (`structural_preparation.py:233`, calls ANYgeometry `apply_intersections`) -> routes through
  `mesh_execution.resolve_route` / `run_parallel_hybrid_mesh` (`mesh_execution.py:231/325`) when the policy
  requests parallel (default `auto`; `MeshExecutionPolicy.off()` for serial).
- Parallel route: `ANYmesh/src/anymesher/component_parallel.py` `generate_hybrid_mesh_result_parallel` (:497) ->
  ANYgeometry `plan_independent_components` (`component_partition.py:123`, `claim` at :228) -> if not
  >= 2 independent components -> `_serial` (:335) = `generate_hybrid_mesh_result`.
- Serial pipeline: `ANYmesh/src/anymesher/hybrid.py` `generate_hybrid_mesh_result` (:4222), recursion sites at
  ~:5308 (native fallback after `structured quality rejected`), ~:5381 (`_refine_declared_junction_transition=True`),
  ~:5423 (`_evaluate_declared_junction_alignment=False`). Quality gate: `_structured_quality_report`;
  growth limit 1.5 (`growth_limit`, tolerance 0.01). The other two recursion sites (~:4478, ~:4565) are quad-first only.
- Connectivity: `ANYmesh/src/anymesher/structural_pipeline.py` `StructuralMeshingPipeline.apply_connectivity` (:964),
  `_add_attachment_coupling` (:649): per call it (face attachment branch) builds `face_points` from **all** elements
  of the target face to get an extent, then `bvh.locate(...)` (`mesh_bvh.py:446` `locate` -> `locate_all` :408 ->
  `inverse_interpolate` :152, Python, ~180k calls).
- ANYgeometry hot loop: `attachment_remapping.py:88` `remap_face_attachments` -> `material_arrangement.py:545`
  `_clip_intervals` -> `arrangement_geometry.py:350` `curve_junctions` -> `line_curve_junctions` (:242) ->
  `point_parameters` (:172; ~700k `numpy.linalg.lstsq`, 471k `cross`, 4.4M `norm`).

## 5. Work packages (independent; do in this order; stop at each gate)

Each package: owner repo, goal, how, acceptance. Unless stated, acceptance also needs: existing tests of the
touched repo pass, and the 5 m example plus `test_barge_example.py` pass unchanged.

### A. Baseline and living note (ANYfem docs only, ~15 min)
Run `native_check.py`, then `stage_times.py off` and `auto` three times each in alternating order, and
`mesher_passes.py large_full large_arcs`. Record medians and the exact command lines in a short living note in this
folder (`RESULTS.md`). This is the reference for every later claim. No source change.

### B. Planner overhead on connected models (the 33 s)
Owner decision needed first (see gate G1): the planner lives in ANYgeometry (`plan_independent_components`), the
route decision in ANYfem (`resolve_route`) and ANYmesh (`component_parallel`).
Candidate designs, cheapest first:
1. **ANYmesh/ANYfem pre-check**: before the geometric planner, prove connectivity from declared structure only
   (every member has a `MEMBER_ON_FACE`/`VERTEX_ON_FACE` attachment to a face that is in the same face-sharing
   component; union-find on shared edges and attachments). If the declared structure is one component, return
   "1 component" without the 951k-bounds claim loop. Must be certified-safe: it may only skip the planner when
   connectedness is *proven*; any doubt runs the planner as today.
2. Make `claim` cheaper (cache entity bounds, batch `entity_bounds_many`) - ANYgeometry, user's area: propose only.
3. Lower-level: let ANYfem `auto` skip the parallel route when the project has one part and attachments connect it.
Acceptance: barge `auto` <= `off` + 2 s (median of >= 3 paired runs); route diagnostics still say "1 component(s)";
models with >= 2 disjoint components still take the parallel route with identical meshes (existing
`ANYmesh/tests/test_component_parallel.py`, `ANYfem/tests/test_mesh_execution_policy.py`, `test_mesh_execution_submission.py`, `test_mesh_parallel_integration.py`); mesh bit-identical
(`mesh_semantic_hash` equal between `off` and `auto`).

### C. The four-pass cascade (largest saving, about 40 s, needs thought)
(See section 1b: the cheapest structural fix is **region-level retry**, not a parallel pool.)
Question: why does the stiffened bulkhead fail `element_growth`, and is the cascade worth running?
1. Reproduce with `growth_violations.py`; for the first rejected report map `growth_violation_pairs` to geometry
   (it already prints face, position, extents). Questions to answer with evidence: which seeding/size field
   produces 0.09 m elements beside 0.3 m ones at the bulkhead boundary below/above the openings? Is it the
   imprint of the 6 vertical stiffener lines (every 0.7 m grid line outside the openings) on the bulkhead face
   plus the opening loops? Does it disappear if stiffeners stop short, or if the openings are shifted 0.1 m?
   (Use `floating_barge_section(..., bulkhead_stiffener=..., openings=...)` variants; the stiffener ends share
   vertices with the deck/bottom stiffeners by design.)
2. Competing explanations to discriminate: (a) mesher grading flaw at imprinted lines (fix in ANYmesh seeding);
   (b) legitimately too-tight gate for this geometry (do **not** relax the gate to make it pass: scientific
   gates and policy are reserved, see section 6); (c) example geometry makes an unavoidable sliver
   (change the example, e.g. stiffener spacing/positions, with the same engineering intent).
3. Evaluate **region-level retry** (user's regional idea): when `_structured_quality_report` rejects, identify the
   faces holding the violating pairs (`growth_violation_pairs` -> `elements_of_face`; here the X = 5 m / 10 m
   bulkheads), re-mesh only those faces with the fallback strategy, and re-run `apply_connectivity(mesh,
   components=<affected keys>)` for the attachments/junctions that touch them (the parameter exists; hybrid does not
   use it). Questions the worker must answer from source before coding: does seeding/conformity across shared edges
   force neighbouring faces to follow (the shared-edge divisions are fixed globally by `solve_seeding`, so a
   fallback face must keep its boundary divisions)? are record/element ids and the mesh hash stable? does the
   quality gate also need a *whole-mesh* check after the local retry (growth across the interface)? Prototype
   behind a flag in an ANYmesh worktree, compare meshes to the current conservative pass for identity or document
   the differences.
4. Independent of the cause, measure what the retries buy: the final accepted mesh is the conservative pass.
   If the first three passes never produce an accepted result for this class of model, an early, *certified*
   shortcut is a mesher design question: raise it, do not implement it unilaterally.
Acceptance: either `large_stiff`/`large_full` meshes in one pass with `element_growth` accepted (document the cause),
or a written finding with evidence why not and the cheapest compliant alternative. If the mesh changes: redo
calibration (section 2), update `BARGE_LARGE_*` constants, `CHANGELOG.md`, `README.md`, the memory note, and rerun
`test_barge_example.py`, `test_qt_examples.py`, `test_connections.py`, `test_load_series.py`, `test_physical_beams.py`.

### D. ANYmesh `_add_attachment_coupling` (about 40 s over four passes, ~10 s per pass)
Owner: ANYmesh (main is clean; work on a branch/worktree there if the owner prefers).
1. Cache per target face the `extent` / `base_tolerance` and `allowed` element tuple (they are recomputed from all
   face elements on every call; constant per face for a given mesh).
2. Then batch the point location: group the attachment stations per target face (these groups are also the regions of
   section 1b: a later process-parallel connect would ship one face + its stations per task and merge by sorted
   attachment order), vectorise the BVH `locate` +
   `inverse_interpolate` for linear quads/tris; keep the scalar path for curved/quadratic and as fallback.
Acceptance: numbering-exact identical mesh (compare node coordinate arrays and `mesh_semantic_hash`),
`apply_connectivity` wall time reduced (report median before/after from `stage_times.py`), ANYmesh test suite
unchanged (known pre-existing failures: see the ANYmesh memory note; do not "fix" them here).

### E. ANYgeometry junction hot loop (34 s per run) - HAND OVER ONLY
`remap_face_attachments` -> `_clip_intervals` -> `curve_junctions` -> `point_parameters` (per-call `lstsq`,
`cross`, `norm`). The user is actively working on ANYgeometry intersections. Do **not** edit ANYgeometry. Write a
short note for the user with the call counts above and the hot path; suggest batching `point_parameters` for
straight edges (closed-form projection instead of `lstsq`). The user schedules it.

### F. Re-verify the example after any change that touches the mesh
`solve_and_calibrate.py` must print peak von Mises 200.000 MPa (to ~1e-9 relative) on a side girder, element 14231
today. If it does not, update `BARGE_LARGE_STRESS_SCALE` the same way as before (new constant = old scale x 200 /
new peak, expressed from `BARGE_LINEAR_STRESS_SCALE`), then rerun the tests.

## 6. Rules for the worker

- Preserve other people's uncommitted work in `ANYfem`; do not commit, push, switch branches, reset or discard
  anything unless the user says so. Cross-repo edits need the owner/user's explicit approval (ANYgeometry:
  never). Reads are free.
- Keep scientific acceptance intact: do not relax `quality_v2` limits, growth limit, tolerances or certification
  modes to make a case pass. A fix may change meshes only with evidence and a recalibration.
- Report measured numbers only (median and spread of paired runs, exact commands); say what was not measured.
- A helper alone is not integration: show the barge path (`Project.generate_mesh` and one mesh job through
  `MeshTaskManager(executor="process")`) uses it.
- Summarise per package: outcome, changed files, checks actually run with results, open questions.

## 7. Gates (ask the user before proceeding)

- **G1** before B: which layer owns the planner shortcut (ANYfem route vs ANYmesh vs ANYgeometry).
- **G2** before C if the cheapest fix is changing the example geometry or the mesher's cascade policy.
- **G4** before building any process pool for regional connect/meshing: is the saving left after A-D worth the
  transport and lifecycle complexity (measure first; section 1b point 3)?
- **G3** before any ANYmesh edit: confirm branch/worktree convention for that repo.

## 8. Files in this folder

`stage_times.py <off|auto>` wall-clock timing of preparation, intersections, connectivity, coupling, hybrid passes,
planner. `mesher_passes.py <variant>` pass count and rejection messages. `growth_violations.py` the first rejected
quality report with violating pairs mapped to faces and positions. `solve_and_calibrate.py` mesh + linear static
solve + peak stresses by group (confirms the 200 MPa calibration). `native_check.py` proves the environment. `one_pass_breakdown.py <stats-file>` profiles one pass (the no-cascade
variant) and prints the direct callees of `generate_hybrid_mesh_result` (section 1b table).
All print to stdout, need only the `PYTHONPATH` of section 3, and change no project files.
