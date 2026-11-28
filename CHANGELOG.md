# Changelog

## Unreleased

- Trimmed cylinder and cone faces are drawn from their trim (display tessellation N5). A face on an exact Cylinder or
  Cone support, without an explicit parameterization and with corners, used to be drawn as its support's whole
  parameter box; the floating tripod's skins (full-turn supports with a 7 m overrun) therefore stuck out past their
  ends. Such a face is now drawn from ANYgeometry's certified display mesh
  (`anygeometry.display_tessellation.face_display_mesh`); a typed refusal keeps the old route.
  Contract B (owner): the old route stays only for a face owned by a feature that writes its support as the face's box
  - any `generator.*` feature, `geometry.extrude` or `geometry.sketch.extrude` - AND structurally a box (four sides
  alternating straight and arc, corners (0, 1, 2, 3), no holes); decided without any numeric tolerance.
  One display angle of 22.5 degrees for the full and the interaction scene (owner). Where a skin meets a planar
  neighbour whose old route samples the same arc more finely, the drawn chords can differ by up to about 1.9 % of the
  radius (r (1 - cos 11.25 deg)).
  Changed results: the tripod's 18 skins (36-, 48- and 60-web variants); every face of the barge examples, the saved
  Drawing barge 60 m / Floating barge 15 m / Barge5Fatigue projects, generated cylinders and command-made extrusions is
  drawn bit-identically. A face made by calling `GeometryModel.extrude` directly (scripts) records no feature, so it has
  no provenance and is drawn from its trim: a quarter-turn arc extrusion goes from 64 quads to 8 triangles
  (tests/test_scene.py::test_curved_geometry_keeps_display_tessellation, count changed, setup unchanged).
  The skins are tessellated once per geometry refresh and shared (bound to geometry object, revision and angle) by the
  full and interaction scenes, the wet-surface overlay and result playback; a trimmed wet face's outline is its
  boundary, without the cross.
