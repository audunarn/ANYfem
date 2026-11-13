"""The two SAT reader opt-ins pass through the application layer, default off, and are recorded."""
import math
from types import SimpleNamespace

import pytest
from anygeometry import GeometryModel, OrientedEdge

from anyfem.application import sat_import as app


def _fake_result():
    model = GeometryModel()
    p = model.add_points([(0., 0., 0.), (1., 0., 0.), (1., 1., 0.)])
    edges = [model.add_line(p[0], p[1]), model.add_line(p[1], p[2]), model.add_line(p[2], p[0])]
    face = model.add_face_from_loop([OrientedEdge(e, True) for e in edges])
    return SimpleNamespace(geometry=model, source_sha256="0" * 64, source_unit_scale_m=1e-3,
                           diagnostics=("note",), source_vertex_map={}, source_edge_map={},
                           source_face_map={7: face})


@pytest.fixture
def calls(monkeypatch):
    seen = []
    reader = pytest.importorskip("anyfileio.sat_import")

    def read_sat(path, **options):
        seen.append(options)
        return _fake_result()

    monkeypatch.setattr(reader, "read_sat", read_sat)
    return seen


def test_opt_ins_are_off_by_default_and_not_passed(calls, tmp_path):
    project, _ = app.import_sat_project(tmp_path / "a.sat")
    assert calls == [{}]
    part = next(iter(project.geometry.parts.values()))
    assert "import_options" not in part.metadata


def test_opt_ins_pass_through_and_are_recorded(calls, tmp_path):
    project, _ = app.import_sat_project(tmp_path / "a.sat", vertex_relocation_tolerance_m=2e-6,
                                        approximate_curve_tolerance_m=5e-6, cancelled=lambda: False)
    assert calls[0]["vertex_relocation_tolerance_m"] == 2e-6
    assert calls[0]["approximate_curve_tolerance_m"] == 5e-6
    assert "cancelled" in calls[0]
    part = next(iter(project.geometry.parts.values()))
    assert dict(part.metadata["import_options"]) == {"vertex_relocation_tolerance_m": 2e-6,
                                               "approximate_curve_tolerance_m": 5e-6}


def test_only_the_given_opt_in_is_passed_and_recorded(calls, tmp_path):
    project, _ = app.import_sat_project(tmp_path / "a.sat", approximate_curve_tolerance_m=5e-6)
    assert calls[0] == {"approximate_curve_tolerance_m": 5e-6}
    part = next(iter(project.geometry.parts.values()))
    assert dict(part.metadata["import_options"]) == {"approximate_curve_tolerance_m": 5e-6}


@pytest.mark.parametrize("text,expected", [("", None), ("  ", None), (None, None), ("2e-6", 2e-6), (" 5e-6 ", 5e-6)])
def test_tolerance_text_parses(text, expected):
    assert app.parse_tolerance_text(text, "Vertex relocation tolerance") == expected


@pytest.mark.parametrize("text", ["abc", "0", "-1e-6", "nan", "inf", "1e-6 m"])
def test_tolerance_text_refuses_bad_input_naming_the_field(text):
    with pytest.raises(ValueError, match="Vertex relocation tolerance"):
        app.parse_tolerance_text(text, "Vertex relocation tolerance")


def test_refusal_hint_names_the_field_for_each_opt_in():
    assert app.opt_in_hint("edge 3: curve exceeds the source resolution; approximate_curve_tolerance_m opts in") \
        == app.OPT_IN_FIELD_LABELS["approximate_curve_tolerance_m"]
    assert app.opt_in_hint("needs vertex_relocation_tolerance_m") == app.OPT_IN_FIELD_LABELS["vertex_relocation_tolerance_m"]
    assert app.opt_in_hint("edge 3: endpoint gap") is None
