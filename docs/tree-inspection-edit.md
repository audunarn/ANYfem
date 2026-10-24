# Inspect and edit from the model tree

Click a geometry row once to highlight it. Beam/member and feature rows highlight their geometry, and sets highlight all resolved members. The highlight does not depend on the current canvas pick filter. Tree branches expand immediately, so a click is not swallowed by an in-progress expansion animation.

Click an existing support, load or mass once to highlight its assigned targets and open its current settings in **Loads & BC**. The panel displays **Editing … — existing record** and an **Update** button.

For supports and the five common load/mass operations, edit the normal checkboxes and unit-aware fields. Update changes that existing record; it does not add another support/load. Its identity, target region, load case, envelope and settings not shown in the normal form are retained. Point-load and mass values are the values stored on that record; editing does not divide them again by the number of highlighted targets.

Changing the canvas selection while editing does not retarget the record. Use **Cancel edit / New** to discard the draft and return to creating a new assignment. Clicking the same tree row again reloads its accepted settings. **Advanced command form** retains the original editor for envelopes and other optional settings; less common attribute types open there directly.

Updates are undoable. Invalid inputs, read-only projects, deleted/replaced records and replaced projects are refused without creating duplicates. If an old draft becomes stale, select the record again to review its current settings.
