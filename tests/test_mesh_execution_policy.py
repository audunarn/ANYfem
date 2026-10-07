"""Focused units for the mesh execution policy: modes, env, sizing, routing.

The policy is execution-only: it must stay a plain picklable dataclass, refuse
invalid input, keep ``auto`` as the default and fail closed to serial with an
explicit reason on every unsupported path.
"""

from __future__ import annotations

import pickle
import subprocess
import sys

import pytest

from anyfem.mesh_execution import (
    AUTO_MAX_WORKERS,
    AUTO_MIN_COMPONENTS,
    AUTO_RESERVED_CORES,
    ENVIRONMENT_VARIABLE,
    MIN_ESTIMATED_ELEMENTS_COLD,
    MIN_ESTIMATED_ELEMENTS_WARM,
    MeshExecutionPolicy,
    MeshExecutionRoute,
    auto_worker_count,
    environment_policy,
    resolve_policy,
    resolve_route,
)


def test_automatic_is_the_default(monkeypatch):
    monkeypatch.delenv(ENVIRONMENT_VARIABLE, raising=False)
    policy = MeshExecutionPolicy()
    assert policy.mode == "auto"
    assert policy.workers is None
    assert policy.requests_parallel
    assert resolve_policy(None, {}) == MeshExecutionPolicy.auto()
    assert resolve_policy(None) == MeshExecutionPolicy.auto()
    assert resolve_policy(None, {ENVIRONMENT_VARIABLE: "off"}) == MeshExecutionPolicy.off()
    assert resolve_policy(MeshExecutionPolicy.off(), {ENVIRONMENT_VARIABLE: "auto"}) == MeshExecutionPolicy.off()


def test_invalid_policies_refuse():
    with pytest.raises(ValueError):
        MeshExecutionPolicy(mode="bogus")
    with pytest.raises(ValueError):
        MeshExecutionPolicy(mode="auto", workers=2)
    with pytest.raises(ValueError):
        MeshExecutionPolicy(mode="explicit")
    with pytest.raises(ValueError):
        MeshExecutionPolicy(mode="explicit", workers=0)
    with pytest.raises(ValueError):
        MeshExecutionPolicy(mode="explicit", workers=True)
    with pytest.raises(ValueError):
        MeshExecutionPolicy(mode="explicit", workers="4")


@pytest.mark.parametrize("workers", [True, 2.5, "2"])
def test_explicit_factory_rejects_non_integer_counts(workers):
    with pytest.raises(ValueError, match="integer worker count"):
        MeshExecutionPolicy.explicit(workers)


def test_policy_is_plain_and_picklable():
    policy = MeshExecutionPolicy.explicit(3)
    assert pickle.loads(pickle.dumps(policy)) == policy
    route = MeshExecutionRoute("parallel", workers=3, reason="r")
    assert pickle.loads(pickle.dumps(route)) == route


def test_environment_variable_resolution():
    assert environment_policy({}) is None
    assert environment_policy({ENVIRONMENT_VARIABLE: ""}) is None
    assert environment_policy({ENVIRONMENT_VARIABLE: "  "}) is None
    for text in ("off", "0", "none", "false", "OFF"):
        assert environment_policy({ENVIRONMENT_VARIABLE: text}) == MeshExecutionPolicy.off()
    assert environment_policy({ENVIRONMENT_VARIABLE: "auto"}) == MeshExecutionPolicy.auto()
    assert environment_policy({ENVIRONMENT_VARIABLE: "3"}) == MeshExecutionPolicy.explicit(3)


def test_invalid_environment_refuses():
    for raw in ("x", "-2", "2.5", "1e3"):
        with pytest.raises(ValueError, match=ENVIRONMENT_VARIABLE):
            environment_policy({ENVIRONMENT_VARIABLE: raw})


def test_explicit_request_wins_over_environment():
    requested = MeshExecutionPolicy.explicit(2)
    assert resolve_policy(requested, {ENVIRONMENT_VARIABLE: "auto"}) is requested
    assert resolve_policy(None, {ENVIRONMENT_VARIABLE: "auto"}) == MeshExecutionPolicy.auto()
    with pytest.raises(ValueError):
        resolve_policy("parallel", {})


def test_auto_sizing_reserves_cores_and_caps_the_pool():
    assert auto_worker_count(cpu_count=1) == 1
    assert auto_worker_count(cpu_count=8) == 8 - AUTO_RESERVED_CORES
    assert auto_worker_count(cpu_count=100) == AUTO_MAX_WORKERS
    assert auto_worker_count(cpu_count=100, platform="win32") == AUTO_MAX_WORKERS


def _api():
    def generate(model, *, parallel=None, **parameters):
        return None

    class Options:
        def __init__(self, workers=None, min_estimated_elements=None,
                     min_components=None, pool_lease=None):
            self.workers = workers

    return generate, Options, "mesh"


def test_route_off_is_serial_with_reason():
    route = resolve_route(MeshExecutionPolicy.off(), strategy="auto", api=_api())
    assert not route.parallel
    assert route.reason == "parallel meshing is off"


def test_route_refuses_unsupported_paths_before_the_api():
    for kwargs in (
        {"automation": object()},
        {"change_set": object()},
        {"quad_options": object()},
        {"strict": True},
    ):
        route = resolve_route(MeshExecutionPolicy.explicit(2), strategy="auto", api=_api(), **kwargs)
        assert not route.parallel, kwargs
        assert route.reason
    assert not resolve_route(MeshExecutionPolicy.explicit(2), strategy="mapped", api=_api()).parallel


def test_route_missing_api_fails_closed_to_serial():
    # The probe is injected: the branch must be tested exactly, not inferred
    # from whatever ANYmesher build happens to be installed here.
    route = resolve_route(
        MeshExecutionPolicy.auto(), strategy="auto", api=None, api_probe=lambda: None
    )
    assert not route.parallel
    assert "does not provide" in route.reason


def test_explicit_route_bypasses_size_gates_only():
    route = resolve_route(MeshExecutionPolicy.explicit(3), strategy="auto", api=_api())
    assert route.parallel and route.workers == 3
    assert route.min_estimated_elements is None
    assert route.min_components is None


def test_auto_route_carries_its_eligibility_gates():
    cold = resolve_route(MeshExecutionPolicy.auto(), strategy="auto", api=_api(),
                         cpu_count=8, platform="linux")
    assert cold.parallel and cold.workers == 6
    assert cold.min_estimated_elements == MIN_ESTIMATED_ELEMENTS_COLD
    assert cold.min_components == AUTO_MIN_COMPONENTS
    warm = resolve_route(MeshExecutionPolicy.auto(), strategy="auto", api=_api(),
                         warm_pool=True, cpu_count=8, platform="linux")
    assert warm.min_estimated_elements == MIN_ESTIMATED_ELEMENTS_WARM
    assert warm.min_components == AUTO_MIN_COMPONENTS


def test_core_execution_modules_import_no_qt_or_tk():
    code = (
        "import sys\n"
        "import anyfem.mesh_execution, anyfem.job_process, anyfem.mesh_jobs\n"
        "loaded = [name for name in sys.modules if name.split('.')[0] in "
        "('PySide6', 'PyQt5', 'PyQt6', 'tkinter')]\n"
        "assert not loaded, loaded\n"
        "print('clean')\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=60
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "clean"
