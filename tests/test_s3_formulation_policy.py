from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace

import numpy as np
import pytest
from anygeometry import GeometryModel
from anymesher import Mesh, S3_QUALITY_CONTRACT_ID
from anymesher import (
    QUALIFIED_S3_PRODUCTION_CONTRACT_ID,
    S3_REPAIR_CONTRACT_ID,
)
from anysolver import (
    LegacyShellElement,
    QualifiedE4PLS3ShellElement,
    QualifiedE4PLShellElement,
    shell_element_from_dict,
)

from anyfem.io.project_file import ProjectFileError, project_from_dict, project_to_dict
from anyfem.io.result_artifact import _solution_submission_identity
from anyfem.io.sesam import ImportedModel, SesamImportError
from anyfem.model import Project, ProjectError, ShellFormulationPolicy
import anyfem.solve.build as build_module


def _bind_qualified_s3_authority(
    project,
    mesh: Mesh,
    *,
    normal=(0.0, 0.0, 1.0),
) -> None:
    """Attach the exact production packet expected from qualified meshing."""

    shell_ids = tuple(sorted(int(value) for value in mesh.shells))
    triangle_ids = tuple(sorted(int(value) for value in mesh.tris))
    shell_nodes = {
        int(node_id)
        for connectivity in mesh.shells.values()
        for node_id in connectivity
    }
    face_by_element = {
        int(element_id): int(face_id)
        for face_id, element_ids in mesh.elements_of_face.items()
        for element_id in element_ids
    }
    mesh.geometry_model_id = str(project.geometry.model_id)
    mesh.geometry_revision = int(project.geometry.revision)
    mesh.structural_preparation = {
        "qualified_s3": {
            "admission": {"elements": [], "topology_violations": []},
            "authority_model": {
                "prepared_revision": int(project.geometry.revision),
                "scope": "PREPARED_GEOMETRY_ORIENTED_SHEET_FACE_USE",
                "source_model_id": str(project.geometry.model_id),
                "source_revision": int(project.geometry.revision),
            },
            "contract_id": QUALIFIED_S3_PRODUCTION_CONTRACT_ID,
            "element_ids": list(triangle_ids),
            "element_owner_normals": {
                str(element_id): list(normal) for element_id in shell_ids
            },
            "element_owner_sources": {
                str(element_id): {
                    "face_id": face_by_element[element_id],
                    "face_use_ids": [1],
                    "sheet_ids": [1],
                }
                for element_id in shell_ids
            },
            "formulation_id": "E4_PL_QUALIFIED_S3_COMPANION_V1",
            "legacy_fallback": "FORBIDDEN",
            "nodal_normals": {
                str(node_id): list(normal) for node_id in sorted(shell_nodes)
            },
            "quality_contract_id": S3_QUALITY_CONTRACT_ID,
            "repair": {},
            "repair_contract_id": S3_REPAIR_CONTRACT_ID,
            "schema": "anymesher.qualified-s3-production-preparation-v1",
            "status": "ADMITTED",
        }
    }


def test_shell_formulation_policy_round_trips_exactly() -> None:
    project = Project(
        "qualified-s3",
        shell_formulation_policy=ShellFormulationPolicy.qualified_s3_candidate(),
    )

    payload = project_to_dict(project)
    restored = project_from_dict(deepcopy(payload))

    assert payload["shell_formulations"] == {
        "schema": "anyfem.shell-formulation-policy-v2",
        "q4": "e4-pl",
        "s3": "e4-pl-s3",
        "higher_order": "legacy",
        "q4_formulation_id": "E4_PL_QUALIFIED_Q4_HYBRID_V2",
        "s3_formulation_id": "E4_PL_QUALIFIED_S3_COMPANION_V1",
        "higher_order_formulation_id": "LEGACY_SHELL_ELEMENT_HIGHER_ORDER",
        "s3_quality_contract_id": S3_QUALITY_CONTRACT_ID,
    }
    assert restored.shell_formulation_policy == project.shell_formulation_policy
    assert project_to_dict(restored) == payload


