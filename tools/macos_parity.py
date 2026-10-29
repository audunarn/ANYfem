"""Compare the existing mesh parity oracle across Mac and Linux artifacts.

Reuses the owner's 1e-9 coordinate/1e-12 weight normalization in the existing
parallel mesh contract fixture; does not introduce new scientific tolerances.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import platform
import runpy
import sys

ROOT = Path(__file__).resolve().parents[1]


def write_snapshot(path):
    from anyfem.mesh_execution import MeshExecutionPolicy
    fixture = runpy.run_path(str(ROOT / "tests/parallel_mesh_contract.py"))
    cases = {}
    for name, options in (("linear", {}), ("quadratic", {"order": "quadratic"}), ("beams", {})):
        project = fixture["separated_plates"](2, beams=name == "beams")
        mesh = project.generate_mesh(0.4, strategy="native", execution_policy=MeshExecutionPolicy.off(), **options)
        cases[name] = fixture["mesh_signature"](mesh)
    # JSON normalizes tuples and numeric dictionary keys equally on both systems.
    data = {"platform": platform.platform(), "machine": platform.machine(), "cases": cases,
            "platform_key": "macOS" if sys.platform == "darwin" else platform.system(),
            "python": f"{sys.version_info.major}.{sys.version_info.minor}"}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True, default=float) + "\n", encoding="utf-8")


def compare(paths):
    snapshots = [(path, json.loads(path.read_text(encoding="utf-8"))) for path in paths]
    expected = {(os_name, python) for os_name in ("macOS", "Linux") for python in ("3.11", "3.14")}
    identities = [(data.get("platform_key"), data.get("python")) for _, data in snapshots]
    if len(identities) != len(expected) or set(identities) != expected:
        raise ValueError("comparison needs the complete Mac/Linux Python 3.11/3.14 matrix without duplicates")
    if any(data.get("machine") != "arm64" for _, data in snapshots if data.get("platform_key") == "macOS"):
        raise ValueError("Mac snapshots must be Apple Silicon")
    baseline = snapshots[0][1]["cases"]
    if set(baseline) != {"linear", "quadratic", "beams"}:
        raise ValueError("comparison has an incomplete case inventory")
    for path, data in snapshots:
        if data.get("cases") != baseline:
            raise ValueError(f"cross-platform mesh parity mismatch: {path}; preserve outputs and investigate")
    return {"status": "passed", "snapshots": [str(path) for path, _ in snapshots], "cases": sorted(baseline)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--write", type=Path)
    group.add_argument("--compare", nargs="+", type=Path)
    parser.add_argument("--out", type=Path)
    options = parser.parse_args(argv)
    if options.write:
        write_snapshot(options.write)
        return 0
    report = {"status": "failed"}
    try:
        report = compare(options.compare)
        return 0
    except Exception as error:
        report["error"] = f"{type(error).__name__}: {error}"
        raise
    finally:
        if options.out:
            options.out.parent.mkdir(parents=True, exist_ok=True)
            options.out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
