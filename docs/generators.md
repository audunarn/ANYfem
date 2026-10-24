# Generator engineering properties

Generators can create geometry alone or assign engineering properties in the same undoable action. The Generators workspace offers existing beam/plate sections, a material override, optional plate thickness and optional local mesh size. Dimensional fields use project units and accept explicit suffixes. Create materials and section definitions in Sections first if the lists are empty.

Plate, bulkhead and frame generators accept plate properties. Girder and stiffener generators accept beam properties. Stiffened panel, cylinder and cone generators accept both when structural members are present. Cylinder/cone stiffener spacing must be specified to expose or assign unassigned member axes; ordinary shell seams stay hidden.

```python
feature = commands.run(commands.AddStiffenedPanel(
    4.0, 3.0, 1.0,
    beam_section="stiffener",
    plate_thickness=0.020,
    material="S355",
    mesh_size=0.4,
))
```

`AddCylinder` and `AddCone` accept the same optional engineering keywords. The generic `AddFeature("generator.plate", parameters={...}, engineering={...})` route supports all applicable kinds. Properties are application-owned; they are not passed to ANYgeometry generator parameters.

Assignments bind to persistent feature outputs. Regeneration fills available new outputs while retaining explicit section overrides. Material overrides create generator-owned section variants and leave shared definitions unchanged. Mesh size creates real local refinements consumed by mesh preparation; it is a target rather than a strict maximum edge length. Settings, generated definitions and assignments survive save/reopen, command recording, undo and redo.

The existing geometry owner can reject topology-changing spacing edits and suppression of an already materialized structural generator when exact lineage cannot be preserved. Those operations remain atomic refusals; no assignment is guessed onto replacement topology. Length changes and translation retain assignments, and a generator created suppressed can be resumed.
