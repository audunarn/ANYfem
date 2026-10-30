# Create a beam along a plate cut

**Quick selection:** Point selects authored model points. Vertex exposes actual topology vertices, including cut/intersection endpoints, while excluding curve-control-only points. Use Vertex to select, inspect or measure those internal points. Plane is a plain selector for existing reference planes in the viewport or Construction planes in the model tree; it has no creation dropdown. Create planes in Construction. Plane selection does not start a sketch or invalidate an existing mesh; switch to another selector or press Escape to leave it.

Select one target **plate face**, then **right-click → Cut** (also available under **Edit → Cut plate…**). Choose one cutter:

- **Explicit plane**: enter its origin in project length units (unit suffixes accepted), and a nonzero normal as three comma-separated components. The plane extends across the entire target.
- **Active construction plane**: use the construction plane currently selected in the Construction panel, including its offset.
- **Another plate**: choose an existing face. Its actual finite extent is respected.

Press **Cut and select trace**. Both sides of each plate remain. The resulting exact intersection edge or edges are selected. Create a beam section in **Sections**, then choose **Edit → Create beam from edge…** and select that section. This creates one physical beam along one connected, unbranched chain. If the cut has disconnected components, select one connected component before creating each beam. Cancellation leaves the model unchanged; undo and redo treat cutting and beam creation as separate steps.

Cut trace curves are selectable under **Lines** in the model tree, including cuts on generated plates. Ordinary generated seams remain grouped with their generator. Once a trace becomes a physical member, its axis appears under **Beams** and leaves **Lines**. Tree selection, Hide selected and Show selected operate on the exact geometry edge; undo/redo and saved projects retain this distinction.

Geometry and topology come from ANYgeometry's public qualified intersection and imprint APIs. The beam uses the actual plate boundary edges, preserving shared topology and exact owner curves. The workflow refuses coplanar, tangent, disjoint, ambiguous and unsupported intersections rather than approximating them. Supported geometry depends on the owner's available qualified routes. A finite cutter that stops inside a plate can create owner-defined imprint subdivisions, rather than two simple rectangular faces.

Cuts are stored as replayable ANYfem design features with persistent target/cutter inputs and semantic trace outputs. They survive saving and reopening, permit later modelling and additional cuts, and use the normal detached feature regeneration path. An upstream edit that makes an input ambiguous or an intersection unsupported fails before publication. Other consumers that do not have the ANYfem feature executor can retain a verified frozen materialization but cannot replay the cut.

Changing an existing cut after creating its physical beam currently produces an owner lineage refusal. The project remains unchanged. Undo beam creation before editing/rebuilding its cut; the application does not recreate or retarget physical members by guessing.

Sections, supports, pressures and geometry-set references follow same-kind owner replacements; cutter assignments stay on cutter descendants. A plane's temporary face and unused topology are removed after imprinting. Existing unrelated entities remain. Standard project mesh invalidation and command undo/redo apply.

The workflow does not extend ANYmesher's supported curve/surface families or authorize approximation tolerances. Exact curved trace storage and beam creation are distinct from successful meshing of every curved plate. Focused tests cover planar integration and a horizontal cylindrical-plate cut retaining an exact Arc: every beam node on that trace shares its node identity with both adjacent shell meshes. This is not a claim for arbitrary curved surfaces.

Display tessellation retains one outline identity per exact owner face, including faces with rounded openings. Shaded edge and selection outlines show the outer trim, holes and true cut boundary without exposing triangle diagonals. Semantic cut traces stay visible and selectable even while their cut feature is collapsed. The geometry and owner-defined subdivisions remain unchanged.
