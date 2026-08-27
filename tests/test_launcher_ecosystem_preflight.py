"""Headless checks for the latest-only source launcher."""

from __future__ import annotations

from importlib import metadata
from pathlib import Path
import runpy
import tomllib

import pytest


ROOT = Path(__file__).resolve().parents[1]


def _namespace():
    return runpy.run_path(str(ROOT / "run_gui.py"), run_name="preflight_test")


@pytest.fixture
def candidate_namespace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    candidate = tmp_path / "ANYmesh-0.3.2"
    candidate.mkdir()
    (candidate / "src").mkdir()
    (candidate / "pyproject.toml").write_text(
        '[project]\nname = "ANYmesher"\nversion = "0.3.2"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("ANYMESHER_SOURCE", str(candidate))
    return _namespace()


def _versions() -> dict[str, str]:
    return {
        "ANYmaterial": "0.1.1",
        "ANYgeometry": "0.4.1",
        "ANYfileio": "0.2.1",
        "ANYmesher": "0.3.2",
        "ANY3dView": "0.5.4",
        "ANYtk3D": "0.5.3",
        "ANYsolver": "0.4.0",
        "ANYfem": "0.4.0",
    }


def _origins(namespace) -> dict[str, str]:
    return {
        module: str(source / module / "__init__.py")
        for _distribution, module, source in namespace["_SOURCE_PROJECTS"]
    }


def test_latest_sources_and_metadata_pass_without_importing_tk(
    candidate_namespace,
):
    namespace = candidate_namespace
    versions = _versions()
    origins = _origins(namespace)

    namespace["require_compatible_ecosystem"](
        versions.__getitem__, origins.__getitem__
    )


def test_launcher_selects_the_coordinated_viewer_source_trees(
    candidate_namespace,
):
    namespace = candidate_namespace
    core = namespace["_ANY3DVIEW_PROJECT"]
    software = namespace["_ANYTK3D_PROJECT"]
    command = namespace["editable_repair_command"]()

    requirements = {
        distribution: requirement
        for distribution, requirement, _minimum, _maximum in namespace[
            "ECOSYSTEM_REQUIREMENTS"
        ]
    }
    assert requirements["ANY3dView"] == "ANY3dView[gpu]>=0.5.4,<0.6"
    assert requirements["ANYtk3D"] == "ANYtk3D>=0.5.3,<0.6"
    assert f'-e "{core}[gpu]"' in command
    assert f'-e "{software}"' in command
    assert command.index(str(core)) < command.index(str(software))


def test_launcher_selects_a_compatible_anymesher_checkout(candidate_namespace):
    namespace = candidate_namespace
    project = namespace["_ANYMESHER_PROJECT"]

    assert namespace["_version_at_least"](
        namespace["_declared_project_version"](project), "0.3.2"
    )
    assert f'-e "{project}"' in namespace["editable_repair_command"]()


def test_incompatible_major_generations_are_rejected_by_declared_caps(
    candidate_namespace,
):
    namespace = candidate_namespace
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
    ) == (
        "ANYgeometry[planar]>=0.4.1,<0.5: installed metadata reports 4.0.0",
        "ANYmesher>=0.3.2,<0.4: installed metadata reports 3.0.0",
        "ANYsolver>=0.4.0,<0.5: installed metadata reports 1.0.0",
        "ANYfem>=0.4.0,<0.5: installed metadata reports 1.0.0",
    )


def test_stale_metadata_fails_with_one_dependency_order_repair_command(
    candidate_namespace,
):
    namespace = candidate_namespace
    versions = _versions()
    origins = _origins(namespace)
    versions["ANYsolver"] = "0.2.9"

    with pytest.raises(RuntimeError) as raised:
        namespace["require_compatible_ecosystem"](
            versions.__getitem__, origins.__getitem__
        )

    message = str(raised.value)
    assert "ANYsolver>=0.4.0,<0.5: installed metadata reports 0.2.9" in message
    command = namespace["editable_repair_command"]()
    assert command in message
    mesh_project = str(namespace["_ANYMESHER_PROJECT"])
    assert command.index("ANYgeometry") < command.index(mesh_project)
    assert command.index(mesh_project) < command.index("ANYfileIO")
    tk_project = str(namespace["_ANYTK3D_PROJECT"])
    solver_project = str(namespace["_ECOSYSTEM_ROOT"] / "ANYsolver")
    fem_project = str(namespace["_ROOT"])
    assert command.index(mesh_project) < command.index(tk_project)
    assert command.index(tk_project) < command.index(f'-e "{solver_project}"')
    assert command.index(f'-e "{solver_project}"') < command.index(
        f'-e "{fem_project}[gui]"'
    )


def test_wrong_module_origin_is_rejected_before_gui_import(candidate_namespace):
    namespace = candidate_namespace
    origins = _origins(namespace)
    origins["anymesher"] = r"C:\stale\site-packages\anymesher\__init__.py"

    problems = namespace["ecosystem_origin_problems"](origins.__getitem__)

    assert len(problems) == 1
    assert "ANYmesher" in problems[0]
    assert "C:\\stale\\site-packages" in problems[0]


