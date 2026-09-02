from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace

import pytest

from anyfem import Project, ShellFormulationPolicy
from anyfem.io.project_file import ProjectFileError, project_from_dict, project_to_dict
from anyfem.io.result_artifact import _solution_submission_identity
from anyfem.solve.build import BuiltModel
from anymesher import Mesh


def test_new_project_persists_qualified_q4_and_v2d_s3_policy() -> None:
    project = Project("qualified defaults")

    payload = project_to_dict(project)
    restored = project_from_dict(deepcopy(payload))

    assert payload["anyfem"]["format"] == 8
    assert payload["shell_formulations"] == {
        "schema": "anyfem.shell-formulation-policy-v1",
        "q4": "e4-pl",
        "s3": "e4-pl-s3-v2d",
        "higher_order": "legacy",
        "q4_formulation_id": "E4_PL_QUALIFIED_Q4_HYBRID_V2",
        "s3_formulation_id": "CANDIDATE_E4_PL_S3_V2D_NATIVE_PARITY_V1",
        "higher_order_formulation_id": "LEGACY_SHELL_ELEMENT_HIGHER_ORDER",
    }
    assert restored.shell_formulation_policy == ShellFormulationPolicy.current_default()
    assert project_to_dict(restored) == payload


def test_pre_policy_project_migrates_both_topologies_to_legacy() -> None:
    payload = project_to_dict(Project("format 7"))
    payload["anyfem"]["format"] = 7
    payload.pop("shell_formulations")

    restored = project_from_dict(payload)

    assert restored.shell_formulation_policy == ShellFormulationPolicy.legacy_compatible()
    assert any(
        "Q4 and TRI3 remain explicit legacy" in item
        for item in restored.compatibility_diagnostics
    )


def test_current_format_requires_policy_and_rejects_rejected_s3_v1() -> None:
    missing = project_to_dict(Project("missing"))
    missing.pop("shell_formulations")
    with pytest.raises(ProjectFileError, match="format 8 shell_formulations is required"):
        project_from_dict(missing)

    rejected = project_to_dict(Project("rejected"))
    rejected["shell_formulations"]["s3"] = "e4-pl-s3"
    with pytest.raises(ProjectFileError, match="unsupported shell formulation s3"):
        project_from_dict(rejected)


def test_built_model_freezes_current_formulation_provenance() -> None:
    project = Project("qualified provenance")
    built = BuiltModel(
        fe_model=SimpleNamespace(),
        load_case=None,
        mesh=Mesh(),
        project=project,
    )
    project.shell_formulation_policy = ShellFormulationPolicy.legacy_compatible()

    identity = _solution_submission_identity(SimpleNamespace(built=built))

    assert identity["shell_formulations"]["q4"] == "e4-pl"
    assert identity["shell_formulations"]["s3"] == "e4-pl-s3-v2d"
    assert identity["shell_formulations"]["s3_formulation_id"] == (
        "CANDIDATE_E4_PL_S3_V2D_NATIVE_PARITY_V1"
    )
