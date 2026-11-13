"""Selectable extent of drawing generation (spec S7): views, sections, plate profiles, index and takeoff."""
import json

import pytest

from test_drawing_thickness_side import _Conformal


def _model():
    m = _Conformal()
    m.flange(), m.web("web", 0, .5)
    return m


def _snapshot(m):
    from anyfem.document import DocumentSession
    return DocumentSession(m.p).snapshot()


def test_extent_presets_defaults_and_refusal_of_empty():
    from anyfem.application.drawing_extent import DrawingExtent
    d = DrawingExtent()
    assert d.views == ("isometric", "plan", "front", "right")
    assert d.sections and d.plate_profiles and d.index_and_takeoff
    assert d == DrawingExtent.all_views() and d.is_default
    iso = DrawingExtent.isometric_only()
    assert iso.views == ("isometric",) and not iso.is_default
    assert not (iso.sections or iso.plate_profiles or iso.index_and_takeoff)
    # order is normalised, aliases accepted
    assert DrawingExtent(views=("right", "iso", "plan")).views == ("isometric", "plan", "right")
    with pytest.raises(ValueError, match="nothing to generate"):
        DrawingExtent(views=(), sections=False, plate_profiles=False, index_and_takeoff=False)
    with pytest.raises(ValueError, match="view"):
        DrawingExtent(views=("top",))
    assert DrawingExtent(views=()).views == ()  # still produces sections, profiles and index
    assert DrawingExtent.from_dict(d.to_dict()) == d


@pytest.mark.parametrize("text, expected", [
    ("all", ("isometric", "plan", "front", "right")), ("iso", ("isometric",)),
    ("front,iso", ("isometric", "front")), ("iso,plan,front,right", ("isometric", "plan", "front", "right"))])
def test_parse_views(text, expected):
    from anyfem.application.drawing_extent import parse_views
    assert parse_views(text) == expected


def test_parse_views_refuses_unknown_and_empty():
    from anyfem.application.drawing_extent import parse_views
    for bad in ("", "top", "iso,,plan"):
        with pytest.raises(ValueError):
            parse_views(bad)


def test_assembly_views_subset_keeps_order_and_content():
    from anyfem.application.drawing_assembly import assembly_views
    m = _model()
    full = assembly_views(m.p, m.part)
    assert [v.projection for v in full] == ["isometric", "plan", "front", "right"]
    sub = assembly_views(m.p, m.part, views=("right", "isometric"))
    assert [v.projection for v in sub] == ["isometric", "right"]
    assert [v.paths for v in sub] == [full[0].paths, full[3].paths]
    assert [v.notes for v in sub] == [full[0].notes, full[3].notes]
    only = assembly_views(m.p, m.part, views=("plan",))
    assert [v.projection for v in only] == ["plan"] and only[0].paths == full[1].paths
    # the historical flag still works
    assert [v.projection for v in assembly_views(m.p, m.part, all_views=False)] == ["isometric"]
    with pytest.raises(ValueError):
        assembly_views(m.p, m.part, views=())
    with pytest.raises(ValueError, match="view"):
        assembly_views(m.p, m.part, views=("top",))


def _files(folder):
    return sorted(str(f.relative_to(folder)).replace("\\", "/") for f in folder.rglob("*") if f.is_file())


def test_default_export_has_everything_and_no_extent_key(tmp_path):
    from anyfem.application.plate_drawings import export_plate_drawings
    m = _model()
    folder = export_plate_drawings(_snapshot(m), m.part, tmp_path / "all")
    names = _files(folder)
    assert "Plate-index.csv" in names and "Weight-takeoff.csv" in names
    assert any(n.startswith("profiles/") for n in names)
    assert "extent" not in json.loads((folder / "Drawing-basis.json").read_text())


def test_export_writes_only_selected_artefacts_and_records_extent(tmp_path):
    from anyfem.application.drawing_extent import DrawingExtent
    from anyfem.application.plate_drawings import export_plate_drawings
    m = _model()
    snap = _snapshot(m)
    full = export_plate_drawings(snap, m.part, tmp_path / "full")
    no_profiles = export_plate_drawings(snap, m.part, tmp_path / "np",
                                        extent=DrawingExtent(plate_profiles=False))
    assert not any(n.startswith("profiles/") for n in _files(no_profiles))
    assert (no_profiles / "Weight-takeoff.csv").read_bytes() == (full / "Weight-takeoff.csv").read_bytes()
    basis = json.loads((no_profiles / "Drawing-basis.json").read_text())
    assert basis["extent"]["plate_profiles"] is False and basis["extent"]["index_and_takeoff"] is True
    assert basis["extent"]["views"] == ["isometric", "plan", "front", "right"]
    no_index = export_plate_drawings(snap, m.part, tmp_path / "ni", extent=DrawingExtent(index_and_takeoff=False))
    names = _files(no_index)
    assert "Plate-index.csv" not in names and "Weight-takeoff.csv" not in names
    assert any(n.startswith("profiles/") for n in names)
    assert sorted(f.name for f in (no_index / "profiles").iterdir()) == sorted(
        f.name for f in (full / "profiles").iterdir())  # DXF bytes carry a timestamp, so compare names
    iso = export_plate_drawings(snap, m.part, tmp_path / "iso", extent=DrawingExtent.isometric_only())
    assert _files(iso) == ["Drawing-basis.json"]
    assert json.loads((iso / "Drawing-basis.json").read_text())["extent"]["views"] == ["isometric"]


