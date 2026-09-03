"""Headless checks for the latest-only source launcher."""

from __future__ import annotations

from importlib import metadata
from pathlib import Path
import runpy

import pytest


ROOT = Path(__file__).resolve().parents[1]


def _namespace():
    return runpy.run_path(str(ROOT / "run_gui.py"), run_name="preflight_test")


def _versions() -> dict[str, str]:
    return {
        "ANYmaterial": "0.2.0",
        "ANYgeometry": "0.4.2",
        "ANYfileio": "0.3.0",
        "ANYmesher": "0.4.0",
        "ANY3dView": "0.5.5",
        "ANYtk3D": "0.5.5",
        "ANYsolver": "0.4.1",
        "ANYfem": "0.4.0",
    }


def _origins(namespace) -> dict[str, str]:
    return {
        module: str(source / module / "__init__.py")
        for _distribution, module, source in namespace["_SOURCE_PROJECTS"]
    }


def test_latest_sources_and_metadata_pass_without_importing_tk():
    namespace = _namespace()
    versions = _versions()
    origins = _origins(namespace)

    namespace["require_compatible_ecosystem"](
        versions.__getitem__, origins.__getitem__
    )


def test_launcher_selects_the_coordinated_viewer_source_trees():
    namespace = _namespace()
    core = namespace["_ANY3DVIEW_PROJECT"]
    software = namespace["_ANYTK3D_PROJECT"]
    command = namespace["editable_repair_command"]()

    assert namespace["_version_at_least"](
        namespace["_declared_project_version"](core), "0.5.1"
    )
    assert namespace["_version_at_least"](
        namespace["_declared_project_version"](software), "0.5.1"
    )
    assert f'-e "{core}[gpu]"' in command
    assert f'-e "{software}"' in command
    assert command.index(str(core)) < command.index(str(software))


def test_launcher_selects_a_compatible_anymesher_checkout():
    namespace = _namespace()
    project = namespace["_ANYMESHER_PROJECT"]

    assert namespace["_version_at_least"](
        namespace["_declared_project_version"](project), "0.4.0"
    )
    assert f'-e "{project}"' in namespace["editable_repair_command"]()


def test_launcher_uses_selected_source_version_when_metadata_is_stale(
    monkeypatch,
):
    namespace = _namespace()
    versions = _versions()
    versions["ANYmesher"] = "0.3.2"
    monkeypatch.setattr(namespace["metadata"], "version", versions.__getitem__)

    assert namespace["_active_distribution_version"]("ANYmesher") == "0.4.0"

    def source_aware_reader(name: str) -> str:
        if name == "ANYmesher":
            return namespace["_active_distribution_version"](name)
        return versions[name]

    assert namespace["ecosystem_compatibility_problems"](source_aware_reader) == ()


def test_newer_major_generations_are_not_rejected_by_version_alone():
    namespace = _namespace()
    versions = _versions()
    versions.update(
        {
            "ANYgeometry": "4.0.0",
            "ANYmesher": "3.0.0",
            "ANYsolver": "1.0.0",
            "ANYfem": "1.0.0",
        }
    )

    assert namespace["ecosystem_compatibility_problems"](
        versions.__getitem__
    ) == ()


def test_stale_metadata_fails_with_one_dependency_order_repair_command():
    namespace = _namespace()
    versions = _versions()
    origins = _origins(namespace)
    versions["ANYsolver"] = "0.2.9"

    with pytest.raises(RuntimeError) as raised:
        namespace["require_compatible_ecosystem"](
            versions.__getitem__, origins.__getitem__
        )

    message = str(raised.value)
    assert "ANYsolver>=0.4.1,<0.5: installed metadata reports 0.2.9" in message
    command = namespace["editable_repair_command"]()
    assert command in message
    mesh_project = str(namespace["_ANYMESHER_PROJECT"])
    assert command.index("ANYgeometry") < command.index(mesh_project)
    assert command.index(mesh_project) < command.index("ANYio")
    tk_project = str(namespace["_ANYTK3D_PROJECT"])
    solver_project = str(namespace["_ANYSOLVER_PROJECT"])
    assert command.index(mesh_project) < command.index(tk_project)
    assert command.index(tk_project) < command.index(f'-e "{solver_project}"')
    assert command.index(f'-e "{solver_project}"') < command.index(
        f'-e "{ROOT}[gui]"'
    )


def test_wrong_module_origin_is_rejected_before_gui_import():
    namespace = _namespace()
    origins = _origins(namespace)
    origins["anymesher"] = r"C:\stale\site-packages\anymesher\__init__.py"

    problems = namespace["ecosystem_origin_problems"](origins.__getitem__)

    assert len(problems) == 1
    assert "ANYmesher" in problems[0]
    assert "C:\\stale\\site-packages" in problems[0]


def test_missing_distribution_metadata_is_actionable():
    namespace = _namespace()
    versions = _versions()

    def reader(name: str) -> str:
        if name == "ANYfileio":
            raise metadata.PackageNotFoundError(name)
        return versions[name]

    assert namespace["ecosystem_compatibility_problems"](reader) == (
        "ANYfileio>=0.3,<0.4: distribution metadata is missing",
    )
