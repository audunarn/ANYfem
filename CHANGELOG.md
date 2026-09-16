# Changelog

## Unreleased

## 0.4.1 - 2026-09-17

- Present multi-entity generators as lightweight feature objects: generated
  plates are grouped into one selectable surface, and generated points and
  lines stay hidden until an explicit per-feature Explode action, while exact
  topology remains available to meshing and saved references. Consume
  ANYgeometry's public topology-role and exact feature-owner contract rather
  than maintaining a duplicate feature-kind policy in the UI.
- Qualify the latest-only application graph against ANYgeometry 0.4.3,
  ANYmesher 0.5.0, ANYfileio 0.3.2, and ANYsolver 0.4.6.
- Expose structured/hybrid planning budgets, solver numerics, resource limits,
  and analysis-specific controls in the GUI while preserving the legacy B3
  shell as the default and keeping B3-GE an explicit opt-in.
- Make mapped butterfly-hole decomposition replayable through ANYgeometry's
  public feature-registry extension contract, with atomic regeneration and
  exact materialization checksums.
- Correct adjacent independently extruded plate connectivity, source-to-work
  association remapping, and refinement-aware hybrid mesh generation.
- Keep mesh hashes deterministic while retaining detached-work provenance in
  saved artifacts.

## 0.4.0 - 2026-09-03

- Relicense source releases from 0.4.0 onward under MPL-2.0; earlier releases
  retain their original terms.
- License original narrative documentation under CC BY 4.0 and add a complete
  direct-dependency license inventory and third-party notices.
- Qualify the application against ANYmaterial 0.2, ANYgeometry 0.4.2,
  ANYmesher 0.4, ANYfileio 0.3.1, ANY3dView 0.5.5, ANYtk3D 0.5.5, and
  ANYsolver 0.4.2, including canonical qualified-Q4 plastic-state replay.
- Stabilize semantic mesh hashes against ANYmesher 0.4 runtime provenance and
  timing fields so identical remeshes and exact undo remain deterministic.
- Correct imported multi-point stress reduction and make CalculiX FRD
  round-trip assertions respect the format's fixed numeric precision.
- Add deterministic release checks for licensing metadata and built artifacts.