- Pins ANYgeometry 3f1db90 (ANYgeometry #55, #56, #59: display tessellation N1-N3; #57 cache census; #52 thread
  ident reuse; #53/#60/#61 release tooling). No intended result change for meshing or analysis.

- Pins ANYmesh b16ac98 (ANYmesh #24, #25, #26, #27), all stated as results unchanged: value-safe floating-point build
  options for the native extension (#24); a native port of the finite-hull triangulation fallback (#25); a
  ghost-vertex Delaunay seed that completes most incomplete super-triangle seeds before that fallback runs (#27); CI
  only (#26). Measured on the bench PC against ANYmesh a3458b0 (which already has #24, #26 and #27; ANYgeometry
  5f68177): the floating tripod example meshes 14.0 % faster (729.6 -> 627.7 s) and the N2c owner model 15.9 % faster
  (973.4 -> 818.9 s), with identical mesh digests.

- Pins ANYmesh e889e62 (ANYmesh #23): quad-first planar seeding's Python finite-hull triangulation fallback is faster,
  with results unchanged. Measured on the bench PC, the floating tripod example meshes 9.4 % faster (999.9 -> 906.4 s)
  and the N2c owner model 9.5 % faster (1260.1 -> 1140.9 s), with identical meshes.

- The bundled barge fatigue example is rebuilt once more so its mesh record carries the beam offsets it was generated
  with (#43). The example rebuilt in #41 had none, so its mesh record went stale after the first model edit. Now its
  record stays current (state "completed", not stale) after an edit that changes neither a mesh input nor a beam
  offset, such as adding an unused section. An edit that changes an offset or another mesh input still makes it
  stale. The workbench, as for any generated mesh, drops the loaded mesh after a model edit and asks for a mesh
  before the next solve. Undoing the edit restores it. The example generator records no regional baseline, so
  remeshing this bundle after an edit is Generate mesh (a full rebuild), not Update mesh. Results are
  unchanged from #41: same mesh, same saved fields and the same four governing damages.

- Pins ANYgeometry 5f68177 (ANYgeometry #51): the intersection arrangement no longer splits a transition skin/web contact
  curve where no other path meets it, so the floating tripod's 36- and 60-web variants no longer get a short artificial
  segment at the upper mitre of their transition web roots.
  The bundled 48-web tripod example meshes differently because its transition web roots are no longer split either:
  Q4 83,755 (unchanged), T3 2,535 to 2,553, qualified S3 still admitted.
- Saved shell surface directors are complete on mixed triangle/quad meshes (owner decision A, 2026-11-25). The
  solver reports `physical_director` for Q4 shells but not for the S3 triangle, so the result writer saved a partial
  `shell_surface_directors` table and ANYfatigue refused it ("the saved shell surface directors are incomplete"):
  fatigue on any mixed mesh failed. The writer now fills each shell without a solver director from its built element,
  `director_polarity * reference_normal` (the S3 element's own "top" side). Linear static, linear batch and transient
  results saved with requested global shell surface stresses now carry the table, a single static result included
  (before: batch and transient only, and only when some shell had a solver director); committed nonlinear histories
  do not, and ANYfatigue does not admit those. A shell with neither a solver director nor a valid reference
  normal is left out, and the table is written even when empty, so ANYfatigue refuses instead of guessing the side
  from the corner order. Changed behaviour: fatigue on legacy shell formulations (`legacy` Q4, `legacy-s3`), which
  used the corner order before, is now refused with "the saved shell surface directors are incomplete".
- The bundled barge fatigue example is regenerated with the foot-on-plate-surface offsets and the live 41.382 MN m
  couple, so it opens without stale marks. Same mesh size (6995 nodes, 8147 elements), same governing elements and
  frames. Governing Miner damage for the 25-year illustration, old to new: Port deck toe 75.406 to 76.307 (+1.2 %),
  Starboard deck toe 96.534 to 97.626 (+1.1 %), Port bulkhead toe 0.25469 to 0.30187 (+18.5 %), Starboard bulkhead toe
  0.0052119 to 0.0075067 (+44 %; small damages near the S-N knee magnify the stress-range change, Port bulkhead toe
  range 28.95 to 29.95 MPa, +3.4 %). The saved directors equal the corner-order normals on this mesh (none flipped), so
  these changes come from the model, not the directors. Maximum displacement -0.31 %. `examples/build_barge_fatigue.py`
  runs again: it passed a keyword `Project.generate_mesh` does not take, and it now resolves its output folder.

- Automatic stiffener offset: the profile foot now stands on the attached plate's surface, not on its mid-surface
  (owner decision 2026-11-24). With `offset_mode="automatic"` the neutral-axis offset is the profile centroid plus half
  the plate's thickness along the plate normal at the line, on the stiffener side. The plate is named by topology or a
  declaration only: a face that uses the line as an edge (the face that also sets the default web direction), else a
  `member_on_face` declaration with exact evidence, connect intent and the whole member range (lowest face ID if
  several). A free, undeclared line, or a plate without a section, gets nothing added; declare the attachment to get
  the surface offset. The side follows the web; a web in the plate's plane takes Front (+normal) or Back (-normal);
  with `rotation_deg` the half thickness still goes along the plate normal. A plate normal that cannot be evaluated at
  the line raises an error naming a manual or centerline offset; today that is a NURBS plate reached by a section-trim
  curve or by a declared interior line (ANYgeometry cannot yet locate a point on a NURBS face), where such models need
  a manual offset until ANYgeometry supports it. Manual and centerline offsets are unchanged. This changes results of every model
  with automatic offsets once its mesh is generated again, including saved projects and the examples; there is no switch
  for the old behaviour.
  Example: a T 250x8 + 100x12 on a 10 mm plate moves from 0.17413 m to 0.17913 m and a 5 m strip deflects 3.8 % less,
  now within 0.03 % of transformed-section theory. The 5 m barge example's computed end couple falls 0.18 %
  (41.456941 to 41.382418 MN m), and the barge fatigue example follows it: its reference couple and the magnitude of every
  nonzero end load fall 0.18 % (zero steps stay zero). A fatigue project rebuilt from the example generator therefore has 0.18 % lower nominal stress ranges from the load
  (about 0.54 % less damage at S-N slope 3, 0.90 % at slope 5), on top of the offset change itself; the bundled
  example project keeps its saved loads, mesh and results (41.457 MN m, mid-surface offsets); regenerating only its
  mesh applies the new offsets but keeps its saved 41.457 MN m loads.
  **Regenerate saved meshes.** A mesh carries the offsets it was generated with, and its model hash does not change
  with this rule, so a mesh saved before this change is still shown as current and solves with mid-surface offsets
  until it is regenerated. This applies to every saved project with automatic-offset stiffeners on a plate,
  including the bundled barge fatigue example. Detecting such meshes automatically is a separate follow-up.

- Meshes saved before the foot-on-plate-surface offset rule open as stale (owner decision B, 2026-11-25). The project
  file header now names the rule (`"beam_offset_rule": "foot_on_plate_surface"`), written only when the project defines
  a beam section, so documents without beams keep their exact bytes. A document that defines a beam section and does
  not name this rule opens, in the workbench, a headless `ProjectMeshSession`, `load_project` or a submodel parent, with
  every saved generated mesh marked stale: it is not adopted, not reused by Update mesh (which refuses and asks for a
  full rebuild), not admitted by `build_fe_model`, and a submodel source or run from it is refused or reported stale.
  Generating the mesh again clears it; saving without regenerating keeps the stale marks. Results solved on such a
  mesh normally show as stale in the workbench through its existing job check, which is not part of this guarantee.
  Imported meshes are unchanged.
  Limits (owner decisions B and B1): the check is by document, not by offset values, so it also marks meshes whose
  offsets did not change (for example explicit-only offsets, or a document saved by ANYfem between the offset change
  and this one). It follows the document link: the active published mesh of a numbered document and a mesh read from
  its saved artifact record. Not checked: identifying an untagged mesh copy (a decoded mesh dictionary, a worker
  transport, a read by bare URI string) by its content, which is refused only when its numbered document's active
  published mesh is stale; a direct call of a regional-update backend with a baseline and mesh supplied by the caller, and
  a hand-assembled `BuiltModel`. The workbench does not adopt a stale mesh, so it never hands one to a worker. Legacy
  pressure recovery on open still reads a stale mesh's shell cells, which do not depend on the beam offset rule.
  Opening a document costs one header check and one pass over its mesh records. The
  bundled barge fatigue example now opens with its mesh and results stale (they were made with mid-surface offsets);
  its fatigue assessment still opens from the saved result.

- A quad-first face refusal now names the working face, a location near the failing cell or lost segments, the
  source face(s) through the preparation lineage and their builder groups, for example
  `[quad-first face 794 near (-22.54, 29.57, -16); source face 302; groups: Arm 2 - heave plate]` (rendered at
  ANYfem's outer meshing boundaries only; `source_face_ids` and `builder_groups` on the error; groups read only at the
  revision the lineage came from). Pins ANYmesh bfad0c2, whose exact `orient2d` fallback lets the tripod's 0.7 m variant
  mesh (qualified S3 admitted); ordinary meshes are unchanged.

- Automatic meshing of a linear model with a curved face (any support other than `Plane`, including a geometrically flat
  `CoonsSurface`) reruns the request once as `quad_first` with the model's stored seam policy, but only when Automatic
  refuses with ANYmesh's exact global-seed element-limit refusal (`StructuredElementLimitExceeded`, same number and text).
  Everything that meshes today is unchanged; planar and quadratic models, explicit strategies and
  `MeshAutomationOptions(strict_method=True)` keep the refusal. The rerun takes quad_first's own layout default and only
  the remaining automation time budget, and is recorded in `hybrid_diagnostics['automatic_fallback']`. Pins ANYmesh
  fd0ea76.

- A model can select the owner's intersection seam policy itself: `NativeMeshSettings` parameter `seam_policy`
  (`baseline` or `ring_split`; `ring_split` is an attempt per face, the owner may decline an individual ring seam, and an
  explicit `ring_split` the owner resolves to another policy, for example under decision tracing, is refused once when
  each mesh or update request starts; a budget or diagnostic context that changes in the middle of a request is not
  checked, and ANYmesh's read-only overlap audit places no seams),
  carried to structural preparation on every route (in-process, thread-route child),
  the prepared-model cache and the mesh identity, and saved with the project. No environment variable is used; models
  without it, and old files, are unchanged. The floating tripod example selects `ring_split`, quad-first, 0.5 m, linear.

- Help → Loaded packages… lists the ANY* and key third-party packages loaded in the session with
  version, version source, the import path actually used (and its resolved target through
  `.ecosystem` junctions) and, for ANY* packages, the checkout HEAD commit. Version: a source checkout's static
  `pyproject.toml` version, else installed metadata that owns the loaded copy, else the module's
  `__version__`, else "unknown"; disagreeing sources are flagged (⚠). Collected on a worker thread
  when the dialog opens (GUI thread ~5-8 ms); nothing is imported. Copy to clipboard included.
  Installed metadata counts only for standard layouts (a wheel RECORD listing the loaded file, or an
  editable install whose absolute file: URL is the loaded copy's own project root); symlinked/junctioned
  or case-aliased paths, egg-info and zip-backed metadata,
  malformed RECORD rows (including non-canonical hashes), RECORD file names containing line breaks
  and nested projects are out of scope and shown as not owned.

- Menus: File now holds Import, Export and Examples submenus; the import, export, plate-drawing and
  example actions moved there unchanged (names, shortcuts, behaviour). The top-level Examples menu is gone.

- Use the ANYfeSolver 0.5 package for structural analyses.

- SAT import: two optional reader tolerances (vertex relocation, approximate curve), empty/off by default, recorded on the imported part; a refusal naming one points to its field. Requires ANYfileio 0.3.4.

- License: ANYfem source code moves to GNU GPL version 3 only (`GPL-3.0-only`)
  on 2026-10-27. Previously published releases retain their original licenses;
  narrative documentation remains CC BY 4.0 and dependencies retain their own terms.

- Larger barge example with detail. `floating_barge_large()` (Examples menu: "Floating barge
  section — 15 m with bulkhead openings…") is the barge at three times the length: 15 m, girders
  every 4 m (X = 0, 4, 8, 12), two transverse bulkheads (X = 5 and 10 m), each with three
  1.4 m × 1.1 m openings with 250 mm corner radii trimmed into the plate (on the 700 mm stiffener
  grid: Y = -3.4, 0.1, 3.6 m), a flat bar FB 120×10 along every opening edge (48 beams: straight
  sides and corner arcs, attached to their bulkhead like the stiffeners, standing off along +X) and
  vertical T150×6/75×8 stiffeners on every grid line of the bulkheads between the openings (6 per
  bulkhead, meeting the deck and bottom stiffeners). It is built by `floating_barge_section(...,
  length=, bulkheads=, girders=, openings=, opening_bar=, bulkhead_stiffener=, stress_scale=,
  target_size=)`, whose defaults still build the 5 m example unchanged. Its default mesh target is
  250 mm: at 700 mm the mesher picks 2 divisions per stiffener bay on this longer model (3 on the
  5 m one), so 250 mm keeps the 0.23 m elements (12 100 nodes, 8 916 shells, 3 112 beams; about
  2.1 min to mesh with the compiled mesher (2.4 min on the pure-Python fallback) and 40 s to solve). The end moment is calibrated to a 200 MPa peak (a side girder
  at X = 12 m) on that mesh; the opening flat bars peak near 158 MPa (at the rounded corners, where
  they follow the plate's edge strain) and the bulkhead plates near 50 MPa.

- Fast multi-load solves. `solve_linear_static_many` (Batch linear static) no longer
  solves every step of a long time history: the dominant step-to-step patterns of the steps'
  nodal forces (pressure per element lumped to its nodes, plus nodal loads) are found by singular
  value decomposition, one real load case per pattern is solved (one factorization, all solver
  guards kept) and every step is recombined from them. Accuracy is judged on displacements: two
  real steps are solved in full as a check, and the next tier of patterns is added, reusing what
  is solved, until they agree to max(10 * load_tolerance, 1e-8) of the largest step. `load_tolerance`
  (default 1e-6, `None` or 0 disables) is the first tier's cut-off. Few cases, gravity, follower
  pressure, prescribed non-zero motion, or compression that would not pay solve every case.
  `batch.info["load_compression"]` records what was done. On the 600-step barge seaway: 46 patterns
  in two calls; 185 s against 1933 s for all steps on ANYsolver 1a0ae901, 42 s against 142 s with
  its load-vector guards amortised per lease. Element centroids and
  normals of wet pressures are cached per mesh.

- Load series and load envelopes. `AddLoadSeries(name, duration, step)` (or explicit
  `times`) makes one load case that spans a time history; loads added to it are stored
  once and each derived step (`name_000` ...) evaluates them at its own time, so a
  600-step history is one definition, not 600 copies. Every load type takes an
  `envelope` (ANYloads): a start/stop window by load case number or by time, and a
  factor from an equation of `t`, `number`, `k`, `tau` and the case parameters, or from
  piecewise-linear `(time, factor)` points. Loads add. Series definitions are not
  solvable and say which steps to solve; loads cannot be added to a step; deleting a
  series deletes its steps; `SetLoadSeriesTimes` re-derives them. The model tree groups
  steps under their series (and copy-style `series` cases under the series name); the
  active case in the Loads task never defaults to a definition or template, which is what
  made a seaway "Linear static" fail with "unknown name 't'". Files with series or
  envelopes are format 11. `LinearBatchSolution.connection_history(name)` returns a
  connection's reference motion across the cases. `floating_barge_seaway()` now uses one
  series with two stored loads (wave pressure fading in, a rigid-end moment windowed
  10-50 s).

- Spring connections and a seaway barge example. `AddConnection(kind="spring",
  stiffness=..., tie="rigid"|"distributing")` ties the selection to an inner section
  point and joins the loaded reference point to it with six linear springs (six
  numbers, or translational and rotational); a component without stiffness carries
  no load, and `connection_motion` also returns the section point's motion. The
  spring is ANYfem's own `SpringConnectorElement` (no solver change; elastic, no mass,
  no stress). `floating_barge_seaway()` (and an Examples menu entry) builds the barge
  with a travelling wave on three wet surfaces and a time-varying moment on a rigid
  end connection: 600 load cases over 60 s at 0.1 s, solved as one batch.

- Add connections for moments and forces on several lines, points or plates. A
  `Connection` ties the selection to a reference point (the length-/area-weighted
  centroid by default, or the bounding-box centre, or an explicit point) and a
  `ConnectionLoad` in a load case applies a force and moment there. `rigid`
  (Nastran RBE2 / Abaqus kinematic coupling) moves the tied nodes as one rigid body;
  `distributing` (RBE3 / distributing coupling) spreads the load over them with
  tributary-length or -area weights and adds no stiffness. `dofs` selects the tied
  motions (full, translations, plane section about x/y/z, or a list). The reference
  point is a hidden solver node with chained affine constraint equations; unused
  reference DOFs are held, a load on one is refused, and a moment a distributing
  connection cannot carry (about the axis of a straight line) is refused rather than
  dropped. A rigid connection may not tie a DOF a support holds. New commands
  `AddConnection` and `AddConnectionLoad`, `solution.connection_motion(name)`, Qt
  Loads task forms, a Connections tree group and a viewport marker with spokes and
  load arrows. Barge example: `floating_barge_section("distributing" | "rigid")` and
  two Examples menu entries; the distributing version reproduces the two line loads
  exactly (200.000 MPa). Files using connections are format 10.

- Load definition and evaluation moved to the new ANYloads package (ANYfem now
  depends on `ANYloads>=0.1,<0.2`): the equation evaluator, trusted Python code
  and its per-user trust list, the CSV/Excel `PressureTable`, and the fluid-side
  sign. ANYfem keeps attaching them to wet surfaces, meshes, the project file and
  the viewport, through `PressureField`/`CaseContext` adapters. Nothing changes
  in project files. The trust list moved from ANYfem's config to ANYloads'
  (`ANYLOADS_TRUSTED_CODE_FILE`; `trusted_code.json` under the ANYloads user
  config directory), and `anyfem.model.expressions` remains as an alias.

- Define wet pressure by Python code or from a CSV/Excel table. Code is a
  `def pressure(case, x, y, z)` (optionally `t`, `name`, `parameters`) called per
  element, or once with numpy arrays (`code_vectorized`); it is only parsed when a
  project is opened, validated or drawn and runs only when its SHA-256 is in a
  per-user trust list outside the project (typing code into ANYfem trusts it; code
  from a file asks before a solve, and the Qt solve shows it first). A
  `PressureTable` reads `case, x, y, z, pressure` rows (CSV/TSV/text/.xlsx; case
  identifier by number, time with optional linear time interpolation, or name; or
  one map), maps them to element centres by nearest point or inverse-distance
  weighting with an optional maximum distance, converts units, and stores the
  file's path and SHA-256 so a changed file is refused.
  `Project.add_load_cases_from_table` makes one case per identifier. Load cases now
  have a stable `number` (series steps use step + 1). Qt: a code editor field,
  Add wet pressure table and Add load cases from table. Files using code or tables
  are format 9. Excel needs the optional `openpyxl` (extra `excel`).

- Add wet surfaces and fluid pressure. A plate marked as a `WetSurface` (with the
  side the fluid is on, or a point inside a closed hull) receives a `WetPressure`
  from a load case: a uniform value, an equation of the element centroid, the case
  time and named parameters (`rho * g * max(0, draft - z)`; a restricted, parsed
  vocabulary, never `eval`), or a direct mapping of pressures to plates or mesh
  elements. Pressure is sampled once per element at its centroid. Fail-closed:
  mapping to a dry plate or element, a plate on two wet surfaces, an unknown name
  or a non-finite value stops the build. `Project.add_load_case_series` creates
  one load case per time step from a template and/or builder for quasi-static
  sequences, each carrying its `time`, parameters, `series` and `step`; the cases
  are solved together by `solve_linear_static_many` with one factorization. The
  Qt Loads task gets Add wet surface, Add wet pressure and Add load case series.
  Persistence adds project format 9, written only when a project uses these
  features (other projects stay format 8; an older ANYfem refuses a format-9 file
  rather than dropping its loads). The batch workflow no longer needs a load case
  named "default", batch results are now marked stale when any solved case's
  loading changes (the old definition pointed at "default" and tracked nothing),
  and a result view shows the loads of the case it displays. Wet surfaces are
  listed in the model tree under "Wet surfaces", like Sets: each row shows its
  plates, selecting it highlights them, it can be hidden, edited, renamed in its
  record and deleted (undoably), and wet pressures appear under Loads. The
  viewport outlines every wet plate (with a cross, in blue, never pickable) and
  draws the active case's pressure as arrows pointing into the plate from the
  fluid side, longer where the pressure is higher and absent where it is zero, so
  a waterline is visible. With a mesh the arrows come from the solver's own
  per-element evaluation (a shared function, so the picture cannot disagree with
  the load); before meshing, plates are sampled on a 5 x 5 grid, and a mapping by
  element id waits for a mesh. It follows the Loads display toggle. Not yet done:
  a nodal (non-uniform within an element) pressure representation.

- Run solves and meshing in a separate worker process instead of a thread of the
  GUI process. The numerical code is largely Python, so a worker thread competes
  with Qt for the interpreter lock, and every GUI repaint (hundreds of lock
  hand-offs) is stretched by it. Measured on the floating barge example with the
  software viewer, one full repaint of the mesh view takes 1.4 s idle, 1.4 s while
  a CPU-bound Python process runs, and 92 s while a CPU-bound Python thread runs
  (a worst case: that loop never releases the lock); a camera-moving repaint takes
  0.04 s, 0.04 s and 1.7 s. In a real Capacity run in a thread only 14 % of the
  expected GUI timer ticks fired; with the worker process 98 % did, and pressing
  Generate mesh no longer blocks the first event-loop pass for about 10 s. The
  camera level-of-detail preview (separate change, docs/QT_CONVERSION.md) removes
  the other cost, the repaint itself; the two compound. Inputs cross as the
  existing immutable snapshot, the solver job gets its codec-cloned mesh, and a
  finished mesh returns through ANYmesher's public codec, as in a saved project;
  progress, structured payloads, error diagnostics and cancellation cross a pipe,
  and solver results are pickled (read-only mapping views and thread-local work
  buffers travel on a private pickler only; ANYfem's rule against copying such
  views elsewhere is unchanged). The worker runs at below-normal priority, exits if
  the application disappears or the project is replaced, and a crash in native
  solver code fails the job without taking the GUI down. The library defaults stay
  `thread` (`JobManager(executor="process")` / `MeshTaskManager(executor="process")`
  opt in; a job function must then be a module-level callable, otherwise the job
  fails with an explicit message rather than silently falling back); the Qt
  workbench opts in, and `ANYFEM_JOB_EXECUTOR=thread` runs jobs in-process for
  diagnosis. The Tk frontend is unchanged. Starting a job costs the worker's
  start-up (under two seconds on the barge example), small against a capacity
  run and noticeable on tiny solves. Remaining costs are not job management:
  stationary full-detail repaints in the software viewer, and the solver's
  production validation (preflight), which assembles the stiffness matrix with
  its element guards enabled and took 13 s of an 18 s Linear static run on the
  barge example (ANYsolver owns that).
- Add an optional standard beam-section library to the Sections task. "Browse
  section library..." opens ANYsection's catalogue (T-bars, angles, bulb flats as
  equivalent angles, flat bars), shows the section properties as ANYsolver resolves
  them, and loads the pick into the Add beam section form for review. Nothing is
  applied until the engineer chooses the material and applies, as one undoable
  command; sections are named `designation [series | standard]` because catalogue
  designations repeat. ANYsection is optional (`pip install ANYfem[sections]`): the
  button is disabled with an install hint when it is missing, and the launcher
  uses a sibling `ANYsection` checkout when one is present.
- Accept both the v1 and the v2 (ANYmesher 0.5 and later) qualified S3
  structural-preparation record when building a solver model. v2 adds the
  `quality_policy` and `quality_target` evidence; a record whose members do not
  match its declared schema, or an unknown schema, still stops the build with
  "malformed preparation record". Projects saved with v1 records stay readable.
- Allow quad-first (automatically recovered) application meshes in nonlinear
  static analyses. Linear static already accepted them; every other analysis
  still stops with "requires separate qualification", and that message now names
  both accepted analyses.

## 0.4.1 - 2026-09-17

- Present multi-entity generators as lightweight feature objects: generated
  plates are grouped into one selectable surface, and generated points and
  lines stay hidden until an explicit per-feature Explode action, while exact
  topology remains available to meshing and saved references. Consume
  ANYgeometry's public topology-role and exact feature-owner contract rather
  than maintaining a duplicate feature-kind policy in the UI.
- Qualify the latest-only application graph against ANYgeometry 0.4.3,
  ANYmesher 0.5.0, ANYfileio 0.3.2, and ANYsolver 0.4.6.
- Expose structured/hybrid planning budgets, solver numerics, resource limits,
  and analysis-specific controls in the GUI while preserving the legacy B3
  shell as the default and keeping B3-GE an explicit opt-in.
- Make mapped butterfly-hole decomposition replayable through ANYgeometry's
  public feature-registry extension contract, with atomic regeneration and
  exact materialization checksums.
- Correct adjacent independently extruded plate connectivity, source-to-work
  association remapping, and refinement-aware hybrid mesh generation.
- Keep mesh hashes deterministic while retaining detached-work provenance in
  saved artifacts.

## 0.4.0 - 2026-09-03

- Relicense source releases from 0.4.0 onward under MPL-2.0; earlier releases
  retain their original terms.
- License original narrative documentation under CC BY 4.0 and add a complete
  direct-dependency license inventory and third-party notices.
- Qualify the application against ANYmaterial 0.2, ANYgeometry 0.4.2,
  ANYmesher 0.4, ANYfileio 0.3.1, ANY3dView 0.5.5, ANYtk3D 0.5.5, and
  ANYsolver 0.4.2, including canonical qualified-Q4 plastic-state replay.
- Stabilize semantic mesh hashes against ANYmesher 0.4 runtime provenance and
  timing fields so identical remeshes and exact undo remain deterministic.
- Correct imported multi-point stress reduction and make CalculiX FRD
  round-trip assertions respect the format's fixed numeric precision.
- Add deterministic release checks for licensing metadata and built artifacts.
