"""Public SmartMapped selection: enum, settings, UI labels, jobs, backend and project dispatch.

SmartMapped (label ``SmartMapped``, token ``smart_mapped``) is an explicit
public strategy that shares the quad-first pipeline.  These tests pin that the
token reaches the ANYmesh owner route (``generate_hybrid_mesh[_result]
(strategy='smart_mapped')``) with the quad options, persists across reopen and
leaves the Automatic token and defaults untouched.  Dispatch tests replace only
the owner entry point by a recorder that stops there; the real-route test at
the end runs the actual owner pipeline and therefore needs the ANYmesh
SmartMapped implementation.
"""
import json
import sys
import types
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from anymesher.quad.public_integration import QuadPublicUnsupported
from anyfem import native_meshing_backend as backend_module
from anyfem.application.examples import BulkheadOpening, floating_barge_section
from anyfem.io import project_from_dict, project_to_dict
from anyfem.mesh_jobs import MeshSettings, run_mesh_job
from anyfem.model import project as project_module
from anyfem.model.project import ProjectError
from anyfem.native_meshing import (
    GenerationToken, MeshBackend, MeshGenerationRequest, MeshPrediction,
    NativeMeshSettings, NativeMeshingCancellation)
from anyfem.quad_first import effective_quad_options


def small_project():
    return floating_barge_section(length=3., breadth=4., bulkheads=(1.5,),
        girders=(0.,), stiffener_spacing=2.,
        openings=(BulkheadOpening(0., 1.25, 1.4, 1.1, .25),), target_size=.5)


class Dispatched(Exception):
    """Raised by the recorder that stands in for the owner entry point."""

    def __init__(self, kwargs):
        super().__init__('dispatched')
        self.kwargs = kwargs


def recorder(*_args, **kwargs):
    raise Dispatched(kwargs)


def project_dispatch(monkeypatch, project, **call):
    from anyfem.mesh_execution import MeshExecutionPolicy

    # Serial execution keeps the recorder on the only possible owner call.
    call.setdefault('execution_policy', MeshExecutionPolicy.off())
    monkeypatch.setattr(project_module, 'generate_hybrid_mesh', lambda working, **kw: recorder(**kw))
    with pytest.raises(Dispatched) as caught:
        project.generate_mesh(**call)
    return caught.value.kwargs


def install_eligibility(monkeypatch, eligible, reason='fixture verdict'):
    """Stand in for the owner's smart_mapped_eligibility and record every call."""
    calls = []

    class Verdict:
        def __init__(self, ok):
            self.eligible = ok

        def to_dict(self):
            return {'eligible': self.eligible, 'reason': reason}

    def fake(geometry, face_ids=None, target_size=None, *, order, refinements,
             overrides, beam_edges, member_ids, seeding):
        calls.append(dict(geometry=geometry, face_ids=face_ids, target_size=target_size,
            order=order, refinements=refinements, overrides=overrides,
            beam_edges=beam_edges, member_ids=member_ids, seeding=seeding))
        return Verdict(eligible)

    # Patch only the application's single lookup seam, never the owner module
    # (the owner pipeline itself imports anymesher.smart_mapped).
    from anyfem import mesh_controls
    monkeypatch.setattr(mesh_controls, '_owner_eligibility', lambda: fake)
    return calls


@pytest.fixture(autouse=True)
def automatic_not_eligible(monkeypatch):
    """Default: Automatic stays on the legacy route (tests opt in to eligibility)."""
    return install_eligibility(monkeypatch, False, 'fixture: not eligible')


# ----------------------------------------------------------------------------
# public enum, labels and persisted settings
# ----------------------------------------------------------------------------
def test_backend_token_and_automatic_compatibility():
    assert MeshBackend('smart_mapped') is MeshBackend.SMART_MAPPED
    assert MeshBackend.SMART_MAPPED.value == 'smart_mapped'
    # The enum constructor knows only the canonical token; the 'auto' alias is
    # normalised by the settings (existing contract), not by MeshBackend().
    assert MeshBackend.AUTOMATIC.value == 'automatic'
    assert NativeMeshSettings.create(.5, backend='auto').backend is MeshBackend.AUTOMATIC
    assert NativeMeshSettings.create(.5, backend='automatic').backend is MeshBackend.AUTOMATIC
    assert NativeMeshSettings.create(.5).backend is MeshBackend.AUTOMATIC
    assert NativeMeshSettings.create(.5).to_dict()['backend'] == 'automatic'
    for bad in ('SmartMapped', 'smart-mapped', 'smartmapped'):
        with pytest.raises(ValueError, match='unknown mesh backend'):
            NativeMeshSettings.create(.5, backend=bad)


