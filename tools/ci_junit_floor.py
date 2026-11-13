"""Fail when a JUnit report has fewer executed tests than expected, or any skipped test (a silently skipped canary is a false green)."""
from __future__ import annotations

import argparse
import sys
import xml.etree.ElementTree as ET


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("report")
    parser.add_argument("--min-tests", type=int, required=True)
    parser.add_argument("--max-skipped", type=int, default=0)
    args = parser.parse_args(argv)
    root = ET.parse(args.report).getroot()
    suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
    tests = sum(int(s.get("tests", 0)) for s in suites)
    skipped = sum(int(s.get("skipped", 0)) for s in suites)
    bad = sum(int(s.get("failures", 0)) + int(s.get("errors", 0)) for s in suites)
    print("tests=%d skipped=%d failed=%d (floor %d, max skipped %d)" % (tests, skipped, bad, args.min_tests, args.max_skipped))
    if bad or tests - skipped < args.min_tests or skipped > args.max_skipped:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
