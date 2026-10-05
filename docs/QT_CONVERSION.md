# PySide6 conversion — living task record

The user superseded the exhaustive replacement goal with **reasonable Qt parity
to Tk**, explicitly accepting alternative solutions and asking to stop once
achieved. Reasonable frontend parity is accepted from the evidence below. Project/
result compatibility, numerical behavior and headless boundaries remain intact.
This does not establish full scientific or hardware qualification. The user then
authorized a polished light workbench and Qt as the default. Qt supplies the
`anyfem` entry point and unqualified development launcher. Tk remains available
explicitly; no package release or Tk removal has occurred.

Policy: `ANY_ECOSYSTEM_RISK_PROPORTIONATE_ENGINEERING_V1`, revision
`2026-09-24.2`, canonical ANYopenSoft philosophy. Preserve owner scientific
predicates, failed evidence and execution/resource restrictions. A focused pass
or a deferred hardware gate does not establish complete acceptance.

## Current implementation and evidence

2026-10-05 Mesh/Results pictograms: replace repeated generic icons with specific
mesh topology/strategy and displacement, rotation, stress-family/component SVGs.
Keep existing selectors, actions and hover help. Small presentation-only slice;
parent implementation with pixel uniqueness/widget checks and visual inspection.
Preserve concurrent application/viewport/tasks/tests; no numerical rerun required.
Delivered: four strategy pictograms and 28 distinct toolbar-size field pictograms;
engineering component badges use vector strokes, independent of installed fonts.
Seven focused widget/icon/help checks pass; inspected field-icons-preview.png.
Evidence reports/qt/field-icons-tests-final.txt; no scientific acceptance claims.


2026-10-05 physical-beam slice: expose persistent ANYgeometry Members as single
Beams tree rows; keep axis subdivisions and FE elements hidden. Use explicit
ordered owner chains, not inferred coordinate coincidence. Rebuild the example
with one member per stiffener/girder leg and full-span plate attachments, then
check persistence, selection/visibility and a bounded default mesh/static run.
OpenAI retains tightly connected implementation following the recorded Mistral
Edit authority violation; independent Read review remains available. Preserve
concurrent workflow/viewport/app/tasks and existing test edits unstaged.
Delivered: AddBeamMember groups a selected connected, unbranched set of unowned
lines in stable axis order; disconnected/branched selections retain separate beam
assignment. Qt Beams rows select/highlight/hide the whole chain, with subdivisions
hidden. Example now has 44 physical beams over the same 188 assigned axis segments.
Rebuilt default mesh has unchanged 4,348/3,185/1,060 node/shell/beam counts;
linear static recovered 200 MPa peak von Mises, MPC residual 4.2013e-19 with
unchanged 1e-10 tolerance. Independent Mistral Read findings adjudicated in
reports/qt/physical-beams-adjudication.md. Legacy singleton members are preserved;
recreate the example to obtain full-span members. Attached member splitting/deletion
continues to require an owner-qualified remap, beyond this tree-construction slice.
Validation: 22 focused tests passed both in the mixed workspace and an isolated
HEAD-plus-staged-files export; headless Qt/Tk import boundary and diff checks pass.
Evidence: reports/qt/physical-beams-final-tests.txt, physical-beams-clean-tests-final.txt,
physical-beams-run.txt and physical-beams-solve.json.


2026-10-05 prospective false-diagonal fix: reproduce a selected quad and actual
mesh triangle in software rendering, then distinguish fill triangulation from
semantic polygon boundaries in the shared viewer. Investigation found Qt painter
triangle pens and retained HUD per-triangle selection outlines. Preserve actual
model/FE edges, picking, clipping, masks and owner identity; do not modify mesh or
geometry topology. Scope ANY3dView Qt/retained presentation and focused tests, then
ANYfem viewer pin/integration checks. Existing dirty ANY3dView adapter/evidence work
is excluded. OpenAI implementation fallback retains Git ownership after the prior
Mistral Edit boundary violation; parent reviews/tests the connected result.
2026-10-05 false-diagonal delivered: GUI and both CI viewer pins now use
2026-10-05 runnable-barge load slice: user confirms longitudinal ends, clamp
X=0 and bending moment at X=5, S355 and approximately 200 MPa stress. Retain
12/10/8 mm plating. Two distributed axial edge loads form a pure My couple;
gross hull plus owner longitudinal-stiffener properties estimate 75.610681 MN m
for nominal elastic stress 200 MPa. Exclude transverse members/bulkhead from
longitudinal section area; local end stress peaks may differ. Set 700 mm target
mesh and linear static analysis. Parent retains this connected setup following
the recorded Edit-worker boundary violation; independent mechanics READ review
will inspect the result. Competing runtime uncertainties are native meshing of
continuous attached members and clamp/load-end stress amplification. One bounded
example mesh/static check (90 s preparation deadline) decides whether it is
demonstrably runnable and whether empirical load scaling is needed; preserve
failure evidence and existing tolerances/owner capability restrictions. No frozen
prepared campaign, default switch, qualification or release authority is consumed.
Outcome: nominal load produced a converged linear solution and recovered maximum
von Mises 364.767294 MPa. Set the default moment to 41.456941 MN m (scale
0.5482947711); owner recovery from the scaled converged displacement vector gives
200 MPa maximum at a beam element, without another factorization. This is a
mesh-specific example load choice, not a change to solver physics or tolerances.
Default mesh: 4,348 nodes, 3,185 shells, 1,060 beams, 700 mm size; installed
ANYgeometry 0.4.5 / ANYmesher 0.5.1 / ANYsolver 0.4.6. Actual runtime pools report
two OpenBLAS threads. Linear solve converged and the MPC postcheck residual is
8.54e-19 against unchanged 1e-10 tolerance. Stress recovery remains owner-labelled;
no broader formulation qualification or convergence claim is made.
Repair two adapter issues exposed by this valid example: source boundary aliases
must not own extra beams, and fully fixed plate masters already imply a homogeneous
zero clamp on their eccentric MPC slave. Reject orphan/ambiguous beam ownership;
retain partial/nonzero/local conditions for the usual constraint audit. Four
continuous end-edge supports reach the same 236 mesh targets as the initial
44 redundant end-edge supports. The external mechanics READ review confirms the
couple, estimate and exact zero-clamp implication; report and parent adjudication
are under `reports/qt/barge-bending-*`. Review coverage additions include zero
weights, local frames, legacy couplings, fully implied scopes and repeated aliases.
Focused setup/Qt/adapter/linear-solve regressions: 27 passed in 12.89 s; six existing
coupling checks pass in 4.18 s. Initial assembly/constraint and test-harness failures
are retained. Unrelated ResultsTask/Qt-workbench owner edits remain excluded.

2026-10-05 continuous-barge/display correction: user rejects the dense plate
grid and vertex markers. Replace 184 patches with one continuous face each for
deck, bottom, port side, starboard side and bulkhead. Preserve exact reinforcement
stations and section definitions; assigned Members are reused, with explicit
ANYgeometry MEMBER_ON_FACE attachments rather than relying on plate bay edges.
Keep topology vertices required by beam intersections; a persistent
`anyfem:user_points` geometry group identifies the eight main corner points.
Scene and Qt tree display those deliberately exposed points; explicit later
geometry.point commands also remain visible. Mesh display suppresses other node
markers for this explicit point-display policy. Ordinary models preserve their
existing behavior. This small connected correction remains with the parent
following the recorded Mistral Edit boundary violation. Mistral READ review
completed; its point-order concern was repaired with sorted explicit IDs while
ordinary mappings retain bounded iteration. Mesh-only markers remain omitted
under the explicit policy intentionally, with a focused normal-model control.
Its bulkhead-coincidence observation led to 44 explicit VERTEX_ON_FACE joint
witnesses after review; those additions have parent checks, not a new review claim.
Runtime: five faces, 188 assigned members and 232 attachments; owner topology
validation empty. Focused example/Qt/scene checks: 46 passed in 11.16 s, including
attachment membership, surface projection, persistence, eight markers/tree rows,
later point creation/undo and ordinary scenes. Evidence under
`reports/qt/barge-continuous-*`. After review, 11 example/Qt/scalability checks
passed in 6.93 s; final six example checks passed in 5.02 s after joint witnesses.
Attachment roundtrip and later explicit point creation/undo pass; the software
preview confirms eight markers and continuous surfaces. Offscreen font squares
remain an environment limitation. No meshing/solver campaign or acceptance added.

2026-10-05 barge reinforcement extension: longitudinal T stiffeners at exact
700 mm spacing on deck, bottom and sides; transverse T girders at X=0 and 4 m.
Preserve 5 m section, nominal vessel dimensions and open cut ends. The final
width/side bays are 500/400 mm; do not redistribute the requested spacing.
Shared station vertices/edges and explicitly oriented plate loops make each beam
an actual plate boundary. Inward web directions and automatic centroid offsets
are explicit; T200×8/100×10 stiffeners and T600×10/200×16 girders are illustrative.
Detached initial geometry now uses public ANYgeometry APIs in one transaction,
avoiding hundreds of primitive history snapshots; subsequent edits still use
the workbench command stack. Separate surface and aggregate beam sets persist.
184 assigned plate patches and 188 beam segments; no mesh/solve campaign.
Four focused checks passed in 4.68 s: exact spacings, shared topology, section
roundtrip, Qt activation/save-reopen and edit/undo; owner topology validation is
empty. Evidence: `reports/qt/barge-members-tests.txt`. Independent Mistral READ
review completed with no confirmed findings; exact spacing, shared topology and
inward webs were checked against source. Its first bounded attempt exhausted
before assessment (system Python lacks dependencies); both logs are retained.
Its undo question concerns initial assignments on a detached new document, not
an active user edit; Qt edit/undo is separately tested. Region definitions are
also compared after serialization in response to its coverage observation.
The software preview was inspected for reinforcement geometry; offscreen text
rendered as missing-font squares, so that image is not UI font acceptance.
Headless imports remain free of Qt/Tk. No physical GPU/scientific acceptance
is added; unrelated source and frozen execution gates are preserved.