def test_new_project_default_selects_qualified_s3_companion() -> None:
    policy = Project("current-default").shell_formulation_policy

    assert policy.q4 == "e4-pl"
    assert policy.s3 == "e4-pl-s3"
    assert policy.formulation_id_for_node_count(4) == (
        "E4_PL_QUALIFIED_Q4_HYBRID_V2"
    )
    assert policy.formulation_id_for_node_count(3) == (
        "E4_PL_QUALIFIED_S3_COMPANION_V1"
    )


def test_project_without_policy_preserves_q4_default_and_legacy_s3() -> None:
    payload = project_to_dict(Project("old-project"))
    payload.pop("shell_formulations")

    payload["anyfem"]["format"] = 7
    restored = project_from_dict(payload)

    assert restored.shell_formulation_policy.q4 == "e4-pl"
    assert restored.shell_formulation_policy.s3 == "legacy-s3"
    assert any(
        "TRI3 remains legacy-s3" in item
        for item in restored.compatibility_diagnostics
    )

    mesh = Mesh(
        nodes={
            1: np.asarray((0.0, 0.0, 0.0)),
            2: np.asarray((1.0, 0.0, 0.0)),
            3: np.asarray((1.0, 1.0, 0.0)),
            4: np.asarray((0.0, 1.0, 0.0)),
            5: np.asarray((-0.5, 0.5, 0.0)),
        },
        quads={10: (1, 2, 3, 4)},
        tris={20: (1, 4, 5)},
        elements_of_face={8: [10, 20]},
    )
    actual: dict[int, object] = {}
    build_module._add_shells(
        SimpleNamespace(
            shell_formulation_policy=restored.shell_formulation_policy,
            geometry=None,
            plate_section_of=lambda _face_id: SimpleNamespace(
                material="steel", thickness=0.01
            ),
        ),
        mesh,
        SimpleNamespace(add_element=lambda key, value: actual.__setitem__(key, value)),
    )
    assert type(actual[10]) is QualifiedE4PLShellElement
    assert type(actual[20]) is LegacyShellElement


def test_current_format_without_policy_migrates_to_explicit_legacy_s3() -> None:
    payload = project_to_dict(Project("current-project"))
    payload.pop("shell_formulations")

    restored = project_from_dict(payload)

    assert restored.shell_formulation_policy == (
        ShellFormulationPolicy.migrated_legacy_s3()
    )
    assert any(
        "lacks an authoritative format-8 shell formulation policy" in diagnostic
        for diagnostic in restored.compatibility_diagnostics
    )
    assert project_to_dict(restored)["shell_formulations"]["s3"] == "legacy-s3"


def test_pre_format_8_cannot_smuggle_a_qualified_policy() -> None:
    payload = project_to_dict(Project("old-qualified-claim"))
    payload["anyfem"]["format"] = 7

    restored = project_from_dict(payload)

    assert restored.shell_formulation_policy == (
        ShellFormulationPolicy.migrated_legacy_s3()
    )


def test_neutral_import_without_formulation_authority_is_explicitly_legacy() -> None:
    imported = ImportedModel(
        name="historical-neutral-model",
        fe_model=SimpleNamespace(),
        mesh=Mesh(),
    )

    project = imported.project()

    assert project.shell_formulation_policy == (
        ShellFormulationPolicy.legacy_compatible()
    )

    with pytest.raises(
        SesamImportError,
        match="no qualified shell-formulation authority",
    ):
        imported.built(project=Project("unsafe-import-policy"))


def test_old_mesh_only_project_migrates_all_shell_topologies_to_legacy() -> None:
    payload = project_to_dict(Project("historical-neutral"))
    payload["anyfem"]["format"] = 7
    payload["mesh_only"] = True

    restored = project_from_dict(payload)

    assert restored.shell_formulation_policy == (
        ShellFormulationPolicy.legacy_compatible()
    )