def test_settings_round_trip_through_project_json_and_reopen():
    project = small_project()
    settings = NativeMeshSettings.create(.5, element_order='quadratic', backend='smart_mapped',
        parameters={'layout_policy': 'adaptive', 'quad_t3_angle_action': 'warning'})
    project.set_native_mesh_settings(settings)
    payload = json.loads(json.dumps(project_to_dict(project)))
    reopened = project_from_dict(payload)
    assert reopened.native_mesh_settings == settings
    assert reopened.native_mesh_settings.backend is MeshBackend.SMART_MAPPED
    assert reopened.native_mesh_settings.to_dict()['backend'] == 'smart_mapped'
    assert reopened.native_mesh_settings.to_dict()['parameters']['layout_policy'] == 'adaptive'
    # Automatic projects keep their historic token and bytes.
    other = small_project()
    other.set_native_mesh_settings(NativeMeshSettings.create(.5))
    assert project_from_dict(project_to_dict(other)).native_mesh_settings.to_dict()['backend'] == 'automatic'


def test_tk_panel_exposes_label_and_token():
    pytest.importorskip('tkinter')
    from anyfem.ui.panels import MeshPanel
    assert MeshPanel._METHOD_LABELS['smart_mapped'] == 'SmartMapped'
    assert MeshPanel._METHOD_VALUES['SmartMapped'] == 'smart_mapped'
    assert 'smart_mapped' in MeshPanel._METHOD_HELP
    # Existing choices keep token and label.
    assert MeshPanel._METHOD_VALUES['Automatic (recommended)'] == 'auto'
    assert MeshPanel._METHOD_VALUES['Quad-first'] == 'quad_first'


def test_qt_strategy_icon_is_distinct():
    pytest.importorskip('PySide6')
    from anyfem.ui.qt.field_icons import MESH_DRAWINGS, field_drawing
    assert field_drawing('smart_mapped') == MESH_DRAWINGS['smart_mapped']
    others = {MESH_DRAWINGS[name] for name in ('quad_first', 'auto', 'mapped', 'native')}
    assert MESH_DRAWINGS['smart_mapped'] not in others


# ----------------------------------------------------------------------------
# mesh job settings (the single public job funnel)
# ----------------------------------------------------------------------------
def test_mesh_settings_validate_hash_and_normalise_smart_mapped():
    settings = MeshSettings.create(.5, element_order='quadratic', strategy=' Smart_Mapped ')
    assert settings.strategy == 'smart_mapped'
    assert settings.quad_options == effective_quad_options(None)
    quad = MeshSettings.create(.5, element_order='quadratic', strategy='quad_first')
    auto = MeshSettings.create(.5, element_order='quadratic', strategy='auto')
    assert len({settings.input_hash, quad.input_hash, auto.input_hash}) == 3
    adaptive = MeshSettings.create(.5, element_order='quadratic', strategy='smart_mapped', layout_policy='adaptive')
    assert adaptive.layout_policy == 'adaptive' and adaptive.input_hash != settings.input_hash
    warned = MeshSettings.create(.5, element_order='linear', strategy='smart_mapped', quad_t3_angle_action='warning')
    plain = MeshSettings.create(.5, element_order='linear', strategy='smart_mapped')
    assert warned.input_hash != plain.input_hash
    # Quad controls remain refused for every other method, Automatic included.
    for other in ('auto', 'mapped', 'native', None):
        with pytest.raises(ValueError, match="quad_options requires strategy='quad_first' or 'smart_mapped'"):
            MeshSettings.create(.5, element_order='linear', strategy=other, quad_options=effective_quad_options(None))
        with pytest.raises(ValueError, match='adaptive layout requires'):
            MeshSettings.create(.5, element_order='linear', strategy=other, layout_policy='adaptive')
    with pytest.raises(ValueError, match='unknown meshing strategy'):
        MeshSettings.create(.5, element_order='linear', strategy='smartmapped')


def stub_project(settings=None):
    class Stub:
        native_mesh_settings = settings
        native_triangulation_backend = 'auto'

        def generate_mesh(self, *args, **kwargs):
            raise Dispatched(kwargs)
    return Stub()


NO_CANCEL = SimpleNamespace(raise_if_cancelled=lambda stage='': None)


def test_mesh_job_passes_smart_mapped_and_quad_controls_to_project():
    settings = MeshSettings.create(.5, element_order='quadratic', strategy='smart_mapped',
        layout_policy='adaptive', quad_t3_angle_action='warning')
    with pytest.raises(Dispatched) as caught:
        run_mesh_job(stub_project(), 'model-hash', settings, lambda *a: None, NO_CANCEL)
    kw = caught.value.kwargs
    assert kw['strategy'] == 'smart_mapped' and kw['order'] == 'quadratic'
    assert kw['quad_options'] == settings.quad_options
    assert kw['layout_policy'] == 'adaptive'
    assert (kw['quad_t3_minimum_angle'], kw['quad_t3_angle_action']) == (15.0, 'warning')


