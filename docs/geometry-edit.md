# Edit selected beams and plates

Select one or more beam axes and plates in the canvas or worktree, right-click and choose **Edit**. Right-clicking an already selected canvas object retains the rest of the selection. The same editor handles one object, many objects, and mixed beam/plate selections. The heading shows compatible counts and any skipped points.

Check only the fields to change. Unchecked fields keep each target's current value, even when those values differ. **Mixed** indicates different current values. Press **Apply to selected** to accept one undoable edit; Cancel changes nothing. A stale or read-only dialog cannot change the model.

## Beam and line inputs

Choose an existing base section or material, section profile and dimensions, rotation relative to the attached plate, Front/Back attachment side, offset mode and manual eccentricity. Web direction is an advanced optional override: enable the field and clear its text to use the attached plate's normal again. Invalid directions, including a direction parallel to the beam axis, are refused before mutation.

Bare rotation values are degrees: enter `30` for 30 degrees. Explicit units such as `0.5 rad` are also accepted. Dimensions and offsets use project length units and accept explicit units such as `25 mm`. Manual eccentricity requires **Offset mode = manual**; an ignored eccentricity is never applied silently.

Unassigned axes need an explicitly selected base section before changing section-dependent inputs. Mesh controls can be assigned independently of a section.

## Plate inputs

Choose an existing base section, material, thickness and mesh target. Unassigned plates require an existing base section before changing thickness or material.

## Shared definitions remain safe

The editor applies changes only to the captured geometry selection. It preserves shared library definitions used by other geometry. When necessary it creates a section variant, and reuses an existing exact variant when available. Different original sections retain their unedited dimensions. Each accepted batch is one Undo action, including newly needed section or mesh-size definitions. Save/reopen and feature regeneration preserve the resulting assignments.

The captured targets remain fixed while the dialog is open; changing the canvas selection does not retarget pending edits. If the document, target assignments or definitions change, reopen Edit before applying.

## Mesh targets

Choose an existing named mesh-size preset **or** enter a numeric target. A named preset applies its existing size, radius and growth to selected targets. A numeric size-only edit preserves each selected target's existing named-control radius and growth, creating or reusing a separate definition for those targets. Entering the existing size leaves its property identity unchanged and adds no Undo entry.

Direct legacy or generator-owned mesh controls show their actual configured target, or Mixed when values conflict. Re-entering the same numeric target is unchanged. Replacing such a control from this editor is refused with guidance to edit or remove it in Mesh or its generator settings first; existing provenance and refinement rules remain intact.

Local target sizes must not exceed a configured global target. Actual element sizes can vary with geometry, adjoining controls and mesher decisions. This editor does not generate a mesh or run an analysis.
