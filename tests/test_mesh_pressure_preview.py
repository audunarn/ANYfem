"""Headless mesh-pressure preview: selected step, signed values, coverage, refusals.

Synthetic source-aware pressure fields from the existing remap fixture; no BEM,
solver or full-history evaluation.
"""
import numpy as np
import pytest
from test_pressure_remap import project_and_meshes, original_history
from anyfem.model import WetPressure
from anyfem.document import canonical_hash
from anyfem.io.project_file import project_to_dict
from anyfem.presentation import mesh_pressure_preview as preview
from anyfem.presentation.hydro_pressure_glyphs import pressure_color, NEUTRAL_COLOR
from anyfem.solve import build as solve_build
from anyfem.solve.build import WetElementPressure


def series(regular, scale=2.):
    p, old, new = project_and_meshes()
    history, _bank, _r = original_history(p, old, regular=regular)
    owner = p.add_load_series('wave', history.times)
    owner.wet_pressures.append(WetPressure(history=history, mapping_key='element', name='w', scale=scale))
    return p, old, new, history


def colours(scene):
    found = {}
    for patch in scene.faces:
        for colour, owners in zip(patch.colors, patch.polygon_owners):
            element = next(o.id for o in owners if o.kind == 'element')
            found[int(element)] = colour
    return found


@pytest.mark.parametrize('regular', [True, False])
def test_selected_step_signed_values_match_canonical_resolved_step(regular, monkeypatch):
    p, old, _new, history = series(regular)
    calls = []
    original = solve_build.wet_element_pressures
    monkeypatch.setattr(solve_build, 'wet_element_pressures',
                        lambda *a, **k: (calls.append(k.get('limit', 'missing')) or original(*a, **k)))
    before = canonical_hash(project_to_dict(p))
    signs = set()
    for number in (1, 4, 8):
        scene, selection, info = preview.build_mesh_pressure_scene(p, old, 'wave', number)
        step = p.series_children('wave')[number - 1]
        expected = {r.element_id: r.pressure for r in original(p, old, p.resolve_case(step))}
        assert selection.number == number and selection.time == pytest.approx(history.times[number - 1])
        assert info['values'] == expected and info['mapped'] == len(expected) == 5
        peak = max(abs(v) for v in expected.values())
        got = colours(scene)
        for element, value in expected.items():
            assert got[element] == pressure_color(value, peak)
            signs.add(np.sign(value))
        assert scene.legend['unit'] == 'Pa' and f'step {number}/' in scene.legend['title']
        assert 'element normal' in scene.legend['title'] and 'not a result' in scene.legend['title']
    assert calls == [None, None, None]  # one snapshot per request, never limited or whole-history
    assert canonical_hash(project_to_dict(p)) == before
    assert -1.0 in signs  # negative values are preserved, not abs()


def test_steps_differ_and_report_actual_number_time():
    p, old, _new, history = series(True)
    first = preview.build_mesh_pressure_scene(p, old, 'wave', 1)[2]['values']
    later = preview.build_mesh_pressure_scene(p, old, 'wave', 4)[2]['values']
    assert first != later
    assert preview.resolve_preview_selection(p, 'wave', 4).label().count('step 4 of 101') == 1
    stepname = p.series_children('wave')[2].name  # a step row selects itself
    assert preview.resolve_preview_selection(p, stepname).number == 3


def test_refusals_are_actionable():
    p, old, _new, _h = series(True)
    with pytest.raises(preview.MeshPressurePreviewError, match='No structural mesh'):
        preview.build_mesh_pressure_scene(p, None, 'wave', 1)
    with pytest.raises(preview.MeshPressurePreviewError, match='outside series'):
        preview.build_mesh_pressure_scene(p, old, 'wave', 999)
    with pytest.raises(preview.MeshPressurePreviewError, match='does not exist'):
        preview.build_mesh_pressure_scene(p, old, 'nothing', 1)
    p.load_case('empty')
    with pytest.raises(preview.MeshPressurePreviewError, match='no wet pressure'):
        preview.build_mesh_pressure_scene(p, old, 'empty')