def test_mesh_job_inherits_persisted_smart_mapped_and_keeps_automatic_default():
    persisted = NativeMeshSettings.create(.5, backend='smart_mapped')
    settings = MeshSettings.create(.5, element_order='linear')
    with pytest.raises(Dispatched) as caught:
        run_mesh_job(stub_project(persisted), 'h', settings, lambda *a: None, NO_CANCEL)
    assert caught.value.kwargs['strategy'] is None  # resolved by the project from its settings
    with pytest.raises(Dispatched) as caught:
        run_mesh_job(stub_project(None), 'h', settings, lambda *a: None, NO_CANCEL)
    kw = caught.value.kwargs
    assert kw['strategy'] is None and kw['quad_options'] is None
    assert kw['quad_t3_minimum_angle'] is None and kw['layout_policy'] == 'existing'


# ----------------------------------------------------------------------------
# project dispatch
# ----------------------------------------------------------------------------
def test_project_explicit_smart_mapped_reaches_owner_with_quad_pipeline(monkeypatch):
    kw = project_dispatch(monkeypatch, small_project(), target_size=.5, strategy='smart_mapped',
        order='linear', layout_policy='adaptive')
    assert kw['strategy'] == 'smart_mapped'
    assert kw['quad_options'] == effective_quad_options(None)
    assert kw['layout_policy'] == 'adaptive'
    assert kw['native_backend'] == 'python'
    assert kw['structured_options'] is None
    assert kw['beam_edges']  # the opening bars travel with the shared pipeline
    assert (kw['quad_t3_minimum_angle'], kw['quad_t3_angle_action']) == (15.0, 'error')


def test_project_persisted_smart_mapped_settings_select_the_route(monkeypatch):
    project = small_project()
    project.set_native_mesh_settings(NativeMeshSettings.create(.5, element_order='linear',
        backend='smart_mapped', parameters={'layout_policy': 'adaptive', 'quad_t3_angle_action': 'warning'}))
    reopened = project_from_dict(project_to_dict(project))
    kw = project_dispatch(monkeypatch, reopened)
    assert kw['strategy'] == 'smart_mapped' and kw['layout_policy'] == 'adaptive'
    assert kw['quad_t3_angle_action'] == 'warning' and kw['order'] == 'linear'
    # The persisted choice is intent: an explicit call strategy still wins.
    kw = project_dispatch(monkeypatch, reopened, strategy='quad_first')
    assert kw['strategy'] == 'native' and kw['quad_options'] is not None


def test_project_other_strategies_are_unchanged(monkeypatch):
    project = small_project()
    quad = project_dispatch(monkeypatch, project, target_size=.5, strategy='quad_first', order='linear')
    assert quad['strategy'] == 'native' and quad['quad_options'] is not None
    auto = project_dispatch(monkeypatch, project, target_size=.5, strategy='auto', order='linear')
    assert auto['strategy'] == 'auto' and auto['quad_options'] is None
    native = project_dispatch(monkeypatch, project, target_size=.5, strategy='native', order='linear')
    assert native['strategy'] == 'native' and native['quad_options'] is None
    default = project_dispatch(monkeypatch, project, target_size=.5, order='linear')
    assert default['strategy'] == 'auto' and default['quad_options'] is None


@pytest.mark.parametrize('strategy', ['quad_first', 'smart_mapped'])
def test_quadratic_opening_bars_are_admitted_for_the_shared_planar_scope(monkeypatch, strategy):
    kw = project_dispatch(monkeypatch, small_project(), target_size=.5, strategy=strategy, order='quadratic')
    assert kw['order'] == 'quadratic' and kw['beam_edges']
    # Only linear shells keep the qualified-S3 admission flag.
    assert kw['qualified_s3'] is False


@pytest.mark.parametrize('strategy', ['quad_first', 'smart_mapped'])
@pytest.mark.parametrize('order', ['linear', 'quadratic'])
def test_boundary_bar_refusals_are_shared_and_leave_no_trace(monkeypatch, strategy, order):
    project = small_project()
    g = project.geometry
    face = next(f for f in g.faces.values() if f.holes)
    g.set_face_parameterization(face.id, face.surface)
    before = repr(g.topology_snapshot())
    monkeypatch.setattr(project_module, 'generate_hybrid_mesh', lambda working, **kw: recorder(**kw))
    with pytest.raises(QuadPublicUnsupported, match='source-boundary'):
        project.generate_mesh(.5, strategy=strategy, order=order)
    assert repr(g.topology_snapshot()) == before and project.mesh_records == {}


