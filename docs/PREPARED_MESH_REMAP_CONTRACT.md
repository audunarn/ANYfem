# Detached prepared mesh remap — isolated candidate

`anyfem.prepared_mesh_remap` now implements a detached, headless candidate.
It is not wired into production meshing, the UI or solver admission. The
unchanged reopened-child test passes with the corrected owner candidates below.
This remains a bounded source-binding candidate, not complete engineering mesh,
solver or publication acceptance.

The candidate FEM-owned entry point is
`remap_prepared_mesh_to_project(project, prepared_geometry, mesh, manifest, *,
closure, association_namespace="prepared_current", association_receipt=...,
association_component=..., association_registry=..., cell_current_faces=...)`.
It returns `DetachedPreparedMeshRemap`: a source-bound Mesh plus a separate
detached CURRENT Mesh/receipt, association ledger, exact Project reference
bindings, FaceUse occurrence/orientation bindings, complete Mesh field inventory,
original manifest and actual process-registered Project closure. Physical IDs,
XYZ, connectivity and unremapped physical values are compared exactly. Input
Project and staged Mesh are not edited. Array storage is detached; preserving
array writeability flags or object identity is not the contract.

`source_reference_transfer_qualified` describes only the bindings made during
that successful invocation. It is not durable execution authority: subsequent
use must revalidate the original live Project, closure, prepared geometry and
complete mesher proof context. Solver and publication flags remain false. The
owner CURRENT receipt retains its own false source-transfer flag. Consumers must
not activate a production route from this candidate or its dataclass flags.

Headless preassembly discriminator: the reopened-child fixture with explicit
S355 material, 10 mm sections and face support retains all 26 triangles and six
child-pressure cells on Windows and WSL. The existing public `build_fe_model`
refuses the absent qualified-S3 production authority record. Source geometry,
Project manifest and both meshes remain unchanged; Qt/Tk are not imported.
Evidence is `reports/prepared-mesh-remap/cd66e3b/preassembly-{windows,linux}.json`.
No solver execution or new admission is claimed. Owner physical-normal and
production/quality/repair qualification in the source namespace is required
before extending this candidate into FE assembly or atomic workflow publication.
Nonempty structural preparation remains unsupported by this remapper; a future
extension must validate that separate owner proof rather than fabricate it.

## Current candidate and scope — 2026-10-04

Exact dependencies are ANYgeometry `972d70a616de4f95b1c0f5ff79386f9725525595`,
wheel SHA256 `97c717ef2ada2508408290a002cf5fa5839dd009dbf7c975fd3ad1d2dd275dbc`,
and ANYmesher `cd66e3ba5713192c472c00caf326f2cc9425b938`. Geometry merged main
`1ef714633cc4beab4d9b3fe0d824ba1704efb437` independently has the same local Git
tree as the tested candidate: `e159f043fdbb2048d5b4b4fa13bc0d1c70b9051a`.
Hosted/main results in the owner's delivery packet remain owner-reported here.
Both platform mesher wheels are built from immutable source archive SHA256
`dfd48c6f83ce14a156fd59d381e72a5d099941c9d6a0ad733d9065fe39b09110`.
Installed geometry matches all 91 packaged files; installed mesher matches its
wheel and all 148 packaged source files. Effective paths/versions and wheel
digests are retained under `reports/prepared-mesh-remap/cd66e3b`.

The unchanged child test passes on Windows and WSL Linux: all 26 T3 cells stay
present and child pressure binds only its exact child cells, not the whole Sheet.
The corrected mesher validates each joint against exact current owner Face/Sheet
incidence, separately from whole-component material coverage. Six installed
mesher incidence guards pass per platform. No attachment, scientific tolerance,
cell ID, coordinate or connectivity is pruned or relaxed to obtain a pass.

Independent source review found and reproduced missing instance-field refusal,
Project changes in late owner callbacks, late copy-hook Project mutation, and
detached output mutation during final callbacks. All raw reports and five failed
regression cases are retained. The repaired order is: build detached data and
artifact; run final owner callbacks; check complete input/output schemas,
snapshots and coordinate storage separation; revalidate the Project; return the
prebuilt artifact. No payload inspection, copy or supplied callback runs after
that last Project check. Final independent source review found no further
consequential defect in this bounded witnessed Plane/Straight scope; it performed
no tests and does not supply independent executed/scientific acceptance.

