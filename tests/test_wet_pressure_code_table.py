"""ANYfem's use of ANYloads pressure: user Python code and CSV/Excel tables.

The evaluation itself (parsing, trust, table reading and interpolation) is
tested in ANYloads. These tests pin what ANYfem adds: attaching those
definitions to wet plates, numbering load cases, persisting them in a project,
gating a solve on trust, and drawing them.
"""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

import numpy as np
import pytest
from anyloads import is_trusted, trust_code, trust_store_path

from test_wet_pressure import _round_trip, _total_force, _wall

from anyfem.io.project_file import project_from_dict, project_to_dict
from anyfem.model import PressureTable, ProjectError, WetPressure, WetSurface
from anyfem.solve import solve_linear_static_many
from anyfem.solve.build import build_fe_model
from anyfem.ui.scene import COLOR_WET, build_attribute_overlay

AREA = 2.0 * 3.0


@pytest.fixture(autouse=True)
def isolated_trust_store(tmp_path, monkeypatch):
    """Never touch the real user's trust list."""

    monkeypatch.setenv("ANYLOADS_TRUSTED_CODE_FILE", str(tmp_path / "trusted.json"))
    return tmp_path / "trusted.json"


def _wet_wall():
    project, face = _wall()
    project.add_wet_surface(WetSurface(ref=project.face(face), fluid_side="negative_normal"))
    return project, face


def _force(project, case="default", size=0.5):
    mesh = project.generate_mesh(size)
    return abs(_total_force(build_fe_model(project, mesh, case))[1])


def _dedent(code):
    return textwrap.dedent(code).strip() + "\n"


# ----------------------------------------------------------------------
# code
# ----------------------------------------------------------------------
def test_malformed_code_is_refused_when_the_record_is_made():
    with pytest.raises(ValueError, match="must define"):
        WetPressure(code="x = 1")
    with pytest.raises(ValueError, match="empty"):
        WetPressure(code="")


def test_code_receives_the_load_case_number_and_the_element_position():
    project, face = _wet_wall()
    source = _dedent(
        """
        def pressure(case, x, y, z):
            return 1000.0 * case
        """
    )
    project.load_case("first").add_wet_pressure(code=source)
    project.load_case("second").add_wet_pressure(code=source)
    assert project.load_cases["first"].case_number == 1
    assert project.load_cases["second"].case_number == 2
    assert _force(project, "first") == pytest.approx(1000.0 * AREA)
    assert _force(project, "second") == pytest.approx(2000.0 * AREA)


def test_code_may_use_position_and_branching():
    project, _ = _wet_wall()
    project.load_case().add_wet_pressure(code=_dedent(
        """
        import math

        def pressure(case, x, y, z):
            if z > 1.5:
                return 0.0          # above the waterline
            return 4000.0 * (1.5 - z) + 0.0 * math.sin(x)
        """
    ))
    # Linear over a waterline that sits on an element edge: exact.
    assert _force(project) == pytest.approx(4000.0 * 1.5**2 / 2.0 * 2.0)


def test_only_declared_arguments_are_passed_and_t_name_parameters_work():
    project, _ = _wet_wall()
    case = project.load_case("wave")
    case.set_time(2.0)
    case.set_parameter("amp", 500.0)
    case.add_wet_pressure(code=_dedent(
        """
        def pressure(t, name, parameters):
            assert name == "wave"
            return parameters["amp"] * t
        """
    ))
    assert _force(project, "wave") == pytest.approx(1000.0 * AREA)


def test_vectorized_code_is_called_once_with_arrays():
    project, _ = _wet_wall()
    project.load_case().add_wet_pressure(
        code=_dedent(
            """
            def pressure(x, y, z):
                import numpy as np
                return 1000.0 * np.maximum(0.0, 1.5 - z)
            """
        ),
        code_vectorized=True,
    )
    assert _force(project) == pytest.approx(1000.0 * 1.5**2 / 2.0 * 2.0)


