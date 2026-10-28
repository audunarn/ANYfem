"""Headless saved wind turbine load definitions: validation, immutability, owned samples / retained Kaimal realization,
deterministic generation and grid refinement, coverage / domain refusals, persistence + format guard, undo / redo."""
import dataclasses
import json

import numpy as np
import pytest

from anyfem.commands import AddWindTurbineLoad, DeleteWindTurbineLoad, EditWindTurbineLoad
from anyfem.io.project_file import (WIND_TURBINE_LOADS_FORMAT, ProjectFileError, project_from_dict,
                                    project_to_dict)
from anyfem.model import Project
from anyfem.model.wind_turbine import WindTurbineLoad, WindTurbineLoadError

CURVE = dict(thrust_speeds=[0.0, 10.0, 20.0], thrust_values=[0.0, 1000.0, 1500.0])
KAIMAL = dict(turbulence_intensity=0.1, integral_scale=340.2, seed=7, timestep=0.5, duration=100.0)


def steady(name="S", **kw):
    args = dict(CURVE, hub_body=[0.0, 0.0, 10.0], hub_node_id=5, wind_mode="steady",
                reference_mean_world=[10.0, 0.0, 0.0])
    args.update(kw)
    return WindTurbineLoad.create(name, **args)


def kaimal(name="K", previous=None, **kw):
    args = dict(CURVE, hub_body=[0.0, 0.0, 10.0], hub_node_id=5, wind_mode="kaimal",
                reference_mean_world=[10.0, 0.0, 0.0], **KAIMAL)
    args.update(kw)
    return WindTurbineLoad.create(name, previous=previous, **args)


def imported(name="I"):
    t = np.linspace(0.0, 10.0, 11)
    v = np.column_stack([10.0 + 0.1 * np.sin(t), 0.2 * np.cos(t), np.zeros_like(t)])
    return WindTurbineLoad.create(name, **CURVE, hub_body=[1.0, 0.0, 10.0], hub_node_id=3, wind_mode="import",
                                  reference_mean_world=[10.0, 0.0, 0.0], import_times=t, import_velocity=v,
                                  import_source="measured.csv")


def test_validation_refuses_impossible_definitions():
    with pytest.raises(WindTurbineLoadError, match="name"):
        steady(name=" ")
    with pytest.raises(WindTurbineLoadError, match="thrust curve"):
        steady(thrust_speeds=[10.0, 5.0, 20.0])
    with pytest.raises(WindTurbineLoadError, match="thrust curve"):
        steady(thrust_values=[0.0, -1.0, 2.0])
    with pytest.raises(WindTurbineLoadError, match="never"):
        steady(rotor_axis=[1.0, 0.1, 0.0])                                 # not normalised silently
    with pytest.raises(WindTurbineLoadError, match="not both"):
        dataclasses.replace(steady(), rotor_axis=(1.0, 0.0, 0.0))          # yaw/tilt AND an axis
    with pytest.raises(WindTurbineLoadError, match="integer"):
        steady(hub_node_id=2.5)
    with pytest.raises(WindTurbineLoadError, match="no Kaimal"):
        dataclasses.replace(steady(), seed=3)                              # a steady wind owns no Kaimal data
    with pytest.raises(WindTurbineLoadError, match="no imported"):
        dataclasses.replace(kaimal(), import_source="x.csv")               # one wind per turbine
    with pytest.raises(WindTurbineLoadError, match="Nyquist"):
        kaimal(max_frequency=2.0)                                           # above 0.5 / timestep
    with pytest.raises(WindTurbineLoadError):
        kaimal(turbulence_intensity=-0.1)
    with pytest.raises(WindTurbineLoadError, match="nonzero declared mean"):
        kaimal(reference_mean_world=[0.0, 0.0, 0.0])
    with pytest.raises(WindTurbineLoadError, match="imported wind"):
        WindTurbineLoad.create("bad", **CURVE, hub_body=[0, 0, 1], hub_node_id=1, wind_mode="import",
                               reference_mean_world=[1, 0, 0], import_times=[0.0, 2.0, 1.0],
                               import_velocity=np.ones((3, 3)))
    k = kaimal()
    with pytest.raises(WindTurbineLoadError, match="Kaimal realization"):   # tampered coefficients are refused
        dataclasses.replace(k, amplitudes=tuple(2.0 * a for a in k.amplitudes))


