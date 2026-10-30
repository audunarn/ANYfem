# Geometry diagnosis, reporting and conservative cleanup

Open **Edit → Review and clean geometry…** and choose **Diagnose model**. The
default diagnosis combines ANYgeometry's full strict audit with quality,
connectivity, near-gap, feature-intent and project-attachment checks. Diagnosis
does not change the model. ANYgeometry owns geometric truth; ANYfem owns project
definitions, document state and the desktop workflow.

## Read the report

Findings include severity, stable entity references, measurements, tolerances,
recommended action and original owner classifications. Select a finding to locate
its geometry. The table is paginated; **Export full report…** writes every finding
and the complete owner audit to JSON or readable Markdown/text, irrespective of
the visible page. **Diagnosis coverage** lists each check's status. Reports retain
the source model/revision, document snapshot, options, entity counts, effective
execution route, candidate counts and phase timings.

**Complete diagnosis** means the declared checks finished with supported,
accounted-for classifications. It does not mean defect-free. **Clean** is a
separate result; warnings/errors/blockers and incomplete checks prevent it.
Unsupported input, unverified classification, checker failure, cancellation and
stale source are explicit non-success states. A cancelled or failed desktop run
can export its status report; unchecked categories are never passes. The report
is diagnostic evidence, not solver, meshing or geometry qualification.

The desktop captures detached input and performs diagnosis in a cancellable,
owned process tree. Editing or replacing the document during diagnosis discards
its stale result. Cancellation and closing retire the owned workers. Export runs
in the background and publishes a complete file atomically; an export failure or
cancellation before publication leaves an existing file intact. An exported
historical report says whether the current document still matches its snapshot.

## Checks and supported domain

- Quality: short straight edges, small planar faces, thin/sliver compactness,
  exact duplicate point/straight-edge/polygon boundaries, nearby coordinates,
  isolated points, unowned edges and topologically disconnected regions.
- Strict owner audit: topology and structural ownership, manifold/orientation,
  intersections, overlaps, crossings, T-junctions, coincidence, attachment and
  junction consistency, orphan control geometry and lineage/classification issues.
- Near gaps: spatially filtered point-to-straight-edge, point-to-supported-planar
  face and finite straight-edge-pair minimum distances. Planar region separation
  includes vertex/face and boundary-edge witnesses. Proximity does not prove
  intended connection. Curved/nonplanar near-gap domains remain explicitly
  unsupported rather than being treated as clear.
- Feature intent: persisted feature definitions, materialization status and
  unresolved inputs/outputs. Feature-driven models are diagnosed even though the
  conservative repair path below does not edit their history.
- Project attachments: stable anchors and resolved manual/query/boolean regions,
  including missing geometry and unresolved regions.

Quality requires straight polygon boundaries and planar supports (including
boundary-derived Coons supports proven planar). Curved/nonplanar metrics remain
explicit coverage limitations even when the strict owner audit can classify a
particular curved interaction. General CAD healing is not implied.

Quality thresholds are positive finite SI values: `short_edge` defaults to
0.001 m, `near_vertex` to 0.00001 m and `small_face` to 0.000001 m².
`sliver_quality` defaults to 0.01 and lies in `(0, 1]`. Comparisons are inclusive.
Compactness is `4*pi*area/perimeter**2`, including hole perimeter; an intentionally
slender face may warrant a finding. Thresholds must suit the model scale.
Length fields accept project display units and explicit suffixes such as `0.1 mm`;
the area field is explicitly in square metres.

The SI quality and near-gap checks require owner coordinates in metres and an
absent or exact identity document transform. Other coordinate configurations
produce an incomplete diagnostic report; the strict owner audit still runs in
its source coordinate frame. Raw owner measurements retain their source units
and provenance. They are not silently converted to SI. `local_origin` and CRS
metadata are descriptive under the owner's coordinate contract.

## Headless diagnosis and export

```python
from anyfem.geometry_cleaner import diagnose_project
from anyfem.geometry_diagnosis_worker import write_diagnosis_report

report = diagnose_project(project)
write_diagnosis_report(report, "geometry-diagnosis.json")
write_diagnosis_report(report, "geometry-diagnosis.md")
print(report.complete, report.clean, report.counts)
```

`anygeometry.cleaner_diagnostics.diagnose_geometry` supplies the owner-level
equivalent. Optional `cancelled()` and `progress(phase, fraction)` callbacks are
available to synchronous headless callers. Independent checker failures retain
other usable diagnostics. Cancellation ends remaining work and marks it unevaluated.
Reports are information, never authority to edit a model.

The full diagnosis retains quality findings beyond the fast repair-review
near-pair cap. Large models use at most eight spatial-classification processes,
capped by available CPUs, with one BLAS thread each. Topology and broadphase run
once; each spatial candidate is classified exactly once. Existing `strict_audit`
callers keep their serial default. Small workloads stay serial. The report
records actual routing/worker count and failures, including a refused daemonic
parent, rather than silently claiming parallel execution. Serial/parallel raw
audit parity is tested, including structural and unsupported cases.

