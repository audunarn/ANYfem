"""Explicit curved input identity, native persistence and solver-owned restart."""
from dataclasses import replace
import numpy as np
import pytest
from scipy.spatial.transform import Rotation
from anymaterial import DNVC208MaterialCurve
from anysolver import b3_ge, stiffened_shell as ss
from anyfem.solve.stiffened_shell import (
    ExperimentalGEPointPanel, ExperimentalGEReferenceModalPanel,
    ExperimentalGENodalRigidFootprintPanel, SCHEMA_CURVED_Q4, load_case, save_case,
)
from anyfem.solve import stiffened_shell as consumer
from anyfem.io.project_file import load_project, save_project
from anyfem.model.project import Project
from anyfem.model.records import AnalysisDefinition
from test_experimental_stiffened_shell_consumer import _case


def curved_case():
    base = _case()
    xyz = np.array(base.shell.coordinates_m, copy=True)
    xyz[:, 2] += .02*xyz[:, 0]**2
    beams = []
    for old in base.beams:
        element, inertia = old.instantiate()
        position = element.operator.reference.coordinates.copy()
        position[:, 2] += .02*position[:, 0]**2
        frames = Rotation.from_rotvec(np.column_stack((np.zeros(3),
            -np.arctan(.04*position[:, 0]), np.zeros(3)))).as_matrix()
        beams.append(b3_ge.define_beam('b3-ge', element.element_id, element.node_ids,
            position, frames, element.section, inertia, quadrature=element.operator.order))
    return replace(base, shell=replace(base.shell,
        coordinates_m=tuple(tuple(float(v) for v in row) for row in xyz)), beams=tuple(beams),
        actions=(ss.NodalAction('beam', 105, (0.,0.,-100.,0.,0.,0.)),),
        q4_pullback_policy=ss.Q4_ALGORITHMIC_PULLBACK_V1,
        shell_reference_policy=ss.CURVED_Q4_POINT_REFERENCE_V1)


def test_curved_input_native_roundtrip_restart_continue(tmp_path, request):
    panel = curved_case()
    reopened = load_case(save_case(panel, tmp_path/'curved.anyfem-ge.json'))
    assert reopened.to_dict() == panel.to_dict()
    assert reopened.to_dict()['schema'] == SCHEMA_CURVED_Q4
    project = Project('curved point consumer', mesh_only=True)
    analysis = AnalysisDefinition.experimental_point_panel('curved points', reopened)
    project.add_analysis(analysis)
    path = save_project(project, tmp_path/'curved.anyfem')
    stored = load_project(path).analyses[analysis.id]
    assert stored.beam_formulation_options['panel'] == panel.to_dict()
    owner = stored.create_point_panel_analysis(); request.addfinalizer(owner.close)
    assert owner.capabilities['curved_reference_policy'] == ss.CURVED_Q4_POINT_REFERENCE_V1
    assert not owner.preflight()['production_qualified']
    owner.solve(panel.actions, (.1,))
    info = owner.preflight(); count = len(info['arc_metric_node_order'])
    control = ss.ArcLengthControl('beam', 105, 2,
        ss.PhysicalChordMetric(np.full(count,1/count), info['reference_length_m']), 1e-5, -1)
    owner.continue_arc_length(panel.actions, control)
    prefix = owner.checkpoint()
    restored = load_project(path).analyses[analysis.id].create_point_panel_analysis()
    request.addfinalizer(restored.close)
    restored.restore(prefix)
    assert restored.checkpoint() == prefix
    actual = owner.continue_arc_length(panel.actions, control)
    repeated = restored.continue_arc_length(panel.actions, control)
    assert restored.checkpoint() == owner.checkpoint()
    np.testing.assert_array_equal(actual.shell_displacement, repeated.shell_displacement)
    np.testing.assert_array_equal(actual.cell_resultants, repeated.cell_resultants)
    assert actual.free_equilibrium_ratio <= 1e-8 and actual.constraint_max <= 1e-9


def test_curved_schema_cannot_retarget_old_or_unavailable_policy(monkeypatch):
    data = curved_case().to_dict()
    with pytest.raises(ValueError, match='curved schema'):
        ExperimentalGEPointPanel.from_dict(dict(data, shell_reference_policy='flat'))
    old = dict(data, schema='anyfem.experimental-ge-point-panel-q4-pullback-v3')
    with pytest.raises(ValueError):ExperimentalGEPointPanel.from_dict(old)
    monkeypatch.delattr(ss, 'CURVED_Q4_POINT_REFERENCE_V1')
    with pytest.raises(ValueError, match='curved schema'):
        ExperimentalGEPointPanel.from_dict(data)


def test_curved_input_rejects_unadmitted_consumer_routes():
    panel = curved_case()
    with pytest.raises(ValueError, match='translational'):
        replace(panel, actions=(ss.NodalAction('beam', 105, (0.,0.,0.,1.,0.,0.)),))
    with pytest.raises(ValueError, match='algorithmic Q4'):
        replace(panel, q4_pullback_policy=_case().q4_pullback_policy)
    with pytest.raises(ValueError, match='algorithmic Q4'):
        replace(panel, shell=replace(panel.shell,
            j2_curve=DNVC208MaterialCurve(1e6,1.05e6,1.1e6,.005,.01,4e6,.2)))
    with pytest.raises(ValueError, match='algorithmic Q4'):
        replace(panel, shell=replace(panel.shell, s3_elements=((1,2,5),)))
    with pytest.raises(ValueError, match='fixed nodal'):
        replace(panel, body_load=ss.GEBodyLoad('0'*64, (0.,0.,-9.81)))
    with pytest.raises(ValueError, match='flat GE'):
        ExperimentalGEReferenceModalPanel.from_definition(panel.definition(), None)
    with pytest.raises(ValueError, match='flat unstaged'):
        ExperimentalGENodalRigidFootprintPanel(panel, ())


@pytest.mark.parametrize('field,schema', (
    ('load_programme', consumer.SCHEMA_STAGED),
    ('body_load', consumer.SCHEMA_BODY_LOAD),
    ('distributed_load', consumer.SCHEMA_DISTRIBUTED_LOAD),
    ('pressure_load', consumer.SCHEMA_PRESSURE_LOAD),
    ('distributed_programme', consumer.SCHEMA_DISTRIBUTED_PROGRAMME),
))
def test_curved_inputs_reject_external_and_staged_wrappers(field,schema):
    panel = curved_case()
    with pytest.raises(ValueError, match='fixed nodal'):
        replace(panel, **{field:object()})
    # Child schema admission fails before an invalid/irrelevant load payload can
    # accidentally select an older flat-only consumer route.
    record = dict(schema=schema, status=panel.status,
        profile=ss.B3_GE_E4_PL_STIFFENED_SHELL_V1, panel=panel.to_dict(), **{field:{}})
    with pytest.raises(ValueError):ExperimentalGEPointPanel.from_dict(record)
