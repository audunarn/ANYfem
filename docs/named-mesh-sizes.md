# Named mesh sizes

Right-click **Mesh sizes** in the model tree and choose **New mesh size…**. Enter a name and target size in project units (or type an explicit suffix such as `250 mm`). Radius and growth use the existing local refinement policy. Creating a property does not assign it or generate a mesh.

Select plates and/or beam/line geometry in the canvas. Right-click a named size and choose **Apply to selection**. Compatible selected faces and edges receive that property in one undoable action; other kinds are skipped. Right-clicking the category or property preserves the canvas selection. Applying another named size replaces the previous named size on those exact targets. Existing ordinary refinement zones remain in place.

Click a named size to edit its definition. Saving a changed size, radius or growth updates all its linked assignments. Names and stable property identities are retained across saving/reopening. An assigned property cannot be deleted; remove or reassign its targets first. Clearing refinement controls in the existing Mesh workspace leaves reusable definitions available, and Undo restores the linked assignments.

A local target must not exceed the stored global target when one is set. The existing mesher may adjust element sizes for geometry and adjoining controls; this is a target, not an exact element-length guarantee. The normal Model **Density** view continues to use the canonical target field, and Mesh **Density** continues to display actual characteristic element sizes.

Unused definitions do not change the physical model hash or invalidate an accepted mesh. Assigned effective controls participate in the normal mesh/model invalidation. Definition names and property-link IDs are document provenance; effective size/radius/growth/geometry controls remain the mesher inputs.

Canceling the editor changes nothing. Invalid values, duplicate names, stale project/definition dialogs, read-only edits, incompatible targets and local targets above the global target are refused atomically. Legacy refinement controls and their existing tree Apply workflow remain available.
