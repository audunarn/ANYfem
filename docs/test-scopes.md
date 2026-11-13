# Test scopes

The default `python tools/test_anyfem.py` runs `quick`: manager contracts,
software Qt, pressure preparation/mapping/apply/undo, cancellation and stale
callbacks, plus tiny real geometry/mesh/solve/project-reopen routes and shell/solid
submodel dispatch. Its owned process-tree deadline is 600 seconds, excluding setup.
`full` is the required ANYfem application suite on Ubuntu/Python 3.13 with 1200 seconds.
Unknown ordinary tests remain in full. Manager/UI acceptance filenames confer no
exemption. `unit` (`fast`), `integration`, `focused` and `qt` remain available.
`compatibility` has 300 seconds across each existing three OS/four Python combination;
its installed-wheel isolated build/mesh/solve/reopen smoke executes within the same owned test tree.

Unmatched expensive scientific/refinement campaigns live in `diagnostics/`,
outside default pytest collection. `qualification` explicitly selects them;
selection does not grant campaign execution authority. Scientific assertions,
tolerances, historical failures and unsupported holds are retained. The expensive
plate-capacity reserve assertion remains a diagnostic; routine capacity checks use
a tiny real beam and assert the manager's owner delegation, arguments and summary.
No scientifically duplicated test has been deleted. `testing-inventory.json`
records all 433 baseline module dispositions and split old/new node identities,
owners, purpose, trigger, budget and unproven owner-coverage gaps.

Schema `anyfem-manager-tiers-v2` records static paths, selected/excluded reasons,
classification and hashes before imports. `suite-collected.json` binds actual
selected pytest node identities; JUnit retains failures/errors/skips. `run.json`
records the exact command, effective interpreter/environment, process-wall time,
deadline and terminal state under `anyfem-manager-run-v2`. Inherited pytest filters
and plugin lists are removed and recorded. Timeout is failure 124 with partial logs;
owned Windows Jobs/POSIX process groups retire descendants before completion.
Existing evidence directories are refused.

Push/PR runs one Ubuntu/Python 3.13 quick lane including software Qt. Release/manual
runs full once, all 12 compatibility lanes, small Windows/Ubuntu Qt checks on 3.11/3.14,
one focused Linux GPU smoke and the separate exact caller lane. Licensing,
distribution checks, dependency resolution and isolated installed-wheel checks
remain required. Publishing retains its separate distribution gates.

`caller` selects exactly the existing four positives, each in a fresh interpreter,
with Windows CPython 3.14.2/numpy 2.4.6/scipy 1.17.1 and required native planning
preflight. Their original bodies, cancellation/retirement checks and completion
validators are unchanged. The exact four-case union is independent of full.
A pass does not establish a shared-host or scientific qualification claim.

Tag publication calls the application and Qt release workflows once and waits for
both plus distribution build. Standalone manual release checks remain available;
manual publish validation does not publish. The full suite must pass within
1200 seconds, and all supported-platform gates must pass before release. No local check here
is attributed to that matrix.

Release Qt additionally requires the two explicit real contained parallel mesh
completion and cancellation positives. The runner sets
`ANYFEM_RUN_PARALLEL_MESH_TESTS=1` for Qt and validates both original nodes
executed without skips; viewer-owner campaigns remain outside this route.

The routine curved pressure test uses offset, independently triangulated source
panels on the three target facets. Its original angular-remesh body remains in
`diagnostics/test_orcawave_pressure_angular_remesh.py` with an unallocated
300-second ceiling. Exact force/moment assertions and projection limits remain
unchanged; no owner-coverage equivalence is claimed.