def test_scale_applies_to_code_and_mapping_alike():
    project, face = _wet_wall()
    project.load_case("code").add_wet_pressure(
        code="def pressure(z):\n    return 1000.0\n", scale=3.0
    )
    project.load_case("map").add_wet_pressure(mapping={face: 1000.0}, scale=3.0)
    assert _force(project, "code") == pytest.approx(3000.0 * AREA)
    assert _force(project, "map") == pytest.approx(3000.0 * AREA)


def test_a_series_numbers_its_steps_from_one():
    project, _ = _wet_wall()
    project.add_load_case_series(
        "s",
        [0.0, 1.0, 2.0],
        builder=lambda case, t, step: case.add_wet_pressure(
            code=_dedent(
                """
                def pressure(case, x, y, z):
                    return 100.0 * case
                """
            )
        ),
    )
    assert [project.load_cases[f"s_{i}"].case_number for i in range(3)] == [1, 2, 3]
    assert [_force(project, f"s_{i}") for i in range(3)] == pytest.approx(
        [100.0 * AREA, 200.0 * AREA, 300.0 * AREA]
    )


def test_a_runtime_error_in_user_code_names_the_line():
    project, _ = _wet_wall()
    project.load_case().add_wet_pressure(code=_dedent(
        """
        def pressure(case, x, y, z):
            ratio = 1.0
            return ratio / (z - z)
        """
    ))
    with pytest.raises(ProjectError, match=r"line 3.*ZeroDivisionError"):
        build_fe_model(project, project.generate_mesh(0.5))


def test_a_non_finite_or_non_numeric_return_is_refused():
    project, _ = _wet_wall()
    project.load_case("nan").add_wet_pressure(code="def pressure(z):\n    return float('nan')\n")
    project.load_case("text").add_wet_pressure(code="def pressure(z):\n    return 'a lot'\n")
    mesh = project.generate_mesh(0.5)
    with pytest.raises(ProjectError, match="non-finite"):
        build_fe_model(project, mesh, "nan")
    with pytest.raises(ProjectError, match="ValueError"):
        build_fe_model(project, mesh, "text")


# ----------------------------------------------------------------------
# trust
# ----------------------------------------------------------------------
def test_code_you_add_is_trusted_but_code_in_a_file_is_not(isolated_trust_store):
    project, _ = _wet_wall()
    source = "def pressure(z):\n    return 1000.0\n"
    project.load_case().add_wet_pressure(code=source)
    assert is_trusted(source) and isolated_trust_store.exists()
    assert project.untrusted_code() == []

    document = json.dumps(project_to_dict(project))
    isolated_trust_store.unlink()  # a different machine: nothing trusted
    received = project_from_dict(json.loads(document))
    assert [item[2] for item in received.untrusted_code()] == [source]
    with pytest.raises(ProjectError, match="not been trusted"):
        build_fe_model(received, received.generate_mesh(0.5))

    trust_code(source)
    assert received.untrusted_code() == []
    assert abs(_total_force(build_fe_model(received, received.generate_mesh(0.5)))[1]) == pytest.approx(
        1000.0 * AREA
    )


def test_loading_validating_and_drawing_never_run_untrusted_code(tmp_path, isolated_trust_store):
    marker = tmp_path / "ran.txt"
    source = (
        f"open({str(marker)!r}, 'w').write('executed')\n"
        "def pressure(z):\n    return 1000.0\n"
    )
    project, _ = _wet_wall()
    project.load_case().add_wet_pressure(code=source)
    marker.unlink(missing_ok=True)  # trusting it did not run it, but be sure
    document = json.dumps(project_to_dict(project))
    isolated_trust_store.unlink()

    received = project_from_dict(json.loads(document))
    received.validate()
    mesh = received.generate_mesh(0.5)
    scene = build_attribute_overlay(received, case_name="default", mesh=mesh)
    scene2 = build_attribute_overlay(received, case_name="default")
    assert not marker.exists(), "untrusted code must not run on load, validation or drawing"
    assert not [a for a in scene.arrows if a.color == COLOR_WET]
    assert not [a for a in scene2.arrows if a.color == COLOR_WET]


