# Entity mesh sizes in ANYfem

* `NativeMeshControl.target_size` is a **target**, not an implicit refinement. A sized
  control defaults to `size_mode="override"`: the scoped entity is meshed at that
  size, finer or coarser than the global target. An explicit `size_mode="refine"`
  keeps the old can-only-refine `min` meaning; a parameters-only control never touches
  the size. The default serializes without a key (a sized control without the key
  loads as `override`); only an explicit `"refine"` is written.
* In `NativeProjectMeshingSession.generate_component`, an override control scoped to
  the component (or global) sets the component's target (overlapping: finest);
  face/edge-scoped overrides become entity `Refinement(mode="override")` on the working
  geometry. A control scoped to another component's sheet/member does not apply.
  Automatic SmartMapped selection accepts override controls (spatial zones still make
  it ineligible).
* `generator_properties` `mesh_size` is the size of the generator's own plates and
  beams (`mesh_size_mode`: default `"override"`, or an explicit `"refine"`);
  `reapply_generator_properties` writes `Refinement(mode=mode)` tagged
  `generator:<feature>:mesh`.
* **Project meshing refuses a missing entity.** `Project.generate_mesh` raises
  `ProjectError` when an entity size override resolves to no entity (before the owner
  sees it, source untouched). Only a suppressed generator's own
  `generator:<id>:mesh` size may be absent. Spatial zones keep their old behaviour.
* **Generator sizes in the incremental session.** The session merges
  `generator_mesh_controls(project)` (stable ids `generator:<feature>:mesh`, scoped to
  the generator's own output faces/edges) into the runtime settings; they are never
  persisted. Absent or suppressed generators contribute nothing; a malformed advertised
  size raises `GeneratorControlError` (no broad exception swallowing).
  `refresh_generator_controls()` (also run by `request_remesh` and on every change
  flush) uses the runtime's **scoped** route when only generator controls changed:
  `NativeMeshingRuntime.set_settings_scoped(settings, components)` keeps the control
  generation, so publications and in-flight jobs of every other component stay valid,
  while each affected component gets a new component generation - its old request is
  cancelled and can never publish the old size - and is rescheduled. The settings
  change, the generation bump and the cancellation of old pending/active jobs happen
  in one critical section (only the slow snapshot capture runs outside it), so an old
  affected job cannot publish in a gap before scheduling. The affected
  components come from the changed controls' scopes. The public global
  `set_settings` is unchanged (new control generation, every known component
  remeshed) and is used when anything other than generator controls changed. A request
  that captured older generator sizes and whose component is affected drops itself.
* **Persistence.** `meshing.refinements[].mode`, `generator_properties.*.mesh_size_mode`
  and every sized override control (including the default) make the project format 13,
  so an older reader refuses it instead of clamping a coarse size. Explicit refine-only
  controls, parameters-only controls and projects with none of these keep their format
  and bytes. An older file whose sized control carries no mode loads under the
  corrected entity intent (`override`); its next save advertises format 13.
* **Migration.** A generator file without `mesh_size_mode` loads as `override`; on
  load only refinements that match a real, unsuppressed generator output slot, its
  recorded `mesh_size` and the zone defaults (tag, no centre) are migrated. Manual
  zones, even with a generator-looking name, stay refine-only.
