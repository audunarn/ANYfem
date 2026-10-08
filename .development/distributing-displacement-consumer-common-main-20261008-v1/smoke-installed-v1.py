"""Disposable installed development smoke; not a registered gate result."""

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
ATTEMPT = HOME / "installed-smoke-v1-a1"
TEST_FILE = ROOT / "tests/test_stiffened_shell_ge_distributing_displacement_consumer.py"
CASES = (
    "test_request_roundtrip_owned_inputs_and_force_identity",
    "test_example_shell_translation_reads_flat_public_output",
    "test_public_checkpoint_example",
)
CAP_SECONDS = 45.0
CAP_BYTES = 2 * 1024**3


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    ATTEMPT.mkdir(exist_ok=False)
    code = (
        "import json,sys;"
        f"sys.path[:0]=[{str(HOME / 'site')!r},{str(SOLVER_SITE)!r}];"
        "import anyfem,anysolver;"
        "print(json.dumps({'anyfem':anyfem.__file__,'anysolver':anysolver.__file__}),flush=True);"
        "import pytest;"
        f"raise SystemExit(pytest.main({['-x', '-q', '-p', 'no:cacheprovider', '-o', 'addopts=', *[str(TEST_FILE) + '::' + case for case in CASES]]!r}))"
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
                "test_file_sha256": sha(TEST_FILE), "runner_sha256": sha(Path(__file__)),
                "qualification": False,
            }
            (ATTEMPT / "receipt.json").write_text(
                json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
    print(json.dumps({"status": status, "wall": elapsed, "peak": peak,
                      "evidence": str(ATTEMPT)}))


if __name__ == "__main__":
    main()