def test_built_model_freezes_formulation_provenance_against_project_edits() -> None:
    project = Project("qualified-build")
    built = build_module.BuiltModel(
        fe_model=SimpleNamespace(),
        load_case=None,
        mesh=Mesh(),
        project=project,
    )
    project.shell_formulation_policy = ShellFormulationPolicy.migrated_legacy_s3()

    identity = _solution_submission_identity(SimpleNamespace(built=built))

    assert identity["shell_formulations"]["q4"] == "e4-pl"
    assert identity["shell_formulations"]["s3"] == "e4-pl-s3"
    assert identity["shell_formulations"]["s3_formulation_id"] == (
        "E4_PL_QUALIFIED_S3_COMPANION_V1"
    )


def test_malformed_policy_fails_closed() -> None:
    payload = project_to_dict(Project("bad-policy"))
    payload["shell_formulations"]["s3"] = "surprise-triangle"

    with pytest.raises(ProjectFileError, match="shell_formulations"):
        project_from_dict(payload)


@pytest.mark.parametrize(
    "field",
    [
        "q4_formulation_id",
        "s3_formulation_id",
        "higher_order_formulation_id",
        "s3_quality_contract_id",
    ],
)
def test_formulation_identity_mutation_fails_closed(field: str) -> None:
    payload = project_to_dict(
        Project(
            "mutated-policy",
            shell_formulation_policy=ShellFormulationPolicy.qualified_s3_candidate(),
        )
    )
    payload["shell_formulations"][field] = "MUTATED"

    with pytest.raises(ProjectFileError, match="shell formulation identity mismatch"):
        project_from_dict(payload)


@pytest.mark.parametrize(
    ("policy", "expected_q4", "expected_s3"),
    [
        (ShellFormulationPolicy.legacy_compatible(), "legacy", "legacy-s3"),
        (ShellFormulationPolicy.qualified_s3_candidate(), "e4-pl", "e4-pl-s3"),
    ],
)
def test_model_builder_routes_each_topology_through_the_central_factory(
    monkeypatch: pytest.MonkeyPatch,
    policy: ShellFormulationPolicy,
    expected_q4: str,
    expected_s3: str,
) -> None:
    calls: list[dict[str, object]] = []
    real_create = build_module.create_shell_element

    def fake_create(element_id, node_ids, material_name="default", **kwargs):
        record = {
            "element_id": element_id,
            "node_ids": tuple(node_ids),
            "material_name": material_name,
            **kwargs,
        }
        calls.append(record)
        return real_create(element_id, list(node_ids), material_name, **kwargs)

    monkeypatch.setattr(build_module, "create_shell_element", fake_create)
    project = SimpleNamespace(
        shell_formulation_policy=policy,
        geometry=SimpleNamespace(
            model_id="routing-source",
            revision=1,
            closest_face=lambda _point, face_ids: (
                tuple(face_ids)[0],
                np.zeros(3),
                (0.5, 0.5),
                0.0,
            ),
            face_normal=lambda _face_id, _u, _v: np.asarray((0.0, 0.0, 1.0)),
        ),
        plate_section_of=lambda _face_id: SimpleNamespace(
            material="steel", thickness=0.01
        ),
    )
    mesh = Mesh(
        nodes={
            1: np.asarray((0.0, 0.0, 0.0)),
            2: np.asarray((1.0, 0.0, 0.0)),
            3: np.asarray((1.0, 1.0, 0.0)),
            4: np.asarray((0.0, 1.0, 0.0)),
            5: np.asarray((-0.5, 0.5, 0.0)),
        },
        elements_of_face={8: [100, 200]},
        quads={100: (1, 2, 3, 4)},
        tris={200: (1, 4, 5)},
    )
    added: list[tuple[int, object]] = []
    fe_model = SimpleNamespace(add_element=lambda key, value: added.append((key, value)))
    _bind_qualified_s3_authority(project, mesh)

    build_module._add_shells(project, mesh, fe_model)

    assert [call["formulation"] for call in calls] == [expected_q4, expected_s3]
    assert [key for key, _value in added] == [100, 200]