def test_quad_controls_stay_refused_for_non_quad_project_strategies():
    project = small_project()
    with pytest.raises(ProjectError, match="requires strategy='quad_first' or 'smart_mapped'"):
        project.generate_mesh(.5, strategy='mapped', order='linear', quad_options=effective_quad_options(None))
    with pytest.raises(ProjectError, match='adaptive layout requires'):
        project.generate_mesh(.5, strategy='auto', order='linear', layout_policy='adaptive')


def test_cancellation_before_owner_dispatch_does_not_publish_or_mutate():
    project = small_project()
    before = repr(project.geometry.topology_snapshot())
    marker = RuntimeError('smart mapped cancellation marker')

    def cancel(phase):
        raise marker
    with pytest.raises(RuntimeError) as caught:
        project.generate_mesh(.5, strategy='smart_mapped', order='quadratic', cancellation_check=cancel)
    assert caught.value is marker
    assert repr(project.geometry.topology_snapshot()) == before and project.mesh_records == {}


# ----------------------------------------------------------------------------
# headless native-meshing backend
# ----------------------------------------------------------------------------
@pytest.mark.parametrize('backend, request_backend, strategy, quad', [
    ('smart_mapped', MeshBackend.SMART_MAPPED, 'smart_mapped', True),
    ('quad_first', MeshBackend.QUAD_FIRST, 'native', True),
    ('automatic', MeshBackend.NATIVE, 'native', False),
    ('automatic', MeshBackend.AUTOMATIC, 'auto', False),
])
def test_headless_backend_dispatch(monkeypatch, backend, request_backend, strategy, quad):
    project = small_project()
    settings = NativeMeshSettings.create(.5, element_order='quadratic', backend=backend,
        parameters={'layout_policy': 'adaptive', 'quad_t3_angle_action': 'warning'} if quad else {})
    monkeypatch.setattr(backend_module, 'generate_hybrid_mesh_result', lambda geometry, **kw: recorder(**kw))
    session = backend_module.NativeProjectMeshingSession(project, settings, max_background_jobs=1)
    try:
        component = session.all_components()[0]
        prediction = MeshPrediction(
            MeshBackend.NATIVE if request_backend is MeshBackend.AUTOMATIC else request_backend)
        request = MeshGenerationRequest(component, GenerationToken(1, 1),
            session.capture_component(component), settings, settings.controls,
            request_backend, prediction, NativeMeshingCancellation())
        with pytest.raises(Dispatched) as caught:
            session.generate_component(request)
    finally:
        session.close()
    kw = caught.value.kwargs
    assert kw['strategy'] == strategy and kw['order'] == 'quadratic'
    if quad:
        assert kw['quad_options'] == effective_quad_options(None)
        assert kw['layout_policy'] == 'adaptive' and kw['quad_t3_angle_action'] == 'warning'
        assert kw['native_backend'] == 'python'
    else:
        assert 'quad_options' not in kw and 'layout_policy' not in kw


# ----------------------------------------------------------------------------
# real owner route (requires the ANYmesh SmartMapped production strategy)
# ----------------------------------------------------------------------------
def test_real_owner_route_meshes_and_reopens_smart_mapped(monkeypatch):
    real = project_module.generate_hybrid_mesh
    seen = []

    def spy(working, **kwargs):
        seen.append(dict(kwargs))
        return real(working, **kwargs)
    monkeypatch.setattr(project_module, 'generate_hybrid_mesh', spy)
    project = small_project()
    project.set_native_mesh_settings(NativeMeshSettings.create(.5, element_order='linear',
        backend='smart_mapped', parameters={'layout_policy': 'adaptive', 'quad_t3_angle_action': 'warning'}))
    reopened = project_from_dict(project_to_dict(project))
    revision = reopened.geometry.revision
    mesh = reopened.generate_mesh()
    assert [item['strategy'] for item in seen] == ['smart_mapped']
    assert mesh.shells and mesh.beams and mesh.couplings and not mesh.is_quadratic
    assert reopened.geometry.revision == revision
    assert reopened.native_mesh_settings.backend is MeshBackend.SMART_MAPPED
    assert reopened.mesh_records == {}


def test_real_owner_route_quadratic_smart_mapped_has_midside_bars():
    project = small_project()
    mesh = project.generate_mesh(.5, strategy='smart_mapped', order='quadratic',
        layout_policy='adaptive', quad_t3_angle_action='warning')
    assert mesh.is_quadratic and mesh.beams and mesh.couplings
    assert all(len(nodes) == 3 for nodes in mesh.beams.values())
    corners = {n for e in mesh.shells for n in mesh.corners_of(e)}
    assert corners < {n for nodes in mesh.shells.values() for n in nodes}