def test_command_line_extent_options(tmp_path):
    from anyfem.application.plate_drawings import main
    from anyfem.io.project_file import save_project
    m = _model()
    source = save_project(m.p, tmp_path / "m.anyfem")
    out = tmp_path / "cli"
    main([str(source), str(out), "--part", str(m.part), "--views", "iso,front",
          "--no-sections", "--no-plate-profiles", "--no-index"])
    # the selected assembly views are written (S7 review 2); nothing else is
    assert _files(out) == ["Drawing-basis.json", "views/View-01.dxf", "views/View-02.dxf"]
    extent = json.loads((out / "Drawing-basis.json").read_text())["extent"]
    assert extent == {"views": ["isometric", "front"], "sections": False, "plate_profiles": False,
                      "index_and_takeoff": False}
    out2 = tmp_path / "cli2"
    main([str(source), str(out2), "--part", str(m.part), "--views", "all"])
    assert "extent" not in json.loads((out2 / "Drawing-basis.json").read_text())
    out3 = tmp_path / "cli3"
    main([str(source), str(out3), "--part", str(m.part), "--views", "iso"])
    assert json.loads((out3 / "Drawing-basis.json").read_text())["extent"]["views"] == ["isometric"]
    with pytest.raises(SystemExit):
        main([str(source), str(tmp_path / "bad"), "--part", str(m.part), "--views", "top"])
    assert not (tmp_path / "bad").exists()


def test_export_writes_selected_assembly_views_on_request(tmp_path):
    from anyfem.application.drawing_extent import DrawingExtent
    from anyfem.application.plate_drawings import export_plate_drawings
    m = _model()
    snap = _snapshot(m)
    default = export_plate_drawings(snap, m.part, tmp_path / "default")
    assert not any(n.startswith("views/") for n in _files(default))
    assert "views" not in json.loads((default / "Drawing-basis.json").read_text())
    out = export_plate_drawings(snap, m.part, tmp_path / "v", write_assembly_views=True,
                                extent=DrawingExtent(views=("front", "isometric"), plate_profiles=False,
                                                     index_and_takeoff=False))
    assert [n for n in _files(out) if n.startswith("views/")] == ["views/View-01.dxf", "views/View-02.dxf"]
    basis = json.loads((out / "Drawing-basis.json").read_text())
    assert [v["drawing"] for v in basis["views"]] == ["views/View-01.dxf", "views/View-02.dxf"]
    assert [v["title"] for v in basis["views"]] == [v.title for v in __import__(
        "anyfem.application.drawing_assembly", fromlist=["x"]).assembly_views(
        m.p, m.part, elevation_datum="support_base", views=("isometric", "front"))]


def test_export_refuses_an_extent_that_writes_nothing(tmp_path):
    from anyfem.application.drawing_extent import DrawingExtent
    from anyfem.application.plate_drawings import export_plate_drawings
    m = _model()
    snap = _snapshot(m)
    for extent in (DrawingExtent(views=(), sections=True, plate_profiles=False, index_and_takeoff=False),):
        with pytest.raises(ValueError, match="nothing"):
            export_plate_drawings(snap, m.part, tmp_path / "none", write_assembly_views=True, extent=extent)
        assert not (tmp_path / "none").exists()


def test_cli_writes_the_views_it_claims(tmp_path):
    from anyfem.application.plate_drawings import main
    from anyfem.io.project_file import save_project
    m = _model()
    source = save_project(m.p, tmp_path / "m.anyfem")
    out = tmp_path / "cli"
    main([str(source), str(out), "--part", str(m.part), "--views", "iso",
          "--no-sections", "--no-plate-profiles", "--no-index"])
    assert "views/View-01.dxf" in _files(out) and len(list((out / "views").iterdir())) == 1
    basis = json.loads((out / "Drawing-basis.json").read_text())
    assert basis["extent"]["views"] == ["isometric"] and len(basis["views"]) == 1
    # without --views nothing is written beyond the historical package
    plain = tmp_path / "plain"
    main([str(source), str(plain), "--part", str(m.part)])
    assert not any(n.startswith("views/") for n in _files(plain))
    # nothing selected at all is refused before anything is written
    with pytest.raises(SystemExit):
        main([str(source), str(tmp_path / "empty"), "--part", str(m.part),
              "--no-plate-profiles", "--no-index"])
    assert not (tmp_path / "empty").exists()
