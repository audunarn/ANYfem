"""Exit 0 only when two folders hold exactly the same files with exactly the same bytes (including ``runtime.lock.json``)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from anyfem import runtime_lock as rl  # noqa: E402


def _listing(root: Path) -> dict:
    return {p.relative_to(root).as_posix(): rl.sha256_file(p) for p in sorted(root.rglob("*")) if p.is_file()}


def main(argv=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 2:
        print("usage: ci_compare_bundles.py <folder-a> <folder-b>", file=sys.stderr)
        return 2
    a, b = Path(args[0]), Path(args[1])
    if not a.is_dir() or not b.is_dir():
        print("FAIL: both folders must exist")
        return 1
    left, right = _listing(a), _listing(b)
    if left != right:
        names = sorted(set(left) ^ set(right)) + sorted(k for k in set(left) & set(right) if left[k] != right[k])
        print("FAIL: builds differ in %d files, e.g. %s" % (len(names), ", ".join(names[:5])))
        return 1
    print("ok: %d files identical" % len(left))
    return 0


if __name__ == "__main__":
    sys.exit(main())
