"""Shared desktop numeric list interpreter, including safe refusal cases."""
import pytest

from anyfem.model.units import UNIT_PROFILES
from anyfem.ui.numeric_input import numeric_sequence_annotation, parse_numeric_sequence


@pytest.mark.parametrize("text", [
    "1,2,3", "1 2 3", "(1,2,3)", "[1,2,3]", "[1 2 3]",
    "(1, 2  3)", "1\t2\n3",
])
def test_numeric_formats(text):
    assert parse_numeric_sequence(text, int) == (1, 2, 3)
    assert parse_numeric_sequence(text) == (1.0, 2.0, 3.0)


def test_exponents_signs_json_and_explicit_units():
    profile = UNIT_PROFILES["SI-mm-N-MPa"]
    parse = lambda value: profile.parse(value, "length")
    for text in ("-1e3 mm 2 cm +3", "(-1e3 mm, 2 cm, +3)",
                 '["-1e3 mm", "2 cm", 3]'):
        assert parse_numeric_sequence(text, parse, units=True) == pytest.approx((-1, .02, .003))
    assert parse_numeric_sequence("[]") == ()


@pytest.mark.parametrize("text", [
    "1,,2", "1,", "1 2,", "1-2", "(1,2]", "[1 2", "1 2)",
    "[1, [2]]", "[true, 2]", "[\"1_2\"]", "nan 2", "1e999 2",
])
def test_malformed_or_nonfinite_numbers_are_rejected(text):
    with pytest.raises(ValueError):
        parse_numeric_sequence(text)


@pytest.mark.parametrize("text", ["1.5 2", "1e1 2", "[1.0, 2]"])
def test_id_lists_require_whole_numbers(text):
    with pytest.raises(ValueError):
        parse_numeric_sequence(text, int)


def test_only_flat_numeric_annotations_are_interpreted_as_lists():
    assert numeric_sequence_annotation("Sequence[float]") == "float"
    assert numeric_sequence_annotation("Optional[Sequence[int]]") == "int"
    assert numeric_sequence_annotation("typing.Optional[typing.Tuple[int, int]]") == "int"
    assert numeric_sequence_annotation("tuple[float, float, float] | None") == "float"
    assert numeric_sequence_annotation("Sequence[Sequence[float]] | None") is None
    assert numeric_sequence_annotation("Sequence[str]") is None
    assert numeric_sequence_annotation("Sequence[EntityRef]") is None
    assert numeric_sequence_annotation("Sequence[float] | str") is None


def test_other_desktop_panels_share_list_parsing():
    from types import SimpleNamespace
    from anyfem.ui.panels import GeometryPanel, ResultsPanel, SolvePanel

    value = lambda text: SimpleNamespace(get=lambda: text)
    assert SolvePanel._triple(value("[0 1 -2]"), "direction") == (0., 1., -2.)
    geometry = SimpleNamespace(_face_sketch_pair=value("(1 3)"))
    assert GeometryPanel._sketch_pair(geometry, SimpleNamespace(point_keys=(0, 1, 2))) == (0, 2)
    results = SimpleNamespace(_limits=value("[0 30e6]"))
    assert ResultsPanel.colour_limits(results) == (0., 30e6)
    with pytest.raises(ValueError):
        SolvePanel._triple(value("0,,1 2"), "direction")
