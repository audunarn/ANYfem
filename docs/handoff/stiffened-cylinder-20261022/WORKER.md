# Stiffened cylinder implementation handoff

State: verified against assigned criteria; parent independent acceptance remains separate.

## Delivered

- ANYfem source-boundary admission now accepts exact Cylinder faces at effective linear order without custom parameterization. Exact planar support stays unchanged; quadratic cylindrical, conical and custom parameterization refusals remain.
- Existing ANYmesh canonical source-edge merge already inherited shell division counts, matched stations by source identity and checked positions before reusing node IDs. Added full linear boundary coverage validation: one B2 per adjacent station pair, unique source-edge element ownership and every axis segment consumed by each incident shell face's topological boundary. No geometric welding or additional shell generation. Cancellation is polled per face/edge and every256 elements; both production merge call sites forward the callback.
- Connected ANYgeometry repair emits both parent endpoint occurrences0 and1 of the existing deterministically selected closed source member. Existing target selection, provenance, residual checks, strict structural validator and topology remain unchanged. Ordinary endpoint/interior and end-to-end contacts retain one attachment.

## Changed files

ANYmesh: src/anymesher/hybrid.py; tests/quad_first_curved/test_stiffened_cylinder.py; docs/STIFFENED_CYLINDER.md; tests/quad_first/test_q_beam_station_merge.py (authorised fixture repair: two actual shell cells consume its existing midpoint).

ANYfem: src/anyfem/model/project.py (admission block only; existing mesh_size_properties dirty edit preserved); tests/test_quad_stiffened_cylinder.py; this handoff.

ANYgeometry: src/anygeometry/member_joints.py; tests/test_closed_member_endpoint_junctions.py. All preexisting dirty files in all repositories preserved. No Git mutation, external provider, worker launch, configuration changes or benchmarks.

## Actual focused checks

- ANYmesh dedicated cylinder + canonical merge fixture:8 passed; check-192012352720-mesh, process wall8.3641299s. Covers rings, periodic seam/member traversal reversal, source station identity, unrelated coincident edge nonwelding, damaged chain and missing shell coverage refusals, quadratic refusal, validation-stage cancellation/nonmutation, scalar offsets and exact coupling reconstruction.
- ANYgeometry endpoint occurrence owner tests:4 passed; check-192357131358-geometry, process wall1.0731701s. Covers closed source0/1, nonclosed source0, deterministic ordinary end-to-end single attachment, idempotence and transaction cancellation/nonmutation.
- ANYfem public cylinder/job tests:9 passed; latest fem check result below, process wall29.5428022s. Includes the full radius1/height5/segments12/longitudinal spacing0.5/target0.25 request, automatic default section offsets and unique section-bearing source ownership; smaller valid ring cylinder with reversed members; persisted linear quad-first settings and real recoverable run_mesh_job; quadratic/conical/custom admission refusal; cancellation/nonpublication; retained65-face atlas budget refusal.
- Scoped diff whitespace checks passed. Parent owns additional integration, process transport, independent review and performance measurements; these are not worker claims.

## Preserved failures and scope boundary

Initial rings failed strict structural preparation. Diagnostic ring-diagnostic.json showed junction1 uses member1@0.2 and closed ring14@0,@1, but only ring14@0 attachment to member1 existed. Equivalent failures at junction14,27,40. Minimal member_joints repair retains both authored source occurrences; validator was not changed.

The extra stress case height5/ring spacing1 creates65 faces and still reaches the existing typed CylinderAtlasError interval_operations qualification resource refusal. Parent explicitly adopted current qualified resource scope: full requested beam-only longitudinal cylinder plus smaller valid rings;65-face case is now a refusal/nonmutation regression. No atlas algorithm, budget, tolerance, failed-certificate fallback or acceptance criterion changed. This limitation is documented in ANYmesh docs.

Scalar offset's ideal analytic radial oracle initially differed by3.93e-8 at a chart endpoint because the existing owner numerical projection supplies UV. Final regression uses authoritative public geometry projected UV/face_normal, verifies exact offset/coupling reconstruction at unchanged1e-10 checks, and retains failed evidence. First scalar fixture lacked declared coupling attachments; added exact source-boundary declarations rather than changing coupling semantics.

Full evidence remains C:/ANYworkspaceWork/stiffened-cylinder-20261022/check-*/{request.json,result.json,stdout.txt,stderr.txt}. All failures retained. Worker cumulative recorded process wall140.92883590003476s of900s; remaining759.0711640999652s. Successful processes may report an existing pyreadline3 console-deallocator WinError on interpreter shutdown; exit status0 and test results recorded above.

Latest public result: C:/ANYworkspaceWork/stiffened-cylinder-20261022/check-192359114014-fem/result.json