def test_number_envelope_selected_step_off_is_plain_and_on_is_coloured():
    from dataclasses import replace
    from anyfem.model.attributes import Envelope
    p, old, _new, _h = series(True)
    case = p.load_cases['wave']
    case.wet_pressures[0] = replace(case.wet_pressures[0], envelope=Envelope(by='number', start=1, stop=1))
    on = preview.build_mesh_pressure_scene(p, old, 'wave', 1)
    off = preview.build_mesh_pressure_scene(p, old, 'wave', 3)
    assert on[2]['mapped'] == 5 and off[2]['values'] == {} and off[2]['mapped'] == 0
    assert set(colours(off[0]).values()) == {preview.NO_PRESSURE_COLOR}
    assert 'plain mesh colour: unloaded or zero' in off[0].legend['title']


def test_exact_cancellation_of_two_wet_loads_is_zero_or_unreturned_never_coloured():
    from dataclasses import replace
    p, old, _new, _h = series(True)
    case = p.load_cases['wave']
    load = case.wet_pressures[0]
    case.wet_pressures[:] = [replace(load, scale=2.), replace(load, scale=-2.)]
    scene, _selection, info = preview.build_mesh_pressure_scene(p, old, 'wave', 4)
    # The owner keeps an explicit accumulated zero for opposing non-zero records.
    assert info['values'] and all(abs(v) < 1e-9 for v in info['values'].values())
    assert set(colours(scene).values()) == {NEUTRAL_COLOR}


def test_changed_mesh_uses_existing_remap_and_unbound_mesh_refuses():
    p, old, new, _h = series(False)
    scene, _selection, info = preview.build_mesh_pressure_scene(p, new, 'wave', 3)
    assert info['mapped'] == len(new.quads) and set(colours(scene)) == set(new.quads)
    assert len(info['values']) == len(new.quads)
    p2, old2, new2 = project_and_meshes()
    history, _b, _r = original_history(p2, old2, regular=True)
    owner = p2.add_load_series('wave', history.times)
    owner.wet_pressures.append(WetPressure(history=history.mapped_history, mapping_key='element'))
    with pytest.raises(preview.MeshPressurePreviewError, match='regenerate wave input'):
        preview.build_mesh_pressure_scene(p2, new2, 'wave', 1)


def test_zero_scale_returns_no_records_and_stays_plain_not_zero():
    p, old, _new, _h = series(True, scale=0.)
    scene, _selection, info = preview.build_mesh_pressure_scene(p, old, 'wave', 2)
    assert info['values'] == {} and info['mapped'] == 0 and info['unmapped'] == len(old.quads)
    assert info['peak'] == 0.0 and info['minimum'] is None and info['maximum'] is None
    assert set(colours(scene).values()) == {preview.NO_PRESSURE_COLOR}
    assert scene.legend['unmapped_label'] == preview.NO_PRESSURE_LABEL


def test_partial_coverage_explicit_zero_and_negative_keep_distinct_colours(monkeypatch):
    p, old, _new, _h = series(True)
    ids = sorted(old.quads)
    normal = np.array([0., 0., 1.])
    records = [WetElementPressure(ids[0], 5., normal, normal), WetElementPressure(ids[1], -5., normal, normal),
               WetElementPressure(ids[2], 0., normal, normal)]
    monkeypatch.setattr(solve_build, 'wet_element_pressures', lambda *a, **k: records)
    scene, _selection, info = preview.build_mesh_pressure_scene(p, old, 'wave', 1)
    got = colours(scene)
    assert got[ids[0]] != got[ids[1]]  # sign preserved, no abs
    assert got[ids[0]] == pressure_color(5., 5.) and got[ids[1]] == pressure_color(-5., 5.)
    assert got[ids[2]] == NEUTRAL_COLOR  # explicit returned zero
    for element in ids[3:]:
        assert got[element] == preview.NO_PRESSURE_COLOR != NEUTRAL_COLOR  # never filled with zero
    assert info['mapped'] == 3 and info['unmapped'] == len(ids) - 3