Final checks pass: seven changed success/freshness/cancellation/import cases on
Windows plus five reviewed refusal regressions; all twelve run together on WSL
Linux. Six unchanged preflight cases per platform reuse their affected-run
passes, as do 27 reference-scope and six installed owner incidence tests per
platform. Exact counts/commands/runtime identities are retained in
`reports/prepared-mesh-remap/cd66e3b/implementation-evidence.json`. Evidence reuse
follows unchanged dependency identity and unchanged terminating preflight paths;
no known failure is hidden by the selected final runs. Original positive file SHA256 remains
`cec74082bbc6b32d46c79eeefddb743b152549fc192350d4a2351ea0e8aaf705`.

Remaining limits: supported complete linear Plane/Straight owner receipt only;
literal unchanged full-range EXACT joint relations only; general property,
parameter and arbitrary attachment semantics remain unqualified. Nonempty
unsupported Mesh fields, unknown instance fields, missing scope or stale context
refuse. The installed-owner/source-FEM checks do not establish a clean installed
FEM artifact or active workflow integration. No solver admission, production
caller, atomic publication, native budget replay, default change or release.
The optional Linux mesher extension build lacks Python.h, so these Linux proofs
use Python code; no compiled/native meshing acceptance is claimed.

## Retained earlier candidates and failures — 2026-10-04

The initial isolated runtime used exact ANYgeometry e509862 / main f03a7e8 wheel SHA256
`2761f6d62611ea62b69f80d6ec47659c4cfa1142d3ac07ed460e54fc191165ed`
and immutable ANYmesher `969c106b7ed94c60890b5a64b0dba9ee6b21a7c4`.
The earlier 6cff source snapshot and failed evidence remain retained. The owner
portable fixture is copied byte-for-byte from commit
`0d47063b2351984e2597a952a8ae9cdedc7d90b4`,
`tools/authored_pair_fixture.py`, into `tests/prepared_pair_owner_fixture.py`
(SHA256 `f660b83cb438bd960005e3c24b70cf725873aa75f74480f1b00bf9cfef3b82b6`).
It retains ANYmesher's MPL-2.0 licensing; ANYfem itself uses GPL-3.0-only. Its private
builder helpers construct test inputs only; the FEM adapter consumes public owner
resolution and the public mesher validator, not those private algorithms.

The source factory captures a real Project extraction before preparing only its
isolated working geometry. Tests cover detached data, all association accounting,
exact loaded-face membership, unique original vertices, occurrence orientations,
ordered chains that differ from sorted node IDs, stale/forged context, unsupported
thickness, cancellation, final Project freshness and GUI-free imports. The
corrected size2 fixture retains all 26 T3 cells; its thin owner S3 shape intake
passes. The 20-T3 v2 control retains exact created ORIGINAL-chart UV; it is not
solver-admitted. The 16-T3 legacy14 owner fixture still fails S3 shape intake and
must not be admitted by changing tolerances.

Windows and WSL Linux source checks: **12 passed, 1 deliberately deselected on
each platform**, recorded in `qualified-source-fixed-windows.xml` and
`qualified-source-linux.xml` under `reports/prepared-mesh-remap`. Exact installed
owner wheels, source hashes and effective runtimes are recorded in
`implementation-evidence.json`. This is headless source validation, not physical
Linux GPU or installed FEM artifact acceptance. The deselected
positive acceptance test remains present and failing in the separately retained
`child-source-windows.xml`; it is not skipped, xfailed or weakened. A preceding
orientation check incorrectly compared serialized words with numeric enums;
its four failed tests are retained in `qualified-source-windows.xml`. The repair
reads live owner orientation enums and the subsequent 12 checks pass.

The reopened-child experiment loads plain design data, then captures a NEW
witnessed source closure before NEW preparation; no old receipt is imported as
authority. Before mesher or FEM binding can run,
`query_prepared_sheet_joint_component(work, current_joint_edge)` refuses with
`prepared Sheet joint component touches unsupported/uncontained Attachments`.
The small geometry-only reproducer and source/before/after packet are
`reports/prepared-mesh-remap/repro_child_owner_scope.py` and
`child-owner-scope-repro.json` (eight roots, current joint edge 17). The owner must
explain the supported scope or provide the missing qualified occurrence/component
proof. No attachment is deleted to obtain a pass. Independent review and active
route/installed FEM artifact acceptance remain pending.

Owner follow-up, 2026-10-04 (reported, not independently accepted): complete Sheet
extraction omitted the original Sheet-junction declaration. Repreparation then
added eight redundant vertex-on-face links to the extracted design. The owner
reports correcting discovery without pruning attachments, and limiting the new
component proof to unchanged original full-range Sheet-joint links/declarations
with identical payload, carrier edge and endpoints. General parameter/property
semantics remain false. The reported 74 source passes do not replace FEM's failed
evidence. Independent owner review and exact installed candidate delivery are
pending; the positive child test remains unchanged for the next exact-wheel run.

