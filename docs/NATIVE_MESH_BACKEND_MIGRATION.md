# Native triangulation backend migration

ANYfem project format 6 separates two choices that older documents could mix:

- `meshing.native.backend` chooses the top-level mapped/native meshing strategy.
- `meshing.native_backend` chooses the constrained triangulation implementation.

New projects use `auto` and format 6 always writes that choice explicitly. The
valid triangulation values are `auto`, `python`, and `native`.

Formats 1 through 5 preserve their historical behavior. If they omit the
triangulation selector, ANYfem migrates them to `python`. A legacy
`native_backend` value anywhere below `meshing.native`, including a local
control, is moved to the format-6 top-level field and removed when the project
is saved. Repeated matching legacy values are accepted deterministically;
conflicting values fail closed.

Format 6 has one canonical source: `meshing.native_backend`. Missing or invalid
values fail closed. Any second `native_backend` below `meshing.native`, including
one in a local control, is rejected even when it matches the top-level value.

Direct and incremental meshing both receive the project-level choice as one
explicit argument. An incremental session snapshots it at session creation, so
changing the project affects only subsequently created sessions and never an
in-flight request.

This migration does not change ANYmesh's default by itself. UI/settings
presentation and the eventual Python-to-auto default switch require their own
registered handoff and qualification.