# ----------------------------------------------------------------------------
# Automatic -> SmartMapped admission (applicability, not a success guarantee)
# ----------------------------------------------------------------------------
def test_automatic_eligible_selects_smart_mapped_after_exactly_one_owner_call(monkeypatch):
    calls = install_eligibility(monkeypatch, True, 'planar boundary bars supported')
    project = small_project()
    kw = project_dispatch(monkeypatch, project, target_size=.5, strategy='auto', order='linear')
    assert len(calls) == 1
    assert kw['strategy'] == 'smart_mapped'
    assert kw['quad_options'] == effective_quad_options(None)
    assert kw['layout_policy'] == 'adaptive'
    assert kw['structured_options'] is None
    assert kw['native_backend'] == 'python'
    assert (kw['quad_t3_minimum_angle'], kw['quad_t3_angle_action']) == (15.0, 'error')
    call = calls[0]
    # The owner sees the detached prepared geometry, not the live source model.
    assert call['geometry'] is not project.geometry
    assert call['order'] == 'linear' and call['target_size'] == .5
    assert call['face_ids'] and call['beam_edges'] and call['member_ids']


def test_automatic_default_and_persisted_automatic_use_the_same_admission(monkeypatch):
    calls = install_eligibility(monkeypatch, True)
    project = small_project()
    project.set_native_mesh_settings(NativeMeshSettings.create(.5, element_order='linear'))
    kw = project_dispatch(monkeypatch, project)
    assert len(calls) == 1 and kw['strategy'] == 'smart_mapped'
    assert project.native_mesh_settings.backend is MeshBackend.AUTOMATIC  # intent not rewritten


def test_automatic_rejected_keeps_the_legacy_route_and_records_the_reason(monkeypatch):
    calls = install_eligibility(monkeypatch, False, 'beam on a non-planar face')
    project = small_project()
    kw = project_dispatch(monkeypatch, project, target_size=.5, strategy='auto', order='linear')
    assert len(calls) == 1
    assert kw['strategy'] == 'auto' and kw['quad_options'] is None
    assert kw['layout_policy'] == 'existing' and kw['structured_options'] is not None
    assert kw['native_backend'] == project.native_triangulation_backend


def test_automatic_forwards_pins_to_the_owner_which_decides(monkeypatch):
    calls = install_eligibility(monkeypatch, False)
    project = small_project()
    edge = next(iter(project.geometry.edges))
    project_dispatch(monkeypatch, project, target_size=.5, strategy='auto', order='linear',
        overrides={edge: 3})
    assert len(calls) == 1
    # Structural preparation splits this authored edge; its division budget
    # must reach the owner on the resulting working edges.
    assert sum(calls[0]['overrides'].values()) == 3


@pytest.mark.parametrize('strategy', ['quad_first', 'smart_mapped', 'native', 'mapped'])
def test_explicit_strategies_never_call_the_owner_eligibility(monkeypatch, strategy):
    calls = install_eligibility(monkeypatch, True)
    project_dispatch(monkeypatch, small_project(), target_size=.5, strategy=strategy, order='linear')
    assert calls == []


def untrimmed_project():
    """The same barge without bulkhead openings: no face carries a hole."""
    return floating_barge_section(length=3., breadth=4., bulkheads=(1.5,),
        girders=(0.,), stiffener_spacing=2., openings=(), target_size=.5)


def test_already_structured_models_skip_the_eligibility_probe(monkeypatch):
    import anymesher
    calls = install_eligibility(monkeypatch, True)
    project = untrimmed_project()
    # Genuinely untrimmed geometry; only the owner mappability verdict is fixed.
    assert not any(face.holes for face in project.geometry.faces.values())
    monkeypatch.setattr(anymesher, 'check_mappable', lambda geometry, face: SimpleNamespace(ok=True))
    kw = project_dispatch(monkeypatch, project, target_size=.5, strategy='auto', order='linear')
    assert calls == [] and kw['strategy'] == 'auto' and kw['quad_options'] is None


def test_holes_are_never_already_structured_even_when_check_mappable_is_ok(monkeypatch):
    import anymesher
    calls = install_eligibility(monkeypatch, True)
    project = small_project()
    assert any(face.holes for face in project.geometry.faces.values())
    # check_mappable permits holes and reports ok=True; the probe must still run.
    monkeypatch.setattr(anymesher, 'check_mappable', lambda geometry, face: SimpleNamespace(ok=True))
    kw = project_dispatch(monkeypatch, project, target_size=.5, strategy='auto', order='linear')
    assert len(calls) == 1 and kw['strategy'] == 'smart_mapped'