def test_trust_is_kept_outside_the_project(isolated_trust_store):
    project, _ = _wet_wall()
    project.load_case().add_wet_pressure(code="def pressure(z):\n    return 1\n")
    assert "trust" not in json.dumps(project_to_dict(project)).casefold()
    assert trust_store_path() == isolated_trust_store


def test_code_round_trips_through_a_project_file():
    project, _ = _wet_wall()
    source = "def pressure(x, y, z):\n    return 2.0 * z\n"
    project.load_case("a").add_wet_pressure(code=source, code_vectorized=True, scale=3.0)
    data, loaded = _round_trip(project)
    assert data["anyfem"]["format"] == 9
    load = loaded.load_cases["a"].wet_pressures[0]
    assert (load.kind, load.code, load.code_vectorized, load.scale) == ("code", source, True, 3.0)
    assert loaded.load_cases["a"].number == project.load_cases["a"].number
    assert _round_trip(loaded)[0] == data


def test_the_editing_commands_trust_what_the_user_types():
    from dataclasses import replace

    from anyfem import commands as cmd

    project, face = _wet_wall()
    stack = cmd.CommandStack(project)
    first = "def pressure(z):\n    return 1.0\n"
    load = stack.run(cmd.AddWetPressure(code=first, case="c"))
    assert is_trusted(first)
    second = "def pressure(z):\n    return 2.0\n"
    stack.run(cmd.EditAttribute(replace(load, code=second)))
    assert is_trusted(second)
    assert project.load_cases["c"].wet_pressures[0].code == second


# ----------------------------------------------------------------------
# overlay
# ----------------------------------------------------------------------
@pytest.mark.parametrize("meshed", [False, True])
def test_trusted_code_is_drawn(meshed):
    project, _ = _wet_wall()
    project.load_case().add_wet_pressure(
        code="def pressure(z):\n    return 1000.0 * max(0.0, 2.5 - z)\n"
    )
    mesh = project.generate_mesh(0.25) if meshed else None
    arrows = [a for a in build_attribute_overlay(project, case_name="default", mesh=mesh).arrows
              if a.color == COLOR_WET]
    assert arrows and all(a.end[2] < 2.5 for a in arrows)


# ----------------------------------------------------------------------
# tables
# ----------------------------------------------------------------------
def _grid():
    """Element centroids of the 2 x 3 m wall at 0.5 m."""

    xs = np.arange(0.25, 2.0, 0.5)
    zs = np.arange(0.25, 3.0, 0.5)
    return np.array([(x, 0.0, z) for x in xs for z in zs])


def _write_csv(path, rows, header="case,x,y,z,pressure", delimiter=","):
    lines = [header.replace(",", delimiter)] if header else []
    lines += [delimiter.join(f"{value:.12g}" if not isinstance(value, str) else value for value in row) for row in rows]
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _rows_at(key, fn):
    return [(key, *point, fn(point)) for point in _grid()]


def test_a_table_maps_pressure_to_the_wet_plate_exactly_on_matching_points(tmp_path):
    path = _write_csv(tmp_path / "p.csv", _rows_at(1, lambda p: 1000 + 100 * p[0] + 2000 * p[2]))
    table = PressureTable.from_file(path, key="number")
    project, _ = _wet_wall()
    project.load_case().add_wet_pressure(table=table)
    # 1000*6 + 100*2*3 + 2000*2*4.5: the midpoint rule is exact for a plane.
    assert _force(project) == pytest.approx(24600.0)