Delivered owner fix 972d70a616de4f95b1c0f5ff79386f9725525595, wheel SHA256
`97c717ef2ada2508408290a002cf5fa5839dd009dbf7c975fd3ad1d2dd275dbc`, removes
that geometry refusal in the unchanged positive test. It now fails before FEM
binding in ANYmesher 969c106's public CURRENT proof: `authored component joint
lacks shell incidence from both roots`. The mesher requires each joint segment's
incident roots to equal the whole component's eight roots. A separate geometry-
only discriminator derives owner boundary incidence [1, 2, 4, 7] for WORK edge 17,
mapped from Project edge 25, while component roots are 1..8. Complete Sheet material
coverage and per-joint incidence are separate scopes; neither is relaxed here.
The owner proof retains two unchanged joint attachments and one declaration in
fresh WORK namespace, with semantic/publication flags still false.

Affected reference/remapper source tests: **39 passed per Windows and WSL Linux,
one positive child acceptance deliberately deselected**, with XML and exact-wheel
runtime evidence under `reports/prepared-mesh-remap/972d70a`. New failure evidence
is `child-windows.xml`; the original geometry failure is preserved separately.
The owner coordinator reports a mesher correction in progress. No repeat of that
known failing input is scheduled until a coherent mesher candidate arrives. No
FEM source or positive test is changed by this verification follow-up.

The implementation resolves the COMPLETE witnessed original inventory for each
supported entity kind and compares it with the source-to-work map. An empty
inverse for a current edge or vertex proves absence of an original ancestor of
that kind only. It never proves absence of all application references: every
manifest reference is separately bound, and the exact current-only association
is retained in CURRENT provenance and the ledger. Source-bearing edges without
an exact owner-qualified boundary traversal refuse. Sorting is used only for
membership sets, never to infer a chain or a UV transform.

The historical checkpoints below retain their original pending statements;
the candidate evidence above supersedes them only within this narrow scope.

## Inputs and namespaces

- `closure` is the witnessed `PreparedProjectClosure` captured before preparation.
  The full Project-reference manifest validates immediately before and after
  binding. A reconstructed closure, stale source, changed load/support/assignment,
  or changed owner preparation refuses.
- Mesh identity/revision names the current prepared model. Every geometry key in
  the association copy names an active **current prepared** entity. Original work
  root IDs and source Project IDs are different namespaces, even when their
  integer values coincide. Root-only maps refuse.
- The mesher provides its qualified complete cell-to-current-child membership
  and makes current child buckets without renumbering cells. Original authored
  root, FaceUse, Sheet and orientation provenance accompanies that copy as a
  separate validated record. A representative child never stands for a root.
- Source ownership comes only from the witnessed source-to-initial-work map and
  public owner replacement resolution. `Resolution.resolved` is sorted by
  handle identity; it is **not** geometric or parametric traversal order.
  Proximity, centroid and inferred parameter mappings are prohibited.
- Generic FaceUse handle resolution is not a complete sibling-ancestry proof:
  the owner reports that an original FaceUse currently resolves only its first
  child. Complete occurrence binding requires an owner-qualified relation from
  `(original root face, original Sheet)` to all current FaceUses. Do not infer
  the omitted siblings from face membership or copy one occurrence's metadata.

## Required complete association receipt

The mesher must enumerate the exact staged association inventory and distinguish
each source-bearing association from prepared-only topology. Missing or
ambiguous ownership refuses; no key is silently dropped or widened. A generated
interior edge may have no source edge: that fact needs explicit provenance and
defined handling, not a fabricated inverse map.

The inventory includes `elements_of_face`, `elements_of_edge`,
`elements_of_sheet`, `elements_of_member`, `nodes_of_edge`,
`offset_nodes_of_edge`, `nodes_of_member`, `node_of_vertex`, `grid_of_face`,
`block_grids_of_face`, `thickness_of_face` and seeding divisions/classes, plus
every geometry-bearing preparation/diagnostic record. FaceUse occurrence
provenance is separate because canonical Mesh has no FaceUse association bucket.
Unknown records or unsupported structural scopes refuse explicitly.

Component completeness is a separate requirement from authored-root cell
coverage. Where owner structural incidence connects other Sheets, a root-only
association receipt cannot authorize publishing that shared component. Require
complete shared-component meshing/association evidence or typed refusal; do not
remove the additional incidence to make a single source face publishable.

Ordered edge/member chains require an exact owner-backed order/orientation and
endpoint incidence receipt. Sorted handles or node IDs cannot establish chain
order. Offset chains preserve their distinction from line-load chains. Child
grids remain separate blocks when a source face has no single honest grid.
Conflicting thickness, seeding or occurrence data refuses rather than using the
last value. Required manifest edge/vertex constraints and child-local references
must remain bound to the actual staged nodes/cells.

## Inspected public receipt consumption — 2026-10-04

Bounded source inspection found public
`PreparedCurrentAssociationReceipt` schema
`anymesher.prepared-current-associations-v1` and
`validate_prepared_current_component_associations(geometry, receipt, component,
mesh, registry, cell_current_faces, *, cancellation_check=None)`.
The validator reconstructs the complete receipt through owner cell/partition and
vertex bindings and compares its contents; `mesh_digest` alone is insufficient.
The proposed FEM interface must therefore require the exact live component,
registry and cell-to-current-face proof inputs, not just a detached receipt.
This inspection is pinned to immutable mesher commit `793a1ca`, public module
Git blob `ceef05e82d64931e05dee0b7166766327c6e0c81`. The live owner file changed
during inspection with an uncommitted v2 created-material-UV extension; it was
preserved and is not accepted here. The v1 signature/schema above were rechecked
from an exact retained `git show` snapshot. This is not an executed consumer or
installed-owner acceptance. Future created-node consumption must bind its exact
qualified owner UV provenance and versioned receipt; v1 does not gain that
capability from private refinement results or concurrent edits.

The binding sequence is:

1. Revalidate the witnessed Project manifest, the explicit prepared namespace and
   the complete mesher receipt with all original proof inputs. Reject unknown
   schema/fields, stale or forged contents, or unsupported/nonempty associations.
2. Build source ownership exclusively from the manifest's witnessed
   `source_to_prepared_ids`, owner replacement proof and occurrence correspondence.
   A whole-root face binds all proved current children; a child-local scope binds
   only its exact children. Preserve original FaceUse/Sheet occurrences separately.
3. Use `source_boundary_chains` for source exterior edges with their recorded
   traversal and orientation. Use exact current vertex/node proof plus original
   vertex preimages where unique source ownership is proved. Empty
   `exterior_source_edge_ancestry` or `current_vertex_preimages` is not a
   generated-only/no-source-reference proof; refuse unresolved ownership rather
   than dropping an association or guessing from position, UV or sorted IDs.
4. Account for every manifest reference and staged association, copy only after
   all supported source scopes bind, and revalidate both inputs after copying.
   Preserve node/cell IDs, coordinates, connectivity, loads, properties and source
   inputs exactly. Unsupported member/offset/grid/seeding/thickness/activity/
   diagnostic/automatic-connection scopes remain explicit refusals.
5. Keep CURRENT association, application source-reference transfer, quality/
   conformity, solver admission and publication qualification distinct. A detached
   synthetic binding must not set solver/publication flags or activate a route.

Mesher checkpoint `793a1cab6eb90355d6c004b48757da116949e4bd` reportedly adds
protected-ID seed refinement, original remaining-work charging, and exact owner
child/root partition proof with Fraction created-node UV. Owner reports 51
focused passes and compiled-interface 10 passes / 4 skips. The synthetic target-2
probe used 2 insertions / 16 operations but was `geometry_limited`: no quality,
native admission, publication or source-transfer acceptance. Combining both root
outputs into a staged MeshCore with shared IDs/chains, current associations and
quality/conformity remains the next mesher slice. Geometry inputs remain
`e509862bfb7f957d4200d090b05207d24f55ca82` / main
`f03a7e85d49dd3d2366fad60a3237d7c0e99afa2`. These run counts and probe outcomes
are owner-reported; ANYfem ran no native, numerical or scientific diagnostic.
Qt progress/button-help source hashes and protected owner work were checked and
remain unchanged. Earlier pending-receipt statements retain their historical
meaning; the live validation context above supersedes the unspecified input.

## Current owner checkpoint and next slice

ANYgeometry authored-child proof is merged at
`48f857ea377eb816505b8b20e6d2cc97e9385178`. The exact straight-planar complete
partition candidate is `be2dd134887e2d245c194e7011124c230aea4d1e`, with owner
packet `C:/Github/ANYgeometry/reports/authored-partition-coverage-be2dd13/handoff.json`
and wheel SHA256
`94c83f919852210ee22c8f3f72e78df47f181eeaca8b7f30e0defad713914d19`.
These identities were reported by the owner; this checkpoint does not add a
consumer installed-artifact acceptance claim for that candidate.

Mesher staging at `bbd5c8ebd11cdc4f144e9dc175549e42c9597567` retains private
`AuthoredRootAssociations` and `AuthoredChildMembershipEvidence` separately.
The owner confirms there is no generating route or complete public source
edge/vertex/member receipt yet; structural member/attachment/junction scopes
remain refused. The initial physical split lacked an owner station receipt for
interior edge 25. Equal integer IDs, XYZ or absent vertex ancestry cannot establish
complete source attachment provenance. Converting the root-keyed staged Mesh to
current face buckets alone cannot constitute a complete association receipt.
FEM will not reinterpret those private records as publication authority or invoke
native root meshing to fill the gap.

Mesher reports clean local-only checkpoint `7737605`, with 79 focused passes and
one wheel-location assertion deselected. This is owner-reported evidence, not
FEM verification or installed-artifact acceptance. No public complete receipt
has been provided at this checkpoint.

Owner station/vertex follow-up: candidate
`fe1ed8128d31cc7ac005bff650b67c83836a0f54`, packet
`C:/Github/ANYgeometry/reports/authored-internal-stations-fe1ed81/handoff.json`,
wheel SHA256
`17b6d6450a1731345ebc47e166508194ec97543a2bd08fe36bb6bc63fbbb978c`
(FEM checked the packet and wheel hash). Its public APIs are
`query_prepared_authored_internal_stations`,
`validate_prepared_authored_internal_station_coordinates`,
`query_prepared_vertex_preimages` and
`validate_prepared_vertex_preimages_binding`. The station contract supplies
stored direction/endpoints, exact original UV/XYZ, current XYZ and paired child
incidence for authenticated straight internal edges on an exact original Plane.
Vertex preimages enumerate original vertex ancestry; an empty preimage proves
only absence of an original **vertex** ancestor. Source-edge, member, attachment
and feature references still require the full FEM inventory and mesher receipt.
It is not blanket generated-only classification or mesh publication permission.

The owner reports 71 installed passes and 89 Git-identical source files; PR17
was pending at handoff. Complete planar partition proof is now on geometry main
`e23cc791ddd26390eb5c01fb08f7e480552e5fe3` (owner reports 8/8 CI checks).
These are owner-reported test/CI results, not a FEM consumer test claim. The
remaining immediate dependency is the mesher's complete public association and
ordered-chain receipt against these owner APIs.

Mesher follow-up `87ccd57` (parent `7737605`) consumes the exact `fe1ed81` wheel
and owner-validates registered exterior/interior station chains, both XYZ
representations, ordered original UV, endpoints/incidence and vertex ancestry.
The owner reports 80 focused passes with one installed-site-packages assertion
deselected for direct exact-wheel import. This closes the geometric station and
vertex-ancestry input gap, not the complete association receipt. Split joint 25
has `sheet_joint` and `sheet_on_joint` attachment semantics which the current
mesher scope binding refuses; exact structural occurrence/reference provenance
has been requested from ANYgeometry. No public receipt, production route or
FEM consumer runtime acceptance is claimed by this update.

Additional owner-reported split-fixture scope: current junction 1 is a
`sheet_joint`; two `sheet_on_joint` attachments target current edge 25 from
Sheets 1 and 2. Sheet 2 FaceUses cover current cutter faces 5–10, while original
junction/attachment inventories were empty. A root-only CURRENT receipt would
omit the connected second Sheet. Mesher preflight already refuses this case.
The station proof establishes geometric correspondence, not complete shared
component semantics; the future FEM adapter must retain this refusal unless a
complete shared-component mesh and association receipt is supplied.

Geometry merge follow-up: station/vertex candidate is now main
`8826ab2e0eec475f7e8fb253a714e066e2187bfa`, owner-reported tree-identical to
`fe1ed81`; the exact wheel hash above is unchanged. Owner reports 8/8 CI checks
and 2114 kernel passes per platform. These remain owner-reported results, not a
FEM consumer execution claim. The next owner receipt will close whole Sheet-joint
components and separately qualify original root-face/Sheet to current FaceUse
occurrences. Complete CURRENT associations and detached remapping remain pending.

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

Next: independently inspect the exact public receipt and its scoped proofs, then
implement the isolated FEM adapter with synthetic namespace, child-scope, ordering, freshness,
immutability and refusal checks. Keep `structural_preparation.py` and the active
production path untouched. The adapter binds associations; independent owner
coverage, conformity, quality and scientific acceptance remain separate gates.