2026-10-05 example-menu slice: add an editable floating-barge midship section
through a headless command-backed builder and the existing project replacement
path. Nominal vessel dimensions 30 × 11 × 2.5 m follow the Damen B11 product page;
the 5 m section length, 1.5 m draft and shell/bulkhead thicknesses are illustrative.
Protect unsaved documents, save/reopen and named geometry sets; no mesh/solve or
floating-equilibrium acceptance is implied. This small application slice remains
with the parent after the previously recorded Mistral Edit Git-boundary violation.
Implemented as Examples → Floating barge section…; starts a new unsaved project
after the normal discard/save decision. Nine assigned plates share twelve
vertices; named sets expose the deck, bottom, sides and transverse bulkhead.
Dimensional reference: [Damen Stan Pontoon B11](https://www.damen.com/vessels/pontoons-and-barges/stan-pontoons/stan-pontoon-b11).
Headless roundtrip, real Qt activation/cancel/discard/save-reopen and existing
button-help checks: 8 passed in 3.75 s; logs in `reports/qt/barge-example-tests*`.
The first check incorrectly referenced nonexistent Project artifact attributes;
that test error and its diagnostic are retained. No numerical campaign run.

ANY3dView `d8b82686e477920cb485a7d32e6f04699b6f4d6f`, based on current integrated
viewer main `2fba4dc`. Only source polygon/explicit logical-element indexed
boundaries are drawn; quad fan diagonals and pale fill-antialias seams disappear.
Real separate FE triangles retain their common edge. Geometry/mesh topology,
numerical behavior, per-triangle colors and picking stay unchanged. Highlight and
preselection outlines follow the same boundary contract. Viewer checks preserve
clipping/holes, visibility, chunks/deformation, animation, topology replacement
and approximate software depth ordering; hardware acceptance remains separate.

Evidence: four quad/plate image regressions fail on the old c69 input, while two
real-triangle controls pass. All six pass on the fixed viewer. Focused viewer/
arrays/retained/selection plus those six application checks give 45 passes and
9 explicit GPU-opt-in skips in 3.72 s (viewer worktree reports). ANYfem consumer
checks give 40 passes and one saved-layout-sensitive button-help failure; five
hover and six image checks pass after isolating test QSettings (no product change).
Failed raster seam, depth and layout diagnostics are retained. Parent headless
imports load neither Qt nor Tk; both wheels build and six image checks pass
from fresh wheel installations (effective module paths confirmed). Licensing
passes for 18 direct dependencies. Evidence is under
`reports/qt/false-diagonal/`. User-approved external Mistral READ review completed
on 2026-10-05; report and parent adjudication are retained in that directory.
Disabled-plane clipping already bypasses clipping; owner/tag selection admission
and the stationary-camera/2,500-primitive HUD guard are unchanged. No confirmed
correctness regression was found. Software fill antialiasing is disabled to avoid
internal seams: outer silhouettes can appear jagged with outlines off; boundary
strokes remain antialiased. This is a recorded visual limitation, not GPU acceptance.
User subsequently approved the two-file backup/update/restore. The local viewer
history diverged, so a normal merge preserves both histories; specific conflict
hunks retain the published owner-cache fix, regression and viewer docstring.
Merged viewer main `9f2bb81f4f3ce155c52175cc20dfbda48328d6a4` is tree-equal to
tested `d8b8268` and matches GitHub. All eight protected owner files are
byte-identical; restored files now occupy tracked paths, with differing owner
contents left unstaged. Preservation hashes are retained.
Both prepared execution gates remain false; no numerical campaign/package release.
2026-10-05 prospective selection/live-plate workflow: user requests clear
AddFace/AddPlate explanations, automatic selected topology IDs, Shift-right-click
Add plate/Add beam actions, and existing-point plate preview committed by Enter
or double-click. Preserve existing owner geometry rules and command/undo paths.
Drag selections require visible boundary-order review rather than inferred
polygon ordering; beam conversion requires an explicit section choice. One owner
handles the connected Qt implementation and focused widget checks. OpenAI
implementation fallback is used because the preceding available Mistral Edit
worker violated its explicit Git ownership boundary; a bounded Mistral READ
review may assess the finished artifact. No geometry-coordination resumption,
prepared execution enablement, numerical run or release is authorized here.

2026-10-05 selection/live-plate delivered: Geometry displays the difference
between Add face (closed existing edge loop) and Add plate (ordered vertices,
new boundary edges); material/thickness assignment remains separate. Matching
selected IDs populate geometry fields in selection order, preserving manual
edits until selection changes. Shift-right-click retains the entire point/line
selection and offers Add plate/Add beam. Add plate opens a modeless editable
boundary-order review with a numbered live outline; Construction also provides
"Construct plate from existing vertices". Enter/double-click applies one AddPlate
command; Escape cancels. Invalid insufficient/repeated/self-crossing/nonplanar
rings are refused using owner validation on a cloned geometry. Beam conversion
requires an explicit existing section and uses one undoable CompositeCommand.
Project replacement, clear, backend switch and shutdown cancel drafts; stale and
read-only commits are refused. No coordinate-based reordering is inferred.

Evidence: 19 construction/action widget checks pass, plus five parent hover-help
checks (24 distinct checks); failed menu-order, read-only and tooltip diagnostics
are retained in `reports/qt/plate-construction/`. Runtime: PySide6 6.10.3,
ANYgeometry 0.4.5, software/offscreen. Parent headless import check loads neither
Qt nor Tk; diff check passes. The wheel builds and includes the new draft module.
Mistral READ review, specifically approved for
source disclosure, finds no concrete defects in mutation, staleness, boundary
validation or duplicate commit paths. Its initial broader turn-limited partial
review and completed narrower review are retained separately. Astra checked owner
error prefixes and missing-vertex errors against source. Offscreen screenshots
have missing font glyphs and do not establish visual/hardware acceptance.
Physical Linux GPU acceptance remains deferred. Both prepared execution gates
remain false, paused coordination/frozen inputs remain unchanged; no numerical
execution, package release or sibling modification is included.
Concurrent main update `62cbb20` is incorporated without overwriting PR11 work.
The only conflict was this living record; both records are retained. All 24
changed-scope widget checks pass in 7.68 s against an exact exported ANY3dView
`c69aa8311e61b16f54895802fe8935cc2fe980ad` source tree with its effective import
path confirmed; evidence is `reports/qt/plate-construction/merged-viewer-tests.xml`.
2026-10-05 PR11 merge: after notification of the recurring modal-analysis
failure, the user explicitly directs merging now. Preserve that failed evidence
and numerical criteria; this delivery is not a claim of full regression or
scientific acceptance. Resolve main conflicts by retaining the newer manual
schema-6 controls and supplying pinned geometry/mesher fallbacks for automatic
Qt runs. Prepared execution opt-ins remain disabled by default.

The October viewer integration pins `c69aa8311e61b16f54895802fe8935cc2fe980ad`
in the GUI extra and both CI workflows. It combines fractional playback,
selection performance and a corrected owner-identity memo. Qt checks now run
automatically for application/test/dependency changes; manual runs can still
override the viewer commit. The earlier snapshots below remain historical
evidence. Current integration and review status is tracked in
[ANY3dView's integration record](https://github.com/audunarn/ANY3dView/blob/codex/qt-integration-20261002/docs/QT_INTEGRATION.md).

2026-10-05 remaining main work: user explicitly requests all remaining authored
local work pushed. Prepared reference/remap/preassembly sources, examples, tests,
candidate inspection/supervisor tools, schema-6 manual CI and documentation are
included. The compatibility directory is a clean private linked worktree at
`2b9a823`, not a main file or submodule; ignore it without deleting it. Mistral
made supervisor support portable using byte-exact Job source and minimal exact
AST control extracts, with provenance and line-ending protection. It exceeded
its delegated Git boundary and pushed `1ebd1df` including pending files; Astra
retained the user-authorized update and audited actual source, scope and evidence
rather than accepting that delivery report. No further Git delegation is granted.
Closed execution gates are preserved. Actual prepared-mesh-building tests require
`ANYFEM_RUN_PREPARED_MESH_TESTS=1` plus separate reviewed execution authority; no
such run is performed. Exact frozen-artifact inspection separately requires
`ANYFEM_RUN_PREPARED_CANDIDATE_INSPECTION=1` and refreshed reviewed pins. Ordinary
reference-scope tests require the coordinated owner API: 27 pass with the pinned
cb88 geometry candidate. Missing unpublished APIs do not silently become product
capabilities. Initial prerequisite failures remain in
`reports/main-update-remaining-20261005/ordinary-initial.xml`. Publishing these
development sources does not accept a new mesh or solver, reopen frozen budgets,
resume paused geometry coordination or release a package. Geometry owner reports
main `f415d84866508cfd6b5451b2caa66e218908e1e1` tree-equal to future `db5071c`;
record only, with its existing wheel identity unchanged.

2026-10-05 main delivery: user authorizes local/GitHub main update. Stage 29
explicit completed UI/workflow/presentation/test files; retain experimental
prepared-mesh sources, execution tools, compatibility candidates, structural
owner edits and unrelated architecture/CI documents locally. Clean intended-tree
checks initially gave 247 passes and two UI failures. Repair the stale tooltip
assertion and Model dock width regression (Mistral-owned three-line scroll-area
repair, independently inspected); 37 affected widget checks pass in 15.48 s.
The other passed checks are not repeated. Tooling/license checks gave 32 passes
and one sandbox-limited owned-process cleanup failure; its external-sandbox
recheck passes in 0.77 s. All initial failed diagnostics remain under
`reports/main-update-20261005/`. Wheel builds and its headless imports load no
Qt/Tk; MPL-2.0 inventory check passes for 18 direct dependencies. No package
publication, scientific campaign or physical Linux GPU claim is included.

2026-10-05 canvas interaction correction: user supersedes the original left-click
menu request with right-click. Left-click only selects; a plain right-click picks
the item and opens Copy/Move/Properties plus set actions. Right-button dragging
continues to orbit, and construction gestures do not open the item menu. The View
toggle now says "Actions on canvas right-click". Eight real-widget software checks
pass in 3.60 s using the existing Qt integration environment (offscreen); evidence
is `reports/qt/right-click-actions/software-final.xml` and `.log`. Initial native
DLL collection failure and the repaired test's stale post-layout coordinates are
retained separately. No hardware GPU acceptance is claimed. This authorized UI
delta changes app/viewport/geometry-actions source: previous preparation hashes
remain historical evidence; any future frozen full-ANYfem manifest must be
reviewed/refreshed before execution. Both engineering execution gates stay false;
geometry coordination remains paused and its frozen input artifacts are untouched.

2026-10-05 future coordinated application input (record only): geometry planar
network candidate `db5071c30612de2a06884b7b3eebef3152c120a7`, development 0.4.5 wheel
SHA256 `0f5fb9883bfe96528141e9d8bc2424dc44b638013fd8c006dfd9cfdae5bbb4c6`.
Owner handoff: `C:/Github/ANYgeometry/reports/planar-network-db5071c/HANDOFF.md`
and `delivery.json`; owner reports 168 installed checks and installed 1,000-operand
strip / 100-connection plate passes. These are geometry-only evidence, not ANYfem
measurements or mesh/project/solver acceptance. The distinct public planar-network
receipt includes overlap and transverse-contact inventories; future application
integration must use persisted occurrence/range mappings and owner validation,
preserving attachments, sections and loads without inferring display coordinates.
ANYmesher's coordinated consumer route and mesh acceptance are prerequisites.
Nonplanar whole-document Member networks and original Junction/orientation
remapping remain unqualified. Keep frozen cb88/2cbb inputs, prior attempts and both
execution gates unchanged; no probe, mesh, preassembly, solve, default or release
action is authorized by this owner handoff. Expanded Mistral milestone ownership
rules apply prospectively; completed reviewed work is not restarted.

2026-10-05 prospective current-candidate consumer slice: pin geometry main
`d4958c73c8b53d35d4392992a3233a75edb0feb4` / source `72c1733` canonical LF
wheel `cb88cc9156531142b5afcfd54f6cefde87ff97df0abc111bc0e7611039085b52`
and mesher `2cbb161daa75bb6785c9dc1afd2d558b7ca652f4`. Prepare an isolated
portable harness around the existing live Project/closure fixtures and remap/
preassembly services. Question: do exact current dependencies still expose the
required public contracts, and what fresh root/child transfer experiment would
discriminate compatibility from missing source authority? Check imports/signatures
and ordinary no-engine guards only. Propose separate fresh root and reopened-child
cases, retaining their prescribed inputs and invocation-local witnesses. No mesh,
FE build or solve is executed before proposal review; no bare geometry artifact
can acquire a Project witness retroactively. Earlier accepted 6ff/f752 evidence
stays frozen. No consumed budgets, UI/default, CI, Git delivery or release changes.

Prepared outcome: `tools/prepared_candidate_integration.py` exposes inspection/
proposal CLI only; engineering dispatch is explicitly disabled. Exact artifacts
are staged under `reports/prepared-mesh-preassembly/current-2cbb161` without
altering an accepted environment. Windows imports and all 12 intended call shapes
bind with no Qt/Tk import. All 12 ordinary guards pass in 1.37 s, including fresh
two-Plane Project closure/clone preservation; no intersections, triangulation,
FE construction or solver are run by these guards. Full source-package/fixture
hashes and archive-derived staged-byte verification prevent stale candidate use.
The first Mistral READ review found case/multiplicity and pin/inspection gaps;
those are repaired, with the original source and reviews retained. Delta READ
review reports no blocking issue for preparation only; neither reviewer invocation
ran tests or commands. Actual fixture-output compatibility remains unmeasured.

Proposal `current-2cbb161/proposal-reviewed.json` requests Windows root and child
cases sequentially, each with its own fresh size2 engine invocation (two total),
180 s/512 MiB proposed limits per case and stop on first failure. Root pressure
is 1200 Pa; reopened-child pressure is 300 Pa on its six cells, with S355/10 mm
and an explicit fixed other-source-face support. The child case creates its own
fresh prior mesh and NEW Project/closure; it does not reuse the root case artifact
or establish separate child engine acceptance. Existing remap/preassembly APIs
remain the authorities; no receipt is duplicated. Review of the execution proposal
and checked supervisor integration are outstanding gates before enabling dispatch.
No run authority is inferred from the ordinary checks or bare geometry evidence.

2026-10-05 prospective supervisor integration: retain that baseline and prepare
180 s/512 MiB per-case controls, sequential/stop-on-first-failure/no retry.
Reuse only hash-checked source mechanics from the prior reviewed Job helper and
deadline/cleanup functions; none of their earlier runtime acceptance is inherited.
Mistral owns the isolated supervisor and mocked tests; Astra owns runtime/source
freezing and reconciliation. Verify exact named Job membership, actual queried
limits, shared deadline and owned-child cleanup in source/mock checks. No OS probe,
engine, preassembly or solver until root reviews the final frozen controls and
commands. Refresh `proposal.json` from the current proposal function. Keep both
execution gates false and preserve prior failures and frozen 6ff/f752 artifacts.

Supervisor preparation outcome (2026-10-05): isolated control and freezing tools
are implemented; 114 pure mocked checks pass in 1.13 s. They cover exact 0x2200
Job flags/512 MiB, named membership, suspended assignment before resume, one common
180 s deadline per case, owned cleanup/timer quiescence, source fingerprints,
one-shot sequence claims, result-packet binding and scalar identity preservation.
The final manifest pins 3,498 files and explicit interpreter/import paths; the
proposal is regenerated from the current function. No OS probe, mesh, FE build or
solve was executed. Both gates remain false. The final independent Mistral READ
review is partial (turn limit before a final report); its deadline arithmetic
finding is repaired with fractional-clock regressions. Root must review the final
frozen manifest/control/commands before any execution, and independent acceptance
is not claimed. All 17 protected UI/owner hashes and the frozen 6ff ZIP match.
Prior mock failures remain recorded, with irrelevant inherited-environment repr
redacted. See `reports/prepared-mesh-preassembly/current-2cbb161/`:
`supervisor-preparation-checkpoint.json`, `supervisor-manifest.json`,
`supervisor-control-contract.json`, `proposal.json`, and `supervisor-reviewed.xml`.
The newer member-bearing geometry owner candidate is not substituted into this
cb88/2cbb baseline. No CI, default, Git delivery or publication changes.

Focused review closure (2026-10-05): independent Mistral READ completion finds no
blocking source issue in final control/command/cleanup and manifest enforcement.
Astra adjudicated its limits and corrected the per-case clock to start before
backstop source loading; the final delta is independently reviewed with no blocker.
All 115 mocked checks pass in 0.98 s. Final 3,498-file fingerprints and all 17
protected hashes verify. See `supervisor-focused-review-adjudication.md`, completed
review reports and refreshed `supervisor-preparation-checkpoint.json` in the same
evidence directory. This completes independent source review only: both gates
remain false and root must review final controls, commands and manifest before
any actual run. No Windows runtime, scientific or physical GPU acceptance is
claimed, and the member-bearing geometry owner delta remains separate.

2026-10-04 detached FEM remapper prospective implementation: consume immutable mesher
6cffcddf6940e6a64e35c4fe508683e96f210ab0 v1/v2 receipts and exact geometry
 e509862/main f03a7e8 in a separate headless module. Validate the witnessed Project
manifest/closure and full live mesher proof before/after; preserve IDs, XYZ,
connectivity, child-local scopes and physical source records. Account for all
associations and references. Current-only-of-kind associations require complete
owner original inventory/handle proof and remain separate current provenance;
empty ancestry alone never licenses dropping data or claiming no references.
Unsupported/missing proof remains typed refusal. Start the portable synthetic
2-Sheet/16-T3 fixture and focused ordinary remapper regressions. No solver,
publication, UI route/default/release changes, broad/native diagnostic or spent
b032 qualification replay. Preserve all protected owner and accepted UI files.

Candidate implementation now exists in `anyfem.prepared_mesh_remap`, without a
production caller. It returns a detached source Mesh with separate CURRENT
provenance, complete accounting, exact reference/occurrence bindings and the
original live witness; solver/publication flags stay false. Owner-corrected mesher
969c106 replaces 6cff for the 26-T3 station fixture. Windows and WSL Linux focused
source checks pass 12/12 on each platform; the separately retained reopened-child positive test still fails
before FEM binding at an ANYgeometry unsupported/uncontained Attachments refusal.
A minimal geometry-only reproducer is retained; no missing authority is recreated
from serialized preparation data. The serialized/numeric orientation bug and
failed offline Linux build evidence are also retained. Exact inputs, implementation
contract, fixture-copy provenance and remaining gates are in
`PREPARED_MESH_REMAP_CONTRACT.md`. Independent review and installed FEM artifact/
active route acceptance remain pending. UI files and protected owner work remain
under the saved baseline hashes; this slice does not extend UI acceptance.

2026-10-04 owner-reported child-scope diagnosis: complete Sheet extraction omitted
an existing Sheet junction, leaving original links 1/2 without their declaration;
repreparation then added eight redundant vertex-on-face links. The owner reports
a discovery correction and a component proof limited to literal unchanged,
original full-range Sheet-joint links/declarations with identical payload, carrier
edge and endpoints. General parameter/property semantics remain unqualified.
Owner reports 74 focused source passes; independent review and exact installed
candidate/wheel checks are pending. This is an owner report, not FEM acceptance.
Keep the existing failing positive unchanged. Next: inspect the reviewed exact
artifact and rerun that child-scope case with NEW live extraction/preparation,
then run only the regressions affected by the owner change. No old closure or
authority reconstruction, native replay, tolerance relaxation or active route.

Owner candidate 972d70a616de4f95b1c0f5ff79386f9725525595 is now delivered for
bounded consumer verification, wheel SHA256
97c717ef2ada2508408290a002cf5fa5839dd009dbf7c975fd3ad1d2dd275dbc.
PR20/hosted gates were pending in its handoff. First rerun the unchanged positive
child test in a separate runtime, then the affected remapper/reference-scope
regressions on Windows and WSL. Inspect the preserved relation IDs in fresh WORK
namespace; no general relation remapping is qualified. Retain old failures and
accepted UI runtime. Exact-wheel comparison and focused consumer checks are the
decision experiment; a pass closes this child binding case, not solver or release
acceptance. Any failure is diagnosed at its first owner/consumer boundary.

Exact geometry 972d70a consumer result: the unchanged child positive now passes
the original geometry boundary but fails at ANYmesher 969c106's CURRENT cell
proof (`authored component joint lacks shell incidence from both roots`). Its
validator compares each segment's incident roots with ALL eight component roots.
The geometry-only discriminator, using the original design packet and NEW
witnessed extraction, independently proves joint WORK edge 17 (Project edge 25)
is incident on roots 1/2/4/7, while the complete component has roots 1..8. Source
data stays unchanged; two literal joint links and one junction are retained.
No cell proof is bypassed. The 39 affected source checks pass on each of Windows
and WSL; the failed positive is deliberately excluded from those selections and
retained separately at `reports/prepared-mesh-remap/972d70a/child-windows.xml`.
The coordinator reports directing the mesher owner to qualify actual face/Sheet
incidence plus complete material coverage, preserving shared-ID/sequence guards.
Await a coherent mesher fix before repeating this known blocker. No FEM source,
positive test, tolerance or production route changed in this follow-up.
Cross-task approval review rejected a new failure-result message as beyond the
earlier attachment-reproducer approval; a scoped follow-up permission question
is pending. No rejected message was sent or routed through a workaround.

Mesher cd66e3ba5713192c472c00caf326f2cc9425b938 is delivered for bounded
verification with exact geometry 972d70a/wheel 97c717ef above. The inspected delta
uses qualified current Coedge/FaceUse/Face/Sheet incidence for each joint, requiring
one shell edge per incident Face and none from other Faces; whole-component
material coverage and shared-node/sequence guards remain. Owner reports 15
affected mesher passes and the unchanged positive passing; those are not consumer
acceptance yet. Freeze the exact source, build isolated platform wheels, run the
unchanged child positive first, then affected remapper/reference checks and owner
incidence guards. Inspect any remaining application source-transfer gap before
accepting the bounded candidate. No broad solve, native/b032 replay, default,
release or publication inference; prior failures and accepted UI stay intact.

cd66+972 pre-review consumer checks completed: the unchanged child positive passes
on Windows and WSL, retaining 26 T3 and exact child pressure scope; 40 total
reference/remapper cases pass per platform (Windows split 1+39 to reuse its first
positive). Six installed mesher incidence guards pass per platform. These checks
do not close independent review: reviewer `/root/detached_remap_review` found
unknown dynamic Mesh fields could escape inventory and a final owner callback
could change Project input after its last validation. Preserve the raw source
review at `reports/prepared-mesh-remap/cd66e3b/independent-review.md`. Execute
three narrow red regressions, repair explicit instance-schema refusal and
post-callback Project validation, then recheck the changed source binding and
request independent delta review. Original child acceptance stays unchanged.
Geometry main 1ef7146 and candidate 972d70a have independently compared equal
local Git trees (`e159f043fdbb2048d5b4b4fa13bc0d1c70b9051a`); hosted/main delivery
details remain owner-reported and do not require rerunning unchanged owner suites.

Independent review discriminators reproduced all three initial holes (3 failures
in 47.09 s), then passed after explicit instance-schema checks and post-owner
Project validation (3 passes). Delta source review confirms those repairs but
identified copying after the final guard: owner-accepted ndarray subclasses can
run a copy hook that changes Project input. Reproduce this one additional case,
stage detached copies/inventory before final validation, and put the mesh snapshot
before the last Project revalidation. Keep all failed evidence and both review
reports. The Linux optional mesher extension build lacked Python.h; its successful
scoped checks use Python proof code and do not qualify compiled/native meshing.
Do not repair unrelated system headers or infer a native acceptance claim.

Final bounded source binding is verified against cd66+972: the unchanged positive
retains 26 T3 and narrow child pressure on both platforms. Independent review's
five counterexamples were reproduced and repaired (instance inventory, late
Project mutation, copy hooks and late detached-output mutation). Final sequence
constructs copies/output first, runs final owner callbacks, verifies all input/
output schemas/snapshots and coordinate detachment, then revalidates the Project
and returns without further payload work. Final independent source review reports
no further consequential issue in bounded witnessed Plane/Straight scope; it is
not independent executed/scientific acceptance. Windows final checks: 7 changed
binding cases plus 5 review regressions pass. WSL: all 12 pass together. Reuse
unchanged 6 preflight refusals, 27 reference tests and 6 installed owner incidence
guards per platform from exact-dependency affected runs; no changed-input failure
is waived. Earlier owner refusals and all review red runs remain on disk.

Exact module SHA256, immutable owner commits/platform wheel hashes, final counts,
review reports, installed package bytes, current source hashes and preservation
checks are in `reports/prepared-mesh-remap/cd66e3b/implementation-evidence.json`.
Remaining source-transfer scope is deliberately narrow: literal full-range EXACT
joint relations and supported manifest references only; general property/parameter/
arbitrary attachment semantics remain unqualified and unsupported nonempty Mesh
fields refuse. Qualification is invocation-local. Clean installed FEM artifact,
active workflow/atomic publication, independent executed/scientific qualification,
solver admission and release remain separate. No production caller, solver,
default or release change is made here. The user subsequently granted scoped
result-reporting permission; the exact verified handoff was sent to ANYgeometry,
which recorded it and forwarded it to ANYmesher. The initial approval-review
rejection remains historical evidence; result reporting is no longer pending.

Next bounded integration discriminator (2026-10-04): compose the unchanged
reopened-child fixture with explicit material, sections and supports, capture a
fresh application manifest, and invoke the existing headless FE build adapter
against the detached source mesh. Question: does complete source binding suffice
for the existing S3 preassembly contract, or is separate owner production-normal
authority missing? Preserve child-only pressure, all 26 cells, source geometry,
input Mesh and scientific criteria. Run this one ordinary probe on Windows and
WSL using the already pinned owner runtimes. If the public adapter refuses,
retain its exact diagnostic and identify the smallest owner contract needed;
do not manufacture a preparation record, weaken intake or run a solver. Active
GUI integration/atomic publication awaits that decision. No native/b032 replay,
renewed execution budget, default switch, package release or automatic publication.

Discriminator outcome: Windows and WSL each retain 26 T3 cells, exact pressure
on source child face 3 (six cells), nonempty support nodes on face 4, and assigned
S355/10 mm sections. Existing `build_fe_model` then refuses with `qualified S3
mesh authority has a malformed preparation record`: the detached mesh has no
qualified-S3 production record. Both runs verify unchanged Project manifest,
source geometry and input/output meshes, and no Qt/Tk imports. This is a supported
negative integration result, not a complete FE build, solve or scientific pass.
Evidence: `reports/prepared-mesh-remap/cd66e3b/preassembly-{windows,linux}.json`;
probe source identity is recorded in each. The initial Windows sandbox DLL access
failure remains in `preassembly-windows-sandbox-import-failure.log`; the authorized
retry used the existing pinned runtime without modifying owner artifacts.

Next owner boundary: obtain live mesher qualification for complete S3 physical
normals and the existing production/quality/repair identities in the remapped
source namespace. CURRENT membership/UV proof and invocation-local FEM transfer
are insufficient. The remapper currently refuses nonempty structural preparation;
consume any new owner record through an explicitly reviewed extension with fresh
binding checks, rather than synthesizing an ADMITTED record or rewriting geometry
identity. Until then leave active workflow and atomic publication unchanged.

Exact source handoff is `reports/prepared-mesh-remap/cd66e3b/source-handoff.json`;
the 11-file source archive SHA256 is
`103d1626180f85ed59725d7f6573ffa622aa95e1527ecb65372fe61259443661`.
It preserves reviewed implementation bytes and the unchanged original positive;
it is not an installed package or release. Twelve accepted UI files and five
protected owner files still match the saved baseline. Effective owned diff checks
pass. Both probe results and the precise owner contract blocker were sent to the
ANYgeometry coordinator under the user's granted scoped reporting permission.

Prospective owner-proof adapter slice: ANYmesher is preparing the missing contract;
do not rerun the unchanged blocked probe while its inputs are unchanged. Prefer a
separate headless adapter around the reviewed remapper, retaining its raw nonempty
`structural_preparation` refusal. The owner must derive and freshly validate the
production payload for exact source geometry/cell/occurrence identities; FEM must
not copy CURRENT authority IDs into source fields or manufacture ADMITTED values.
Stage all detached output/payload work before final callbacks. After those
callbacks, check complete input/output schemas, byte snapshots, detachment and
the exact owner proof, with Project validation last and no subsequent payload
work. A plain serialized/reconstructed proof, stale model/cells/normals,
source-ID mismatch, source mutation, late callback/output mutation and cancellation
must refuse. Preserve all 26 cells, the six-cell pressure scope, sections/support
binding and original physical values. Test missing proof as a refusal control;
reuse unchanged remapper/source-reference checks by impact. Review the exact new
adapter and pin the new owner artifact before claiming preassembly integration.
This preparation changes no runtime or scientific/publication authority.

Mesher `daa8263` provides `prepare_source_bound_qualified_s3_mesh`; a separate
FEM candidate now composes it in `anyfem.prepared_mesh_preassembly`, retaining
remapper `2adf4ed` unchanged. The candidate checks an existing FE build without
returning a live solver model or publishing workflow state; final callback,
input/output schema/snapshot, coordinate-detachment and Project guards remain.
Independent owner review identified Q4 alias mutation, source freshness across
callbacks and insufficient SOURCE material mapping for translated parallel
faces. ANYmesher owns repairs. Do not run new preassembly/solve checks against
that superseded owner input; prepare focused adapter tests and independent source
review, then consume the corrected exact artifact. No solver execution is
authorized by a field called ADMITTED or this candidate's preassembly summary.

Independent adapter source review found live-Project normalization, foreign load
case escape and legacy-formulation ambiguity. Repairs build on an exact detached
Project copy, restrict case selection to a witnessed name/ID and require/check
V2D S3 consumption. Original source and findings are preserved in
`reports/prepared-mesh-preassembly`; no pre-repair red run is claimed. Focused
regressions and delta review are prepared; runtime checks wait for the corrected
mesher input. Contract: `PREPARED_MESH_PREASSEMBLY.md`.

First changed adapter run against exact mesher `2648ae8` reached production
qualification, then the module fixture failed because plain `deepcopy(Project)`
cannot copy owner mapping proxies (14 setup errors, one shared cause). Retain
`reports/prepared-mesh-preassembly/2648ae8/adapter-windows.{log,xml}`. Use the
owner's public detached geometry clone with preserved identity, memoized into
the Project copy, and still compare complete copied inputs before FE build.
Meanwhile owner review found incomplete sampled material support and same-revision
freshness in 2648; it remains an intermediate input, not accepted authority.
Wait for the corrected exact owner before the next composition run/WSL check.

The detached-copy repair passed two standalone checks each on Windows and WSL
(1.12 s / 7.37 s): copying immutable owner containers and normalizing legacy
assignments only in the detached build Project. These invoke no S3 qualification
and do not supersede the retained composition setup failure. Independent delta
source review reports no additional adapter finding. Owned diff/whitespace checks
pass; all 12 accepted UI and five protected owner file hashes remain intact.

Implementation checkpoint is `reports/prepared-mesh-preassembly/checkpoint.json`
(adapter SHA256 `be5246f60e814aa3b8618cd363c6b4eea73f3b04a0d677ba5e8ae01e77e9150c`).
Automatic approval review rejected sending this new internal checkpoint to the
ANYgeometry coordinator, stating that earlier permission covered only the prior
result handoff. A specific approval question is pending; keep the checkpoint
local and do not bypass the rejection. This does not block local preparation or
incoming corrected owner artifacts. Full composition/runtime acceptance remains
pending; preserve the original failed XML and scientific/publication limits.

Latest owner-reported boundary: mesher review `0e2` closes persisted-snapshot and
sampled-hole gaps, but floating source UV is not certified equivalent to actual
XYZ on scaled/skewed Planes. ANYgeometry is implementing an exact owner
whole-triangle XYZ validator, Plane-only and fail-closed. This is an owner report,
not FEM execution/acceptance; no new exact geometry/mesher artifact has been
consumed here. Hold fresh composition/solve until that pinned handoff and review
resolution. Reuse unchanged remapper/copy evidence. No native/b032 replay,
authority renewal, active caller, default or release change.

Exact geometry candidate `f752cd8584226c3226e57b2e1cd692f69a593932` is staged for
artifact imports, wheel SHA256
`163fc334d8ae97c06a39b8471f74fe970f7a6cddf02a21d461680523c05c48dd`.
The new API accepts actual XYZ and one fresh bound Plane material region; no
rounded UV substitute. The initial comparator used Git archive output, which
applies checkout line endings in this environment; its 91 differences were
only CRLF/LF. Direct raw Git-blob comparison subsequently confirms all 91 wheel
files match exactly. Retain the initial raw-identity failure/discriminator,
leave wheel bytes intact, and record the corrected comparison in
`reports/prepared-mesh-preassembly/exact-xyz/geometry-identity.json`. Windows
artifact import resolves to this staged wheel and exposes the exact new API.
Accepted runtime is unchanged. Owner reports 45 source/45 installed passes and
clear READ review; these are not FEM execution claims. Wait for the mesher bridge
that consumes this exact API before new composition; hosted geometry gates,
solver admission/science/native and publication remain separate.

Latest owner review of intermediate mesher `01bb` reports a remaining consumer
snapshot gap: callbacks during earlier SOURCE-region checks can temporarily
substitute later-cell XYZ, then restore it before the final digest. ANYmesher
must freeze every cell/face/XYZ input before callbacks and certify those same
cells. Geometry `f752` exact-XYZ API review remains clear; hosted kernel gates
are owner-reported pending. Do not consume `01bb` for solve or replay the
unchanged blocked probe. Wait for the corrected reviewed exact bridge; FEM
adapter, staged wheel and existing evidence stay unchanged.

Prospective final bridge slice: consume reviewed mesher
`6ff16ee8ca39b7b6b8d786c863e35be97d111cfc`, exact source archive SHA256
`f722b14c1bec9dbe9425b5f9378a3c362702071965411148f6112323db0dc4b7`,
and geometry `f752` wheel `163fc334...`. Source review closure is
`C:/Github/ANYgeometry/reports/source-s3-authority-review-6ff16ee.md`.
The bridge freezes SOURCE/CURRENT inputs before callbacks and uses those cells
through exact XYZ proof and no-repair S3 qualification. Run the changed adapter
and copy regressions on Windows/WSL. If those APIs accept the detached artifact,
execute one explicit existing S3-V2D linear-static probe per platform on the
unchanged 26-cell/six-pressure-cell S355/10 mm/support fixture. Record finite
displacements, clamped DOFs and available solver diagnostics with exact runtime
identities; refusal becomes a precise blocker, not a threshold/physics repair.
This is bounded integration evidence, not complete scientific acceptance.
No active workflow publication, broad/native/b032 replay, budget renewal,
default switch or package release. Reuse unchanged earlier evidence.

Final bridge outcome (2026-10-04): all 16 changed adapter/copy checks pass on
Windows in 338.59 s and WSL in 278.66 s against those exact inputs. One explicit
linear-static probe per platform also passes: 26 T3 cells/physical normals,
finite nonzero displacements, solver convergence and a passed constraint
postcheck with zero fixed-support DOFs. Both report maximum translation
0.1680548990249682 m and relative backward error 2.1186723952599376e-21.
The six pressure cells are prescribed/source-bound inputs, not an independent
inspection of the solved pressure representation. S355/10 mm, 300 Pa and the
fixed source-face support are unchanged. This closes the bounded composition/
preassembly/solve integration question; it does not establish physical accuracy
against a reference solution, stress acceptance or full scientific acceptance.

Source review closes the adapter findings and finds the probe interpretation
appropriate within that limit; the reviewer ran no tests. Retain the original
14 mappingproxy fixture errors and superseded owner failures. Exact source,
artifact/runtime identities, XML/logs, probe results and review limits are in
`reports/prepared-mesh-preassembly/6ff16ee/implementation-evidence.json`.
The staged geometry wheel matches all 91 raw Git blobs; the earlier archive
line-ending diagnostic is retained. Geometry PR21/main and hosted gates are
owner-reported, separately from these executed FEM checks.

Next integration requires an explicitly bounded fresh workflow/publication
slice; this candidate returns invocation-local evidence and has no active UI
caller. Clean installed-FEM, broader scientific/native acceptance and physical
Linux GPU remain separate gates. No native/b032 authority, release/default
change or frozen budget is consumed or renewed here. The new coordinator
checkpoint initially remained local following automatic approval rejection.
The user subsequently approved it, and the completed checkpoint was sent to
ANYgeometry coordinator `01a0bafd-a1ac-7c63-ac40-71035d98082a`. The exact source
ZIP remains unchanged; its historical pending-reporting state is superseded by
`reports/prepared-mesh-preassembly/6ff16ee/handoff-receipt.json`.

2026-10-03 hover descriptions: extend the current progress slice with descriptive
button tooltips across the Qt workbench, including toolbar/workspace icons,
operation buttons, run controls and dynamically created dialogs. Preserve useful
existing hints and show the currently selected operation in Apply help. Use one
window-scoped help provider so future dynamically created buttons inherit help;
verify real-window Help events and document/window lifecycle. This presentation
change adds no numerical or scientific execution authority.

2026-10-04 hover descriptions completed in local main (5 explicit files;
uncommitted). Every existing Qt workbench button has hover help, including
workspace/action icons, checkboxes, run controls and dynamic child dialogs.
Descriptions explain the action; Apply and feature creation follow the currently
chosen operation. Existing custom hints remain intact. The window-scoped help
provider leaves other windows untouched and is removed during shutdown. Native
OS-owned dialogs retain their platform behavior. Five new real-window tests cover
all existing buttons, displayed Help events, changing operations, regenerated
icons, vector dialogs and custom hints/window isolation. The combined installed
progress + tooltip candidate passed 42/42 affected UI/headless-boundary checks
on Windows and WSL XCB/software, plus three runner-profile checks. Wheel SHA256:
`ab1ee03f5ba857f78cf1ff59243ef234d98e805c06d9545894af7f3b78c6f59b`. Installed module bytes match this exact wheel;
32 unrelated baseline files, the other progress source files and five protected
owner files remain unchanged. No solver/mesher computation or new scientific/
physical-GPU acceptance was run. Evidence:
`reports/qt/button-help/implementation-evidence.json`.

2026-10-03 live run progress: improve mesh/solve text and graphs using existing
worker notifications. Add a shared Qt run monitor with job identity/state, stage,
reported work fraction or indeterminate activity, measured elapsed/update age,
bounded timestamped log and live owner-reported metrics. Improve chart axes,
units, latest-point labels and missing-data gaps; batch rendering on the UI
thread. Preserve terminal/stale/project-replacement/cancellation semantics and
existing full job logs. No estimated ETA, fabricated solver percentages, new
solver callbacks, computation changes or scientific diagnostic runs. A tooltip
check failed despite activation: compare native QTest mouse delivery with a
direct Qt MouseMove event, recording sample coordinates and tooltip state. If
only direct delivery works, fix the test harness; otherwise repair the handler.
Verify
synthetic notifications and existing progress/frontend contracts, plus installed
Windows/WSL widgets and lifecycle checks. Preserve all concurrent owned work.

2026-10-03 live run progress completed and integrated into local main (9 explicit
files; uncommitted). The bottom Run monitor opens for mesh/solve runs and shows
job identity, stage, measured elapsed/update age, worker-reported completion or
indeterminate activity, numeric metrics, a timestamped log, and selectable live
charts. Newton trial residuals remain separate from accepted histories; solver
work percentages apply only to the current stage. Existing load/displacement/
reaction histories retain their units and owner meaning. Charts show axes, units,
latest values, single samples, missing-data gaps and point tooltips. Rendering is
batched on the Qt UI thread; displayed logs/histories are bounded. Full job logs
and computation remain unchanged. Cancellation targets the selected manager/job
and stays disabled while pending. New sessions, including reopening the same
saved document ID, clear histories; both graph timers stop on window shutdown.

Installed Windows and WSL XCB/software baseline checks: 52/52 each. The final
lifecycle delta changes only `ui/qt/run_progress.py` and `ui/qt/tasks.py` package
bytes, so unchanged checks reuse that evidence; 13/13 focused installed checks
per platform passed on the final wheel, SHA256
`9657ee6dcfa6c9057215471ffb888feb621b2da339b578cacbdbbfee1c85d8a0`.
Three runner-profile checks pass; both new suites join the existing Qt profile.
Installed module origins and byte hashes match the final wheel. A real Windows
workbench image with synthetic residual notifications confirms the dashboard
layout. The retained tooltip probe found native QTest mouse movement delivered
no event; direct Qt MouseMove delivery produced the correct tooltip. The test
now verifies the handler deterministically, without claiming physical mouse
acceptance. No new engineering calculation, scientific qualification or physical
Linux GPU acceptance was run. All 33 other baseline files and all five protected
owner files stayed byte-identical. Evidence and retained initial failures:
`reports/qt/run-progress/implementation-evidence.json`.

2026-10-03 selected visibility: implement Ctrl+H Hide selected and Ctrl+S Show
selected for geometry, features, sets and mesh selection. Move Save to Ctrl+Alt+S
and retain Save as Ctrl+Shift+S. Visibility is transient per window, preserved
across display refreshes and renderer changes, reset on project replacement, and
never mutates design/analysis inputs. Keep hidden items selectable in the tree
with lighter text; retain selected sets and mark complete/partial hidden state.
Verify mixed sets, collapsed feature polygon ownership, ordered selection,
shortcuts, picking, refresh, project replacement and source scene immutability
with bounded synthetic Windows/WSL installed widget checks; no solving/meshing
or owner scientific authority is consumed. Preserve concurrent remap/UI work.

Implemented and integrated eight visibility-owned files. Hidden topology is
muted in every tree occurrence; sets/features show hidden or partial-hidden
tooltips. View and tree context menus also expose Hide/Show, plus View → Show all.
The selected items remain selected so Ctrl+S can restore them. Filtering retains
per-polygon visibility separately from collapsed-feature pick ownership; partial
cylinder hiding preserves siblings. Full/interaction scenes, refresh and backend
changes preserve visibility. Mesh/result replacement clears only mesh IDs to
avoid hiding reused IDs; project replacement clears all transient visibility.
No Project commands, dirty state, persistence or numerical inputs change.

Exact installed candidate SHA256
`f9eaa4c04aedb818bafebc294384aeedc7c738651ca8ec83ffebe43483582f32`:
**29/29 Windows and 29/29 WSL XCB checks**, plus **3/3 Windows GPU checks**
which assert GPU remains active, and **3/3 runner-profile checks**. Zero final
skips/errors/failures. Evidence and imported origins are under
`reports/qt/selection-visibility/implementation-evidence.json`; packaged bytes
match the integrated source. Preserve initial missing-build-tool/cache logs and
the initial WSL activation failure: the test now waits for an active window and
focused event widget before sending shortcuts. 28 other dirty files and all five
protected owner files remain exact. Local implementation is uncommitted; no
release or new physical Linux GPU acceptance is claimed.

2026-10-04 strict transfer preparation: bounded source inspection of the mesher
public CURRENT receipt found that fresh validation requires the owner component,
station registry and exact cell-to-current-face inputs, in addition to the
receipt and staged Mesh. The proposed FEM interface now retains those proof
inputs and specifies a fail-closed binding sequence in
`PREPARED_MESH_REMAP_CONTRACT.md`. Empty edge/vertex ancestry never proves absence
of source ownership. Mesher `793a1cab6eb90355d6c004b48757da116949e4bd` reports
51 focused passes, compiled-interface 10 passes / 4 skips, and a synthetic
geometry-limited refinement probe; combined MeshCore, quality, source transfer
and admission/publication remain pending. This is design/source-inspection
evidence, not implemented FEM remapping or owner-runtime acceptance. All current
progress/button-help source hashes and five protected owner hashes match their
retained evidence. No new runtime test, broad/native diagnostic, default or UI
change was made. The complete public proof inputs and independently inspected
staged artifact are prerequisites to a later isolated consumer implementation.
The owner public file changed concurrently with an uncommitted v2 created-UV
extension; preserve it without acceptance. Consumption requirements were pinned
and rechecked against immutable 793a1ca v1 source; exact snapshot/signature
inspection is retained in
`reports/prepared-reference-scope/current-receipt-consumption-793a1ca.json`.

2026-10-04 owner-reported continuation checkpoint (not FEM acceptance): geometry
`e509862bfb7f957d4200d090b05207d24f55ca82` / main
`f03a7e85d49dd3d2366fad60a3237d7c0e99afa2`, wheel SHA256
`2761f6d62611ea62b69f80d6ec47659c4cfa1142d3ac07ed460e54fc191165ed`,
provides distinct CURRENT material UV and exact Fraction partition inputs without
reinterpreting ancestral/source UV. Mesher `8d2684ae03cd507afaa7cd32ae0c238badeb44c9`
reports public `query_prepared_current_component_associations` and fresh binding
validation for a complete linear Plane/Straight Sheet-joint component, exercised
with synthetic boundary-corner T3 data. Unsupported semantic fields remain typed
refusals; source-reference-transfer, solver and publication flags remain false.
Latest mesher local checkpoint `b347d3f5d9b9036026e4a78047d02170dd6d410f` reports
protected global IDs retained through PSLG deduplication/triangulation and detached
T3 output, exact Fraction UV for protected stations, and original-chart UV for
created rows. Owner reports 33 low-level passes / 4 compiled skips. No accepted
native mesh or MeshCore publication exists at this checkpoint. Owner partition,
cumulative work charge, quality, FEM source remap and atomic publication remain
pending. These are owner reports, not independently executed FEM checks. Earlier
missing-receipt statements below/above describe historical checkpoints. Keep
production meshing and protected `structural_preparation.py` untouched; no owner
candidate is installed into the accepted GUI runtime by this note. Progress and
button-help UI work is complete and its evidence remains separate.

2026-10-03 coordinated remap checkpoint: after completing the postprocessing
slice, inspect whether the existing detached source remapper can consume the
mesher's authored-root staging without losing source attachments or confusing
namespaces. The bounded comparison is the existing remapper against canonical
Mesh fields and the owner's explicit current-child/root provenance contracts;
no numerical run or native meshing is authorized by this checkpoint. The
comparison found silent dropping of unmapped IDs and incomplete structural/grid
mapping, so the existing helper is unsuitable for this new route unchanged.
`PREPARED_MESH_REMAP_CONTRACT.md` records the proposed FEM interface and required
complete association receipt. It is not implemented or installed acceptance.
Mesher confirms that receipt and its generating route remain pending, with an
owner station mapping gap for interior edge 25 and missing authenticated vertex
endpoint provenance/generated-only classification. Current child face buckets
alone cannot satisfy the complete association contract. Await that concrete contract
before implementing a binding that would otherwise guess. Existing UI changes,
protected owner work and production meshing remain untouched.
Owner follow-up `fe1ed8128d31cc7ac005bff650b67c83836a0f54` now provides a candidate
internal-station and vertex-preimage contract. FEM checked the packet and exact
wheel hash; no runtime tests were added or repeated for this documentation
checkpoint. Empty vertex ancestry does not exclude source-edge/member/attachment
references. The complete mesher association/ordered-chain receipt remains the
immediate dependency; see the pending contract for exact identities and limits.
Mesher `87ccd57` has since consumed the station/vertex candidate: owner reports
80 focused passes and one installed-location assertion deselected. The geometric
input gap is closed at that owner checkpoint. The full receipt still depends on
exact split-joint Sheet/attachment occurrence provenance; unsupported semantics
remain refused. No FEM runtime or Qt changes result from this status update.
Further mesher scope inspection reports that split joint 1 connects Sheets 1/2
through two attachments on edge 25, with additional cutter faces 5–10 on Sheet 2.
Root-only output would omit that connected Sheet. The pending adapter contract
therefore requires a complete shared-component receipt for this case or typed
refusal; station/partition proof alone cannot authorize its publication.
Owner reports station/vertex merge at `8826ab2e0eec475f7e8fb253a714e066e2187bfa`,
tree-identical to the candidate and retaining the same exact wheel. Generic
original FaceUse handle resolution reaches only its first child; it cannot
prove complete sibling occurrence ancestry. The pending owner receipt must
qualify original root-face/Sheet to all current FaceUses separately from whole
Sheet-joint component closure. No FEM runtime/acceptance claim is added.

2026-10-03 postprocessing tree: user requests left-side expandable result families
and components, including stress, displacement and available strain. Add a
searchable results browser alongside the model tree, direct contour/component
selection, result/frame status and responsive field/frame controls. Consume
existing live field and retained descriptor contracts; never invent missing
components, strain recovery or engineering invariants. Verify representative
live/retained selection, component values, unsupported locations, freshness,
frame changes, lazy catalog access and project replacement in bounded Qt checks
on Windows and WSL. Preserve all concurrent geometry/engineering work.

Implemented a **Results** tab in the left model dock with searchable, expandable
result families and exact components. Live stress fields retain membrane,
bending, shear, top/bottom surface and beam groupings; saved descriptors expose
displacement, stress, available strain, reactions and other carried quantities.
Single-click selects the contour or component table/history, with source, unit,
location and basis details. Saved component names are offered in a selector;
unknown components refuse rather than silently display a norm. Contour legends
identify the selected saved component. Field/frame/unit changes redraw directly,
frames display from one while storage APIs remain zero-based, and controls wrap
in narrow docks. Playback uses the selected quantity's own frames and deferred
draws cannot cross project replacement. Existing dropdowns and tools remain.

Installed candidate `1defe262130f4c5e55f1f6a7d38fee0efa12d12bf383b89ff7b915af1c83d771`:
**28/28 Windows and 28/28 WSL** focused checks, zero skips/failures, including
signed component tables, retained/live source switching, lazy metadata browsing,
search, exact output-scope selection, units, frame limits, playback, replacement
and headless boundaries. Real Qt `windows` / `xcb` software viewers and captured
layouts inspected; this does not establish physical Linux GPU qualification.
Source, installed identities, initial failures and visuals are retained under
`reports/qt/postprocessing-tree`. Added both browser suites to the Qt test profile.
Eight owned files integrated; 25 unrelated dirty files and five protected owner
files remained byte-identical. Local implementation only; no commit or release.
Runner checks: 28 passed in the sandbox; its process-tree cleanup test was denied
`taskkill`. The same failed case passed outside the sandbox (1/1); initial failure
and retry evidence retained. No runner cleanup implementation changed.

2026-10-03 prepared project reference scope: human-authorized coordinated
implementation follows the geometry owner's handoff. Question: can a complete
geometry snapshot establish absence of a later child-local FEM pressure? It
cannot; test the split-plate/left-child counterexample against a whole-root scope,
plus stale and altered project/manifest inputs and exact closure mapping. Add a
read-only, preparation-bound external reference inventory with current child
boundaries and original/current FaceUse/Sheet identities. Unsupported/missing
references refuse. Geometry input is exact PR14 main 6fc64b794e81fa671a4472e2d506a92b595c40d9,
wheel 2d7468767d0cbca26185dcab543912582048d828d435a15fd3c6c6736cb4b553.
No active mesher routing, scientific/native diagnostic replay, default changes
or publication. Preserve Qt sets and all concurrent engineering work.

Implemented candidate `anyfem.prepared_reference_scope`: immutable typed external
reference inventory, explicit empty-container counts, current-child boundaries,
complete owner original/current occurrence documents and exact source namespaces.
Detached preparation requires a witnessed owner extraction before working-model
edits. Validation binds full pure Project/source content and owner preparation,
then recomputes mappings and inventory. Root-only associations refuse child-local
loads; unsupported/missing inputs and reconstructed transient authority refuse.
Public contract: `docs/PREPARED_REFERENCE_SCOPE.md`; portable counterexample:
`examples/prepared_reference_scope.py`. Installed focused tests: **41/41 Windows,
41/41 WSL Linux**, no skips; packaged API bytes verified against both installations.
Separate Read review found pre-capture source/mapping binding and hidden references
in object arrays; both repaired with counterexamples. Review and initial failed
setup evidence retained in `reports/prepared-reference-scope`. Local wheel SHA256:
`ea1a7d2daf0cb269eb8271ae48c8ad35918afded7046a3f0f1089f028cf9def5`.
This is input visibility and freshness, not cell membership or mesh qualification.
No active production mesher routing, scientific runs, release or default change.

2026-10-03 downstream report: ANYmesher consumed the exact candidate wheels in
isolated commit `b5a6f36` and reports **64 focused tests passed**, including the
split-left-pressure root-only refusal. It reports freshness/source namespace and
required edge/vertex retention validation before its separate full gate, with no
FEM API changes requested. This is owner-reported downstream evidence, not an
ANYfem rerun or scientific acceptance. Per-cell current-child partition proof
and detached source-output remapping remain fail-closed; no production route.

2026-10-03 geometry sets / canvas actions: user authorizes persistent named
geometry sets in the model tree with complete resolved-content highlighting, and
a left-click canvas menu with Copy, Move and Properties using vector input.
Reuse public ManualRegion persistence/lineage for sets and ANYgeometry's neutral
transform feature for undoable movement. Keep selection gestures/construction,
project replacement, units and source associations intact. Bound verification to
set round-trips/undo/replacement, transformation identity/attachments/regeneration,
and real Windows/WSL Qt clicks, vector validation and shutdown. Do not modify
the separate prepared-provenance or mesher-owner integration work.

Implemented: visible manual geometry regions appear as named **Sets** with
bounded child rows and complete resolved-content highlighting. Create via the
tree button or Edit / Create geometry set (Ctrl+G), add the current selection
via Edit / Add selection to set, and rename/delete/inspect via the tree menu.
Sets preserve UUIDs, mixed entity kinds, persistence and explicit split lineage.
Plain left entity clicks show Copy / Move / Properties; drag, modifier and
construction gestures keep their existing behavior. View / Actions on canvas
click toggles the popup. Copy and Move use displacement X/Y/Z with project units
and explicit suffixes, one undo item and stale-document validation. Properties
shows owner geometry data and existing section assignments without mutation.

Evidence: `reports/qt/sets-canvas-actions/` retains build, installed runtime
origins, source/wheel identity, JUnit, visual captures and intermediate failures.
The installed candidate passed **53 affected Qt/set checks plus 102 affected
headless checks on each of Windows and WSL**. The latter cover commands, feature
attachments, snapshots, runner boundaries, headless imports and licensing.
Windows OpenGL reports NVIDIA RTX 3090 Ti; WSL XCB uses Mesa llvmpipe. These are
focused frontend checks, not renewed scientific or physical Linux acceptance.
Independent source review found prompt-time stale binding; it is repaired and
covered for create/add/rename, with reviewer closure and counts verification.
The final dialog-width refinement has focused installed Qt/visual rechecks.
No package publication or prepared-mesh owner changes are included in this slice.

Schema-6 consumer integration, 2026-10-02: implementation is now authorized via
the geometry owner's user-approved follow-up. Exact geometry runtime d05cd3a37d884702e043b678808f50d03ed4a27e,
wheel SHA256 54367f21cd661413c43701ce629f90f09d525a87a82e8ccaf267dc68fc8112b8.
Question: do delegated codecs, public face evaluation and lineage already cover
these records, or do trimmed child display / mixed-operand preparation need FEM
changes? Start with supplied authored/prepared fixtures, persistence/recovery/
undo and scene checks, then real Qt picking/highlighting. Adopt only measured
consumer fixes. Current mesher main 615de9b7ac78a1b39c90eb1717e443ab47a17e44 includes
the fail-closed inspection-only safeguard for uncertified quadratic extrusion
fallbacks. Preserve that gate and the unresolved face-11 limit; no quality sweep,
package release or new scientific acceptance claim. Preserve prior evidence and
the existing uncommitted Qt presentation work.

Consumer evidence under `reports/qt/schema6-d05cd3a/`: isolated installed wheels
pass **49 checks on Windows and 49 on WSL**, with **15 focused existing
launcher/licensing/engineering/cancellation regressions per platform**. The 49
cover twelve authored/prepared documents, save/reopen, snapshots, recovery,
reference attachments, undo/redo, failed transactions, exact scene face/edge
inventories, detached shell preparation/lineage, and six visible Qt fixture
families with Automatic startup, representative joint-edge/face click selection,
highlighting, tree synchronization, software fallback and UI save/reopen.
Coincident tangent edges are exercised through the viewer's click cycling; this
is representative interactive picking, not an every-edge picking claim.
`installed-final-{win,linux}.xml`, `regression-{win,linux}.xml` and
`environment-{win,linux}.json` bind measured results and imported package origins.
Windows: Python 3.14 / NVIDIA RTX 3090 Ti; WSL: Python 3.12 / XCB Mesa llvmpipe.
PySide6 6.10.3 and viewer d65f47b remain the tested presentation inputs.

No FEM runtime/codec change was needed for these owner documents. Undo restores
design while reserving allocated IDs and advancing geometry revisions; tests
assert both behaviors. Source geometry remains exact through display and detached
preparation. Intermediate failed tests/builds are retained. Two Windows recovery
directory renames raised transient PermissionError in an intermediate combined
run; both passed a bounded retry and the final full run without code changes.
Their underlying cause is unestablished. A focused independent source review
found no concrete defect in the revised representative contract.

Mesh acceptance remains incomplete: whole plate plus extruded wall was correctly
rejected at a 13.14-degree triangle corner against the unchanged 15-degree limit.
A separate single spline-extruded wall raises ANYmesher's shared-boundary-station
lift mismatch; reproducer `probe-extruded-mesh.py` and its traceback were sent to
the authorized ANYmesher worker. Do not claim arbitrary schema-6 models can be
meshed/solved or that the existing face-11 limit is closed. The owner's subsequent
large-model stage is separate; continue using exact d05cd3a until a verified new
wheel is supplied. Physical Linux GPU acceptance remains deferred.
Owner FYI, 2026-10-02: large-model geometry candidate
`364a9f962657db7a65e097d81b6856326cfd56d7`, wheel SHA256
`79b05a80b18f8f0f33c40830dde0a3b4972effc3116500e5c5e0bcbfadea4167`,
reportedly fixes translated cubic material integration and member splits at
face-decomposition ports without a schema change. Owner full checks are pending;
the mixed-10 linear mesher probe exhausted 120 seconds in overlap audit and was
handed to ANYmesher. This is owner-reported pending evidence, not ANYfem acceptance.
Keep the verified d05cd3a inputs until coordinated acceptance. No new run,
dependency-default update or package release is authorized by this FYI.
Subsequent owner FYI, 2026-10-02: coordinated geometry runtime
`71b394a40a78f3a9d836bb18195e495bd58265be` and mesher
`7e2fded699d055b59ebe5b02d52e6e23255aa556` reportedly pass installed strip-1000
linear, hub-100 linear and small planar quadratic checks, including complete
joint-node checks. Geometry reportedly passes exactly 1000 mixed operands.
Mixed curved automatic meshing still exhausts 120 seconds in surface
preprocessing and is not accepted. Owner `set_prepared_face_corners` reportedly
preserves valid preparation binding; mesher integration remains isolated.
Owner evidence: `C:/Github/ANYgeometry/.worktrees/intersection-continuation/reports/general_intersections/large_connected_capability.json`.
These are owner-reported results, not independently verified ANYfem acceptance.
The ANYfem d05cd3a evidence and existing dependency pins remain unchanged; mains
and releases have not been advanced by this coordination update. Revisit only
with a bounded consumer scope and verified artifacts after the mixed-support gap
is resolved or explicitly excluded from the intended claim.
Owner merge FYI, 2026-10-02: ANYgeometry PR12 is reported merged at main
`cf2688be58a131ef67dacd07dcd62a6eb508e5d7`, with eight development jobs and
1704 kernel tests per Windows/Linux job. ANYmesher PR10 is reported merged at
main `8c939066345cc8347f5ddb9feccef7265f9cbc0c`. Exact wheel identities in the
owner's large-connected capability record are unchanged. Large planar linear
acceptance is owner-reported; mixed curved meshing remains unaccepted and
time-limited. The chart-validation cache follow-on is separate development.
This records supplied coordination evidence without a new ANYfem consumer run,
independent verification of hosted results, pin adoption or package/default change.

Candidate CI now accepts explicit geometry/mesher refs and an opt-in
`schema6_fixtures` input. To reproduce this slice select geometry
`d05cd3a37d884702e043b678808f50d03ed4a27e`, mesher
`615de9b7ac78a1b39c90eb1717e443ab47a17e44` and enable that input.
The geometry owner's public fixture builder supplies the documents; ANYfem does
not reproduce its algorithms. Existing CI dependency defaults and installation
requirements remain unchanged. CI uses editable sources; local installed-wheel
evidence establishes the exact artifact identity. This update is local only;
no hosted run, package release or dependency-default adoption is claimed.

2026-10-02 workspace navigation follow-up: add original vector pictograms as
square workspace shortcuts alongside the top selection controls, plus wrapping
option shortcuts under each workspace's existing main selector. Keep dropdowns
and icon selection synchronized. Icons route through existing controls and
buttons, preserving command validation and model-edit semantics. Verify keyboard/
mouse navigation, dynamic result-field updates, narrow-panel wrapping and real
Windows/WSL rendering; reuse unchanged scientific acceptance evidence.

Delivered 11 top-level square workspace shortcuts and icon grids for every
workspace's main option selector; Scripts mirrors its existing action buttons.
Dropdowns remain available and synchronized. Original SVG pictograms use Qt's
multi-resolution icon rendering, tooltips, accessible names, selected states and
wrapping grids. Dynamic result choices and disabled controls update their icons.
Installed artifact checks: **13 passed on Windows NVIDIA GPU and 13 on WSL
XCB/llvmpipe**, plus **16 headless launcher/boundary/licensing checks**. Evidence:
`reports/qt/integration-pr10/workspace-icons-installed-{win,linux}.xml` and
`workspace-icons-headless.xml`; visual previews in `workspace-icons-preview/`.
Only navigation/presentation changed; no engineering computation or dependency
candidate adoption. Applied to local main without GitHub publication.

2026-10-02 follow-up: the user reported missing Point/Line/Face shortcuts,
Automatic starting black, and feature rows without canvas feedback. This bounded
slice adds synchronized selection actions and visual feature-output highlighting.
Startup diagnosis distinguishes hidden-widget OpenGL probing from rendering
failure after exposure. Verify Automatic's first exposed frames without manual
renderer switching or capture triggering rendering, plus disabled-GPU fallback.
Retain numerical and lifecycle evidence for unchanged workflows.

Implemented Point/Line/Face actions with bidirectional mode synchronization.
Feature rows highlight the live scene outputs using ANYgeometry's public owner
map, separately from load/support assignment selection. Highlight and feature
row selection clear together on Escape or canvas selection. Automatic first
uses a visible software surface, then probes GPU after exposure; replacements
are explicitly shown and pending initialization cannot outlive window shutdown.
The original reported black startup did not reproduce in the isolated Windows
environment; the corrected lifecycle is verified without capture or manual
switches causing the first frame.

Evidence under `reports/qt/integration-pr10/`: initial installed artifact full
Qt suite **167 passed on Windows NVIDIA GPU and 167 on WSL XCB/llvmpipe**
(`selection-auto-full-{win,linux}.xml`). After independent review reconciled
feature row dismissal, the final installed artifact passed **24 affected checks
per platform**, including initial scene retention, disabled GPU, pending shutdown,
selection, replacement and docking (`selection-auto-final-{win,linux}.xml`).
Headless launcher/boundary/licensing checks: **16 passed**; licensing inventory:
18 direct dependencies; diff check passed. Source-only independent delta review
found no remaining concrete defect. Failed intermediate checks/build diagnostics
remain in the evidence directory. Cylinder/plate preview:
`selection-auto-preview/cylinder-highlight.png`. Physical Linux GPU remains
deferred; this fix does not claim that acceptance. These five Qt-owned files are
applied to the local main checkout; GitHub publication is not part of this slice.

ANYfem owns shared workflow services and Qt presentation; ANY3dView owns the
retained viewer. Widgets invoke existing commands/services. Each window has one
WorkbenchController. Qt Widgets provide the central viewport, project/task docks,
job/log panel, menus, shortcuts and status. GPU presentation uses QOpenGLWidget;
the shared viewer supplies a software fallback with explicit failure diagnostics.
PySide6 remains optional for headless use. Launchers are `anyfem`, `anyfem-qt`
and `python run_gui.py` for Qt; `anyfem-tk` and `python run_gui.py --tk` retain Tk.
The user-selected light theme adds task navigation, grouped action/selection
toolbars, blue primary actions, readable form spacing, wrapped guidance and
tabbed Jobs/Messages. Existing layouts restore; View → Reset workspace layout
restores the improved arrangement. Scientific behavior is unchanged.

Default-switch evidence: both complete installed Qt suites pass **163/163** with
zero failures/errors/skips and three existing deprecated Qt test-API warnings.
`polish-default-{win,linux}-qt` retains the evidence; Windows uses physical GPU,
WSL uses XCB/llvmpipe. The focused launcher/licensing/headless set passes 29/29.
Real installed `anyfem` entry-point captures include overview, engineering forms,
results and laptop size. Paired Windows/WSL mesh/solve/save-reopen succeed; a fresh
Windows environment also completes that workflow at device-pixel ratio 1.5.
Images and acceptance records are under `polish-final-visual-{win,linux}` and
`polish-clean-verified-hidpi`. The first capture's automatic-offset beam setup
timed out; the established centerline fixture succeeds. Failure evidence remains.

A fresh GUI installation exposed published ANYmesher 0.5.0 missing Qt meshing
control APIs. Installed `gui` pins tested mesher e21c0fc and viewer d65f47b.
Source `qt`/`tk` extras accept editable siblings, preventing installed-source pins
from replacing them. Actual editable bootstrap dry-run resolves successfully.
Licensing records every extra's exact requirement separately, rejects duplicate
declarations within a scope and retains all 18 unique dependencies. Source and
installed license checks pass. Independent review cleared the bootstrap conflict
and found no new concrete navigation, dependency or scoped-inventory defect.

Starting ANYfem main is `892507a3bcca98b73cbfdbd28f5906c2687774b3`.
The isolated integration branch is `codex/qt-coordinated-integration`; this
delivery is based on `7169b34a129c5de34731a2fa94d5dcb2dba6b411`. The current
slice polishes Qt and makes it the explicit user-authorized default.
Candidate viewer input is exactly `d65f47b264f3e28348a6309eea996db51d8bb901`.
ANYgeometry input is `26e7e3c98ac1a5573e19643d6658d00094bff0bc` (0.4.5).
Published owner main `db2947e02cb4fe243c52b13d6bec262b4f897378` has no runtime
`src/anygeometry` delta from that tested input. Owner publication stays with its
owner; unrelated dirty work is preserved.

Current polished default-Qt installed wheel SHA256:
`baaacbbd831b35746f810446e1b1068c79b286bdc67cf693a5c7788edc4a9def`.
The final disabled-button contrast adjustment was checked through that installed
default launcher at DPR 1.5 (`polish-delivery-hidpi`); the preceding complete
suites cover the same behavioral implementation. `polish-delivery-dist` holds
the delivery wheel/sdist. Earlier reasonable-parity and polish builds remain
historical evidence, not the current artifact.
Both packages' installed origins are asserted under Python `-I`, with pytest
source-path injection disabled. Python/PySide6: Windows 3.14/6.10.3 and WSL Linux
3.12.3/6.10.3. WSL uses XCB/llvmpipe OpenGL and is not physical GPU evidence.

| Current evidence | Windows | WSL Linux | Scope |
| --- | --- | --- | --- |
| Previous ba3e5f4 complete installed Qt suite | 152 passed | 152 passed | Explicit requested GPU backend activation, real windows, no skips/errors/failures |
| Previous ba3e5f4 installed headless contracts | 96 passed | 96 passed | Persistence, binding identity, rollback, hash/cache invalidation, original two repaired CI cases, frontend import boundaries |
| Current installed Definitions checks | 6 passed | 6 passed | Picker controls/backend; built-in/custom units; Cartesian/cylindrical coordinates, invalid basis refusal, undo/redo and save/reopen |
| Final installed changed-slice acceptance | 12 passed | 12 passed | Seven multi-selection load/mass variants, active case and total/per-target policies, UUID-preserving undo/redo, explicit reference precedence; coordinate/forms/backend regressions |
| Wheel/sdist checks | Passed | Same wheel | Safe artifacts, licensing/notice contents; no release |
| Independent changed-slice source review | Findings repaired | Portable code | No remaining concrete defect found; no final full-parity acceptance claim |

Complete logs/JUnit, input snapshots, wheel/sdist and failed attempts are retained
in the primary checkout's ignored `reports/qt/integration-pr10/`. Final evidence
is `raw-scope-final-{win,linux}-{headless,qt}` and `raw-scope-final-summary.json`.
The current focused evidence is `operand-installed-{win,linux}`; wheel/sdist are
in `operand-dist`. The initial no-isolation build lacked setuptools; the declared
isolated build succeeded. No scientific matrix was repeated for this widget edit.
Latest Definitions evidence is `definitions-final-win` and
`definitions-final-linux-retry`, using `definitions-dist`. The first Linux run
aborted at QApplication initialization before tests; a separate XCB probe
succeeded and the one retry passed. The abort remains in
`definitions-final-linux.log`; its root cause is not established.
Final acceptance logs/JUnit are `reasonable-parity-final-{win,linux}`, with
`reasonable-parity-dist` wheel/sdist. Initial load tests assumed a default case
existed; that fixture assumption failed five cases and was corrected without
changing production behavior (`load-batch-source-win`). Source nine-case regression
and final installed twelve-case runs pass. The central editor change extracts
option parsing without changing its existing command/query path.
Older `raw-delete-*` builds/runs are intermediate evidence. The failed Qt delete
run exposed revision serialization outside rollback; it is not superseded by
pretending that deletion-save is supported.

Current repairs bind raw Imperfection/owner Refinement authored references to
exact singleton feature-output regions. Optional project data persists that map;
staging and undo retain it; orphan keys are pruned before replay/persistence.
Only these raw record types consume it: loads/supports/masses keep their own
scopes even when they share the authored cache. Canonical binding overrides raw
lineage during deletion. Detach/undo restores the exact binding, and unrelated
inactive/orphan raw intent cannot block another deletion. Binding-only physical
changes participate in model hashing and invalidate mesh cache; label-only
changes remain presentation edits. Immutable snapshots retain their submitted
scope. Numerical node order, values and owner models are preserved.

Same-window reopen reuses its owned lock; failed reopen preserves the current
project and lock. Revision serialization now occurs inside the transaction's
rollback boundary before revision/cache publication. Owner refusal restores the
project, history, selection and caches; the window remains usable and saveable.
Nested transactions retain one outer revision. Review found and repaired stale
map deletion, hash omission and canonical-alias precedence defects.

## Workflow parity and acceptance ledger

Implemented controls and passing representative cases are evidence of that scope,
not qualification of arbitrary geometry or numerical models.
Unsupported coordinate/material transformations and constitutive models retain
their owner diagnostics in both frontends; GUI coverage cannot broaden them.
The original Tk inventory remains `reports/qt/tk-task-surface.txt`. Complete historical counts,
failures and earlier feature-by-feature investigations are preserved in the
archive linked below.

| Requirement | Current evidence | Open requirement |
| --- | --- | --- |
| Shared open/save, locks, recovery, undo/redo, replacement/shutdown | Shared services; installed lifecycle and revision-refusal checks | Final complete-workbench review |
| Geometry, generators, sketches, materials/sections, loads/constraints | Typed controls, ID-preserving edits, construction selection, dependent-feature and raw suppression roundtrips | Edited sketch support suppression and selected feature-output deletion persistence (owner defects below) |
| Mesh controls, preview/recovery, cancellation | Four routes, inspection/admission diagnostics, commit/discard, stale/cancel/replace checks | Integrated owner acceptance and existing scientific limits |
| Analysis controls and solve lifecycle | Nine public analyses; snapshot/case routing, cancel/edit/replace, retained stale results | Free-modal scientific failure and complete scientific acceptance |
| Results and visualization | Stored/live fields, tables/histories/probes, display scale, FPS, clipping, PNG/GIF/CSV/Markdown/HTML, sidecar identity and provenance | Final review of the complete supported surface and physical Linux GPU acceptance |
| Imports/exports, scripting/navigation | SESAM/CalculiX and neutral FEM/SIF/INP/DAT/FRD workflows, unknown-preserving export, async failure/replacement, scripting/recording/palette/docks/shortcuts | Final integrated acceptance; bounded preview tests are not scalability qualification |
| Headless/installed compatibility, licensing | Import-boundary checks, installed wheel/sdist, unchanged 18-dependency MPL inventory | Clean final switched artifact once acceptance permits switching |
| Default and retirement | Temporary explicit dual launchers | Resolve every gate, independent final review, then switch defaults and remove Tk/ANYtk3D from ANYfem; update guidance/notices/CI |

## Concrete unresolved failures

1. **ANYgeometry suppression after parent edit.** Owner-only public API script
   creates a plate and extruded dependent sketch, edits plate length/origin, then
   suppresses the plate. Regeneration refuses with unresolved historical edge/
   vertex descendants. Undo/redo and save/reopen are not required to trigger it.
   Unedited suppression succeeds; all edited variants fail atomically on both
   installed platforms. The Qt combined failure is retained. No consumer store
   patch, lineage deletion or proximity workaround is permitted.
2. **ANYgeometry selected output deletion persistence.** After explicitly removing
   a singleton Sheet/empty Part and its generated face using public APIs, owner
   serialization refuses a missing active feature output. The owner-only script
   reproduces this too. ANYfem now rolls this refusal back safely; it does not
   claim a passing deletion-save workflow. The persistent public contract for
   selected output deletion, including compound features, is still required.
3. **Scientific free-modal outcome.** Completed full main run `36880776026` runs
   1212 tests per cell with 130 skips. Seven cells fail the two attachment cases
   repaired by candidate `8abf590`; Linux Python 3.13 additionally reports four
   rigid-body modes instead of the unchanged required six. Runtime there is
   Python 3.13.15, NumPy 2.5.3, SciPy 1.18.1, solver input
   `5ca31de9be3ca3ffa70b09612a5d98c0ef212a5b`. All eight separate verification
   reports pass 21/21; that inventory does not subsume the failed integration
   test. No tolerance or assertion has been weakened.

Owner-only reproduction and inputs:
`reports/qt/integration-pr10/reproduce_sketch_suppression.py`,
`owner-feature-lifecycle-final-{win,linux}/`. Forwardable owner requests are
`ANYgeometry-owner-request.md` and `ANYsolver-modal-owner-request.md` in the same
ignored evidence directory. Nothing has been messaged to an owner task.

## Acceptance and delivery constraints

Reasonable parity means the Qt workbench offers usable routes for the core create/
open/edit/assign/mesh/solve/inspect/save workflow and the principal existing tools,
with compatible persistence, validated command entry, undo/redo and job lifecycle.
The prior complete installed suites, current focused window checks, source audits
and independent reviews establish that standard. The last review found no concrete
load-batch defect and accepted this conclusion conditional on both installed runs,
which now pass. Alternative Qt forms and reusable regions are acceptable; exact Tk
layout/default replication and every owner edge case are not completion criteria.
Stop this parity task here. The original exhaustive gates above remain recorded
for future qualification/default-switch decisions, not as blockers to this goal.

Current bounded surface audit asks whether any explicitly invoked Tk command
lacks a usable Qt or shared-workflow route, loses a required field, or has only
syntactic exposure. Compare the source inventories, trace differences to actual
controls and validate any concrete gap through a real window. Invocation presence
alone cannot establish interaction or scientific parity. The source audit found
shared/Qt routes for all 54 commands explicitly referenced by Tk; its apparent
CompositeCommand difference uses the shared batch wrapper. A concrete picker gap
is repaired: hidden regions are excluded, names/domain/kind/mesh labels refresh,
and selection follows stable IDs through list changes. Same-ID label changes
update existing Qt items. Real-window checks include hiding a selected operand
and creating the resulting union after restoring explicit selection.

The next bounded Definitions audit compares coordinate/unit/output fields and
their validation with Tk. Exercise actual custom-unit and coordinate forms,
undo/redo and save/reopen; owner basis validation and SI storage must be preserved.
This is frontend evidence, not new scientific qualification authority.
The next engineering slice compares multi-selection loads with Tk: point forces/
moments, pressure, line load, traction and mass must use one atomic batch, retain
active case/coordinates, and divide total-distributed point force/mass across
targets exactly as Tk does. Explicit references and single-target paths remain
under their existing command contracts; no solver formulation changes are needed.
Independent review found built-in unit fields editable with values silently
ignored by Apply. The repaired controls synchronize displayed built-in symbols
and disable custom fields until Custom is selected, as Tk does. Unrelated refresh
preserves unapplied Custom edits. The regression first failed twice on the old
installed wheel (`definitions-before-win`), then passed for both coordinate kinds.
Exact custom units and right-handed bases survive save/reopen; invalid parallel
directions leave the revision unchanged. Delta source review found no new defect.

The previous candidate `36910091247` on `1f4d71a` completed successfully in all four
Windows/Linux Python 3.11/3.14 Qt cells. The already dispatched `36911363248` on
`b403d41` remains live at handoff; it does not include the final load-batch slice.
Final load-batch acceptance uses the paired installed checks above. Exact delivery
commit and latest run handle are recorded in the retained
`reports/qt/integration-pr10/shared-feature-checkpoint.json`. Inspect that
same handle; never restart a live job because an observation timed out. Do not
repeat the unchanged expensive scientific matrix for each small frontend edit.

For future full qualification, obtain owner corrections/public API guidance for the two geometry cases and
adjudicate the modal failure under existing owner authorities. Retest exact
published inputs with the retained reproducers, installed workflows and applicable
scientific gates. Independently review complete lifecycle/persistence/viewer
integration and the supported surface for any future full qualification claim.

The user explicitly deferred physical Linux GPU acceptance because only WSL is
available. Preserve it as an open gate; WSL cannot close it. The later explicit
request authorizes the Qt default switch based on reasonable parity and installed
UI evidence. Tk removal, package release, force push and branch-protection bypass
remain outside this delivery. Owner working-tree changes remain preserved.

## Preserved history

[Conversion history through d47d42a](archive/QT_CONVERSION_HISTORY_2026-10-01.md)
is a frozen archive of the previous 1195-line living record. Its Git-normalized
blob exactly matches `d47d42a:docs/QT_CONVERSION.md`:
`b92b1866926715115acd3ac2ef34284dc939bca7`. Working-file SHA256 before checkout
line-ending conversion: `a0b29b688b8af97c43929934ca2e7a869784c55889eb4392c768249e65d5a428`.
It retains every superseded result, failed experiment, review and procedural
transition. Historical proposals are evidence, not renewed execution authority.
This file is the single current conversion note.