def test_automatic_selection_is_not_blocked_by_recovery_automation(monkeypatch):
    from anymesher import MeshAutomationOptions
    calls = install_eligibility(monkeypatch, True)
    seen = []

    def recovery(working, **kw):
        seen.append(kw)
        raise Dispatched(kw)
    import anymesher
    monkeypatch.setattr(anymesher, 'generate_automatic_mesh_result', recovery)
    project = small_project()
    automation = MeshAutomationOptions(strict_method=False, allow_inspection=True, max_seconds=None)
    with pytest.raises(Dispatched) as caught:
        project.generate_mesh(.5, strategy='auto', order='linear', automation=automation)
    kw = caught.value.kwargs
    assert len(calls) == 1
    assert kw['strategy'] == 'smart_mapped' and kw['automation'] is automation
    assert kw['quad_options'] == effective_quad_options(None) and kw['layout_policy'] == 'adaptive'
    # The recorder interrupts before publication. Final selection diagnostics
    # are checked separately on a completed real owner call.


def test_async_job_with_qt_style_automation_selects_smart_mapped_for_holed_geometry(monkeypatch):
    """The Qt task always supplies MeshAutomationOptions; user Automatic must still select."""
    import anymesher
    from anymesher import MeshAutomationOptions
    calls = install_eligibility(monkeypatch, True)

    def recovery(working, **kw):
        raise Dispatched(kw)
    monkeypatch.setattr(anymesher, 'generate_automatic_mesh_result', recovery)
    project = small_project()
    assert any(face.holes for face in project.geometry.faces.values())
    automation = MeshAutomationOptions(strict_method=False, allow_inspection=True, max_seconds=None)
    settings = MeshSettings.create(.5, element_order='linear', strategy='auto', automation=automation)
    with pytest.raises(Dispatched) as caught:
        run_mesh_job(project, 'model-hash', settings, lambda *a: None, NO_CANCEL)
    kw = caught.value.kwargs
    assert len(calls) == 1
    assert kw['strategy'] == 'smart_mapped' and kw['automation'] == automation
    assert kw['quad_options'] == effective_quad_options(None) and kw['layout_policy'] == 'adaptive'
    # Final diagnostics are populated only after the owner returns.


def test_explicit_smart_mapped_reaches_the_recovery_route(monkeypatch):
    from anymesher import MeshAutomationOptions
    calls = install_eligibility(monkeypatch, True)
    import anymesher

    def recovery(working, **kw):
        raise Dispatched(kw)
    monkeypatch.setattr(anymesher, 'generate_automatic_mesh_result', recovery)
    automation = MeshAutomationOptions(strict_method=False, allow_inspection=True, max_seconds=None)
    with pytest.raises(Dispatched) as caught:
        small_project().generate_mesh(.5, strategy='smart_mapped', order='linear', automation=automation)
    kw = caught.value.kwargs
    assert calls == [] and kw['strategy'] == 'smart_mapped'
    assert kw['quad_options'] == effective_quad_options(None) and kw['automation'] is automation


def test_explicit_quad_options_with_automatic_stay_refused_without_a_probe(monkeypatch):
    calls = install_eligibility(monkeypatch, True)
    with pytest.raises(ProjectError, match="requires strategy='quad_first' or 'smart_mapped'"):
        small_project().generate_mesh(.5, strategy='auto', order='linear',
            quad_options=effective_quad_options(None))
    assert calls == []


def test_selected_route_is_recorded_in_final_diagnostics_after_source_remap(monkeypatch):
    calls = install_eligibility(monkeypatch, True, 'planar boundary bars supported')
    project = small_project()
    project.set_native_mesh_settings(NativeMeshSettings.create(.5, element_order='linear',
        parameters={'quad_t3_angle_action': 'warning'}))
    mesh = project.generate_mesh()
    assert len(calls) == 1
    record = mesh.hybrid_diagnostics['automatic_selection']
    assert record['selected'] == 'smart_mapped' and record['reason'] == 'planar boundary bars supported'
    assert record['selection_seconds'] >= 0.0 and 'provenance' in record
    assert project._last_mesh_execution['automatic_selection'] == record
    assert mesh.beams and mesh.couplings and project.mesh_records == {}