def test_each_load_case_takes_its_own_rows_by_number(tmp_path):
    rows = _rows_at(1, lambda p: 1000.0) + _rows_at(2, lambda p: 5000.0)
    table = PressureTable.from_file(_write_csv(tmp_path / "p.csv", rows), key="number")
    project, _ = _wet_wall()
    project.load_case("one").add_wet_pressure(table=table)
    project.load_case("two").add_wet_pressure(table=table)
    assert _force(project, "one") == pytest.approx(1000.0 * AREA)
    assert _force(project, "two") == pytest.approx(5000.0 * AREA)
    project.load_case("three").add_wet_pressure(table=table)
    with pytest.raises(ProjectError, match="no rows for number 3"):
        build_fe_model(project, project.generate_mesh(0.5), "three")


def test_a_time_keyed_table_can_interpolate_between_times(tmp_path):
    rows = _rows_at(0.0, lambda p: 1000.0) + _rows_at(2.0, lambda p: 3000.0)
    path = _write_csv(tmp_path / "p.csv", rows)
    project, _ = _wet_wall()
    exact = PressureTable.from_file(path, key="time")
    smooth = PressureTable.from_file(path, key="time", interpolate_time=True)
    for name, time, table in (("a", 2.0, exact), ("b", 0.5, smooth), ("c", 0.5, exact), ("d", 9.0, smooth)):
        case = project.load_case(name)
        case.set_time(time)
        case.add_wet_pressure(table=table)
    assert _force(project, "a") == pytest.approx(3000.0 * AREA)
    assert _force(project, "b") == pytest.approx(1500.0 * AREA)
    with pytest.raises(ProjectError, match=r"no rows for time 0.5.*interpolate_time"):
        build_fe_model(project, project.generate_mesh(0.5), "c")
    with pytest.raises(ProjectError, match="outside the table's times"):
        build_fe_model(project, project.generate_mesh(0.5), "d")


def test_a_name_keyed_table_and_a_single_map_without_identifiers(tmp_path):
    rows = _rows_at("calm", lambda p: 100.0) + _rows_at("storm", lambda p: 900.0)
    named = PressureTable.from_file(_write_csv(tmp_path / "n.csv", rows), key="name")
    project, _ = _wet_wall()
    project.load_case("storm").add_wet_pressure(table=named)
    assert _force(project, "storm") == pytest.approx(900.0 * AREA)
    project.load_case("other").add_wet_pressure(table=named)
    with pytest.raises(ProjectError, match=r"no rows for case 'other'.*calm"):
        build_fe_model(project, project.generate_mesh(0.5), "other")

    plain = _write_csv(
        tmp_path / "one.csv", [(*p, 700.0) for p in _grid()], header="x,y,z,pressure"
    )
    project.load_case("any").add_wet_pressure(table=PressureTable.from_file(plain))
    assert _force(project, "any") == pytest.approx(700.0 * AREA)


def test_units_scale_the_table_into_pascals(tmp_path):
    path = _write_csv(
        tmp_path / "mm.csv",
        [("A", *(p * 1000 for p in point), 800.0) for point in _grid()],
        header="Run,X [mm],Y [mm],Z [mm],P [kPa]",
    )
    table = PressureTable.from_file(
        path, key="name",
        columns={"key": "Run", "x": "X [mm]", "y": "Y [mm]", "z": "Z [mm]", "pressure": "P [kPa]"},
        length_scale=1e-3,
    )
    project, _ = _wet_wall()
    project.load_case("odd").add_wet_pressure(table=table, table_case="A", scale=1000.0)
    assert _force(project, "odd") == pytest.approx(800_000.0 * AREA)


def test_max_distance_refuses_elements_far_from_any_table_point(tmp_path):
    path = _write_csv(tmp_path / "p.csv", [(0.25, 0.0, 0.25, 1000.0)], header="x,y,z,pressure")
    project, _ = _wet_wall()
    project.load_case().add_wet_pressure(table=PressureTable.from_file(path, max_distance=0.6))
    with pytest.raises(ProjectError, match=r"no table point within 0.6 m"):
        build_fe_model(project, project.generate_mesh(0.5))