def test_missing_distribution_metadata_is_actionable(candidate_namespace):
    namespace = candidate_namespace
    versions = _versions()

    def reader(name: str) -> str:
        if name == "ANYfileio":
            raise metadata.PackageNotFoundError(name)
        return versions[name]

    assert namespace["ecosystem_compatibility_problems"](reader) == (
        "ANYfileio[semantics]>=0.2.1,<0.3: distribution metadata is missing",
    )


def test_s3_policy_binds_coordinated_solver_and_mesher_floors(
    candidate_namespace,
) -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))[
        "project"
    ]
    assert {
        "ANYsolver>=0.4.0,<0.5",
        "ANYmaterial>=0.1.1,<0.2",
        "ANYgeometry[planar]>=0.4.1,<0.5",
        "ANYmesher>=0.3.2,<0.4",
        "ANYfileio[semantics]>=0.2.1,<0.3",
    } <= set(project["dependencies"])
    assert {
        "ANY3dView[gpu]>=0.5.4,<0.6",
        "ANYtk3D>=0.5.3,<0.6",
    } <= set(project["optional-dependencies"]["gui"])

    versions = _versions()
    versions["ANYmesher"] = "0.3.1"
    assert candidate_namespace["ecosystem_compatibility_problems"](
        versions.__getitem__
    ) == ("ANYmesher>=0.3.2,<0.4: installed metadata reports 0.3.1",)

    versions = _versions()
    versions["ANYfileio"] = "0.3.0"
    assert candidate_namespace["ecosystem_compatibility_problems"](
        versions.__getitem__
    ) == (
        "ANYfileio[semantics]>=0.2.1,<0.3: installed metadata reports 0.3.0",
    )


def test_actions_ecosystem_checkout_root_is_preferred(tmp_path) -> None:
    namespace = _namespace()
    repository_root = tmp_path / "ANYfem"
    embedded = repository_root / ".ecosystem"
    (embedded / "ANYsolver").mkdir(parents=True)
    (embedded / "ANYmesh").mkdir()
    (tmp_path / "ANYsolver").mkdir()
    (tmp_path / "ANYmesh").mkdir()

    assert namespace["_ecosystem_root"](repository_root) == embedded


def test_anyfileio_uses_only_the_canonical_repository_and_source_path(
    candidate_namespace,
) -> None:
    workflow = (ROOT / ".github" / "workflows" / "tests.yml").read_text(
        encoding="utf-8"
    )
    source = dict(
        (distribution, project)
        for distribution, _module, project in candidate_namespace["_SOURCE_PROJECTS"]
    )

    assert "repository: audunarn/ANYfileIO" in workflow
    assert "path: .ecosystem/ANYfileIO" in workflow
    assert "repository: audunarn/ANYio" not in workflow
    assert ".ecosystem/ANYio" not in workflow
    assert source["ANYfileio"] == (
        candidate_namespace["_ECOSYSTEM_ROOT"] / "ANYfileIO" / "src"
    )
    assert 'ANYfileio[semantics]>=0.2.1,<0.3' in (
        ROOT / "pyproject.toml"
    ).read_text(encoding="utf-8")


def test_ci_binds_exact_release_graph_revisions_and_fails_closed_for_solver() -> None:
    workflow = (ROOT / ".github" / "workflows" / "tests.yml").read_text(
        encoding="utf-8"
    )
    expected_refs = {
        "2b6431c291c8f571803484f69d08807875996b72",
        "97b06b0cfc72179c4f6522f9077d8a1d91911d61",
        "c06c8fa9ca58f282941a921548bf8303a8ddd084",
        "07124405ce0160437928e9b0c3c7a0d530c1f5de",
        "ebe8245538504633b2b5a6579e16c4fd321d2f0e",
        "516aa46ec8affaa737fd165efad7c7b45a2b852a",
    }
    for revision in expected_refs:
        assert workflow.count(f"ref: {revision}") == 2
    assert workflow.count("ref: REBIND_FINAL_ANYSOLVER_COMMIT") == 2
    assert workflow.count("repository: audunarn/") == 14
    assert workflow.count("          ref: ") == 14


def test_production_publish_uses_verified_prebuilt_release_assets() -> None:
    workflow = (ROOT / ".github" / "workflows" / "publish.yml").read_text(
        encoding="utf-8"
    )
    production = workflow.split("  publish-production:\n", 1)[1]

    assert "release:\n    types: [published]" in workflow
    assert "workflow_dispatch:" in workflow
    assert "if: github.event_name == 'workflow_dispatch'" in workflow
    assert 'test "$RELEASE_TAG" = "v0.4.0"' in production
    assert 'gh release download "$RELEASE_TAG"' in production
    assert "ANYfem-0.4.0-SHA256SUMS.txt" in production
    assert "checksum manifest does not exactly cover distributions" in production
    assert '"anyfem-0.4.0-py3-none-any.whl"' in production
    assert '"anyfem-0.4.0.tar.gz"' in production
    assert "release does not contain the exact ANYfem artifact set" in production
    assert "unexpected ANYfem distribution asset" in production
    assert "release checksum mismatch" in production
    assert "pypa/gh-action-pypi-publish@release/v1" in production
    assert "packages-dir: dist/" in production
    assert "python -m build" not in production
    assert "timeout-minutes:" not in workflow