def test_builder_rejects_factory_formulation_identity_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = SimpleNamespace(
        shell_formulation_policy=ShellFormulationPolicy.current_default(),
        geometry=None,
        plate_section_of=lambda _face_id: SimpleNamespace(
            material="steel", thickness=0.01
        ),
    )
    mesh = Mesh(
        nodes={
            1: np.asarray((0.0, 0.0, 0.0)),
            2: np.asarray((1.0, 0.0, 0.0)),
            3: np.asarray((1.0, 1.0, 0.0)),
            4: np.asarray((0.0, 1.0, 0.0)),
        },
        quads={10: (1, 2, 3, 4)},
        elements_of_face={8: [10]},
    )
    monkeypatch.setattr(
        build_module,
        "create_shell_element",
        lambda *_args, **_kwargs: SimpleNamespace(formulation_id="DRIFTED"),
    )

    with pytest.raises(ProjectError, match="formulation identity mismatch"):
        build_module._add_shells(
            project, mesh, SimpleNamespace(add_element=lambda *_args: None)
        )


def test_real_factory_builds_exact_topology_classes_and_ids() -> None:
    geometry = SimpleNamespace(
        model_id="factory-source",
        revision=1,
        closest_face=lambda _point, face_ids: (
            tuple(face_ids)[0],
            np.zeros(3),
            (0.5, 0.5),
            0.0,
        ),
        face_normal=lambda _face_id, _u, _v: np.asarray((0.0, 0.0, 1.0)),
    )
    project = SimpleNamespace(
        shell_formulation_policy=ShellFormulationPolicy.qualified_s3_candidate(),
        geometry=geometry,
        plate_section_of=lambda _face_id: SimpleNamespace(
            material="steel", thickness=0.01
        ),
    )
    mesh = Mesh(
        nodes={
            1: np.asarray((0.0, 0.0, 0.0)),
            2: np.asarray((1.0, 0.0, 0.0)),
            3: np.asarray((1.0, 1.0, 0.0)),
            4: np.asarray((0.0, 1.0, 0.0)),
            5: np.asarray((-0.5, 0.5, 0.0)),
        },
        quads={10: (1, 2, 3, 4)},
        tris={20: (1, 4, 5)},
        elements_of_face={8: [10, 20]},
    )
    added: dict[int, object] = {}
    _add = lambda key, value: added.__setitem__(key, value)
    _bind_qualified_s3_authority(project, mesh)

    build_module._add_shells(project, mesh, SimpleNamespace(add_element=_add))

    assert type(added[10]) is QualifiedE4PLShellElement
    assert added[10].formulation_id == "E4_PL_QUALIFIED_Q4_HYBRID_V2"
    assert type(added[20]) is QualifiedE4PLS3ShellElement
    assert added[20].formulation_id == "E4_PL_QUALIFIED_S3_COMPANION_V1"
    np.testing.assert_allclose(added[20].reference_normal, (0.0, 0.0, 1.0))

    restart_identity = added[20].to_dict()
    assert {
        key: restart_identity[key]
        for key in (
            "formulation_id",
            "formulation_schema",
            "quadrature_id",
            "bubble_convention",
            "state_layout_id",
        )
    } == {
        "formulation_id": "E4_PL_QUALIFIED_S3_COMPANION_V1",
        "formulation_schema": "anysolver.e4_pl_s3.linear.v1",
        "quadrature_id": "dunavant_degree5_7point",
        "bubble_convention": "hierarchical_rotation_relative_to_corner_average",
        "state_layout_id": "S3_EXTERNAL18_BUBBLE2_PL3_LINEAR_V1",
    }
    assert type(shell_element_from_dict(restart_identity)) is (
        QualifiedE4PLS3ShellElement
    )

    project.shell_formulation_policy = ShellFormulationPolicy.legacy_compatible()
    added.clear()
    build_module._add_shells(project, mesh, SimpleNamespace(add_element=_add))
    assert type(added[10]) is LegacyShellElement
    assert type(added[20]) is LegacyShellElement


