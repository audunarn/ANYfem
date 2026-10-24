# Worktree tables

Right-click any base heading in the worktree and choose **Open table**. Empty categories also have tables. Tables include the complete category, rather than the tree's limited visible window. Search filters every column; sorting and editing retain the original record identity. Numeric columns sort by value.

Double-click an editable cell, or press F2, enter the new value and press Enter. Engineering inputs accept project units and explicit units such as `20 mm` or `190 GPa`. A successful edit changes the existing record, preserving its identity and unshown settings. Each accepted edit or deletion is undoable from the main window. Invalid input remains inside the table with an explanation. A table bound to a replaced project cannot change the new document.

Selecting rows highlights their associated geometry. Inspection does not rebuild the model or mesh. Tables refresh after accepted commands, Undo and Redo, without polling. Closing a table removes its listeners and releases its row data.

## Editable inputs

| Category | Cell inputs |
| --- | --- |
| Points | X, Y, Z |
| Plates | Existing section assignment; thickness and material are derived |
| Beams and Lines | Existing beam section assignment; material is derived |
| Materials | Young modulus, Poisson ratio, density, yield stress; other material symmetries retain their full editor |
| Plate sections | Thickness, material |
| Beam sections | Profile, material, section dimensions, eccentricity, offset mode, rotation |
| Features and Sketches | Name and parameter record, through feature regeneration |
| Supports | Name and prescribed displacement/rotation for each DOF; Free removes a DOF |
| Masses | Name, mass, distribution policy |
| Loads | Applicable pressure, force/moment components and distribution policy |
| Imperfections | Amplitude; kind is shown |
| Mesh sizes | Name, target size, radius, growth for named properties |

Identity and derived fields remain read-only. The tooltip explains this, and **Edit / source settings** opens the existing editor or relevant configuration workspace. Generated geometry must be changed through its source feature. Deleting a derived member directly is refused instead of changing its owner's topology. Legacy refinement policies remain available through Mesh settings.

## Add and delete

**Add** opens a shared unit-aware creation dialog for points, materials, plate sections, beam sections and load cases, or the named mesh-size dialog. Other categories open their canonical creation workspace. Sets Add prompts for a name and creates an empty set; Beams use selected line axes and the existing section chooser. Beam creation requires compatible selected axes; the table explains what to select first.

Meshes, analyses, jobs and results describe generated artifacts. Their Add action opens Mesh or Solve configuration and never starts meshing, a solver or an external job. Their computed columns are read-only. Deletion uses the existing dependency checks and retains artifact files. Used definitions, global coordinates and other protected records retain the normal project restrictions.

Deleting several selected rows is one command; a refused dependent row rolls back preceding completed deletions. Canceling a creation dialog changes nothing. Read-only documents and stale tables refuse changes. Name changes are offered only when the canonical API supports preserving references; definition UUIDs are visible in tooltips rather than editable cells.

## Sets and assignment scopes

Sets contains every visible user-defined scope, including manual, Boolean, feature-backed and mesh-bound sets. Internal scopes used by section, support, load and mesh assignments are omitted from the worktree and tables. Their records and assignments remain saved unchanged. There is no separate Regions heading.

Use **Sets tools…** from the Sets table or category menu to create sets from geometry or mesh selections or combine existing sets. Manual geometry sets retain their existing geometry editing actions. Boolean, query and mesh scopes open a read-only inspector rather than an inappropriate geometry editor. Mesh-bound rows remain visible even when their mesh is unavailable; geometry highlighting is offered only for actual resolved geometry.

Output-request Set pickers offer visible sets. An existing internal assignment is retained as **Existing assignment (unchanged)** when its settings are opened; it is not silently replaced by another set.

## Build a set incrementally

Choose **New set…** from the Sets category menu, or **Add** in the Sets table, and enter a name. This creates an empty named geometry set without selecting anything. Cancel changes nothing. The existing canvas **Create set…** action still creates a set from the selected geometry.

Select plates, beam axes or other geometry in the canvas or worktree. Right-click the destination set and choose **Add to set**. The right-click retains the previous geometry selection; members are added without duplicates. **Remove from set** subtracts only the selected members that already belong to that set. It can remove the final member while preserving the set's name and identity. Unchanged membership adds no Undo entry.

Each accepted membership edit is one undoable action. Feature-backed members retain their persistent feature anchors. Save/reopen preserves empty and populated sets. Boolean, query and mesh set membership remains defined by its source: the actions are disabled with an explanation. Read-only or stale menus cannot change the accepted model.
