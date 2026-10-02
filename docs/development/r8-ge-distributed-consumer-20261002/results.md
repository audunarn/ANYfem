# Native distributed GE load consumer — 2 October 2026

The existing ExperimentalGEPointPanel now owns an explicit optional distributed
load request. A separate strict schema preserves the legacy inner panel. Native
Project save/reopen preserves exact element IDs, load densities, model binding and
beam definitions. Default Project dispatch is reference-linear; explicit targets
select the existing finite owner. Body, staged and legacy actions remain separate;
unsupported mixing and footprint wrapping fail explicitly.

The runnable example exercises native Project construction/save/reopen, linear
solve, cancellation at accepted0.25, on-disk checkpoint restore and identical
continued0.5/1 results. Fixed spatial couples retain potential=None. Input
persistence and accepted checkpoints remain distinct. No solver mechanics copied.

## Executed checks and artifact identity

- Source:91passed,17.65s pytest;18.646s route,183906304bytes peak.
- Installed initial:91passed,8.68s pytest;11.293s route,197296128bytes peak.
- Independent review found example cleanup could leak the first owner if second
  creation failed. Registered each owner immediately; added failure injection.
- Installed affected final workflow plus cleanup:2passed,5.53s pytest;
  7.871s route,181972992bytes peak. Unchanged90checks reused by impact, so92unique
  final checks are covered. Product modules did not change after wheel build.
-98ANYfem and395ANYsolver source/wheel/site modules match. 282actual
  imported modules are verified under the isolated site. Actual1BLAS/Numba thread,
  JIT disabled, Python3.14.2. Each run bounded600s/1GiB with observed process-tree
  resources; no limits reached. Timing is execution evidence, not a speedup claim.

ANYfem wheel SHA256:
`6343157fe3e96731da9f4aaf78407156977a3934af3dbce0be85dc31bdce8a2d`.
ANYsolver wheel SHA256:
`7b0886fd7ddb3796c629f4abd451357c9b64c44f5c58fa6bcf7f60cb9999627f` (solver e4676e5b).
Raw archive SHA256:`6e85c4a4fc77b87bd84a8fe45e56cb54e5d91656da5091c55a4e990b23e51f46`.
Commands, manifests, versions, actual imported sites, logs, original and revised
fixtures, Project files, checkpoints and physical observations are preserved.

## Remaining scope

Experimental point-owner consumer behavior, not complete G4/G6 qualification.
The real consumer example is generalized Q4; fibre/mixed/S3 cases have solver
checks but are not independently executed consumer workflows here. New-load active
yielding, line/footprint/combined loading, physical/external comparisons, practical
scale, other platforms and all open programme obligations remain unaccepted.
No defaults/version/main merge/publication changed. F03 remains unconsumed.
The new archive scan does not remediate previously reported historical credentials.
