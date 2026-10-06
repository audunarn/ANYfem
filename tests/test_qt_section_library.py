"""The optional ANYsection beam-section library inside the Sections task.

The library dialog is modal, so these tests replace ``choose_section`` with a
function returning a catalogue choice; everything after the pick (form loading,
unit display, the command and its undo) is the real ANYfem path.
"""
import importlib.util
import json
import os
import sys
import pytest
if os.environ.get("ANYFEM_RUN_QT_TESTS") != "1":
    pytest.skip("set ANYFEM_RUN_QT_TESTS=1", allow_module_level=True)
pytest.importorskip("PySide6")
pytest.importorskip("anysection")
from anysection import SectionChoice
from anysection.beams import BeamSectionCatalog, build_beam_catalog
from anyfem import commands
from anyfem.model.materials import dnv_steel_material
from anyfem.model.units import UNIT_PROFILES
from anyfem.ui.qt import engineering
from anyfem.ui.qt.engineering import SectionTask
from test_qt_geometry_actions import qapp, window  # noqa: F401  (shared Qt fixtures)


def _write_raw(directory):
    """A three-series Dlubal-style capture: a T-section, an angle and a flat bar."""
    def parameter(key, value):
        return {"id": key, "symbol": key, "unit": "mm", "description": key, "value": value, "valueJson": value}

    def section(identifier, name, geometry, area):
        return {"id": identifier, "name": name, "url": name.lower().replace(" ", "-"), "errorMsg": None,
                "branches": [{"name": "Geometry", "parameters": [parameter(k, v) for k, v in geometry.items()]},
                             {"name": "Sectional Area", "parameters": [parameter("A", area)]}]}

    def entry(identifier, category, name, standard, shape):
        return {"id": identifier, "name": name, "url": "series-" + name.lower(), "standard": standard,
                "manufacturer": "--", "region": "Europe", "shape": shape, "categoryId": category,
                "sectionName": "Standardized - Steel", "expectedSections": 1, "isValid": True}

    categories = [{"id": 1, "description": "T-Sections, Steel Tees", "sectionName": "Standardized - Steel"},
                  {"id": 2, "description": "L-Sections, Steel Angles", "sectionName": "Standardized - Steel"},
                  {"id": 3, "description": "Bar Sections", "sectionName": "Standardized - Steel"}]
    series = [entry(10, 1, "HALF-HEA", "DIN 1025-3", "Parallel flange T-sections"),
              entry(11, 2, "EA", "EN 10056-1", "Hot rolled equal leg angles"),
              entry(12, 3, "FL", "EN 10058", "Flat bars")]
    contents = {10: section(1, "1/2 HEA 360", {"h": "175.0", "b": "300.0", "t_w": "10.0", "t_f": "17.5"}, "71.40"),
                11: section(2, "EA 110x110x12", {"h": "110.0", "t": "12.0"}, "25.14"),
                12: section(3, "FL 90x30", {"b": "90.0", "t": "30.0"}, "27.00")}
    (directory / "series").mkdir(parents=True)
    (directory / "manifest.json").write_text(json.dumps({"categories": categories, "series": series}))
    for identifier, content in contents.items():
        (directory / "series" / f"{identifier}.json").write_text(
            json.dumps({"seriesTable": {"id": identifier, "sections": [content]}}))


@pytest.fixture(scope="module")
def catalog(tmp_path_factory):
    root = tmp_path_factory.mktemp("section_library")
    _write_raw(root / "raw")
    build_beam_catalog(root / "raw", root / "beams.sqlite")
    return BeamSectionCatalog(root / "beams.sqlite")


@pytest.fixture
def library(window, catalog, monkeypatch):
    """The Sections task with the modal dialog replaced by a scripted pick."""
    picks = []

    def pick(name, orientation="standing"):
        picks.append(SectionChoice(catalog.get(name), orientation))

    import anysection.gui
    monkeypatch.setattr(anysection.gui, "choose_section",
                        lambda parent=None, catalog=None, **_: picks.pop(0) if picks else None)
    editor = window.panels["Sections"]
    editor.pick = pick
    return editor


def test_default_project_has_the_single_material_the_library_suggests(window):
    assert list(window.project.materials) == ["S355"]


def section_fields(editor):
    return editor.record_fields["section"][1]


def test_button_is_present_enabled_and_described(window):
    button = window.panels["Sections"].library_button
    assert button.text().startswith("Browse section library") and button.isEnabled()
    assert "beam section" in button.toolTip().lower()


