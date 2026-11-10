# Update a published mesh

For this source candidate, use ANYmesh `a1ce9d3e655c0087825eca8eb6df711fabda8f6e` and ANYgeometry `602e4a7ac35eeeea6490e0bf25e22f03f72b86a7` with the other dependencies pinned in ANYfem’s caller workflow. These revisions provide the numbering, ownership and boundary APIs used below.

In the Mesh panel, select **Prepare mapped mesh for regional updates** and choose **Generate mesh**. After an edit, choose **Update mesh**, review the affected region, and confirm. For two independent plates, change the edge divisions on one plate. The other plate keeps its node numbers, coordinates, element numbers and ordered connectivity through saving, reopening and solver assembly.

The **Automatic** method can also prepare an update baseline for admitted mapped plates and source-backed shell surfaces. On eligible surfaces, including four-sided Bézier extrusions, leaving native controls at their inherited settings lets the mesher select point placement and geometric approximation. The panel enables **Update mesh** once the active mesh has a baseline. Its preview checks the current edit. **Generate mesh** requests a full rebuild with fresh numbers.

## Working from Python

Use a project mesh session for generation, publication, undo history and disk artifacts. Given an existing project with two disconnected plates and the edge IDs of the first plate, the following updates only the first plate.

```python
from anyfem.io.project_file import load_project
from anyfem.mesh_jobs import MeshSettings

session = project.mesh_session()
settings = MeshSettings.create(0.5, element_order="linear", strategy="mapped")
parent = session.generate_mesh(settings)
path = session.save("assembly.anyfem")

session = load_project(path).mesh_session()
updated = MeshSettings.create(
    0.5, element_order="linear", strategy="mapped",
    overrides={edge: 4 for edge in first_plate_edges},
)
print(session.preview_update(updated).description)
child = session.update_mesh(updated)
session.save()
```

Keep the project file with its artifact directory when moving it. **Save As** copies the recorded artifacts to the destination. The lower-level `project.generate_mesh(...)` API returns a candidate for existing generation-only callers.

A headless mesh session takes the project’s edit lock when it publishes a change to an opened project. Call `save()` to persist that revision and release the lock, or `close()` to discard unsaved work. The artifact directory remembers the highest published numbers even when the newer mesh is discarded. If another session has saved a newer revision, reopen the file before publishing another mesh.

## Numbers and references

Nodes and elements have separate positive signed 64-bit number spaces. Shells, beams and couplings share the element counter. A new element receives a fresh number. Deletion, undo and branching can leave gaps. A cancelled, superseded or rejected update leaves the active mesh and both counters intact.

Mesh selections, loads, regions and output requests carry the identity of their producing mesh. An update advances an attachment when its referenced entity survives, including the orientation of an element face. The Qt panel lists references that need a new target. For interior patches it also highlights the exact parent elements before publication. Cancelling that review restores the prior mesh and selection. Results continue to open against the mesh used in their analysis.

Changing geometry can make the active mesh stale for solving. Update it or choose an explicit full rebuild. An old project without a compatible baseline, a zero-numbered legacy mesh or a changed producer build requires a full rebuild before regional updates. CalculiX deck export checks number limits before writing and accepts an explicit 64-bit target through `integer_bits=64` for INTSIZE64 builds.

## Supported edits

| Geometry and mesh | Regional operation |
| --- | --- |
| Disjoint, straight-sided mapped plates | Change geometry or edge divisions, or add or remove a separately owned plate. Check disjointness in both revisions. |
| Mapped planar plates with shared sides | Hold ordered stations on retained edges, including quadratic midside nodes. A required change in shared seeding expands the region shown in the preview. |
| Supported planar beam and shell components | Regenerate the affected component and rebuild its attachments. Supported prepared crossings and three-sided plate junctions use source-to-working correspondence. |
| Single planar Q4 plate | Remesh complete cells for supported polygonal holes, point sizing zones and unconstrained interior sizing curves. An edit to a spline control point is detected even if its bounds and sizing stations stay identical. A diagnosed change to the outer boundary can expand once to the whole plate, as shown in the preview. |
| Single open cylindrical or conical sector | Refine interior blocks on an unchanged, untrimmed source and fixed rim. Changing only compatible rim edge divisions can expand once to the whole sector. The preview identifies the face, and the new mesh must match the requested ordered rim stations. |
| Single closed cylindrical ring of untrimmed sectors | A density change can regenerate the whole ring when the geometry owner certifies one complete period and the quad-first route passes its quality checks. The update verifies both ordered sides of every shared edge, including the periodic seam and quadratic midside nodes. |
| Independently owned linear shell faces with source-cell evidence | Update eligible Bezier extrusions, sampled ruled surfaces, explicit Coons patches and NURBS surfaces. The preview names the complete faces to replace. Supported edits include edge divisions, target size, changes to the authored surface with unchanged bounds, and an inner trim defined on its NURBS support. |
| Disconnected literal planar and curved shell faces | Editing either face can preserve every node and shell on the other. Straight-sided planar faces and untrimmed tensor surfaces require proven separation of their finite material. |
| Connected linear shell faces with a shared source edge | Remesh the affected connected component. Ruled panels with separated control nets and NURBS panels with a certified common chart are covered. Unchanged disconnected components keep their mesh. Both incident faces must match the full ordered station chain on each shared edge. |
| New four-sided hole on a source-backed NURBS Q4 face | Remove one or more complete cells when the trim follows their perimeter. A strictly convex hole inside one cell can instead create a ring of four Q4s. Where the growth limit requires it, the patch can expand into adjacent cells. The preview highlights the exact parent cells and the new hole edges. |
| Local point sizing on one unchanged source-backed curved face | A new point refinement can replace seven interior Q4 cells with 22 Q4 cells. The preview highlights the exact parent elements. The source owner verifies a fixed 12-node interface, checks new edge sizes against the point field, and proves the remaining cells lie outside its range. This route is available for compatible Automatic or Native meshes with quad recombination. |

For eligible disconnected four-sided faces, **Update mesh** can reuse an unchanged face's cells after reopening the project. The mesher skips tessellation on that face, checks its source definition and every rim station, and validates its cells against the current geometry. Publication also checks the permanent numbers, coordinates, oriented connectivity, grids and ownership against the parent mesh. The update record lists the faces actually regenerated and reused. Support for the polynomial charts requires the exact directed boundary to agree with its stored tensor representation.

The full and regional commands allocate permanent numbers while the generation worker still holds its live source-cell evidence. The solver input uses those same numbers. Curved Q4 shells support the verified static solve. For V2D triangles the default **Source surface normal** convention can refuse a curved triangle whose source normal differs from its facet normal. To model a faceted reference surface, open **Mesh → Advanced settings → V2D triangle reference surface** and choose **Flat triangle normal, oriented by source** before generating the baseline. Each triangle uses its chord-plane normal after the source normal passes the original signed-area and alignment checks. The project saves this choice. Changing it requires a full mesh generation.

When a trim crosses old cells obliquely, or a point refinement has no admissible interior patch, the preview identifies the whole-face operation where supported. Interface and quality failures leave the previous publication intact. General trimmed networks, arbitrary intersections and higher-order curved source patches still require owner support. The NURBS full-cut, reported four-face spline and split quarter-cylinder release holds remain in force. Full-model certification continues through the existing full audit.
