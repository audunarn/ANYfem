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

Status: implementation complete; source check and 24 focused tests passed.
Fresh checkout, builds, installed-distribution checks and independent review in progress.
Final validation/review results will be recorded here before handoff.