def test_records_are_immutable_and_explicit_axis_reuses_anyloads_for_yaw_tilt():
    from anyloads.wind_turbine import TurbineLoadApplicator
    s = steady(rotor_yaw_deg=7.0, rotor_tilt_deg=-5.0)
    with pytest.raises(dataclasses.FrozenInstanceError):
        s.hub_node_id = 9
    assert isinstance(s.hub_body, tuple) and isinstance(s.thrust_speeds, tuple)
    d = s.to_dict(); d["hub_body"][2] = 99.0
    assert s.hub_body == (0.0, 0.0, 10.0)
    ref = TurbineLoadApplicator.from_yaw_tilt([0.0, 0.0, 10.0], yaw=np.radians(7.0), tilt=np.radians(-5.0))
    np.testing.assert_array_equal(s.rotor_axis_body(), ref.rotor_axis_body)
    a = steady(rotor_axis=[0.0, 1.0, 0.0])
    assert a.rotor_yaw_deg is None and a.rotor_axis_body().tolist() == [0.0, 1.0, 0.0]


def test_kaimal_is_deterministic_retained_and_refinement_resamples_the_same_realization(monkeypatch):
    import anyloads.wind_turbine as awt
    a, b = kaimal(), kaimal()
    assert a.phases == b.phases and a.amplitudes == b.amplitudes and len(a.frequencies) > 0
    assert kaimal(seed=8).phases != a.phases
    # omitted spectral variance is retained, not redistributed
    real = a.kaimal_realization()
    assert real.missing_low_variance > 0.0 and real.missing_high_variance > 0.0
    assert abs(real.represented_variance + real.missing_low_variance + real.missing_high_variance
               - real.target_variance) <= 1e-9 * real.target_variance
    # identical to the ANYloads generator evaluated directly
    t = np.arange(201) * 0.5
    direct = awt.generate_kaimal_realization(mean_speed=10.0, turbulence_intensity=0.1, integral_scale=340.2,
                                             direction=[1.0, 0.0, 0.0], seed=7, period=100.0, max_frequency=1.0)
    times, vel, ref, prov, r = a.wind_history(t)
    np.testing.assert_array_equal(vel, direct.to_wind_history(t).velocity)
    np.testing.assert_array_equal(ref, [10.0, 0.0, 0.0])                     # declared mean, not the sample mean
    assert prov["kind"] == "kaimal" and prov["seed"] == 7 and prov["turbine"]["id"] == a.id
    # a refined integration grid re-samples the SAME retained realization (no regeneration)
    fine = np.arange(401) * 0.25
    _, vel_fine, _, _, r_fine = a.wind_history(fine)
    np.testing.assert_allclose(vel_fine[::2], vel, rtol=0.0, atol=1e-12)
    np.testing.assert_array_equal(r_fine.phases, r.phases)

    # edits that keep the Kaimal parameters never call the generator again
    def forbidden(**kw):
        raise AssertionError("an unchanged Kaimal parameter set must not be regenerated")
    monkeypatch.setattr(awt, "generate_kaimal_realization", forbidden)
    renamed = kaimal(name="K2", previous=a, hub_body=[0.0, 1.0, 10.0])
    assert renamed.id == a.id and renamed.phases == a.phases and renamed.name == "K2"
    with pytest.raises(AssertionError, match="regenerated"):
        kaimal(previous=a, turbulence_intensity=0.12)                      # a changed parameter regenerates


def test_coverage_and_domain_refusals():
    k = kaimal()
    with pytest.raises(WindTurbineLoadError, match="period"):
        k.wind_history(np.arange(241) * 0.5)                              # beyond the realization: never looped
    with pytest.raises(WindTurbineLoadError, match="aliasing"):
        k.wind_history(np.arange(101) * 1.0)                              # grid too coarse for the retained band
    with pytest.raises(WindTurbineLoadError, match="strictly"):
        k.wind_history([0.0, 0.0, 1.0])
    i = imported()
    t, v, _, prov, _ = i.wind_history(np.linspace(0.0, 10.0, 41))
    assert t.size == 11 and prov["kind"] == "import" and len(prov["sha256"]) == 64   # owned samples, interpolated later
    with pytest.raises(WindTurbineLoadError, match="does not cover"):
        i.wind_history(np.linspace(0.0, 11.0, 12))                        # no extrapolation
    s = steady()
    st, sv, _, _, _ = s.wind_history(np.linspace(0.0, 5.0, 6))
    assert sv.shape == (6, 3) and np.all(sv == [10.0, 0.0, 0.0])


