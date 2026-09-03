# Changelog

## 0.4.0 - 2026-09-03

- Relicense source releases from 0.4.0 onward under MPL-2.0; earlier releases
  retain their original terms.
- License original narrative documentation under CC BY 4.0 and add a complete
  direct-dependency license inventory and third-party notices.
- Qualify the application against ANYmaterial 0.2, ANYgeometry 0.4.2,
  ANYmesher 0.4, ANYfileio 0.3.1, ANY3dView 0.5.5, ANYtk3D 0.5.5, and
  ANYsolver 0.4.1.
- Stabilize semantic mesh hashes against ANYmesher 0.4 runtime provenance and
  timing fields so identical remeshes and exact undo remain deterministic.
- Correct imported multi-point stress reduction and make CalculiX FRD
  round-trip assertions respect the format's fixed numeric precision.
- Add deterministic release checks for licensing metadata and built artifacts.