@pytest.mark.parametrize(
    "field",
    [
        "formulation_id",
        "formulation_schema",
        "quadrature_id",
        "bubble_convention",
        "state_layout_id",
    ],
)
def test_qualified_s3_hot_restart_identity_mutation_fails_closed(
    field: str,
) -> None:
    element = QualifiedE4PLS3ShellElement(
        20,
        [1, 2, 3],
        reference_normal=(0.0, 0.0, 1.0),
    )
    payload = element.to_dict()
    payload[field] = "MUTATED"

    with pytest.raises(ValueError, match="unknown serialized|incompatible"):
        shell_element_from_dict(payload)


def test_qualified_build_rejects_reversed_or_unowned_triangles_before_creation() -> None:
    geometry = SimpleNamespace(
        model_id="reversed-source",
        revision=1,
        closest_face=lambda _point, face_ids: (
            tuple(face_ids)[0], np.zeros(3), (0.5, 0.5), 0.0
        ),
        face_normal=lambda _face_id, _u, _v: np.asarray((0.0, 0.0, 1.0)),
    )
    project = SimpleNamespace(
        shell_formulation_policy=ShellFormulationPolicy.qualified_s3_candidate(),
        geometry=geometry,
        plate_section_of=lambda _face_id: SimpleNamespace(material="steel", thickness=0.01),
    )
    mesh = Mesh(
        nodes={
            1: np.asarray((0.0, 0.0, 0.0)),
            2: np.asarray((1.0, 0.0, 0.0)),
            3: np.asarray((0.5, np.sqrt(3.0) / 2.0, 0.0)),
        },
        tris={20: (1, 3, 2)},
        elements_of_face={8: [20]},
    )
    added: list[object] = []
    _bind_qualified_s3_authority(project, mesh)

    with pytest.raises(ProjectError, match="qualified S3 mesh admission failed"):
        build_module._add_shells(
            project, mesh, SimpleNamespace(add_element=lambda *_args: added.append(_args))
        )
    assert added == []


def test_qualified_builder_preserves_reversed_faceuse_physical_normal() -> None:
    geometry = GeometryModel()
    points = geometry.add_points(
        ((0.0, 0.0, 0.0), (1.0, 0.0, 0.0),
         (1.0, 1.0, 0.0), (0.0, 1.0, 0.0))
    )
    face = geometry.add_face(geometry.add_polyline(points, close=True))
    geometry.add_sheet((face,), orientations=(-1,))
    project = SimpleNamespace(
        geometry=geometry,
        shell_formulation_policy=ShellFormulationPolicy.current_default(),
        plate_section_of=lambda _face_id: SimpleNamespace(
            material="steel", thickness=0.01
        ),
    )
    mesh = Mesh(
        nodes={
            1: np.asarray((0.0, 0.0, 0.0)),
            2: np.asarray((1.0, 0.0, 0.0)),
            3: np.asarray((1.0, 1.0, 0.0)),
            4: np.asarray((0.0, 1.0, 0.0)),
        },
        tris={20: (1, 3, 2), 21: (1, 4, 3)},
        elements_of_face={face: [20, 21]},
    )
    _bind_qualified_s3_authority(project, mesh, normal=(0.0, 0.0, -1.0))
    added: dict[int, object] = {}

    build_module._add_shells(
        project,
        mesh,
        SimpleNamespace(add_element=lambda key, value: added.__setitem__(key, value)),
    )

    assert set(added) == {20, 21}
    assert all(type(element) is QualifiedE4PLS3ShellElement for element in added.values())
    for element in added.values():
        np.testing.assert_array_equal(element.reference_normal, (0.0, 0.0, -1.0))