def test_persistence_stores_actual_samples_and_coefficients_with_a_format_guard():
    project = Project()
    plain = project_to_dict(project)
    assert "wind_turbine_loads" not in plain and plain["anyfem"]["format"] < WIND_TURBINE_LOADS_FORMAT
    k, i, s = kaimal(), imported(), steady()
    for load in (k, i, s):
        project.add_wind_turbine_load(load)
    data = json.loads(json.dumps(project_to_dict(project), allow_nan=False))
    assert data["anyfem"]["format"] == WIND_TURBINE_LOADS_FORMAT
    stored = {entry["name"]: entry for entry in data["wind_turbine_loads"]}
    assert stored["I"]["import_velocity"][3] == list(i.import_velocity[3])            # samples, not only a path
    assert stored["K"]["phases"] == list(k.phases) and stored["K"]["seed"] == 7
    back = project_from_dict(data)
    assert back.wind_turbine_loads == project.wind_turbine_loads
    grid = np.arange(201) * 0.5
    np.testing.assert_array_equal(back.wind_turbine_loads[k.id].wind_history(grid)[1], k.wind_history(grid)[1])
    older = dict(data, anyfem=dict(data["anyfem"], format=17))
    with pytest.raises(ProjectFileError, match="format"):
        project_from_dict(older)
    broken = json.loads(json.dumps(data))
    broken["wind_turbine_loads"][0]["unexpected"] = 1
    with pytest.raises(ProjectFileError, match="unknown"):
        project_from_dict(broken)


def test_add_edit_delete_are_reversible_and_touch_no_other_record():
    project = Project()
    cases, results = dict(project.load_cases), project.floating_transient_results
    s = steady()
    add = AddWindTurbineLoad(s); add.do(project)
    assert project.wind_turbine_load("S") is s
    with pytest.raises(ValueError, match="already exists"):
        AddWindTurbineLoad(steady()).do(project)                            # names are unique
    edited = steady(name="S", previous=s, hub_node_id=6)
    edit = EditWindTurbineLoad(edited); edit.do(project)
    assert project.wind_turbine_loads[s.id].hub_node_id == 6
    edit.undo(project)
    assert project.wind_turbine_loads[s.id] is s
    edit.redo(project)
    delete = DeleteWindTurbineLoad(s.id); delete.do(project)
    assert project.wind_turbine_loads == {}
    delete.undo(project)
    assert project.wind_turbine_loads[s.id] is edited
    add_undo = AddWindTurbineLoad(kaimal()); add_undo.do(project); add_undo.undo(project)
    assert list(project.wind_turbine_loads) == [s.id]
    assert project.load_cases == cases and project.floating_transient_results is results


SEQUENCE_FIELDS = ("thrust_speeds", "thrust_values", "hub_body", "reference_mean_world", "import_times",
                   "import_velocity", "frequencies", "bin_energy", "amplitudes", "phases")


def test_inactive_empty_lists_are_detached_tuples_and_cannot_mutate_a_record():
    times, velocity, coefficients = [], [], []
    s = WindTurbineLoad("S", [0.0, 10.0], [0.0, 5.0], [0.0, 0.0, 10.0], 5, "steady", [10.0, 0.0, 0.0],
                        import_times=times, import_velocity=velocity, frequencies=coefficients, phases=coefficients)
    before = s.content_hash()
    assert all(isinstance(getattr(s, key), tuple) for key in SEQUENCE_FIELDS)
    times.append(1.0); velocity.append([1.0, 2.0, 3.0]); coefficients.append(0.5)    # caller-held lists mutate ...
    assert s.import_times == () and s.import_velocity == () and s.frequencies == () and s.phases == ()
    assert s.content_hash() == before                                              # ... the record does not
    with pytest.raises(WindTurbineLoadError, match="no imported"):
        dataclasses.replace(s, import_times=[0.0, 1.0])
    with pytest.raises(WindTurbineLoadError, match="three"):
        dataclasses.replace(s, import_velocity=[[1.0, 2.0]])                       # shape checked before mode rules


@pytest.mark.parametrize("make", [steady, imported, kaimal], ids=["steady", "import", "kaimal"])
def test_every_mode_roundtrips_to_equal_tuples_with_a_stable_content_hash(make):
    load = make()
    data = load.to_dict()
    back = WindTurbineLoad.from_dict(json.loads(json.dumps(data, allow_nan=False)))
    assert back == load and hash(back) == hash(load) and back.content_hash() == load.content_hash()
    assert all(isinstance(getattr(back, key), tuple) for key in SEQUENCE_FIELDS)
    for key in SEQUENCE_FIELDS:                                                    # to_dict hands out copies
        data[key].append(0.0)
    assert back == load and back.content_hash() == load.content_hash()


def test_nonfinite_integers_are_refused_as_definition_errors():
    for bad in (float("inf"), float("nan")):
        with pytest.raises(WindTurbineLoadError, match="integer"):
            steady(hub_node_id=bad)
        with pytest.raises(WindTurbineLoadError, match="integer"):
            kaimal(seed=bad)
