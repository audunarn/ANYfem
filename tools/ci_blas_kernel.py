"""Record / check the effective OpenBLAS kernel of the running interpreter (N2c identity gate, kernel pin).

The full-model prepared model, plan and counters depend on the BLAS kernel (S3 T7b section C: HASWELL and NEHALEM differ from the
frozen SKYLAKEX constants; NEHALEM refuses preparation).  The constants are therefore valid for ONE kernel, which the driver must pin
with OPENBLAS_CORETYPE and the child must record.  This tool:

  python tools/ci_blas_kernel.py --constants tools/n2c_identity_constants.json   preflight: exit 0 only if the pin is requested AND in effect
  python tools/ci_blas_kernel.py                                                  just print the record (JSON)

`record()` is the record the measurement child stores as child-result["blas"] (same shape; the child inlines the same few lines).
`threadpoolctl`'s `architecture` is the kernel OpenBLAS really selected: a SKYLAKEX request on a CPU without AVX-512 may fall back
silently, which shows up as requested != effective (exit 4, same code as the gate's KERNEL exit).  The verdict logic is the gate's
(`ci_n2c_gate.kernel_problems`), loaded from this folder, so tool and gate cannot disagree.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
from pathlib import Path

EXIT_KERNEL = 4
EXIT_INPUT = 2


def _threadpool_info() -> list:
    import numpy  # noqa: F401  (loaded before the scan so its BLAS library is in the process)
    try:
        import scipy.linalg  # noqa: F401
    except ImportError:
        pass
    from threadpoolctl import threadpool_info
    return threadpool_info()


def record() -> dict:
    pools = []
    for pool in _threadpool_info():
        if pool.get("internal_api") == "openblas":
            pools.append({"internal_api": "openblas", "architecture": pool.get("architecture"), "num_threads": pool.get("num_threads")})
    return {"requested_coretype": os.environ.get("OPENBLAS_CORETYPE"), "openblas_pools": pools}


def _gate():
    spec = importlib.util.spec_from_file_location("ci_n2c_gate_for_kernel", Path(__file__).with_name("ci_n2c_gate.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def check(rec: dict, kernel: dict) -> list:
    return _gate().kernel_problems("this interpreter", rec, kernel)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--constants", help="check the record against the kernel in this constants file (exit 4 on a mismatch)")
    args = parser.parse_args(argv)
    try:
        rec = record()
    except Exception as exc:                    # threadpoolctl missing or failing: nothing proves the kernel
        print("N2C KERNEL PREFLIGHT INPUT ERROR (fail closed): cannot read the BLAS kernel: %r" % (exc,))
        return EXIT_INPUT
    print(json.dumps(rec, sort_keys=True))
    if not args.constants:
        return 0
    gate = _gate()
    try:
        kernel = gate.load_constants(args.constants)["blas_kernel"]
    except gate.InputError as exc:
        print("N2C KERNEL PREFLIGHT INPUT ERROR (fail closed):", exc)
        return EXIT_INPUT
    problems = check(rec, kernel)
    for line in problems:
        print("N2C KERNEL PREFLIGHT FAILED:", line)
    if problems:
        return EXIT_KERNEL
    print("N2C KERNEL PREFLIGHT OK: %s requested and in effect" % kernel["openblas_architecture"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