def test_pick_loads_the_beam_form_without_changing_the_model(window, library):
    before = dict(window.project.beam_sections)
    library.pick("1/2 HEA 360")
    library.browse_library()
    assert library.choice.currentData() == "AddBeamSection"
    fields = section_fields(library)
    assert fields["name"][0].text() == "1/2 HEA 360 [HALF-HEA | DIN 1025-3]"
    assert fields["profile"][0].text() == "T-bar"
    assert fields["material"][0].text() == "S355"  # the project's only material is suggested
    assert "Loaded 1/2 HEA 360" in library.feedback.text() and "undoable" in library.feedback.text()
    assert window.project.beam_sections == before  # nothing is applied until Apply


def test_catalogue_dimensions_are_shown_clean_but_exact(window, library):
    library.pick("EA 110x110x12")
    library.browse_library()
    fields = section_fields(library)
    assert fields["web_height"][0].text() == "0.098 m"          # not 0.098000000000000004 m
    assert fields["flange_width"][0].text() == "0.11 m"
    section = library.command_options()["section"]
    assert section.web_height == 0.098 and section.flange_width == 0.11   # exact, not rounded
    window.run(commands.SetUnitProfile(UNIT_PROFILES["SI-mm-N-MPa"]))
    library.pick("1/2 HEA 360")
    library.browse_library()
    assert section_fields(library)["web_height"][0].text() == "157.5 mm"
    assert section_fields(library)["flange_width"][0].text() == "300 mm"
    assert library.command_options()["section"].web_height == pytest.approx(0.1575, rel=0, abs=1e-15)


def test_apply_is_one_undoable_command_with_correct_dimensions(window, library):
    library.pick("1/2 HEA 360")
    library.browse_library()
    library.execute()
    name = "1/2 HEA 360 [HALF-HEA | DIN 1025-3]"
    section = window.project.beam_sections[name]
    assert section.profile == "T-bar" and section.material == "S355"
    assert section.web_height == pytest.approx(0.1575)          # overall depth 175 mm - tf
    assert section.web_height + section.flange_thickness == pytest.approx(0.175)
    assert (section.web_thickness, section.flange_width) == (pytest.approx(0.010), pytest.approx(0.300))
    # ANYfem resolves the properties itself, from the same four dimensions
    assert section.properties()["area"] == pytest.approx(0.1575 * 0.010 + 0.300 * 0.0175)
    window.commands.undo()
    assert name not in window.project.beam_sections


def test_dimensions_follow_the_project_unit_profile(window, library):
    window.run(commands.SetUnitProfile(UNIT_PROFILES["SI-mm-N-MPa"]))
    library.pick("EA 110x110x12")
    library.browse_library()
    text = section_fields(library)["web_height"][0].text()
    assert text.endswith("mm") and float(text.split()[0]) == pytest.approx(98.0)
    section = library.command_options()["section"]
    assert section.web_height == pytest.approx(0.098)           # parsed back to SI
    assert section.profile == "Angle"
    assert "web direction" in library.feedback.text()           # asymmetric profile


def test_flat_bar_stands_on_edge_in_anyfems_convention(window, library):
    library.pick("FL 90x30")
    library.browse_library()
    section = library.command_options()["section"]
    # ANYfem Flatbar = flange_width (y) x flange_thickness (z, the web direction)
    assert (section.flange_width, section.flange_thickness) == (pytest.approx(0.030), pytest.approx(0.090))
    library.pick("FL 90x30", "flat")
    library.browse_library()
    section = library.command_options()["section"]
    assert (section.flange_width, section.flange_thickness) == (pytest.approx(0.090), pytest.approx(0.030))


def test_material_is_never_guessed_between_several(window, library):
    window.run(commands.AddMaterial(dnv_steel_material("S235", 0.01)))
    assert len(window.project.materials) == 2  # the default S355 and this one
    library.pick("EA 110x110x12")
    library.browse_library()
    assert section_fields(library)["material"][0].text() == ""
    assert "Choose the material" in library.feedback.text()
    with pytest.raises(ValueError):                             # required field still empty
        library.execute()
    assert not any("EA 110x110x12" in name for name in window.project.beam_sections)


def test_cancelling_the_library_changes_nothing(window, library):
    before = (library.choice.currentData(), library.feedback.text())
    assert library.browse_library() is None
    assert (library.choice.currentData(), library.feedback.text()) == before


def test_missing_anysection_disables_the_button_with_an_install_hint(window, monkeypatch):
    real = importlib.util.find_spec
    monkeypatch.setattr(engineering.importlib.util, "find_spec",
                        lambda name, *a, **k: None if name == "anysection" else real(name, *a, **k))
    task = SectionTask(window)
    assert not task.library_button.isEnabled()
    assert "ANYsection" in task.library_button.toolTip()
    task.deleteLater()


def test_unimportable_library_is_a_clear_error_not_a_crash(window, monkeypatch):
    monkeypatch.setitem(sys.modules, "anysection.gui", None)   # PySide6-less or broken install
    with pytest.raises(ValueError, match="ANYsection with PySide6"):
        window.panels["Sections"].browse_library()
