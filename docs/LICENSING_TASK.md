# GPL and optional commercial licensing readiness

Owner: Odin. Branch: `codex/gpl-commercial-readiness`. Baseline:
`e243c3ebe888a7f09e1e8d80aa6c3f05910faf69`, refreshed 2026-10-27.
Authority: implement licensing/docs/CI on a dedicated branch; no main merge,
release, access change, operative commercial grant, numerical or GUI changes.

## Plan and acceptance criteria

1. Establish historical, provenance, dependency and integration facts. Output:
   transition/audit reports separating measured facts from legal unknowns.
2. Document public GPL scope and optional commercial route, contribution policy,
   non-operative term sheet and A/B/C strategy. Depends on 1. Acceptance: old
   rights, docs/assets and third-party exclusions preserved; no commercial grant.
3. Strengthen source, notice, wheel/sdist and installed-metadata consistency gates
   with refusal tests and CI. Acceptance: fresh checkout and installed artifacts
   pass focused checks, tampered artifacts fail, runtime files unchanged.
4. Independent read-only review, reconcile findings, commit dedicated branch and
   report evidence and remaining commercial/distribution clearance obligations.

Risk: packaging and public license policy, not scientific qualification. Main
open risks are copyright chain of title, unreviewed transitive/binary components,
unknown final installers and proprietary engine combination treatment.

## Ownership/fallback checkpoint

Claude Sonnet 5.5 subscription authentication succeeded, but the bounded Edit
worker produced zero tool calls/edits and repeated `api_retry` service errors.
Its owned process was stopped (zero remaining owned processes verified); evidence remains outside source at
`C:/Github/licensing-evidence-20261027/implementation`. Odin implements directly
because this execution route is unavailable. No Claude API or Mistral probe used.
The saved terminal failure identifies sandbox DNS `ENOTFOUND`. Independent
read-only review is now running through the subscription-only Claude launcher
with approved network access and actual successful Read calls. That is a separate
review author, not commercial title clearance or legal advice.

Audit evidence: `C:/Github/licensing-evidence-20261027/audit-snapshot.json`,
`audit-summary.txt`, `audit-context.md`, `pypi-anyfem.json`. Original dirty checkout
and earlier GPL worktree remain untouched. Baseline has 641 tracked files.

## Verification and review checkpoint

Implementation is complete. Source consistency passed for GPL-3.0-only and 22
direct dependencies. `C:/Python/Python314/python.exe -m pytest
tests/test_license_policy.py tests/test_licensing_artifacts.py -q` passed **41
tests, 0 failures** after review-driven repairs. An initial fresh checkout at
`dd76958` passed 24 then-current tests, wheel/sdist build, metadata/artifact checks,
isolated no-dependency installation and an sdist-to-wheel rebuild. Final revised
artifact verification is recorded per candidate in the external evidence archive.

Independent review: two subscription-only Claude Sonnet 5.5 high-effort Read
reviews completed with actual successful Read calls and no edits/commands. The
follow-up found **no blocker to delivered source**. Findings were reconciled:
licensing-only wheel notices, scoped tracked-source scanning, hash-bound source
exceptions, source/notice/vendoring refusals, actual human notice rows and release
holds. Public audit wording was corrected after follow-up; unsupported future
license/header styles and title/binary clearance remain explicit limitations.
No review is a legal opinion or approval of an OEM grant.

Evidence: `C:/Github/licensing-evidence-20261027/` contains both reviews,
`review-disposition.md`, initial and final check commands, candidate identity,
process-wall timing, exit/cleanup receipts, complete outputs and built artifacts.
The first sandboxed Edit attempt made zero edits and failed DNS; its failure
evidence is preserved. Python's optional pyreadline console finalizer emits a
shutdown OSError in this host; the focused tests finish with exit zero.

Unrun: numerical/GUI/scientific campaigns, supported-platform matrix, complete
installed dependency release audit and installer qualification. Runtime source
and behavior are untouched; the sampled environment has missing/out-of-range
dependencies, so none of those gates is claimed. Workflow YAML syntax is checked
locally; hosted CI is not claimed green.

Publication check intentionally refuses version 0.4.2; the existing direct-URL
gui dependency is a second documented release hold. No release/merge, package
access change, commercial rights grant or sibling modification is authorized.
Recommend a bounded paid GPL onboarding/support engagement first, and an OEM
agreement only after a concrete customer and source/deployment rights clearance.
