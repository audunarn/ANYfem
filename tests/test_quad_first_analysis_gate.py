from __future__ import annotations

from types import SimpleNamespace

import pytest

from anyfem.model.project import ProjectError
from anyfem.solve import run as run_module


def _quad_first_built() -> SimpleNamespace:
    mesh = SimpleNamespace(hybrid_diagnostics={"quad_first_api": True})
    return SimpleNamespace(mesh=mesh)


def _resolve(**options):
    return run_module._resolve_built(
        None,
        _quad_first_built(),
        mesh=None,
        target_size=None,
        overrides=None,
        progress=None,
        **options,
    )


def test_quad_first_meshes_stay_blocked_for_unqualified_analyses() -> None:
    with pytest.raises(ProjectError, match="separate qualification") as caught:
        _resolve()
    assert (
        "linear-static and nonlinear-static consumption only"
        in str(caught.value)
    )


def test_nonlinear_static_accepts_quad_first_meshes(monkeypatch) -> None:
    assert _resolve(allow_quad_first=True) is not None
    seen: dict[str, object] = {}

    def fake_resolve(project, built, **options):
        seen.update(options)
        raise RuntimeError("stop after resolution")

    monkeypatch.setattr(run_module, "_resolve_built", fake_resolve)
    with pytest.raises(RuntimeError, match="stop after resolution"):
        run_module.solve_nonlinear_static(built=_quad_first_built())
    assert seen["allow_quad_first"] is True


_ANALYSES = {
    "solve_linear_static": ({}, True),
    "solve_nonlinear_static": ({}, True),
    "solve_modal": ({}, False),
    "solve_buckling": ({}, False),
    "solve_capacity": ({}, False),
    "solve_arc_length": ({}, False),
    "solve_transient": ({"dt": 0.1, "t_end": 1.0}, False),
    "solve_impact": ({"collision": object()}, False),
}


@pytest.mark.parametrize("name", tuple(_ANALYSES))
def test_quad_first_acceptance_is_limited_to_the_qualified_analyses(
    monkeypatch, name: str
) -> None:
    arguments, accepted = _ANALYSES[name]
    seen: dict[str, object] = {}

    class Stop(Exception):
        pass

    def fake_resolve(project, built, **options):
        seen.update(options)
        raise Stop

    monkeypatch.setattr(run_module, "_resolve_built", fake_resolve)
    with pytest.raises(Stop):
        getattr(run_module, name)(built=_quad_first_built(), **arguments)
    assert bool(seen.get("allow_quad_first", False)) is accepted
