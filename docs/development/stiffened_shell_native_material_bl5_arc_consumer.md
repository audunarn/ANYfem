# Native material BL5 arc consumer

Status: IMPLEMENTED_BUT_UNVERIFIED source candidate. No interpreter, test, build,
install or numerical run is authorized for this separately scoped ANYfem slice.
The earlier solver allowance is not reused. OpenAI owns implementation because
Mistral/local availability remains cached unavailable.

## Early checkpoint

The existing v6 `ExperimentalGEBL5Panel` already calls public
`anysolver.stiffened_shell.create_analysis`. Its existing
`AnalysisDefinition.experimental_bl5_panel` and Project save/reopen path preserves
that exact input route and returns the same public owner through
`create_bl5_panel_analysis`. Source inspection found no blocking bridge defect.
No new consumer schema or product source change is needed.

The new headless example will use the existing mixed/biaxial/mixed-member/
continuous-P1-coarse fixture and constants, persist the original case and Project,
reopen them unchanged, load the ordinary and fixed-translation seeds, and call
the existing public `PhysicalChordMetric`, `ArcLengthControl` and
`continue_arc_length`. It will save actual accepted checkpoint bytes separately
from input definitions, cancel before any arc commit and after one accepted
prefix, then restore that prefix on a fresh owner from the reopened input and
continue the same controller. Public recovery, full retained/material/reaction
correspondence and invalid-checkpoint/controller atomicity will be checked.

Planned files: one example, one integrated test file and this document. No solver
private helper import, reconstructed constitutive state or independent mechanics.
The metric remains full-frame and measures normal spin, not a director quotient.
Only the matched mixed/P1 loaded seed has a simultaneous-yield premise. Installed
ANYfem/new solver compatibility, runtime checks, independent acceptance and all
scientific/release gates remain unrun/open. The public route is available in the
solver candidate wheel `4c027af197b7d8337e9ceb1d106e2b77befbb493179a7e6528ba9fb6e6cbf98a`;
this document does not claim the new consumer has executed against it.

## Implemented public workflow

The example reuses `panel`, `CONTROL`, `TARGETS` and `OPTIONS` from the existing
native mixed-J2 consumer. Its single fixture is mixed shells, biaxial physical
fibres, mixed physical/generalized members and `continuous_p1_coarse` lines.
No equations, material properties, attachments, schemas, defaults or source
bridges were changed. The Project stays explicitly experimental/mesh-native;
it does not qualify ordinary native Project workflows.

The new output directory contains the original canonical case JSON, original
Project, actual loaded seed, accepted arc prefix and continued solver checkpoint
bytes, plus structured workflow diagnostics. Checkpoints are separate result
artifacts, never input definitions or reconstructed elastic/material histories.
The owner comes from the reopened Project's existing public
`create_bl5_panel_analysis`; the fresh continuation owner comes from that same
reopened record. All case fields and native beam bytes must round-trip unchanged.
The scalar input schema stays `anyfem.experimental-ge-bl5-panel-native-mixed-j2-v6`.

After ordinary `.001` force and `TARGETS[:2]` fixed-translation loading, the
matched fixture must contain positive named physical-fibre and S3 histories.
Physical membership is filtered by actual public `beam_fibre_material` IDs;
the unfiltered station accumulator includes generalized members and is never
treated as physical fibre history. The metric uses preflight's actual node order,
explicit positive equal weights and characteristic length 1. These caller choices
are not inferred mass/quadrature or mesh-independent scientific prescriptions.
The full-frame metric measures normal spin; it is not a position/director quotient.

The example exercises pre-cancel before a step, cancellation immediately before
the atomic commit, and observer cancellation after one accepted prefix. It uses
the public `CancellationToken.raise_if_cancelled(stage)` hook for the current DEV
stage `stiffened-shell.arc.commit`, without private imports or monkeypatching.
This diagnostic is bound to the candidate's stage spelling; it promises no new
public stage API or cross-version compatibility. A missing stage fails explicitly.
The existing public `SolveCancelled` is caught only where cancellation is intended;
other solver/caller errors propagate. The saved prefix is restored on a fresh
public owner after corrupt-checkpoint refusal; that successful restore proves
the failed attempt left the fresh owner usable. Wrong-model restore must leave
public recovery empty, and a changed controller must preserve the live prefix.

Comparison recursively visits every public dataclass field, mapping, ordered
sequence, scalar and array. It covers full material records (station IDs,
high/low contents, frames, measures), shell/beam/retained frames and resultants,
constraints/multipliers, loads, reactions, histories, attempts and generation.
Direct and fresh-owner continued results and checkpoints must agree exactly.
Status differences are handled by comparing actual recovered prefixes together
and actual completed continuation results together, not by dropping result fields.

## Supplied tests — UNRUN

One integrated workflow test exercises actual case/Project persistence, reopened
public owner creation, yielded seed, cancellation/refusal, named physical recovery,
prefix restoration and continuation. It asserts actual status/generation/history,
constraint and equilibrium criteria unchanged from the solver contract, compares
reopened original input definitions, and checks checkpoint and structured output
artifacts. It does not substitute helper-only or count-only checks.

