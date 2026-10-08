"""Pinned installed development smoke for the 19 affected legacy controls."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import time

import psutil


HOME = Path(__file__).resolve().parent
ROOT = HOME.parents[1]
SOLVER_SITE = Path(
    "C:/Github/ANYsolver/.perf2-worktrees/common-main-20261008/"
    ".development/common-main-20261008/site-v5"
)
PYTHON = Path("C:/Python/Python314/python.exe")
ATTEMPT = HOME / "installed-legacy-smoke-v1-a1"
MANIFEST = HOME / "manifest-v3.json"
REGISTRATION = HOME / "installed-legacy-smoke-registration-v2.json"
CAP_SECONDS = 45.0
CAP_BYTES = 2 * 1024**3


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    registration = json.loads(REGISTRATION.read_text(encoding="utf-8"))
    assert registration["status"] == "AUTHORIZED"
    assert registration["runner_sha256"] == sha(Path(__file__))
    assert registration["manifest_sha256"] == sha(MANIFEST)
    assert registration["consumer_commit"] == subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    assert registration["prior_ledger_sha256"] == sha(HOME / "ledger-v3.json")
    assert registration["child_cap_seconds"] == CAP_SECONDS
    assert registration["prior_remaining_seconds"] - CAP_SECONDS >= registration["reserve_seconds"]
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    phase = manifest["phases"]["affected-legacy"]
    assert phase["expected_cases"] == 19
    selectors = phase["tests"]
    assert len(selectors) == 5 and all(
        value.startswith("tests/") and "::test_" in value for value in selectors
    )
    test_hashes = {}
    for selector in selectors:
        relative = selector.split("::", 1)[0]
        test_path = ROOT / relative
        expected = manifest["test_files"].get(relative)
        assert expected and sha(test_path) == expected
        test_hashes[relative] = expected
    installation = json.loads((HOME / "installation-v3.json").read_text(encoding="utf-8"))
    assert sha(HOME / "wheel/anyfem-0.4.1-py3-none-any.whl") == installation["wheel_sha256"]
    solver_binding = json.loads((HOME / "solver-artifact-common-main-v1.json").read_text(encoding="utf-8"))
    assert solver_binding["wheel_sha256"] == manifest["solver_artifact"]["wheel_sha256"]
    assert solver_binding["wheel_sha256"] == "866509295b400c71c398d569e95077d2a38798c8dcdb3e36d4d62b0b9e01412b"
    ATTEMPT.mkdir(exist_ok=False)
    code = (
        "import json,sys;"
        f"sys.path[:0]=[{str(HOME / 'site')!r},{str(SOLVER_SITE)!r}];"
        "import anyfem,anysolver;"
        "print(json.dumps({'anyfem':anyfem.__file__,'anysolver':anysolver.__file__}),flush=True);"
        "import pytest;"
        f"raise SystemExit(pytest.main({['-x', '-q', '-p', 'no:cacheprovider', '-o', 'addopts=', *[str(ROOT / value) for value in selectors]]!r}))"
    )
    command = [str(PYTHON), "-I", "-B", "-c", code]
    env = os.environ.copy()
    env.update(
        OMP_NUM_THREADS="1", OPENBLAS_NUM_THREADS="1", MKL_NUM_THREADS="1",
        NUMEXPR_NUM_THREADS="1", NUMBA_DISABLE_JIT="1", NUMBA_NUM_THREADS="1",
        VECLIB_MAXIMUM_THREADS="1", BLIS_NUM_THREADS="1",
        PYTEST_DISABLE_PLUGIN_AUTOLOAD="1", PYTHONDONTWRITEBYTECODE="1",
    )
    for name in ("PYTEST_ADDOPTS", "PYTEST_PLUGINS", "PYTHONPATH"):
        env.pop(name, None)
    started = time.perf_counter_ns()
    peak = 0
    status = "started"
    with (ATTEMPT / "stdout.log").open("wb") as out, (ATTEMPT / "stderr.log").open("wb") as err:
        child = subprocess.Popen(command, cwd=ROOT, env=env, stdout=out, stderr=err)
        owned = psutil.Process(child.pid)
        owned.cpu_affinity([0])
        try:
            while child.poll() is None:
                tree = [owned, *owned.children(recursive=True)]
                try:
                    peak = max(peak, sum(item.memory_info().rss for item in tree))
                except psutil.NoSuchProcess:
                    pass
                elapsed = (time.perf_counter_ns() - started) / 1e9
                if peak > CAP_BYTES:
                    status = "memory_limit"
                    break
                if elapsed >= CAP_SECONDS:
                    status = "timeout"
                    break
                time.sleep(0.05)
            if status != "started":
                for item in reversed([owned, *owned.children(recursive=True)]):
                    try:
                        item.kill()
                    except psutil.NoSuchProcess:
                        pass
                child.wait(timeout=2)
            else:
                status = "passed" if child.returncode == 0 else "failed"
                if status == "passed":
                    output = (ATTEMPT / "stdout.log").read_text(encoding="utf-8")
                    if "19 passed" not in output:
                        status = "failed_count"
                    else:
                        first = output.splitlines()[0]
                        origins = json.loads(first)
                        if (Path(origins["anyfem"]).resolve() != (HOME/"site/anyfem/__init__.py").resolve() or
                                Path(origins["anysolver"]).resolve() != (SOLVER_SITE/"anysolver/__init__.py").resolve()):
                            status = "failed_origin"
        finally:
            elapsed = (time.perf_counter_ns() - started) / 1e9
            receipt = {
                "status": status, "exit_code": child.returncode,
                "process_wall_seconds": elapsed, "cap_seconds": CAP_SECONDS,
                "peak_tree_rss_bytes": peak, "command": command,
                "cwd": str(ROOT), "cpu_affinity": [0],
                "environment": {key: env[key] for key in (
                    "OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
                    "NUMBA_DISABLE_JIT", "NUMBA_NUM_THREADS")},
                "consumer_commit": "345db0cb73e7e7683872188b2a5ee4df0fd84b86",
                "solver_wheel_sha256": "866509295b400c71c398d569e95077d2a38798c8dcdb3e36d4d62b0b9e01412b",
                "test_files_sha256": test_hashes,
                "manifest_sha256": sha(MANIFEST),
                "consumer_wheel_sha256": installation["wheel_sha256"],
                "registration_sha256": sha(REGISTRATION),
                "prior_used_seconds": registration["prior_used_seconds"],
                "cumulative_used_seconds": registration["prior_used_seconds"] + elapsed,
                "remaining_seconds": registration["prior_remaining_seconds"] - elapsed,
                "runner_sha256": sha(Path(__file__)),
                "qualification": False,
            }
            (ATTEMPT / "receipt.json").write_text(
                json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
    print(json.dumps({"status": status, "wall": elapsed, "peak": peak,
                      "evidence": str(ATTEMPT)}))


if __name__ == "__main__":
    main()