## Preview and explicitly selected repair

For feature-free geometry, open **Repair options**, select removable isolated
point findings and enable removal, or select/capture model points and enable
bounded grid snapping. **Preview selected repairs** lists before/after values in
project display units. **Apply previewed repairs** creates one document Undo
action. Changing selections, options or thresholds invalidates the preview.
Read-only, changed or geometry-disabled documents cannot apply it.

Only isolated, unannotated, nonconstruction points can be removed. Attached
supports/loads and region anchors are protected and rechecked before applying.
Grid snapping requires explicit vertex IDs, finite positive grid spacing and a
Euclidean displacement limit. It uses the raw metre document origin and
ties-to-even rounding. It refuses collisions/collapse, curve-defining vertices,
unsupported neighborhoods and candidates leaving the supported planar domain.
Geometry identity and attached properties are preserved.

Slivers, structural duplicates, short edges and disconnected regions require
engineering review and manual geometry/feature editing. They are never deleted
or joined automatically. Any feature history or captured feature baseline makes
direct repair unavailable, so regeneration cannot silently revert the changes.
Imported/raw feature-free geometry supports the conservative operations above.

The compatibility fast API remains available:

```python
from anyfem.geometry_cleaner import (
    review_project, preview_project_repair, command_for_project_repair,
)

review = review_project(project)
selected = [item.id for item in review.findings if item.kind == "unused_vertex"]
plan = preview_project_repair(project, review, finding_ids=selected)
command = command_for_project_repair(project, plan, read_only=session.read_only)
if command is not None:
    session.execute(command)
```

Plans are issued in-memory capabilities bound to the exact live model object,
UUID, revision and operation data. Forged/copied/stale/foreign plans refuse.
Detached diagnosis results are not repair authority; the UI obtains a fresh
live quality review before preview. Clone validation precedes one atomic kernel
transaction. Empty plans bypass history. Undo/redo restore exact design, stable
IDs, allocator state and attachments; owner revision/checksum remain monotonic.

## Speed evidence and verification

The agreed basis is **10,000 faces within 30 seconds**, measured from requesting
diagnosis on an already loaded model through full report availability/export.
Model construction and prior loading are reported separately.

The desktop acceptance case completed in **27.48 seconds**, including detached
snapshot capture, background diagnosis, report display and full JSON/Markdown
exports. The connected planar model contained 10,000 faces, 20,200 edges and
10,201 vertices; all seven checks completed and all 299,000 strict spatial
candidates were classified. All owned workers retired. The measured Windows
source runtime used Python 3.14.2, eight classification workers on a 32 logical
CPU host, normal process priority and one observed BLAS thread per worker.
The longest observed UI heartbeat gap was 0.36 seconds.

This verifies the agreed case on this host; arbitrary curved, overlapping or
defect-dense models can require more time or report unsupported coverage.
Earlier measurements and failures remain retained, including the 80.90-second
serial diagnosis and 39.21-second desktop path before optimization. Exact
commands, source/input identities, phase timings, exports and process cleanup
receipts are in the living task record outside the source tree.

Focused checks cover full report/owner provenance, independent failures,
unsupported/cancelled/stale status, all-page export, event-loop responsiveness,
process cleanup, transfer independence, serial/parallel audit parity, repair
refusal/rollback, attachments, feature intent, undo/redo and persistence. Tests
use the source environment; the current Qt 6.12 runtime is outside the declared
package pin and does not establish supported installed-artifact compatibility.
No release, merge, installed-artifact or scientific qualification is claimed.

## Reading a diagnosis with many findings

The result separates **model errors**, **model blockers**, **review warnings**,
**information** and **coverage limitations**. Severity remains exactly as reported
by the geometry owner. Coverage limitations mean that a check or candidate is
unsupported or unverified; they are not confirmed model defects and do not imply
success. An incomplete report explicitly says the strict owner audit has not
passed. The Finding groups tab counts repeated kinds and distinct affected
entities; the paginated raw findings and full exports retain every record.

For authored structural models, primitive crossings and T-junctions describe the
current authored topology. Mesh preparation may later split or connect geometry,
but successful meshing does not establish that the authored model passed this
audit. Diagnose never performs that preparation or modifies geometry.

The default 15 m floating barge uses modern `MEMBER_ON_FACE_BOUNDARY` declarations
for opening bars. The owner audit now recognizes those declarations on qualified
boundaries, without accepting unrelated sheets, incorrect target geometry or
incorrect member intervals. This removes 48 inconsistent-kind errors. The four
end-girder `MEMBER_ON_FACE` declarations still conflict with their boundary
relationship and produce 40 rows; they remain visible. Their opposite trim/axis
orientations cannot all be expressed by the current ascending target-parameter
contract, so this diagnostic fix preserves the authored members and metadata.

Curved quality and near-gap coverage remains unsupported where stated, and
unclassified strict curved predicates remain blockers. This change adds no
curved-geometry qualification or general CAD healing capability.