One pure-input test checks detached Project records, v6/legacy-policy isolation,
staged-load/checkpoint-as-input refusal, native definition digest refusal and
caller-owned metric-weight isolation. It performs no loaded-solve parameter sweep.
These tests are written but neither collected nor run; no AST/compile/import,
build, installed artifact or runtime check has occurred in this slice.

Future authorized headless command:

```powershell
python examples/stiffened_shell_native_material_bl5_arc_consumer.py --output <new-directory>
```

It creates a new directory and fails if that directory already exists, preserving
previous inputs/failure artifacts. Runtime provenance records actual ANYfem and
ANYsolver module paths, exact schema, model identity and public solver route.
Source and installed origins/wheel bytes still require an external authorized
binding check; the example does not infer a wheel identity from a version string.

## Source-only handoff and open obligations

The work is restricted to this example, its test and this document in
`C:/Github/ANYfem/.worktrees/experimental-ge-panel-consumer`, base
`67744ba7dda18f1518b61ddc99e7f293fb9d25a5`. The absent worktree AGENTS.md was
resolved by reading the governing `C:/Github/ANYfem/AGENTS.md`; global guidance,
local ECOSYSTEM_GUIDE and adopted philosophy were also read. No existing source
bridge defect was found, so no product source edits were made. Native source
inspection/whitespace/hash evidence is the only evidence for this new consumer.
Earlier protected failures, solver campaigns and ANYfem evidence remain untouched.

Remaining: root source review; fresh separately bounded consumer authority;
effective runtime/source and installed-module/input/wheel binding; actual public
workflow/test execution and any needed repair; independent acceptance. Solver
limited DEV evidence does not execute or accept this ANYfem consumer. The earlier
23.3312962s is not consumed or renewed here. Different-family, scientific,
mesh/unit/stability, real cutback/turning/capacity and publication gates remain open.

## Final review and handoff — 2026-10-06

Root reported an independent source-only review of the complete example/test
slice after the ExitStack/thickness repair, with no further blocking defect.
Every owner constructor is now inside ExitStack and registered for cleanup
immediately; wrong-model refusal changes positive shell thickness rather than
J2 elastic constants. This is source-review evidence only, not runtime testing,
scientific acceptance or different-model-family qualification. Root retains the
review and acceptance decision; the author does not supply its sole assessment.

Delivered: exactly three new files — this document, the headless example and its
test. Existing stiffened_shell.py, stiffened_shell_project.py, selectors, schemas
and defaults are unchanged. Native whitespace inspection found no trailing
whitespace in the two new Python files; scoped source diff showed no existing
product bridge changes. Source/private-access inspection found no private solver
imports or state access. No interpreter, AST/compile/import, test, numerical job,
build/install or ledger write occurred. Both supplied tests are UNRUN.

Handoff state: IMPLEMENTED_BUT_UNVERIFIED consumer candidate. Root may investigate
reuse of the frozen ANYfem artifact because product source is unchanged, but this
slice makes no installed compatibility/binding claim. Future verification needs
fresh consumer execution scope and measured authority; no solver allowance is
silently inherited. Preserve the one integrated workflow to avoid loading many
duplicate material fixtures. Root owns Git/integration, full runtime/artifact
proof and scientific/release decisions. No sibling/main/config/history/evidence
changes were made by this author.

## Exact source byte inventory

| New owned file | SHA256 |
| --- | --- |
| examples/stiffened_shell_native_material_bl5_arc_consumer.py | 7382caab8ac85d8e676be1bc6b529c87e9a01c728ef188888d75d4ebe2a77bc6 |
| tests/test_stiffened_shell_native_material_bl5_arc_consumer.py | 77c2109cdc4a336615e58943e00031d3e98553e283fe7eaf2850f985f4d4ad5c |

## Root artifact applicability — source inspection only

Native Zip/SHA256 comparison of current ANYfem source against frozen wheel
`d1c9714d7ca3e21d5d95c4ab9813bbf40d635c99cfa732a5f9284242f45d8daf`
found99 source and99 wheel Python modules, zero mismatches. The three new files
are examples/tests/documentation, so the current installed consumer experiment
can reuse that wheel with the solver wheel
`4c027af197b7d8337e9ceb1d106e2b77befbb493179a7e6528ba9fb6e6cbf98a`.
This native administrative hash check executes no interpreter and makes no
new workflow/runtime claim. Fresh150s consumer authority has been requested;
no answer or execution allowance is inferred. Old solver remainder untouched.
 
### 2026-10-06 pending installed consumer check

A source-only harness is prepared in
`.qualification/r8-native-material-bl5-arc-consumer-dev-20261006-v1/`.
Its `handoff.md` contains the exact prospective command and scope;
`source-handoff.json` binds the frozen inputs, existing wheels/sites, current
product bytes and harness hashes. Native PowerShell preparation only was run.
Both consumer tests and the Python harness remain UNRUN. The supervisor refuses
interpreter launch while fresh 150-second user approval is pending. No existing
solver or historical consumer execution allowance is reused, and no build/install
is proposed.