def test_rejected_probe_is_execution_only_and_leaves_the_mesh_diagnostics_alone(monkeypatch):
    install_eligibility(monkeypatch, False, 'not eligible fixture')
    project = small_project()
    mesh = project.generate_mesh(.5, strategy='auto', order='linear')
    assert 'automatic_selection' not in mesh.hybrid_diagnostics
    record = project._last_mesh_execution['automatic_selection']
    assert record['selected'] == 'legacy_auto' and record['reason'] == 'not eligible fixture'


def test_mesh_semantic_hash_ignores_only_the_selection_timing():
    from anyfem.mesh_jobs import mesh_semantic_hash
    project = small_project()
    mesh = project.generate_mesh(.5, strategy='smart_mapped', order='linear',
        layout_policy='adaptive', quad_t3_angle_action='warning')

    def record(seconds):
        return {'selected': 'smart_mapped', 'reason': 'r', 'eligibility': {'eligible': True},
                'provenance': 'p', 'selection_seconds': seconds}
    mesh.hybrid_diagnostics = {**mesh.hybrid_diagnostics, 'automatic_selection': record(0.1)}
    first = mesh_semantic_hash(mesh, model_hash='m', mesh_input_hash='i')
    mesh.hybrid_diagnostics = {**mesh.hybrid_diagnostics, 'automatic_selection': record(9.9)}
    assert mesh_semantic_hash(mesh, model_hash='m', mesh_input_hash='i') == first


# ----------------------------------------------------------------------------
# headless backend Automatic uses the same shared selection
# ----------------------------------------------------------------------------
def run_backend(monkeypatch, backend, request_backend):
    project = small_project()
    settings = NativeMeshSettings.create(.5, element_order='quadratic', backend=backend)
    monkeypatch.setattr(backend_module, 'generate_hybrid_mesh_result', lambda geometry, **kw: recorder(**kw))
    session = backend_module.NativeProjectMeshingSession(project, settings, max_background_jobs=1)
    try:
        view = backend_module.GeometryMeshingView(project.geometry)
        component = next(
            component for component in session.all_components()
            if any(project.geometry.faces[face].holes for face in (
                view.faces_for_sheet(component.id) if component.kind == 'sheet'
                else (component.id,) if component.kind == 'face' else ())))
        prediction = MeshPrediction(
            MeshBackend.NATIVE if request_backend is MeshBackend.AUTOMATIC else request_backend)
        request = MeshGenerationRequest(component, GenerationToken(1, 1),
            session.capture_component(component), settings, settings.controls,
            request_backend, prediction, NativeMeshingCancellation())
        with pytest.raises(Dispatched) as caught:
            session.generate_component(request)
    finally:
        session.close()
    return caught.value.kwargs


def test_headless_automatic_eligible_uses_the_shared_selection(monkeypatch):
    calls = install_eligibility(monkeypatch, True)
    kw = run_backend(monkeypatch, 'automatic', MeshBackend.AUTOMATIC)
    assert len(calls) == 1 and calls[0]['order'] == 'quadratic'
    assert kw['strategy'] == 'smart_mapped' and kw['layout_policy'] == 'adaptive'
    assert kw['quad_options'] == effective_quad_options(None) and kw['native_backend'] == 'python'


def test_headless_automatic_rejected_and_explicit_backends_keep_their_routes(monkeypatch):
    calls = install_eligibility(monkeypatch, False)
    kw = run_backend(monkeypatch, 'automatic', MeshBackend.AUTOMATIC)
    assert len(calls) == 1 and kw['strategy'] == 'auto' and 'quad_options' not in kw
    calls.clear()
    install_eligible = install_eligibility(monkeypatch, True)
    for name, enum in (('automatic', MeshBackend.NATIVE), ('quad_first', MeshBackend.QUAD_FIRST),
                       ('smart_mapped', MeshBackend.SMART_MAPPED)):
        run_backend(monkeypatch, name, enum)
    assert install_eligible == []


# ----------------------------------------------------------------------------
# application workflow: async/sync strategy resolution, persistence, jobs
# ----------------------------------------------------------------------------
def workflow_stub(project):
    from anyfem.application.workflow import WorkbenchWorkflow
    submitted = []

    @contextmanager
    def transaction(*_args, **_kwargs):
        yield

    stub = SimpleNamespace(
        project=project, seeding_overrides={},
        mesh_task_manager=SimpleNamespace(busy=False,
            submit=lambda job_id, snapshot, settings: submitted.append(settings)),
        session=SimpleNamespace(transaction=transaction,
            snapshot=lambda: SimpleNamespace(revision=SimpleNamespace(model_hash='h'))),
        set_status=lambda *a, **k: None, refresh_panels=lambda: None,
        _project_mesh_strategy=WorkbenchWorkflow._project_mesh_strategy,
        _project_structure_preference=WorkbenchWorkflow._project_structure_preference)
    stub._store_mesh_strategy = lambda *a, **k: WorkbenchWorkflow._store_mesh_strategy(stub, *a, **k)
    return WorkbenchWorkflow, stub, submitted


