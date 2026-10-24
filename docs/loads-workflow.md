# Interactive Loads & BC

Use **Supports**, **Point loads**, **Line loads**, **Pressure**, **Surface traction** or **Mass** for the normal selection-driven workflow.

1. Pick geometry with Points, Lines, Plate edges or Faces. The target summary counts compatible entities and reports skipped selections. Plate edges uses the perimeter-selection tool; every selected compatible edge receives the assignment.
2. Review the active load case and coordinate system, then enter values. Inputs accept project units or explicit suffixes such as `2 kN`, `-2 kN/m`, `3 kPa` or `2 mm`.
3. Apply once, or press Enter in an input. One action assigns every compatible target and creates one undo item. Selection changes update the summary without resetting entered values.

For supports, checked translations/rotations are prescribed to **numeric zero**; unchecked components remain free. Fixed, pinned, simply supported, symmetry and antisymmetry presets fill the same checkboxes. Manual changes switch the preset to Custom. The Advanced target and prescribed values section allows nonzero displacements (length units) and rotations (angle units).

For vector loads, choose components or a direction and magnitude. Direction is normalized; its magnitude is entered in the appropriate force, force-per-length or traction units. The coordinate-system list uses existing project systems. Pressure retains the canonical signed plate-normal convention.

Point loads default to the full force/moment on each target. Choosing **Total value shared by targets** divides the entered force and moment among selected points. Mass defaults to a shared total; **Full value on each target** applies the full entered mass to each selected geometry entity. Line loads and surface tractions remain force per length and force per area on every selected target; these are not divided.

Supports and masses are project attributes; the active load case is disabled for them. Other primary loads use the selected existing case; the default case is created through the canonical load command if needed. Template cases are refused.

**Advanced command form** exposes every original parameter for the selected operation, including envelopes and explicit references. Connections, wet loads, acceleration, follower pressure, cases, combinations, series and attribute editing remain available under Advanced operation names with their original icons/forms.

A replaced project requires explicit confirmation of the current targets before applying an old draft. A changed unit profile requires explicit value review/reopening the operation, preventing silent reinterpretation of numbers. Read-only projects and invalid values are refused. Failed bulk edits restore the accepted attribute/region/case state; undo/redo preserves record IDs and existing unrelated section/region identities.