def test_a_changed_or_missing_file_is_refused_not_silently_reused(tmp_path):
    path = _write_csv(tmp_path / "p.csv", _rows_at(1, lambda p: 1000.0))
    table = PressureTable.from_file(path, key="number")
    project, _ = _wet_wall()
    project.load_case().add_wet_pressure(table=table)
    mesh = project.generate_mesh(0.5)
    _write_csv(path, _rows_at(1, lambda p: 9999.0))
    with pytest.raises(ProjectError, match="has changed since it was added"):
        build_fe_model(project, mesh)
    with pytest.raises(ProjectError, match="has changed"):
        project.validate()
    path.unlink()
    with pytest.raises(ProjectError, match="does not exist"):
        project.validate()


def test_cases_can_be_generated_from_a_table_and_solved_together(tmp_path):
    rows = (
        _rows_at(0.0, lambda p: 1000.0)
        + _rows_at(1.0, lambda p: 2000.0)
        + _rows_at(2.0, lambda p: 3000.0)
    )
    table = PressureTable.from_file(_write_csv(tmp_path / "p.csv", rows), key="time")
    project, _ = _wet_wall()
    cases = project.add_load_cases_from_table("run", table)
    assert [c.name for c in cases] == ["run_0", "run_1", "run_2"]
    assert [c.time for c in cases] == [0.0, 1.0, 2.0]
    assert [c.series for c in cases] == ["run"] * 3
    batch = solve_linear_static_many(
        project, mesh=project.generate_mesh(0.5), load_cases=[c.name for c in cases]
    )
    peaks = [s.max_translation()[1] for s in batch.shapes]
    assert peaks[1] == pytest.approx(2.0 * peaks[0], rel=1e-6)
    assert peaks[2] == pytest.approx(3.0 * peaks[0], rel=1e-6)

    keyed = PressureTable.from_file(
        _write_csv(tmp_path / "n.csv", _rows_at(7, lambda p: 10.0) + _rows_at(9, lambda p: 20.0)),
        key="number",
    )
    made = project.add_load_cases_from_table("num", keyed)
    assert [_force(project, c.name) for c in made] == pytest.approx([10.0 * AREA, 20.0 * AREA])
    with pytest.raises(ProjectError, match="already exist"):
        project.add_load_cases_from_table("num", keyed)


def test_a_table_round_trips_and_keeps_its_checksum(tmp_path):
    path = _write_csv(tmp_path / "p.csv", _rows_at(1, lambda p: 1000.0))
    table = PressureTable.from_file(
        path, key="number", length_scale=1.0, method="idw", neighbours=3, max_distance=1.5
    )
    project, _ = _wet_wall()
    project.load_case("a").add_wet_pressure(table=table, table_case=1, scale=2.0)
    data, loaded = _round_trip(project)
    entry = data["load_cases"][0]["wet_pressures"][0]
    assert entry["table"]["sha256"] == table.sha256 and entry["table_case"] == 1
    load = loaded.load_cases["a"].wet_pressures[0]
    assert load.table == table and load.table_case == 1
    assert _round_trip(loaded)[0] == data
    # Defaults stay out of the file.
    plain = PressureTable.from_file(path, key="number")
    project.load_case("b").add_wet_pressure(table=plain)
    entry = next(c for c in project_to_dict(project)["load_cases"] if c["name"] == "b")["wet_pressures"][0]
    assert set(entry["table"]) == {"path", "sha256", "key"}


def test_table_pressure_is_drawn(tmp_path):
    path = _write_csv(tmp_path / "p.csv", [(*p, 100.0 * (3 - p[2])) for p in _grid()], header="x,y,z,pressure")
    project, _ = _wet_wall()
    project.load_case().add_wet_pressure(table=PressureTable.from_file(path))
    mesh = project.generate_mesh(0.5)
    arrows = [a for a in build_attribute_overlay(project, case_name="default", mesh=mesh).arrows
              if a.color == COLOR_WET]
    assert arrows
    by_height = sorted(arrows, key=lambda a: a.end[2])
    assert np.linalg.norm(by_height[0].end - by_height[0].start) > np.linalg.norm(by_height[-1].end - by_height[-1].start)
