"""Immutable mesh snapshot contracts used when submitting solver jobs."""

from copy import deepcopy
from types import MappingProxyType
from types import SimpleNamespace

import pytest
from anymesher.serialize import mesh_to_dict

from anyfem.mesh_jobs import clone_mesh_for_job
from anyfem.document import DocumentSession
from anyfem.model.project import Project


@pytest.mark.parametrize('policy',[None,{}, {'maximum_aspect_ratio':3.5}])
def test_quality_policy_presence_survives_canonicalization_and_transport(policy):
    from dataclasses import replace
    import inspect,pickle
    from anyfem.mesh_jobs import MeshSettings
    settings=MeshSettings.create(.5,element_order='linear',strategy='native',quality_policy=policy)
    assert type(settings.quality_policy_explicit) is bool
    assert settings.quality_policy_explicit is (policy is not None)
    assert settings.quality_policy # Existing canonical default expansion stays intact.
    assert replace(settings).quality_policy_explicit is settings.quality_policy_explicit
    assert pickle.loads(pickle.dumps(settings))==settings
    assert inspect.signature(MeshSettings).parameters['quality_policy_explicit'].kind is inspect.Parameter.KEYWORD_ONLY
    legacy=MeshSettings(.5,quality_policy=(('maximum_aspect_ratio',3.5),))
    assert legacy.quality_policy_explicit is True
    assert MeshSettings(.5).quality_policy_explicit is False


@pytest.mark.parametrize('strategy',[None,'auto','native','mapped'])
def test_quality_policy_hash_preserves_omitted_payload_and_binds_explicit_policy(strategy):
    from dataclasses import replace
    from anyfem.document import canonical_hash
    from anyfem.mesh_jobs import MeshSettings
    absent=MeshSettings.create(.5,element_order='linear',strategy=strategy)
    # Frozen pre-repair settings payload: presence must not churn absent hashes.
    old_payload=dict(target_size=.5,element_order='linear',overrides={},strategy=strategy,
        quad_options=None,layout_policy=None,controls=None,
        structure_preference='balanced' if strategy in (None,'auto') else None,
        quality_policy=dict(absent.quality_policy) if strategy in (None,'auto') else None,
        structured_controls=None)
    assert absent.input_hash==canonical_hash(old_payload)
    explicit=MeshSettings.create(.5,element_order='linear',strategy=strategy,quality_policy={})
    assert explicit.input_hash!=absent.input_hash
    expected=dict(old_payload,quality_policy=dict(explicit.quality_policy),quality_policy_explicit=True)
    assert explicit.input_hash==canonical_hash(expected)
    altered=MeshSettings.create(.5,element_order='linear',strategy=strategy,
        quality_policy={'maximum_aspect_ratio':3.5})
    assert altered.input_hash!=explicit.input_hash
    from anyfem.mesh_execution import MeshExecutionPolicy
    assert replace(explicit,execution_policy=MeshExecutionPolicy.explicit(4)).input_hash==explicit.input_hash


@pytest.mark.parametrize('flag',[0,1,'yes'])
def test_quality_policy_presence_rejects_nonboolean_constructor_input(flag):
    from anyfem.mesh_jobs import MeshSettings
    with pytest.raises(TypeError,match='quality_policy_explicit'):
        MeshSettings(.5,quality_policy_explicit=flag)


@pytest.mark.parametrize('policy',[None,{}, {'maximum_aspect_ratio':3.5}])
def test_quality_policy_job_forwarding_preserves_absence_and_explicit_mapping(policy):
    from anyfem.mesh_jobs import MeshSettings,run_mesh_job
    from anyfem.jobs import _cancellation_token
    settings=MeshSettings.create(.5,element_order='linear',strategy='native',quality_policy=policy)
    seen=[];sentinel=RuntimeError('stop after argument transport')
    class FakeProject:
        native_mesh_settings=None
        def generate_mesh(self,*args,**kwargs):seen.append(kwargs['quality_policy']);raise sentinel
    with pytest.raises(RuntimeError) as caught:
        run_mesh_job(FakeProject(),'model',settings,lambda *args:None,_cancellation_token())
    assert caught.value is sentinel
    assert seen==[None if policy is None else dict(settings.quality_policy)]


