"""CI T1 health step: the built bundle passes the application's health check, and (for a CI smoke bundle) is refused without the opt-in.

Exit 0 only when both hold.  Uses the application's own ``thread_route.check_health``; the opt-in is the in-process
``allow_ci_smoke_bundle`` call, which nothing in the application ever makes.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from anyfem import runtime_lock as rl  # noqa: E402
from anyfem import thread_route as tr  # noqa: E402


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=300.0)
    args = parser.parse_args(argv)
    python = args.runtime / "python.exe"
    marked = os.path.lexists(args.runtime / rl.CI_SMOKE_MARKER)
    if marked:
        tr.allow_ci_smoke_bundle(False)
        refused = tr.check_health(python, timeout=args.timeout)
        if refused.status != tr.UNAVAILABLE or "ci smoke" not in (refused.reason or "").lower():
            print("FAIL: a CI smoke bundle was not refused by the application check: %s %s" % (refused.status, refused.reason))
            return 1
        print("ok: the application check refuses the smoke bundle without the opt-in")
        tr._health_cache.clear()
        tr.allow_ci_smoke_bundle(True)
    outcome = tr.check_health(python, timeout=args.timeout)
    if outcome.status != tr.OK:
        print("FAIL: health check %s: %s" % (outcome.status, outcome.reason))
        return 1
    print("ok: health check passed; %s" % ((outcome.body or {}).get("health"),))
    return 0


if __name__ == "__main__":
    sys.exit(main())
