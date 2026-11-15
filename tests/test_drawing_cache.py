"""The per-call drawing memo: shared inside one call, never stale across workbench edits."""
import csv
import threading

import numpy as np
import pytest

pytest.importorskip("shapely", minversion="2")

from anyfem.application import drawing_cache, plate_profiles
from anyfem.application.drawing_cache import drawing_scope
from anyfem.application.thickness_side import resolve_sides, resolve_thickness_sides, set_override
from anyfem.document import DocumentSession

from test_drawing_thickness_side import _Conformal


def _tee():
    m = _Conformal()
    flange, web = m.flange(), m.web("web", 0, .5)
    return m, flange, web


def _plate_view(m, sheet, **options):
    from anyfem.application.plate_view import build_plate_view
    face = m.g.face_uses[m.g.sheets[sheet].face_use_ids[0]].face_id
    return build_plate_view(m.p, face, **options)


def _side_note(view):
    return next(n for n in view.notes if n.startswith("Thickness side"))


def _counting(monkeypatch):
    calls = []
    original = plate_profiles._compute_planar_geometry

    def counted(geometry, faces, sheet):
        calls.append(None if sheet is None else sheet.id)
        return original(geometry, faces, sheet)
    monkeypatch.setattr(plate_profiles, "_compute_planar_geometry", counted)
    return calls


def test_one_call_shares_contours_and_the_memo_closes_with_it(monkeypatch):
    m, flange, web = _tee()
    calls = _counting(monkeypatch)
    _plate_view(m, flange)
    assert sorted(calls) == sorted(set(calls)), "a Sheet contour was rebuilt inside one call"
    assert getattr(drawing_cache._state, "memo", None) is None
    with drawing_scope():
        _plate_view(m, flange)
        first = len(calls)
        _plate_view(m, web)
        _plate_view(m, flange)
        assert len(calls) == first, "the open scope must reuse the first view's contours"
    assert getattr(drawing_cache._state, "memo", None) is None


def test_thickness_side_edits_between_calls_regenerate():
    m, flange, web = _tee()
    assert resolve_thickness_sides(m.p, m.part)[flange][0] == "front"
    assert _side_note(_plate_view(m, flange)).startswith("Thickness side front")
    set_override(m.p, flange, "back")
    assert resolve_thickness_sides(m.p, m.part)[flange] == ("back", "manual", "override")
    assert _side_note(_plate_view(m, flange)).startswith("Thickness side back")
    set_override(m.p, flange, "auto")
    assert resolve_sides(m.p)[flange] == "front"


def test_side_changes_inside_one_scope_are_part_of_the_key():
    m, flange, web = _tee()
    with drawing_scope():
        assert resolve_sides(m.p)[flange] == "front"
        assert resolve_sides(m.p, {flange: "back"})[flange] == "back"
        m.p.drawing_thickness_sides[flange] = "centre"
        assert resolve_sides(m.p)[flange] == "centre"
        assert resolve_thickness_sides(m.p, m.part)[flange] == ("centre", "manual", "override")


def test_geometry_edit_between_calls_regenerates():
    m, flange, web = _tee()
    assert resolve_thickness_sides(m.p, m.part)[flange][2] == "1 neighbour on +n"
    m.web("down", -.5, 0)  # a second web below the flange: the same Part, new geometry
    assert resolve_thickness_sides(m.p, m.part)[flange] == (
        "centre", "auto", "neighbours on both sides (1 on +n, 1 on -n)")


def test_export_regenerates_after_side_and_datum_edits(tmp_path):
    from anyfem.application.plate_drawings import export_plate_drawings
    m, flange, web = _tee()

    def rows(name, **options):
        folder = export_plate_drawings(DocumentSession(m.p).snapshot(), m.part, tmp_path/name, **options)
        with (folder/"Plate-index.csv").open(newline="", encoding="utf-8") as stream:
            return {row["Plate"]: row for row in csv.DictReader(stream)}

    before = rows("a")
    set_override(m.p, flange, "back")
    after = rows("b")
    assert before["flange"]["Thickness side"] != after["flange"]["Thickness side"]
    moved = rows("c", elevation_datum="global")
    assert moved != after
    assert {k: v["Net mass per plate (kg)"] for k, v in moved.items()} == \
        {k: v["Net mass per plate (kg)"] for k, v in before.items()}


def test_cached_results_are_read_only_and_refusals_are_fresh():
    m, flange, web = _tee()
    faces = [m.g.faces[m.g.face_uses[u].face_id] for u in m.g.sheets[flange].face_use_ids]
    with drawing_scope():
        shape = plate_profiles._planar_geometry(m.g, faces, m.g.sheets[flange])
        with pytest.raises(ValueError):
            shape.origin[0] = 1.
        assert plate_profiles._planar_geometry(m.g, faces, m.g.sheets[flange]) is shape
        errors = []
        for _ in range(2):
            with pytest.raises(ValueError, match="Empty or repeated") as caught:
                plate_profiles._planar_geometry(m.g, faces + faces[:1], m.g.sheets[flange])
            errors.append(caught.value)
        assert errors[0] is not errors[1] and str(errors[0]) == str(errors[1])
    unscoped = plate_profiles._planar_geometry(m.g, faces, m.g.sheets[flange])
    assert unscoped is not shape and unscoped.origin.flags.writeable
    assert np.array_equal(unscoped.origin, shape.origin)


def test_each_thread_has_its_own_memo():
    seen = {}
    with drawing_scope():
        drawing_cache.memo("probe", 1, lambda: "main")

        def worker():
            seen["memo"] = getattr(drawing_cache._state, "memo", None)
            seen["value"] = drawing_cache.memo("probe", 1, lambda: "worker")
        thread = threading.Thread(target=worker)
        thread.start()
        thread.join()
        assert drawing_cache.memo("probe", 1, lambda: "again") == "main"
    assert seen == {"memo": None, "value": "worker"}