def test_quality_policy_nondefault_edit_cannot_keep_absent_presence():
    from dataclasses import replace
    from anyfem.mesh_jobs import MeshSettings
    absent=MeshSettings.create(.5,element_order='linear',strategy='native')
    assert replace(absent).quality_policy_explicit is False
    edited=replace(absent,quality_policy=(('maximum_aspect_ratio',3.5),))
    assert edited.quality_policy_explicit is True
    declared=MeshSettings.create(.5,element_order='linear',strategy='native',
        quality_policy={'maximum_aspect_ratio':3.5})
    assert edited.input_hash==declared.input_hash
    contradictory=MeshSettings(.5,strategy='native',quality_policy=(('maximum_aspect_ratio',3.5),),
        quality_policy_explicit=False)
    assert contradictory.quality_policy_explicit is True
    assert contradictory.input_hash==declared.input_hash


def _plate_mesh():
    project = Project("job snapshot")
    points = project.geometry.add_points(
        ((0.0, 0.0, 0.0), (1.0, 0.0, 0.0),
         (1.0, 1.0, 0.0), (0.0, 1.0, 0.0))
    )
    project.geometry.add_plate(points)
    return project.generate_mesh(0.5)


def test_job_mesh_clone_accepts_read_only_geometry_provenance() -> None:
    mesh = _plate_mesh()
    mesh.structural_preparation = MappingProxyType(
        {
            "source_revision": 4,
            "source_to_working_faces": MappingProxyType({1: (1,)}),
        }
    )

    with pytest.raises(TypeError, match="mappingproxy"):
        deepcopy(mesh)

    cloned = clone_mesh_for_job(mesh)

    assert cloned is not mesh
    assert mesh_to_dict(cloned) == mesh_to_dict(mesh)
    assert cloned.structural_preparation == {
        "source_revision": 4,
        "source_to_working_faces": {"1": [1]},
    }


def test_failed_mesh_snapshot_does_not_leave_an_orphan_analysis(
    monkeypatch,
) -> None:
    from anyfem.ui import app as app_module

    project = Project("failed submission")
    application = SimpleNamespace(
        mesh=object(),
        imported=None,
        project=project,
        session=DocumentSession(project),
        _require_trusted_pressure_code=lambda: None,
    )

    def reject_snapshot(_mesh):
        raise TypeError("unsupported mesh provenance")

    monkeypatch.setattr("anyfem.application.workflow.clone_mesh_for_job", reject_snapshot)

    with pytest.raises(TypeError, match="unsupported mesh provenance"):
        app_module.AnyFemApp.solve(application, "Linear static")

    assert project.analyses == {}
def test_prepared_geometry_id_maps_keep_historical_hash_after_json_transport():
    import json
    from anymesher.mesh import Mesh
    from anyfem.mesh_jobs import matches_mesh_hash, mesh_semantic_hash

    mesh = Mesh()
    inputs = dict(model_hash="model", mesh_input_hash="settings")
    prep = {"prepared_boundary_provenance": {"prepared_seeding": {
        "divisions": {2: 4, 10: 6}, "classes": {2: 2, 10: 10}}}}
    restored = json.loads(json.dumps(prep))
    numeric = mesh_semantic_hash(mesh, **inputs, structural_preparation=prep)
    textual = mesh_semantic_hash(mesh, **inputs, structural_preparation=restored, _text_seeding_keys=True)
    assert numeric != textual
    assert mesh_semantic_hash(mesh, **inputs, structural_preparation=restored) == numeric
    assert all(matches_mesh_hash(mesh, h, **inputs, structural_preparation=restored) for h in (numeric, textual))
    restored["prepared_boundary_provenance"]["prepared_seeding"]["divisions"]["2"] += 1
    assert all(not matches_mesh_hash(mesh, h, **inputs, structural_preparation=restored) for h in (numeric, textual))