def test_workflow_resolves_and_persists_smart_mapped_and_keeps_other_tokens():
    workflow, stub, _ = workflow_stub(small_project())
    resolve = workflow._project_mesh_strategy
    assert resolve(stub.project, ' Smart_Mapped ') == 'smart_mapped'
    assert resolve(stub.project, 'quad_first') == 'quad_first'
    assert resolve(stub.project, None) == 'auto'
    for token in ('auto', 'mapped', 'native'):
        assert resolve(stub.project, token) == token
    stub.project.set_native_mesh_settings(NativeMeshSettings.create(.5, backend='smart_mapped'))
    assert resolve(stub.project, None) == 'smart_mapped'


def test_workflow_async_submission_carries_and_persists_smart_mapped():
    workflow, stub, submitted = workflow_stub(small_project())
    record = workflow.generate_mesh_async(stub, .5, strategy='smart_mapped', layout_policy='adaptive')
    settings = submitted[0]
    assert settings.strategy == 'smart_mapped' and settings.layout_policy == 'adaptive'
    assert settings.quad_options == effective_quad_options(None)
    assert record.summary['strategy_requested'] == 'smart_mapped'
    assert record.summary['layout_policy_requested'] == 'adaptive'
    assert record.summary['quad_options_requested'] is not None
    persisted = stub.project.native_mesh_settings
    assert persisted.backend is MeshBackend.SMART_MAPPED
    reopened = project_from_dict(json.loads(json.dumps(project_to_dict(stub.project))))
    assert reopened.native_mesh_settings.backend is MeshBackend.SMART_MAPPED
    assert reopened.native_mesh_settings.to_dict()['parameters']['layout_policy'] == 'adaptive'


def test_workflow_async_automatic_stays_automatic_and_reopened_smart_mapped_is_inherited():
    workflow, stub, submitted = workflow_stub(small_project())
    workflow.generate_mesh_async(stub, .5, strategy='auto')
    assert submitted[0].strategy == 'auto' and submitted[0].quad_options is None
    assert stub.project.native_mesh_settings.backend is MeshBackend.AUTOMATIC
    workflow, stub, submitted = workflow_stub(small_project())
    stub.project.set_native_mesh_settings(NativeMeshSettings.create(.5, backend='smart_mapped'))
    workflow.generate_mesh_async(stub, .5)
    assert submitted[0].strategy == 'smart_mapped'
    assert submitted[0].quad_options == effective_quad_options(None)


def test_workflow_sync_route_reaches_project_with_smart_mapped(monkeypatch):
    from anyfem.mesh_execution import MeshExecutionPolicy
    workflow, stub, _ = workflow_stub(small_project())
    monkeypatch.setattr(project_module, 'generate_hybrid_mesh', lambda working, **kw: recorder(**kw))
    with pytest.raises(Dispatched) as caught:
        workflow.generate_mesh(stub, .5, strategy='smart_mapped', layout_policy='adaptive',
            execution_policy=MeshExecutionPolicy.off())
    kw = caught.value.kwargs
    assert kw['strategy'] == 'smart_mapped' and kw['layout_policy'] == 'adaptive'
    assert kw['quad_options'] == effective_quad_options(None)
    assert stub.project.native_mesh_settings.backend is MeshBackend.SMART_MAPPED


# ----------------------------------------------------------------------------
# Qt visible label (SmartMapped) maps to the persisted token
# ----------------------------------------------------------------------------
def test_qt_mesh_task_label_is_smart_mapped_and_maps_to_the_token():
    pytest.importorskip('PySide6')
    import os
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    from PySide6.QtWidgets import QApplication
    from anyfem.ui.qt.tasks import MeshTask
    qapp = QApplication.instance() or QApplication([])
    app = SimpleNamespace(project=small_project(), guarded=lambda function: function,
        cancel_mesh=lambda: None, commit_mesh_layout_preview=lambda: None,
        clear_mesh_layout_preview=lambda: None)
    task = MeshTask(app)
    labels = [task.strategy.itemText(i) for i in range(task.strategy.count())]
    assert labels == ['quad_first', 'auto', 'mapped', 'native', 'SmartMapped']
    for index, token in enumerate(('quad_first', 'auto', 'mapped', 'native', 'smart_mapped')):
        task.strategy.setCurrentIndex(index)
        assert task.strategy_token() == token
        assert task.settings()['strategy'] == token
    task.set_strategy('smart_mapped')
    assert task.strategy.currentText() == 'SmartMapped'
    del qapp