def test_qualified_builder_accepts_raw_triangular_face_from_bound_authority() -> None:
    geometry = GeometryModel()
    points = geometry.add_points(
        ((0.0, 0.0, 0.0), (1.0, 0.0, 0.0),
         (0.5, np.sqrt(3.0) / 2.0, 0.0))
    )
    face = geometry.add_face(geometry.add_polyline(points, close=True))
    geometry.add_sheet((face,))
    project = SimpleNamespace(
        geometry=geometry,
        shell_formulation_policy=ShellFormulationPolicy.current_default(),
        plate_section_of=lambda _face_id: SimpleNamespace(
            material="steel", thickness=0.01
        ),
    )
    mesh = Mesh(
        nodes={
            1: np.asarray((0.0, 0.0, 0.0)),
            2: np.asarray((1.0, 0.0, 0.0)),
            3: np.asarray((0.5, np.sqrt(3.0) / 2.0, 0.0)),
        },
        tris={20: (1, 2, 3)},
        elements_of_face={face: [20]},
    )
    _bind_qualified_s3_authority(project, mesh)
    added: dict[int, object] = {}

    build_module._add_shells(
        project,
        mesh,
        SimpleNamespace(add_element=lambda key, value: added.__setitem__(key, value)),
    )

    assert type(added[20]) is QualifiedE4PLS3ShellElement
    np.testing.assert_array_equal(added[20].reference_normal, (0.0, 0.0, 1.0))


@pytest.mark.parametrize(
    "mutation",
    (
        "missing-record",
        "wrong-formulation",
        "stale-model",
        "stale-revision",
        "missing-normal",
        "nonunit-normal",
        "scope",
    ),
)
def test_qualified_builder_rejects_malformed_or_stale_normal_authority(
    mutation: str,
) -> None:
    geometry = GeometryModel()
    points = geometry.add_points(
        ((0.0, 0.0, 0.0), (1.0, 0.0, 0.0),
         (0.5, np.sqrt(3.0) / 2.0, 0.0))
    )
    face = geometry.add_face(geometry.add_polyline(points, close=True))
    geometry.add_sheet((face,))
    project = SimpleNamespace(
        geometry=geometry,
        shell_formulation_policy=ShellFormulationPolicy.current_default(),
        plate_section_of=lambda _face_id: SimpleNamespace(
            material="steel", thickness=0.01
        ),
    )
    mesh = Mesh(
        nodes={
            1: np.asarray((0.0, 0.0, 0.0)),
            2: np.asarray((1.0, 0.0, 0.0)),
            3: np.asarray((0.5, np.sqrt(3.0) / 2.0, 0.0)),
        },
        tris={20: (1, 2, 3)},
        elements_of_face={face: [20]},
    )
    _bind_qualified_s3_authority(project, mesh)
    record = mesh.structural_preparation["qualified_s3"]
    if mutation == "missing-record":
        mesh.structural_preparation = {}
    elif mutation == "wrong-formulation":
        record["formulation_id"] = "LEGACY_SHELL_ELEMENT_TRI3"
    elif mutation == "stale-model":
        record["authority_model"]["source_model_id"] = "stale-model"
    elif mutation == "stale-revision":
        record["authority_model"]["source_revision"] += 1
    elif mutation == "missing-normal":
        record["element_owner_normals"].pop("20")
    elif mutation == "nonunit-normal":
        record["element_owner_normals"]["20"] = [0.0, 0.0, 2.0]
    else:
        record["element_ids"] = []
    added: list[object] = []

    with pytest.raises(ProjectError, match="qualified S3"):
        build_module._add_shells(
            project,
            mesh,
            SimpleNamespace(add_element=lambda *_args: added.append(_args)),
        )

    assert added == []
