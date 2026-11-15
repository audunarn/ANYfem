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


def test_same_faces_moved_between_calls_regenerate():
    m, flange, web = _tee()
    assert resolve_thickness_sides(m.p, m.part)[flange][0] == "front"
    with m.g.transaction():  # the web's top edge goes below the flange: same faces and IDs
        for x, y in ((1, 0), (1, 1)):
            m.g.move_point(m.points[x, y, .5], x, y, -.5)
    assert resolve_thickness_sides(m.p, m.part)[flange] == ("back", "auto", "1 neighbour on -n")
    assert _side_note(_plate_view(m, flange)).startswith("Thickness side back")


def _index(folder):
    with (folder/"Plate-index.csv").open(newline="", encoding="utf-8") as stream:
        return {row["Plate"]: row for row in csv.DictReader(stream)}


def test_export_regenerates_after_side_datum_and_extent_edits(tmp_path):
    from anyfem.application.drawing_extent import DrawingExtent
    from anyfem.application.plate_drawings import export_plate_drawings
    m = _Conformal()
    flange, web = m.flange(z=1.), m.web("web", 1., 1.5)

    def export(name, **options):
        return export_plate_drawings(DocumentSession(m.p).snapshot(), m.part, tmp_path/name, **options)

    front = _index(export("a"))
    assert front["flange"]["Thickness side"].startswith("front")
    assert (front["flange"]["Lower edge Z above support base (mm)"],
            front["flange"]["Upper edge Z above support base (mm)"]) == ("0.000", "10.000")
    set_override(m.p, flange, "back")
    back = _index(export("b"))
    assert back["flange"]["Thickness side"].startswith("back")
    assert (back["flange"]["Lower edge Z above support base (mm)"],
            back["web"]["Upper edge Z above support base (mm)"]) == ("0.000", "510.000")
    world = _index(export("c", elevation_datum="global"))
    assert (world["flange"]["Lower global Z (mm)"], world["flange"]["Upper global Z (mm)"],
            world["web"]["Upper global Z (mm)"]) == ("990.000", "1000.000", "1500.000")
    assert {k: v["Net mass per plate (kg)"] for k, v in world.items()} == \
        {k: v["Net mass per plate (kg)"] for k, v in front.items()}
    bare = export("d", extent=DrawingExtent(plate_profiles=False))
    assert not (bare/"profiles").exists() and {row["Profile file"] for row in _index(bare).values()} == {""}
    assert (tmp_path/"a"/"profiles").is_dir()


def test_a_cached_refusal_keeps_its_type_message_and_cause():
    def refuse():
        try:
            raise KeyError("missing")
        except KeyError as error:
            raise ValueError("refused") from error

    def fresh():
        with pytest.raises(ValueError) as caught:
            drawing_cache.memo("probe", 1, refuse)
        return caught.value
    expected = fresh()  # no scope: computed afresh
    with drawing_scope():
        first, second = fresh(), fresh()
    for error in (first, second):
        assert type(error) is ValueError and str(error) == "refused"
        assert type(error.__cause__) is KeyError and str(error.__cause__) == str(expected.__cause__)
        assert error.__suppress_context__ is expected.__suppress_context__ is True
    assert second is not first and second.__traceback__ is not None


def _dxf_folder(folder, names):
    folder.mkdir(parents=True)
    for name in names:
        (folder/f"{name}.dxf").write_text("0\nEOF\n")
    return folder


class _Converter:
    """Fake OpenCAD Studio process: ``plan[name] = (seconds, return code | "timeout" | "launch")``."""

    def __init__(self, plan, launched, killed):
        self.plan, self.launched, self.killed = plan, launched, killed

    def __call__(self, command, **options):
        import subprocess
        import threading
        from pathlib import Path
        converter = self
        name = Path(command[2]).stem
        seconds, outcome = self.plan.get(name, (0., 0))
        if outcome == "launch":
            raise OSError(f"cannot launch {name}")
        self.launched.append(name)

        class Process:
            returncode = None

            def __init__(self):
                self.stop = threading.Event()
                self.args = command

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def communicate(self, input=None, timeout=None):
                if not self.stop.is_set() and not self.stop.wait(seconds) and outcome == "timeout":
                    raise subprocess.TimeoutExpired(command, timeout)
                if self.stop.is_set():
                    self.returncode = 1
                    return b"", b"killed"
                self.returncode = outcome
                return b"", f"bad {name}".encode()

            def kill(self):
                converter.killed.append(name)
                self.stop.set()

            def poll(self):
                return self.returncode
        return Process()


def _convert(tmp_path, monkeypatch, plan, names, workers):
    import sys
    from anyfem.application import plate_drawings
    launched, killed = [], []
    monkeypatch.setattr(plate_drawings.subprocess, "Popen", _Converter(plan, launched, killed))
    monkeypatch.setattr(plate_drawings, "_DWG_WORKERS", workers)
    folder = _dxf_folder(tmp_path/f"case{len(list(tmp_path.iterdir()))}", names)
    try:
        plate_drawings._make_dwg_copies(sys.executable, folder, folder/"dwg", backend="studio")
    finally:
        _convert.launched, _convert.killed = launched, killed


def test_parallel_dwg_reports_the_first_failure_in_input_order_and_stops(tmp_path, monkeypatch):
    plan = {"a": (.3, 7), "b": (0., "launch"), "c": (.6, 0)}
    with pytest.raises(ValueError, match="returned 7 during DWG conversion. bad a"):
        _convert(tmp_path, monkeypatch, plan, "abcdefgh", workers=2)
    # A sequential run stops at a. b failed to launch at once, so nothing after b starts.
    assert _convert.launched == ["a"]


def test_parallel_dwg_failure_is_not_delayed_by_running_conversions(tmp_path, monkeypatch):
    import time
    plan = {"a": (.05, 7), "b": (30., 0), "c": (30., 0)}
    started = time.perf_counter()
    with pytest.raises(ValueError, match="returned 7"):
        _convert(tmp_path, monkeypatch, plan, "abcdef", workers=3)
    assert time.perf_counter() - started < 5, "running conversions must be stopped, not waited for"
    assert sorted(_convert.launched) == ["a", "b", "c"] and sorted(_convert.killed) == ["b", "c"]


def test_parallel_dwg_timeout_and_launch_errors_match_a_sequential_run(tmp_path, monkeypatch):
    import subprocess
    for workers in (1, 3):
        with pytest.raises(ValueError, match="timed out while writing DWG files") as caught:
            _convert(tmp_path, monkeypatch, {"a": (0., "timeout"), "b": (0., "launch")}, "abc", workers)
        assert isinstance(caught.value.__cause__, subprocess.TimeoutExpired) and "a" in _convert.killed
        with pytest.raises(OSError, match="cannot launch b"):
            _convert(tmp_path, monkeypatch, {"b": (0., "launch"), "c": (0., 9)}, "bc", workers)
        with pytest.raises(ValueError, match="returned 9 during DWG conversion. bad c"):
            _convert(tmp_path, monkeypatch, {"c": (0., 9)}, "bcd", workers)


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


def test_one_scope_thaws_a_snapshot_once_and_each_call_afresh():
    from anyfem.application.drawing_cache import thawed
    m, flange, web = _tee()
    snapshot = DocumentSession(m.p).snapshot()
    with drawing_scope():
        project = thawed(snapshot)
        assert thawed(snapshot) is project
        assert thawed(DocumentSession(m.p).snapshot()) is not project
    assert thawed(snapshot) is not project and thawed(snapshot) is not thawed(snapshot)


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
